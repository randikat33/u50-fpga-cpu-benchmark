// lm_pipeline.hpp - multi-output (ABR ladder) live-stream pipeline shared by the FPGA host
// and the CPU baseline.
//
//   decoder --(slot: B frames)--> worker k: ladder stage (5 rungs x B frames)
//      ^                              |
//      |                         reorder (job order)
//      |                              |
//      |          +-------+-------+---+---+-------+
//      |        enc240  enc360  enc480  enc720  enc1080   (one thread + ffmpeg each)
//      |          +-------+-------+---+---+-------+
//      +--------- free-slot pool (a slot returns when all 5 encoders have consumed it)
//
// Only the Resizer differs between platforms: FPGA = 1 H2D + 1 kernel start + 1 D2H per job;
// CPU = cv::resize for every (frame, rung).
#pragma once
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <opencv2/opencv.hpp>
#include "bench_common.hpp"
#include "lm_config.hpp"

namespace lm {

struct Options {
    std::string video, sink = "null", out_dir, encoder = "libx264", preset = "ultrafast";
    long frames = 0;             // 0 = whole video
    bool resize_only = false;
    int workers = 0;             // FPGA: CUs, CPU: ladder threads (0 = default)
    int slots_per_worker = 3;    // v1: "3-deep" pipelining
    int batch = 1;               // frames per ladder call (1 = lowest latency)
    long iters = 0;              // resize-only: frames to process
    bool checksum = false, verify = false;
    double fps_override = 0;
    bool fpga_markers = false;   // print the v1 FPGA-host markers instead of the v1 CPU ones

