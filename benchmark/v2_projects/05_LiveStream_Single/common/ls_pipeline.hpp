// ls_pipeline.hpp - live-stream pipeline shared by the FPGA host and the CPU baseline.
//
//   decoder thread --(slot, seq)--> worker k (resize stage) --> reorder --> encoder/sink
//        ^                                                                     |
//        +-------------------------- free-slot pool <--------------------------+
//
// The ONLY platform-specific part is the Resizer (FPGA: H2D + kernel + D2H on the CU that
// owns the slot; CPU: cv::resize). Decoding (OpenCV VideoCapture / FFmpeg), frame order
// restoration, encoding (ffmpeg libx264 subprocess, as in v1) and all timers are identical
// code on both platforms.
//
// Slots: every worker owns `slots_per_worker` input/output buffer pairs. The decoder writes
// each frame straight into a free slot's input buffer (for the FPGA this is the mapped BO
// memory, so there is no extra host copy). The number of slots bounds the frames in flight
// and gives back-pressure from the encoder to the decoder.
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
#include <sys/types.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <opencv2/opencv.hpp>
#include "bench_common.hpp"
#include "ls_config.hpp"

namespace ls {

// ------------------------------------------------------------------------------------
struct Options {
    std::string video, sink = "null", rtmp_url = "rtmp://localhost/live/stream", out, encoder = "libx264",
                preset = "ultrafast";
    int quality = 4;            // 0..4 = 240p..1080p
    long frames = 0;            // 0 = whole video (pipeline) / 120 (resize-only)
    bool resize_only = false;
    int workers = 0;            // FPGA: CUs, CPU: resize threads (0 = default)
    int slots_per_worker = 2;
    int iters = 0;              // resize-only: frames to process (0 = frames)
    bool checksum = false;
    bool verify = false;
    double fps_override = 0;
    bool audio = true;          // rtmp sink muxes the source audio (v1 behaviour)