    static void usage(const char* prog, const char* extra) {
        std::printf(
            "usage: %s --video FILE %s\n"
            "  [--sink null|raw|file|hls]  null: no encoders; raw: 5 raw BGR files in --out-dir;\n"
            "                              file: 5 x ffmpeg libx264 -> --out-dir/<rung>.mp4;\n"
            "                              hls: v1 behaviour (--out-dir, default /tmp/hls)\n"
            "  [--frames N] [--batch B (1..%d)] [--workers N] [--slots-per-worker 3]\n"
            "  [--resize-only [--iters N]] [--checksum] [--verify]\n"
            "  [--encoder libx264] [--preset ultrafast] [--fps F]\n",
            prog, extra, LM_MAX_BATCH);
    }
    void parse(const bench::Args& a) {
        video = a.str("video");
        sink = a.str("sink", sink);
        out_dir = a.str("out-dir", sink == "hls" ? "/tmp/hls" : "");
        encoder = a.str("encoder", encoder);
        preset = a.str("preset", preset);
        frames = (long)a.f64("frames", 0);
        resize_only = a.has("resize-only");
        workers = (int)a.f64("workers", 0);
        slots_per_worker = std::max(1, (int)a.f64("slots-per-worker", slots_per_worker));
        batch = (int)a.f64("batch", batch);
        iters = (long)a.f64("iters", 0);
        checksum = a.has("checksum");
        verify = a.has("verify");
        fps_override = a.f64("fps", 0);
        if (video.empty()) throw std::runtime_error("--video is required");
        if (batch < 1 || batch > LM_MAX_BATCH) throw std::runtime_error("--batch must be 1.." + std::to_string(LM_MAX_BATCH));
        if (sink != "null" && sink != "raw" && sink != "file" && sink != "hls") throw std::runtime_error("bad --sink");
        if (sink != "null" && out_dir.empty()) throw std::runtime_error("--sink " + sink + " needs --out-dir");
    }
};

struct Geometry { int in_w = 0, in_h = 0; };
struct StageTimes { double h2d = 0, kernel = 0, d2h = 0, resize = 0; };

class Resizer {
public:
    virtual ~Resizer() = default;
    virtual int workers() const = 0;
    virtual int slots_per_worker() const = 0;
    virtual int batch() const = 0;
    // input: batch() frames, frame f at byte f * in_words(w,h) * 64 (zero padded)
    virtual uint8_t* in_buf(int slot) = 0;
    // output: frame f, rung k at byte (f * out_words_per_frame() + rung_offset(k)) * 64
    virtual uint8_t* out_buf(int slot) = 0;
    virtual void process(int worker, int slot, const Geometry& g, int nframes, StageTimes& t) = 0;
};

inline const uint8_t* rung_ptr(Resizer& rz, int slot, int f, int k) {
    return rz.out_buf(slot) + (f * out_words_per_frame() + rung_offset(k)) * LM_WORD_BYTES;
}

template <typename T>
class BQueue {
    std::deque<T> q_; std::mutex m_; std::condition_variable cv_; bool closed_ = false;
public:
    void push(T v) { { std::lock_guard<std::mutex> g(m_); q_.push_back(std::move(v)); } cv_.notify_one(); }
    bool pop(T& v) {
        std::unique_lock<std::mutex> g(m_);
        cv_.wait(g, [&] { return closed_ || !q_.empty(); });
        if (q_.empty()) return false;
        v = std::move(q_.front()); q_.pop_front(); return true;
    }
    void close() { { std::lock_guard<std::mutex> g(m_); closed_ = true; } cv_.notify_all(); }
};

// One ffmpeg process per rung, v1 encoder settings.
class FfmpegSink {
    FILE* pipe_ = nullptr; pid_t pid_ = -1;
public:
    FfmpegSink(const Options& o, int k, double fps) {
        const Rung& r = kRungs[k];
        const std::string g = std::to_string((int)(fps + 0.5));
        std::string cmd = "exec ffmpeg -hide_banner -loglevel error -y -f rawvideo -pix_fmt bgr24 -s " +
                          std::to_string(r.enc_w) + "x" + std::to_string(r.h) + " -r " + std::to_string(fps) +
                          " -i pipe:0 -c:v " + o.encoder + " -preset " + o.preset +
                          (o.encoder == "libx264" ? " -tune zerolatency" : "") + " -pix_fmt yuv420p -b:v " + r.bitrate +
                          " -maxrate " + r.maxrate + " -bufsize " + r.bufsize + " -g " + g + " -threads " +
                          std::to_string(r.x264_threads);
        if (o.sink == "hls") {
            const std::string d = o.out_dir + "/" + r.name;
            mkdir(d.c_str(), 0755);
            cmd += " -f hls -hls_time 1 -hls_list_size 5 -hls_flags delete_segments+append_list -hls_segment_filename '" + d +
                   "/segment_%03d.ts' '" + d + "/stream.m3u8'";
        } else {
            cmd += " '" + o.out_dir + "/" + r.name + ".mp4'";
        }
        int fd[2];
        // O_CLOEXEC: encoders started later must not inherit this pipe's write end,
        // otherwise closing it here would never deliver EOF to this encoder.
        if (pipe2(fd, O_CLOEXEC)) throw std::runtime_error("pipe2() failed");
        pid_ = fork();
        if (pid_ < 0) throw std::runtime_error("fork() failed");
        if (pid_ == 0) {
            close(fd[1]); dup2(fd[0], STDIN_FILENO); close(fd[0]);
            execl("/bin/sh", "sh", "-c", cmd.c_str(), (char*)nullptr);
            _exit(127);
        }
        close(fd[0]);
        pipe_ = fdopen(fd[1], "wb");
        setvbuf(pipe_, nullptr, _IOFBF, 4 << 20);
    }
    bool write(const uint8_t* p, size_t n) { return std::fwrite(p, 1, n, pipe_) == n; }
    int finish() {
        if (pipe_) { std::fclose(pipe_); pipe_ = nullptr; }
        int st = 0;
        if (pid_ > 0) { waitpid(pid_, &st, 0); pid_ = -1; }
        return WIFEXITED(st) ? WEXITSTATUS(st) : -1;
    }
    ~FfmpegSink() { if (pipe_ || pid_ > 0) finish(); }
};

struct Stats {
    double sum = 0; long n = 0;
    void add(double v) { sum += v; n++; }
    double mean() const { return n ? sum / n : 0; }
};

inline Geometry probe(const std::string& video, double* fps, long* nframes) {
    cv::VideoCapture cap(video, cv::CAP_FFMPEG);
    if (!cap.isOpened()) throw std::runtime_error("cannot open video " + video);
    Geometry g{(int)cap.get(cv::CAP_PROP_FRAME_WIDTH), (int)cap.get(cv::CAP_PROP_FRAME_HEIGHT)};
    if (fps) *fps = cap.get(cv::CAP_PROP_FPS);
    if (nframes) *nframes = (long)cap.get(cv::CAP_PROP_FRAME_COUNT);
    if (!dims_ok(g.in_w, g.in_h))
        throw std::runtime_error("unsupported input " + std::to_string(g.in_w) + "x" + std::to_string(g.in_h) +
                                 " (need width%8==0, 1920x1080 .. 3840x2160)");
    return g;
}

inline void add_common_results(bench::Report& R, const Options& o, const Geometry& g) {
    R.set("project", "live_multi");
    R.set("in_w", g.in_w); R.set("in_h", g.in_h);
    R.set("rungs", LM_NOUT);
    R.set("mode", o.resize_only ? "resize_only" : "pipeline");
    R.set("sink", o.resize_only ? "none" : o.sink);
    R.set("encoder", (o.sink == "file" || o.sink == "hls") ? o.encoder + ":" + o.preset : "none");
    R.set("batch", o.batch);
    R.set("cv_version", CV_VERSION);
    R.set("cv_ipp", cv::ipp::useIPP() ? 1 : 0);
    R.set("cv_threads", cv::getNumThreads());
}

// Writes one rung of one frame, centre-cropped to the encoder width (432->426, 856->854).
class RungWriter {
    std::vector<uint8_t> tmp_;
public:
    const uint8_t* crop(const uint8_t* p, int k) {
        const Rung& r = kRungs[k];
        if (r.enc_w == r.w) return p;
        tmp_.resize(frame_bytes(r.enc_w, r.h));
        const int x0 = (r.w - r.enc_w) / 2;
        for (int y = 0; y < r.h; y++)
            std::memcpy(&tmp_[(size_t)y * r.enc_w * 3], p + ((size_t)y * r.w + x0) * 3, (size_t)r.enc_w * 3);
        return tmp_.data();
    }
};

// ------------------------------------------------------------------------------------------
inline long run_pipeline(const Options& o, Resizer& rz, bench::Report& R) {
    signal(SIGPIPE, SIG_IGN);
    double vfps = 0; long vframes = 0;
    Geometry g = probe(o.video, &vfps, &vframes);
    double fps = o.fps_override > 0 ? o.fps_override : (vfps > 0 ? vfps : 30);
    cv::VideoCapture cap(o.video, cv::CAP_FFMPEG);
    add_common_results(R, o, g);
    R.set("video_fps", fps);
    R.set("video_frames", vframes);
    if (o.sink != "null") mkdir(o.out_dir.c_str(), 0755);

    const int W = rz.workers(), SPW = rz.slots_per_worker(), S = W * SPW, B = rz.batch();
    const size_t in_bytes = frame_bytes(g.in_w, g.in_h), in_step = in_words(g.in_w, g.in_h) * LM_WORD_BYTES;
    struct Job { long seq; int slot; int nframes; };
    BQueue<int> free_slots;
    for (int s = 0; s < S; s++) free_slots.push(s);
    std::vector<std::unique_ptr<BQueue<Job>>> wq(W);
    for (auto& q : wq) q.reset(new BQueue<Job>());
    std::vector<std::unique_ptr<BQueue<Job>>> eq(LM_NOUT);
    for (auto& q : eq) q.reset(new BQueue<Job>());
    std::vector<std::atomic<int>> refs(S);

    std::mutex rm; std::condition_variable rcv;
    std::map<long, Job> ready; long jobs_total = -1;
    std::atomic<bool> failed{false};
    std::vector<Stats> st_h2d(W), st_k(W), st_d2h(W), st_rs(W);
    std::vector<Stats> st_enc(LM_NOUT);
    Stats st_dec;
    std::atomic<long> frames_done{0};

    std::vector<std::unique_ptr<FfmpegSink>> enc(LM_NOUT);
    std::vector<FILE*> rawf(LM_NOUT, nullptr);
    for (int k = 0; k < LM_NOUT; k++) {
        if (o.sink == "file" || o.sink == "hls") enc[k].reset(new FfmpegSink(o, k, fps));
        if (o.sink == "raw") {
            std::string p = o.out_dir + "/" + kRungs[k].name + ".raw";
            rawf[k] = std::fopen(p.c_str(), "wb");
            if (!rawf[k]) throw std::runtime_error("cannot create " + p);
        }
    }
    std::vector<uint64_t> cks(LM_NOUT, 1469598103934665603ull);

    std::printf("%s (v2, %d workers x %d slots, batch %d, sink %s)\n",
                o.fpga_markers ? "Starting 3-deep pipelined processing" : "Starting parallel processing of all 5 resolutions",
                W, SPW, B, o.sink.c_str());
    std::fflush(stdout);
    bench::mark("start");
    const double t0 = bench::now_s();

    std::thread dec([&] {
        long seq = 0, nf_total = 0;
        bool eof = false;
        while (!failed && !eof && (o.frames <= 0 || nf_total < o.frames)) {
            int slot;
            if (!free_slots.pop(slot)) break;
            int n = 0;
            while (n < B && (o.frames <= 0 || nf_total < o.frames)) {
                double a = bench::now_s();
                cv::Mat dst(g.in_h, g.in_w, CV_8UC3, rz.in_buf(slot) + n * in_step);
                if (!cap.read(dst)) { eof = true; break; }
                if (dst.data != rz.in_buf(slot) + n * in_step) std::memcpy(rz.in_buf(slot) + n * in_step, dst.data, in_bytes);
                st_dec.add(bench::now_s() - a);
                n++; nf_total++;
            }
            if (n == 0) { free_slots.push(slot); break; }
            refs[slot] = LM_NOUT;
            wq[slot / SPW]->push(Job{seq++, slot, n});
        }
        for (auto& q : wq) q->close();
        std::lock_guard<std::mutex> lk(rm);
        jobs_total = seq;
        rcv.notify_all();
    });

    std::vector<std::thread> wk;
    for (int w = 0; w < W; w++)
        wk.emplace_back([&, w] {
            Job j;
            while (wq[w]->pop(j)) {
                StageTimes t;
                try { rz.process(w, j.slot, g, j.nframes, t); }
                catch (const std::exception& e) { std::fprintf(stderr, "worker %d: %s\n", w, e.what()); failed = true; }
                st_h2d[w].add(t.h2d); st_k[w].add(t.kernel); st_d2h[w].add(t.d2h); st_rs[w].add(t.resize);
                std::lock_guard<std::mutex> lk(rm);
                ready.emplace(j.seq, j);
                rcv.notify_all();
            }
        });

    std::vector<std::thread> et;
    for (int k = 0; k < LM_NOUT; k++)
        et.emplace_back([&, k] {
            RungWriter rw;
            const size_t nb = frame_bytes(kRungs[k].enc_w, kRungs[k].h);
            Job j;
            while (eq[k]->pop(j)) {
                double a = bench::now_s();
                for (int f = 0; f < j.nframes; f++) {
                    const uint8_t* p = rw.crop(rung_ptr(rz, j.slot, f, k), k);
                    if (enc[k] && !enc[k]->write(p, nb)) { std::fprintf(stderr, "encoder %s closed\n", kRungs[k].name); failed = true; }
                    if (rawf[k] && std::fwrite(p, 1, nb, rawf[k]) != nb) failed = true;
                    if (o.checksum) cks[k] = bench::fnv1a64(p, nb, cks[k]);
                }
                st_enc[k].add(bench::now_s() - a);
                if (--refs[j.slot] == 0) free_slots.push(j.slot);
            }
        });

    long next = 0;
    for (;;) {
        Job j;
        {
            std::unique_lock<std::mutex> lk(rm);
            rcv.wait(lk, [&] { return ready.count(next) || (jobs_total >= 0 && next >= jobs_total) || failed; });
            if (failed || !ready.count(next)) break;
            j = ready[next]; ready.erase(next);
        }
        for (int k = 0; k < LM_NOUT; k++) eq[k]->push(j);
        frames_done += j.nframes;
        next++;
    }
    for (auto& q : eq) q->close();
    for (auto& t : et) t.join();
    free_slots.close();
    dec.join();
    for (auto& t : wk) t.join();
    double t_fin = 0;
    {
        double a = bench::now_s();
        for (int k = 0; k < LM_NOUT; k++) {
            if (enc[k]) { int rc = enc[k]->finish(); if (rc) { failed = true; R.set(std::string("encoder_rc_") + kRungs[k].name, rc); } }
            if (rawf[k]) std::fclose(rawf[k]);
        }
        t_fin = bench::now_s() - a;
    }
    const double t_win = bench::now_s() - t0;
    bench::mark("end");
    std::printf("%s (%ld frames)\n", o.fpga_markers ? "Shutdown signal received." : "Processing Complete", frames_done.load());
    std::fflush(stdout);

    Stats h2d, kk, d2h, rs;
    for (int w = 0; w < W; w++) {
        h2d.sum += st_h2d[w].sum; kk.sum += st_k[w].sum; d2h.sum += st_d2h[w].sum; rs.sum += st_rs[w].sum;
        R.set("jobs_worker" + std::to_string(w), st_rs[w].n);
    }
    const long nf = frames_done.load();
    const double pf = nf > 0 ? 1e3 / nf : 0;   // seconds-sum -> ms per frame
    R.set("frames", nf);
    R.set("jobs", next);
    R.set("workers", W);
    R.set("slots_per_worker", SPW);
    R.set("t_window_s", t_win);
    R.set("t_compute_s", t_win);
    R.set("t_encoder_finish_s", t_fin);
    R.set("fps", nf > 0 ? nf / t_win : 0.0);
    R.set("realtime_factor", nf > 0 ? nf / t_win / fps : 0.0);
    R.set("decode_ms_mean", st_dec.mean() * 1e3);
    R.set("ladder_ms_per_frame", rs.sum * pf);
    R.set("h2d_ms_per_frame", h2d.sum * pf);
    R.set("kernel_ms_per_frame", kk.sum * pf);
    R.set("d2h_ms_per_frame", d2h.sum * pf);
    double enc_sum = 0;
    for (int k = 0; k < LM_NOUT; k++) {
        R.set(std::string("sink_ms_per_frame_") + kRungs[k].name, st_enc[k].sum * pf);
        enc_sum += st_enc[k].sum;
        if (o.checksum) R.set(std::string("out_fnv1a64_") + kRungs[k].name, bench::hex64(cks[k]));
    }
    R.set("sink_ms_per_frame", enc_sum * pf);
    return failed ? -1 : nf;
}

// ------------------------------------------------------------------------------------------
inline long run_resize_only(const Options& o, Resizer& rz, bench::Report& R) {
    Geometry g = probe(o.video, nullptr, nullptr);
    add_common_results(R, o, g);
    cv::VideoCapture cap(o.video, cv::CAP_FFMPEG);
    const int W = rz.workers(), SPW = rz.slots_per_worker(), S = W * SPW, B = rz.batch();
    const long iters = o.iters > 0 ? o.iters : (o.frames > 0 ? o.frames : 120);
    const size_t in_bytes = frame_bytes(g.in_w, g.in_h), in_step = in_words(g.in_w, g.in_h) * LM_WORD_BYTES;
    {
        bench::Timer tt(R, "t_read_s");
        cv::Mat f;
        int nread = 0;
        std::vector<const uint8_t*> seen;
        for (int s = 0; s < S; s++)
            for (int b = 0; b < B; b++) {
                uint8_t* dst = rz.in_buf(s) + b * in_step;
                if (nread == (int)seen.size() && cap.read(f) && f.isContinuous()) {
                    std::memcpy(dst, f.data, in_bytes);
                    nread++;
                } else {
                    if (seen.empty()) throw std::runtime_error("video has no frames");
                    std::memcpy(dst, seen[seen.size() % nread], in_bytes);
                }
                seen.push_back(dst);
            }
        R.set("distinct_frames", nread);
    }
    {   // untimed warm-up
        std::vector<std::thread> th;
        for (int w = 0; w < W; w++)
            th.emplace_back([&, w] { StageTimes t; for (int k = 0; k < SPW; k++) rz.process(w, w * SPW + k, g, B, t); });
        for (auto& t : th) t.join();
    }
    const long jobs = (iters + B - 1) / B;
    std::atomic<long> todo{jobs};
    std::vector<Stats> st_h2d(W), st_k(W), st_d2h(W), st_rs(W);
    std::printf("%s (v2 resize-only, %d workers x %d slots, batch %d, %ld frames)\n",
                o.fpga_markers ? "Starting 3-deep pipelined processing" : "Starting parallel processing of all 5 resolutions",
                W, SPW, B, jobs * B);
    std::fflush(stdout);
    bench::mark("start");
    const double t0 = bench::now_s();
    std::vector<std::thread> th;
    for (int w = 0; w < W; w++)
        th.emplace_back([&, w] {
            int k = 0;
            while (todo.fetch_sub(1) > 0) {
                StageTimes t;
                rz.process(w, w * SPW + k, g, B, t);
                st_h2d[w].add(t.h2d); st_k[w].add(t.kernel); st_d2h[w].add(t.d2h); st_rs[w].add(t.resize);
                k = (k + 1) % SPW;
            }
        });
    for (auto& t : th) t.join();
    const double t_win = bench::now_s() - t0;
    bench::mark("end");
    std::printf("%s (%ld frames)\n", o.fpga_markers ? "Shutdown signal received." : "Processing Complete", jobs * B);
    Stats h2d, kk, d2h, rs;
    for (int w = 0; w < W; w++) { h2d.sum += st_h2d[w].sum; kk.sum += st_k[w].sum; d2h.sum += st_d2h[w].sum; rs.sum += st_rs[w].sum; }
    const double nf = (double)(jobs * B), pf = 1e3 / nf;
    uint64_t cks = 1469598103934665603ull;
    for (int s = 0; s < S; s++)
        for (int b = 0; b < B; b++)
            for (int k = 0; k < LM_NOUT; k++) cks = bench::fnv1a64(rung_ptr(rz, s, b, k), frame_bytes(kRungs[k].w, kRungs[k].h), cks);
    R.set("frames", (long)nf);
    R.set("jobs", jobs);
    R.set("slots", S);
    R.set("workers", W);
    R.set("slots_per_worker", SPW);
    R.set("t_window_s", t_win);
    R.set("t_compute_s", t_win);
    R.set("fps", nf / t_win);
    R.set("mpix_in_per_s", nf * g.in_w * g.in_h / 1e6 / t_win);
    R.set("ladder_ms_per_frame", rs.sum * pf);
    R.set("h2d_ms_per_frame", h2d.sum * pf);
    R.set("kernel_ms_per_frame", kk.sum * pf);
    R.set("d2h_ms_per_frame", d2h.sum * pf);
    R.set("out_fnv1a64", bench::hex64(cks));
    return (long)nf;
}

// ------------------------------------------------------------------------------------------
// Untimed check: every (slot, frame, rung) output vs single-threaded cv::resize(INTER_LINEAR).
// exact=true for the CPU path; FPGA tolerance: max |diff| <= 2 and PSNR >= 40 dB.
inline bool verify_slots(Resizer& rz, const Geometry& g, int frames_per_slot, bool exact, bench::Report& R) {
    const int S = rz.workers() * rz.slots_per_worker();
    const size_t in_step = in_words(g.in_w, g.in_h) * LM_WORD_BYTES;
    int saved = cv::getNumThreads();
    cv::setNumThreads(1);
    double worst = 0, worst_psnr = 1e9;
    bool ok = true;
    for (int s = 0; s < S; s++) {
        // see ls_verify.hpp: re-run the slot so its outputs match the frames now in in_buf(s)
        { StageTimes t; rz.process(s / rz.slots_per_worker(), s, g, frames_per_slot, t); }
        for (int f = 0; f < frames_per_slot; f++) {
            cv::Mat in(g.in_h, g.in_w, CV_8UC3, rz.in_buf(s) + f * in_step);
            for (int k = 0; k < LM_NOUT; k++) {
                const Rung& r = kRungs[k];
                cv::Mat got(r.h, r.w, CV_8UC3, (void*)rung_ptr(rz, s, f, k)), ref, d;
                cv::resize(in, ref, cv::Size(r.w, r.h), 0, 0, cv::INTER_LINEAR);
                cv::absdiff(got, ref, d);
                double mx; cv::minMaxLoc(d.reshape(1), nullptr, &mx);
                double psnr = mx == 0 ? 999.0 : cv::PSNR(got, ref);
                worst = std::max(worst, mx); worst_psnr = std::min(worst_psnr, psnr);
                ok &= exact ? mx == 0 : (mx <= 2 && psnr >= 40);
            }
        }
    }
    cv::setNumThreads(saved);
    R.set("verify_max_absdiff", worst);
    R.set("verify_min_psnr_db", worst_psnr);
    R.set("verified", ok ? 1 : 0);
    return ok;
}

}  // namespace lm