    static void usage(const char* prog, const char* extra) {
        std::printf(
            "usage: %s --video FILE %s\n"
            "  [--quality 0..4]        output 240p/360p/480p/720p/1080p (default 4)\n"
            "  [--sink null|file|raw|rtmp] null: no encoder; file: ffmpeg libx264 -> --out F (.mp4);\n"
            "                          raw: raw BGR frames -> --out F; rtmp: v1 behaviour (--rtmp-url base)\n"
            "  [--frames N]            stop after N frames (0 = all)\n"
            "  [--resize-only]         pre-decode frames, loop only the resize stage (--iters N)\n"
            "  [--workers N] [--slots-per-worker 2] [--checksum] [--verify]\n"
            "  [--encoder libx264] [--preset ultrafast] [--no-audio] [--fps F]\n",
            prog, extra);
    }
    void parse(const bench::Args& a) {
        video = a.str("video", video);
        sink = a.str("sink", sink);
        rtmp_url = a.str("rtmp-url", rtmp_url);
        out = a.str("out", out);
        encoder = a.str("encoder", encoder);
        preset = a.str("preset", preset);
        quality = (int)a.f64("quality", quality);
        frames = (long)a.f64("frames", (double)frames);
        resize_only = a.has("resize-only");
        workers = (int)a.f64("workers", workers);
        slots_per_worker = std::max(1, (int)a.f64("slots-per-worker", slots_per_worker));
        iters = (int)a.f64("iters", iters);
        checksum = a.has("checksum");
        verify = a.has("verify");
        fps_override = a.f64("fps", 0);
        audio = !a.has("no-audio");
        if (quality < 0 || quality > 4) throw std::runtime_error("--quality must be 0..4");
        if (video.empty()) throw std::runtime_error("--video is required");
        if ((sink == "file" || sink == "raw") && out.empty()) throw std::runtime_error("--sink " + sink + " needs --out");
        if (sink != "null" && sink != "file" && sink != "raw" && sink != "rtmp") throw std::runtime_error("bad --sink");
    }
};

struct Geometry { int in_w = 0, in_h = 0, out_w = 0, out_h = 0; };

// Timers of one processed frame, filled by the Resizer (seconds).
struct StageTimes { double h2d = 0, kernel = 0, d2h = 0, resize = 0; };

// Platform-specific part.
class Resizer {
public:
    virtual ~Resizer() = default;
    virtual int workers() const = 0;
    virtual int slots_per_worker() const = 0;
    virtual uint8_t* in_buf(int slot) = 0;    // >= padded_bytes(in_w, in_h), padding zero
    virtual uint8_t* out_buf(int slot) = 0;   // >= padded_bytes(out_w, out_h)
    // Resize the frame in in_buf(slot) into out_buf(slot). Called only from worker `worker`
    // (the owner of `slot`), so per-worker state needs no locking.
    virtual void process(int worker, int slot, const Geometry& g, StageTimes& t) = 0;
    virtual void on_worker_start(int /*worker*/) {}
};

// ------------------------------------------------------------------------------------
template <typename T>
class BQueue {   // bounded blocking queue with close()
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

// ffmpeg subprocess sink (rawvideo bgr24 on stdin), same encoder settings as v1.
class FfmpegSink {
    FILE* pipe_ = nullptr; pid_t pid_ = -1;
public:
    FfmpegSink(const Options& o, const Geometry& g, double fps) {
        char size[32]; std::snprintf(size, sizeof size, "%dx%d", g.out_w, g.out_h);
        std::string cmd = "exec ffmpeg -hide_banner -loglevel error -y -f rawvideo -pix_fmt bgr24 -s " +
                          std::string(size) + " -r " + std::to_string(fps) + " -i pipe:0 ";
        const std::string tune = (o.encoder == "libx264") ? " -tune zerolatency" : "";
        if (o.sink == "rtmp") {
            if (o.audio) cmd += "-i \"" + o.video + "\" -map 0:v:0 -map 1:a:0? -c:a aac -b:a 128k -shortest ";
            cmd += "-c:v " + o.encoder + " -preset " + o.preset + tune + " -f flv \"" + o.rtmp_url + "_" +
                   kQualities[o.quality].name + "\"";
        } else {   // file
            cmd += "-c:v " + o.encoder + " -preset " + o.preset + tune + " \"" + o.out + "\"";
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
        setvbuf(pipe_, nullptr, _IOFBF, 8 << 20);
    }
    bool write(const uint8_t* p, size_t n) { return std::fwrite(p, 1, n, pipe_) == n; }
    int finish() {   // flush, close stdin, wait for the encoder to finish the file/stream
        if (pipe_) { std::fclose(pipe_); pipe_ = nullptr; }
        int st = 0;
        if (pid_ > 0) { waitpid(pid_, &st, 0); pid_ = -1; }
        return WIFEXITED(st) ? WEXITSTATUS(st) : -1;
    }
    ~FfmpegSink() { if (pipe_ || pid_ > 0) finish(); }
};

inline void add_common_results(bench::Report& R, const Options& o, const Geometry& g) {
    R.set("project", "live_single");
    R.set("quality", o.quality);
    R.set("quality_name", kQualities[o.quality].name);
    R.set("in_w", g.in_w); R.set("in_h", g.in_h); R.set("out_w", g.out_w); R.set("out_h", g.out_h);
    R.set("mode", o.resize_only ? "resize_only" : "pipeline");
    R.set("sink", o.resize_only ? "none" : o.sink);
    R.set("encoder", o.sink == "file" || o.sink == "rtmp" ? o.encoder + ":" + o.preset : "none");
    R.set("cv_version", CV_VERSION);
    R.set("cv_ipp", cv::ipp::useIPP() ? 1 : 0);
    R.set("cv_threads", cv::getNumThreads());
}

struct Stats {   // running mean/max of a per-frame quantity
    double sum = 0, mx = 0; long n = 0;
    void add(double v) { sum += v; mx = std::max(mx, v); n++; }
    double mean() const { return n ? sum / n : 0; }
};

// ------------------------------------------------------------------------------------
// Full pipeline: decode -> resize -> (reorder) -> sink. Returns frames processed.
inline long run_pipeline(const Options& o, Resizer& rz, bench::Report& R, uint64_t* checksum_out) {
    signal(SIGPIPE, SIG_IGN);
    cv::VideoCapture cap(o.video, cv::CAP_FFMPEG);
    if (!cap.isOpened()) throw std::runtime_error("cannot open video " + o.video);
    Geometry g;
    g.in_w = (int)cap.get(cv::CAP_PROP_FRAME_WIDTH);
    g.in_h = (int)cap.get(cv::CAP_PROP_FRAME_HEIGHT);
    g.out_w = kQualities[o.quality].w;
    g.out_h = kQualities[o.quality].h;
    if (!dims_ok(g.in_w, g.in_h, g.out_w, g.out_h))
        throw std::runtime_error("unsupported geometry " + std::to_string(g.in_w) + "x" + std::to_string(g.in_h));
    double fps = o.fps_override > 0 ? o.fps_override : cap.get(cv::CAP_PROP_FPS);
    if (!(fps > 0)) fps = 30;
    add_common_results(R, o, g);
    R.set("video_fps", fps);
    R.set("video_frames", (long)cap.get(cv::CAP_PROP_FRAME_COUNT));

    const int W = rz.workers(), SPW = rz.slots_per_worker(), S = W * SPW;
    const size_t in_bytes = frame_bytes(g.in_w, g.in_h), out_bytes = frame_bytes(g.out_w, g.out_h);
    struct Job { long seq; int slot; double t_dec; };
    BQueue<int> free_slots;
    for (int s = 0; s < S; s++) free_slots.push(s);
    std::vector<std::unique_ptr<BQueue<Job>>> wq(W);
    for (auto& q : wq) q.reset(new BQueue<Job>());

    std::mutex rm; std::condition_variable rcv;
    std::map<long, Job> ready; long decoded_total = -1;   // set when the decoder is done
    std::atomic<bool> failed{false};
    std::vector<Stats> st_h2d(W), st_k(W), st_d2h(W), st_rs(W);
    Stats st_dec, st_enc;
    std::atomic<long> copies{0};

    std::unique_ptr<FfmpegSink> sink;
    FILE* rawf = nullptr;
    if (o.sink == "file" || o.sink == "rtmp") sink.reset(new FfmpegSink(o, g, fps));
    if (o.sink == "raw") { rawf = std::fopen(o.out.c_str(), "wb"); if (!rawf) throw std::runtime_error("cannot create " + o.out); }

    std::printf("Starting pipeline (v2, %d workers x %d slots, quality %s, sink %s)\n", W, SPW,
                kQualities[o.quality].name, o.sink.c_str());
    std::fflush(stdout);
    bench::mark("start");
    const double t0 = bench::now_s();

    // ---- decoder
    std::thread dec([&] {
        long seq = 0;
        cv::Mat tmp;
        while (!failed && (o.frames <= 0 || seq < o.frames)) {
            int slot;
            if (!free_slots.pop(slot)) break;
            double a = bench::now_s();
            cv::Mat dst(g.in_h, g.in_w, CV_8UC3, rz.in_buf(slot));   // decode straight into the slot
            if (!cap.read(dst)) { free_slots.push(slot); break; }
            if (dst.data != rz.in_buf(slot)) {   // backend reallocated (should not happen)
                std::memcpy(rz.in_buf(slot), dst.data, in_bytes);
                copies++;
            }
            double b = bench::now_s();
            st_dec.add(b - a);
            wq[slot / SPW]->push(Job{seq++, slot, b - a});
        }
        for (auto& q : wq) q->close();
        std::lock_guard<std::mutex> lk(rm);
        decoded_total = seq;
        rcv.notify_all();
    });

    // ---- workers
    std::vector<std::thread> wk;
    for (int w = 0; w < W; w++)
        wk.emplace_back([&, w] {
            rz.on_worker_start(w);
            Job j;
            while (wq[w]->pop(j)) {
                StageTimes t;
                try { rz.process(w, j.slot, g, t); }
                catch (const std::exception& e) {
                    std::fprintf(stderr, "worker %d: %s\n", w, e.what());
                    failed = true;
                }
                st_h2d[w].add(t.h2d); st_k[w].add(t.kernel); st_d2h[w].add(t.d2h); st_rs[w].add(t.resize);
                std::lock_guard<std::mutex> lk(rm);
                ready.emplace(j.seq, j);
                rcv.notify_all();
            }
        });

    // ---- encoder / sink (this thread): strictly in decode order
    long next = 0;
    uint64_t cks = 1469598103934665603ull;
    double t_first_out = 0;
    for (;;) {
        Job j;
        {
            std::unique_lock<std::mutex> lk(rm);
            rcv.wait(lk, [&] { return ready.count(next) || (decoded_total >= 0 && next >= decoded_total) || failed; });
            if (failed || (!ready.count(next))) break;
            j = ready[next]; ready.erase(next);
        }
        double a = bench::now_s();
        const uint8_t* p = rz.out_buf(j.slot);
        if (sink && !sink->write(p, out_bytes)) { std::fprintf(stderr, "encoder pipe closed\n"); failed = true; }
        if (rawf && std::fwrite(p, 1, out_bytes, rawf) != out_bytes) failed = true;
        if (o.checksum) cks = bench::fnv1a64(p, out_bytes, cks);
        st_enc.add(bench::now_s() - a);
        if (next == 0) t_first_out = bench::now_s() - t0;
        free_slots.push(j.slot);
        next++;
    }
    free_slots.close();
    dec.join();
    for (auto& t : wk) t.join();
    double t_enc_finish = 0;
    if (sink) { double a = bench::now_s(); int rc = sink->finish(); t_enc_finish = bench::now_s() - a; if (rc) failed = true; R.set("encoder_rc", rc); }
    if (rawf) std::fclose(rawf);
    const double t_win = bench::now_s() - t0;
    bench::mark("end");
    std::printf("Consumer finished (%ld frames)\n", next);
    std::fflush(stdout);

    Stats h2d, k, d2h, rs;
    for (int w = 0; w < W; w++) {
        h2d.sum += st_h2d[w].sum; h2d.n += st_h2d[w].n;
        k.sum += st_k[w].sum; k.n += st_k[w].n;
        d2h.sum += st_d2h[w].sum; d2h.n += st_d2h[w].n;
        rs.sum += st_rs[w].sum; rs.n += st_rs[w].n;
        R.set("frames_worker" + std::to_string(w), st_rs[w].n);
    }
    R.set("frames", next);
    R.set("workers", W);
    R.set("slots_per_worker", SPW);
    R.set("t_window_s", t_win);
    R.set("t_compute_s", t_win);                  // pipeline boundary = whole processing window
    R.set("t_first_frame_s", t_first_out);
    R.set("t_encoder_finish_s", t_enc_finish);
    R.set("fps", next > 0 ? next / t_win : 0.0);
    R.set("realtime_factor", next > 0 ? (next / t_win) / fps : 0.0);
    R.set("decode_ms_mean", st_dec.mean() * 1e3);
    R.set("sink_ms_mean", st_enc.mean() * 1e3);
    R.set("resize_ms_mean", rs.mean() * 1e3);     // whole resize stage per frame (FPGA: h2d+kernel+d2h)
    R.set("h2d_ms_mean", h2d.mean() * 1e3);
    R.set("kernel_ms_mean", k.mean() * 1e3);
    R.set("d2h_ms_mean", d2h.mean() * 1e3);
    R.set("resize_stage_s_sum", rs.sum);
    R.set("decode_copies", copies.load());
    if (o.checksum) R.set("out_fnv1a64", bench::hex64(cks));
    if (checksum_out) *checksum_out = cks;
    return failed ? -1 : next;
}

// ------------------------------------------------------------------------------------
// Resize-only benchmark: N distinct decoded frames are placed in the slots once; the
// workers then loop over their own slots until `iters` frames have been resized.
inline long run_resize_only(const Options& o, Resizer& rz, bench::Report& R, uint64_t* checksum_out) {
    cv::VideoCapture cap(o.video, cv::CAP_FFMPEG);
    if (!cap.isOpened()) throw std::runtime_error("cannot open video " + o.video);
    Geometry g;
    g.in_w = (int)cap.get(cv::CAP_PROP_FRAME_WIDTH);
    g.in_h = (int)cap.get(cv::CAP_PROP_FRAME_HEIGHT);
    g.out_w = kQualities[o.quality].w;
    g.out_h = kQualities[o.quality].h;
    if (!dims_ok(g.in_w, g.in_h, g.out_w, g.out_h)) throw std::runtime_error("unsupported geometry");
    add_common_results(R, o, g);
    const int W = rz.workers(), SPW = rz.slots_per_worker(), S = W * SPW;
    const long iters = o.iters > 0 ? o.iters : (o.frames > 0 ? o.frames : 120);
    const size_t in_bytes = frame_bytes(g.in_w, g.in_h), out_bytes = frame_bytes(g.out_w, g.out_h);
    {   // untimed: decode S distinct frames into the slots
        bench::Timer tt(R, "t_read_s");
        cv::Mat f;
        int nread = 0;
        for (int s = 0; s < S; s++) {
            if (nread == s && cap.read(f) && f.isContinuous()) {
                std::memcpy(rz.in_buf(s), f.data, in_bytes);
                nread++;
            } else {   // short video: repeat earlier frames
                if (nread == 0) throw std::runtime_error("video has no frames");
                std::memcpy(rz.in_buf(s), rz.in_buf(s % nread), in_bytes);
            }
        }
        R.set("distinct_frames", nread);
    }
    {   // untimed warm-up: every slot once
        std::vector<std::thread> th;
        for (int w = 0; w < W; w++)
            th.emplace_back([&, w] { rz.on_worker_start(w); StageTimes t; for (int k = 0; k < SPW; k++) rz.process(w, w * SPW + k, g, t); });
        for (auto& t : th) t.join();
    }
    std::atomic<long> todo{iters};
    std::vector<Stats> st_h2d(W), st_k(W), st_d2h(W), st_rs(W);
    std::printf("Starting pipeline (v2 resize-only, %d workers x %d slots, %ld frames, quality %s)\n", W, SPW, iters,
                kQualities[o.quality].name);
    std::fflush(stdout);
    bench::mark("start");
    const double t0 = bench::now_s();
    std::vector<std::thread> th;
    for (int w = 0; w < W; w++)
        th.emplace_back([&, w] {
            int k = 0;
            while (todo.fetch_sub(1) > 0) {
                StageTimes t;
                rz.process(w, w * SPW + k, g, t);
                st_h2d[w].add(t.h2d); st_k[w].add(t.kernel); st_d2h[w].add(t.d2h); st_rs[w].add(t.resize);
                k = (k + 1) % SPW;
            }
        });
    for (auto& t : th) t.join();
    const double t_win = bench::now_s() - t0;
    bench::mark("end");
    std::printf("Consumer finished (%ld frames)\n", iters);
    Stats h2d, kk, d2h, rs;
    for (int w = 0; w < W; w++) {
        h2d.sum += st_h2d[w].sum; h2d.n += st_h2d[w].n;
        kk.sum += st_k[w].sum; kk.n += st_k[w].n;
        d2h.sum += st_d2h[w].sum; d2h.n += st_d2h[w].n;
        rs.sum += st_rs[w].sum; rs.n += st_rs[w].n;
    }
    // checksum over the S output slots (deterministic content: S distinct frames)
    uint64_t cks = 1469598103934665603ull;
    for (int s = 0; s < S; s++) cks = bench::fnv1a64(rz.out_buf(s), out_bytes, cks);
    R.set("frames", iters);
    R.set("slots", S);
    R.set("workers", W);
    R.set("slots_per_worker", SPW);
    R.set("t_window_s", t_win);
    R.set("t_compute_s", t_win);
    R.set("fps", iters / t_win);
    R.set("mpix_per_s", iters * (double)g.in_w * g.in_h / 1e6 / t_win);
    R.set("resize_ms_mean", rs.mean() * 1e3);
    R.set("h2d_ms_mean", h2d.mean() * 1e3);
    R.set("kernel_ms_mean", kk.mean() * 1e3);
    R.set("d2h_ms_mean", d2h.mean() * 1e3);
    R.set("out_fnv1a64", bench::hex64(cks));
    if (checksum_out) *checksum_out = cks;
    return iters;
}

}  // namespace ls
