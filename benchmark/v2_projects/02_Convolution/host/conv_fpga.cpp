// conv_fpga.cpp - Alveo U50 host for the v2 3x3 convolution kernels (XRT native C++ API).
//
// Data path (no host-side packing): the PNG/raw decoder writes every image row directly
// into the mapped memory of the input buffer objects (rows padded to 64 bytes); the 2
// overlap rows between strips are memcpy'd. The accelerated section is an asynchronous
// 3-stage pipeline:  H2D thread(s) -> one run thread per CU -> D2H thread pool,
// so uploads of strip k+1 overlap the kernel run and download of strip k.
// Outputs are read directly from the output BO memory (row pointers), rows 0 and H-1
// (always zero) point to a shared zero row.
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "bench_common.hpp"
#include "conv_params.hpp"
#include "conv_plan.hpp"
#include "conv_ref.hpp"
#include "img_io.hpp"

using namespace conv;

#ifndef SIM_CUS
#define SIM_CUS 2
#endif

static void usage() {
    std::puts(
        "conv_fpga - U50 3x3 convolution (sharpen, edge, blur), XRT native API\n"
        "  --xclbin X                   bitstream (required)        --device N (default 0)\n"
        "  --variant rgb16|rgb8|gray8   kernel conv3_<variant> (default rgb8)\n"
        "  --in IMAGE                   .png, or raw interleaved samples (needs --width --height)\n"
        "  --synthetic WxH              generated input instead of --in\n"
        "  --out-prefix P               write P_sharpen/P_edge/P_blur (.png, or .raw with --out-format raw)\n"
        "  --out-format png|raw         (default png)   --png-level N  zlib level (default 1)\n"
        "  --repeat N                   repeat the accelerated section N times (default 1)\n"
        "  --cus N                      use at most N compute units (auto-detect; xrt_sim: build default)\n"
        "  --strip-mb M                 target input MB per strip (default 64; 0 = one strip per CU)\n"
        "  --strip-rows R               force R output rows per strip\n"
        "  --max-bo-mb M                per-BO size cap (default 1024)\n"
        "  --h2d-threads N / --d2h-threads N   transfer threads (default 1 / 3)\n"
        "  --threads N                  host threads for synthetic input generation / verify\n"
        "  --verify                     compare with the scalar reference (untimed)\n"
        "RESULT keys: project platform impl variant width height channels bits mpix n_cu n_strips repeat\n"
        "  bytes_in bytes_out t_xclbin_s t_alloc_s t_read_s t_pack_s t_h2d_s t_kernel_s t_d2h_s\n"
        "  t_accel_window_s t_compute_s t_compute_min_s t_write_s t_cksum_s t_verify_s t_total_s\n"
        "  mpix_per_s mpix_per_s_kernel h2d_gbps d2h_gbps cks_sharpen cks_edge cks_blur verify ok");
}

// ---------------------------------------------------------------- blocking queue
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

struct StripBufs {
    Strip s;
    xrt::bo in, out[3];
    uint8_t* in_map = nullptr;
    uint8_t* out_map[3] = {nullptr, nullptr, nullptr};
    size_t in_bytes = 0, out_bytes = 0;
};

struct HostCtx {
    ImageSpec sp;
    size_t stride = 0;
    std::vector<StripBufs> strips;
    // row y -> list of (strip, local row) that contain it; first entry is the decode target
    std::vector<std::vector<std::pair<int, int>>> row_map;
    uint8_t* row_ptr(int y) { auto& e = row_map[y][0]; return strips[e.first].in_map + (size_t)e.second * stride; }
};

static uint8_t* host_row(void* ctx, int y) { return static_cast<HostCtx*>(ctx)->row_ptr(y); }

// ---------------------------------------------------------------- pipeline
struct Round {
    std::mutex m; std::condition_variable cv;
    int remaining = 0; bool failed = false; std::string err;
    double t_h2d = 0, t_run = 0, t_d2h = 0;
    void add(double& acc, double v) { std::lock_guard<std::mutex> g(m); acc += v; }
    void fail(const std::string& e) { { std::lock_guard<std::mutex> g(m); failed = true; if (err.empty()) err = e; } cv.notify_all(); }
    void done_one() { { std::lock_guard<std::mutex> g(m); --remaining; } cv.notify_all(); }
};

class Pipeline {
    HostCtx& hc;
    std::vector<xrt::kernel>& krn;
    int n_h2d, n_d2h;
    std::vector<std::thread> th;
    BQueue<std::pair<Round*, int>> q_h2d;
    std::vector<std::unique_ptr<BQueue<std::pair<Round*, int>>>> q_cu;
    BQueue<std::tuple<Round*, int, int>> q_d2h;

public:
    Pipeline(HostCtx& h, std::vector<xrt::kernel>& k, int nh, int nd) : hc(h), krn(k), n_h2d(nh), n_d2h(nd) {
        for (size_t c = 0; c < krn.size(); ++c) q_cu.emplace_back(new BQueue<std::pair<Round*, int>>());
        for (int i = 0; i < n_h2d; ++i) th.emplace_back([this] { h2d_loop(); });
        for (size_t c = 0; c < krn.size(); ++c) th.emplace_back([this, c] { cu_loop((int)c); });
        for (int i = 0; i < n_d2h; ++i) th.emplace_back([this] { d2h_loop(); });
    }
    ~Pipeline() {
        q_h2d.close(); for (auto& q : q_cu) q->close(); q_d2h.close();
        for (auto& t : th) t.join();
    }
    void run(Round& r) {
        r.remaining = 3 * (int)hc.strips.size();
        for (int k = 0; k < (int)hc.strips.size(); ++k) q_h2d.push({&r, k});
        std::unique_lock<std::mutex> g(r.m);
        r.cv.wait(g, [&] { return r.remaining == 0 || r.failed; });
    }

private:
    void h2d_loop() {
        std::pair<Round*, int> it;
        while (q_h2d.pop(it)) {
            Round& r = *it.first; StripBufs& sb = hc.strips[it.second];
            try {
                const double t0 = bench::now_s();
                sb.in.sync(XCL_BO_SYNC_BO_TO_DEVICE, sb.in_bytes, 0);
                r.add(r.t_h2d, bench::now_s() - t0);
                q_cu[sb.s.cu]->push(it);
            } catch (const std::exception& e) { r.fail(std::string("H2D: ") + e.what()); }
        }
    }
    void cu_loop(int c) {
        xrt::run run(krn[c]);
        std::pair<Round*, int> it;
        while (q_cu[c]->pop(it)) {
            Round& r = *it.first; StripBufs& sb = hc.strips[it.second];
            try {
                const double t0 = bench::now_s();
                run.set_arg(0, sb.in);
                run.set_arg(1, sb.out[0]);
                run.set_arg(2, sb.out[1]);
                run.set_arg(3, sb.out[2]);
                run.set_arg(4, static_cast<uint32_t>(hc.sp.w));
                run.set_arg(5, static_cast<uint32_t>(sb.s.n_in));
                run.set_arg(6, static_cast<uint32_t>(sb.in_bytes / 64));
                run.set_arg(7, static_cast<uint32_t>(sb.out_bytes / 64));
                run.start();
                run.wait2();   // throws if the command does not complete successfully
                r.add(r.t_run, bench::now_s() - t0);
                for (int j = 0; j < 3; ++j) q_d2h.push(std::make_tuple(&r, it.second, j));
            } catch (const std::exception& e) { r.fail(std::string("kernel: ") + e.what()); }
        }
    }
    void d2h_loop() {
        std::tuple<Round*, int, int> it;
        while (q_d2h.pop(it)) {
            Round& r = *std::get<0>(it); StripBufs& sb = hc.strips[std::get<1>(it)];
            try {
                const double t0 = bench::now_s();
                sb.out[std::get<2>(it)].sync(XCL_BO_SYNC_BO_FROM_DEVICE, sb.out_bytes, 0);
                r.add(r.t_d2h, bench::now_s() - t0);
                r.done_one();
            } catch (const std::exception& e) { r.fail(std::string("D2H: ") + e.what()); }
        }
    }
};

// ---------------------------------------------------------------- main
int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Args a(argc, argv);
    bench::Report rep;
    if (a.has("help") || a.has("h")) { usage(); return 0; }
    int ok = 1;
    try {
        if (!a.has("xclbin")) throw std::runtime_error("--xclbin PATH is required (see --help)");
        const VariantInfo vi = variant_info(a.str("variant", "rgb8"));
        const int repeat = std::max(1, (int)a.i64("repeat", 1));
        const int threads = (int)a.i64("threads", std::max(1u, std::thread::hardware_concurrency()));
        rep.set("project", "conv"); rep.set("platform", "fpga"); rep.set("impl", "u50_vecstencil");
        rep.set("variant", vi.name); rep.set("repeat", repeat);

        Source src; src.open(a, vi);
        HostCtx hc; hc.sp = src.spec;
        const ImageSpec& sp = hc.sp;
        hc.stride = sp.stride_bytes();
        rep.set("width", sp.w); rep.set("height", sp.h); rep.set("channels", sp.C); rep.set("bits", sp.bps * 8);
        rep.set("mpix", sp.mpix()); rep.set("input", src.label());
        if (sp.w > CONV_MAX_WIDTH)
            throw std::runtime_error("width " + std::to_string(sp.w) + " > CONV_MAX_WIDTH (" + std::to_string(CONV_MAX_WIDTH) + ") of the bitstream");
        const bool trivial = sp.h < 3 || sp.w < 3;   // all-zero outputs, no kernel call

        // ---- device, xclbin, compute units
        xrt::device dev;
        xrt::uuid uuid;
        std::vector<xrt::kernel> krn;
        {
            bench::Timer t(rep, "t_xclbin_s");
            dev = xrt::device(static_cast<unsigned int>(a.i64("device", 0)));
            uuid = dev.load_xclbin(a.str("xclbin"));
            int max_cu = (int)a.i64("cus", 16);
#ifdef XRT_SIM
            if (!a.has("cus")) max_cu = SIM_CUS;   // xrt_sim accepts any CU name
#endif
            for (int i = 1; i <= max_cu; ++i) {
                const std::string nm = std::string(vi.top) + ":{" + vi.top + "_" + std::to_string(i) + "}";
                try { krn.emplace_back(dev, uuid, nm); } catch (const std::exception&) { break; }
            }
            if (krn.empty()) throw std::runtime_error(std::string("no compute unit ") + vi.top + "_1 in the xclbin");
        }
        const int n_cu = (int)krn.size();
        rep.set("n_cu", n_cu);

        // ---- strip plan + buffer objects (allocated once, reused by all repeats)
        const size_t max_bo = (size_t)(a.f64("max-bo-mb", 1024) * 1048576.0);
        const size_t target = (size_t)(a.f64("strip-mb", 64) * 1048576.0);
        std::vector<Strip> plan = trivial ? std::vector<Strip>{}
                                          : plan_strips(sp.h, hc.stride, n_cu, max_bo, target, (int)a.i64("strip-rows", 0));
        rep.set("n_strips", (int)plan.size());
        size_t bytes_in = 0, bytes_out = 0;
        std::vector<uint8_t> zero_row(sp.row_bytes() + 1, 0);
        bench::AlignedBuf<uint8_t> trivial_in;
        {
            bench::Timer t(rep, "t_alloc_s");
            hc.strips.resize(plan.size());
            for (size_t k = 0; k < plan.size(); ++k) {
                StripBufs& sb = hc.strips[k];
                sb.s = plan[k];
                sb.in_bytes = (size_t)sb.s.n_in * hc.stride;
                sb.out_bytes = (size_t)sb.s.n_out * hc.stride;
                const xrt::kernel& kk = krn[sb.s.cu];
                try {
                    sb.in = xrt::bo(dev, sb.in_bytes, xrt::bo::flags::normal, kk.group_id(0));
                    for (int j = 0; j < 3; ++j) sb.out[j] = xrt::bo(dev, sb.out_bytes, xrt::bo::flags::normal, kk.group_id(1 + j));
                } catch (const std::exception& e) {
                    throw std::runtime_error("buffer allocation failed for strip " + std::to_string(k) + " (" +
                                             std::to_string(sb.in_bytes >> 20) + " MB in / " + std::to_string(sb.out_bytes >> 20) +
                                             " MB out, CU " + std::to_string(sb.s.cu + 1) + "): " + e.what() +
                                             " -- reduce --strip-mb / --max-bo-mb");
                }
                sb.in_map = sb.in.map<uint8_t*>();
                for (int j = 0; j < 3; ++j) sb.out_map[j] = sb.out[j].map<uint8_t*>();
                bytes_in += sb.in_bytes; bytes_out += 3 * sb.out_bytes;
            }
            hc.row_map.assign(sp.h, {});
            for (int k = 0; k < (int)plan.size(); ++k)
                for (int i = 0; i < plan[k].n_in; ++i) hc.row_map[plan[k].in_y0 + i].push_back({k, i});
            if (trivial) {   // no BOs: decode into host memory only (needed for --verify)
                trivial_in.alloc(sp.row_bytes() * sp.h + 1);
                hc.stride = sp.row_bytes();
            }
        }
        rep.set("bytes_in", (unsigned long long)bytes_in);
        rep.set("bytes_out", (unsigned long long)bytes_out);

        // ---- read / decode directly into BO memory
        std::vector<const uint8_t*> in_rows(sp.h);
        {
            bench::Timer t(rep, "t_read_s");
            if (trivial) {
                struct TC { bench::AlignedBuf<uint8_t>* b; size_t rb; } tc{&trivial_in, sp.row_bytes()};
                src.fill([](void* c, int y) { auto* p = static_cast<TC*>(c); return p->b->data() + (size_t)y * p->rb; }, &tc, threads);
                for (int y = 0; y < sp.h; ++y) in_rows[y] = trivial_in.data() + (size_t)y * sp.row_bytes();
            } else {
                src.fill(host_row, &hc, threads);
                const size_t rb = sp.row_bytes(), pad = hc.stride - rb;
                for (int y = 0; y < sp.h; ++y) {
                    uint8_t* p = hc.row_ptr(y);
                    if (pad) std::memset(p + rb, 0, pad);
                    for (size_t e = 1; e < hc.row_map[y].size(); ++e) {
                        auto& d = hc.row_map[y][e];
                        std::memcpy(hc.strips[d.first].in_map + (size_t)d.second * hc.stride, p, hc.stride);
                    }
                    in_rows[y] = p;
                }
            }
        }
        rep.set("t_pack_s", 0.0);   // raw row stream: the kernel consumes decoded rows as-is

        // ---- accelerated section
        std::vector<double> tw;
        double s_h2d = 0, s_run = 0, s_d2h = 0;
        {
            Pipeline pipe(hc, krn, std::max(1, (int)a.i64("h2d-threads", 1)), std::max(1, (int)a.i64("d2h-threads", 3)));
            bench::mark("start");
            for (int it = 0; it < repeat && !trivial; ++it) {
                Round r;
                const double t0 = bench::now_s();
                pipe.run(r);
                tw.push_back(bench::now_s() - t0);
                if (r.failed) throw std::runtime_error(r.err);
                s_h2d += r.t_h2d; s_run += r.t_run; s_d2h += r.t_d2h;
            }
            bench::mark("end");
        }
        const double nrep = std::max<size_t>(1, tw.size());
        double win = 0; for (double x : tw) win += x; win /= nrep;
        const double th2d = s_h2d / nrep, tker = s_run / nrep, td2h = s_d2h / nrep;
        rep.set("t_h2d_s", th2d); rep.set("t_kernel_s", tker); rep.set("t_d2h_s", td2h);
        rep.set("t_accel_window_s", win);
        rep.set("t_compute_s", win);
        rep.set("t_compute_min_s", tw.empty() ? 0.0 : *std::min_element(tw.begin(), tw.end()));
        rep.set("t_serial_sum_s", th2d + tker + td2h);
        rep.set("mpix_per_s", win > 0 ? sp.mpix() / win : 0.0);
        rep.set("mpix_per_s_kernel", tker > 0 ? sp.mpix() * n_cu / tker : 0.0);  // per-CU kernel time summed over CUs
        rep.set("h2d_gbps", th2d > 0 ? bytes_in / th2d / 1e9 : 0.0);
        rep.set("d2h_gbps", td2h > 0 ? bytes_out / td2h / 1e9 : 0.0);

        // ---- output row views straight into the BO memory
        std::vector<const uint8_t*> orow[3];
        for (int j = 0; j < 3; ++j) {
            orow[j].assign(sp.h, zero_row.data());
            for (auto& sb : hc.strips)
                for (int i = 0; i < sb.s.n_out; ++i) orow[j][sb.s.out_y0 + i] = sb.out_map[j] + (size_t)i * hc.stride;
        }
        const std::vector<const uint8_t*>* rows[3] = {&orow[0], &orow[1], &orow[2]};
        {
            bench::Timer t(rep, "t_write_s");
            if (a.has("out-prefix"))
                write3(a.str("out-prefix"), a.str("out-format", "png"), (int)a.i64("png-level", 1), rows, sp);
        }
        {
            bench::Timer t(rep, "t_cksum_s");
            uint64_t ck[3]; checksum3(rows, sp, ck);
            rep.set("cks_sharpen", bench::hex64(ck[0])); rep.set("cks_edge", bench::hex64(ck[1])); rep.set("cks_blur", bench::hex64(ck[2]));
        }
        if (a.has("verify")) {
            bench::Timer t(rep, "t_verify_s");
            std::atomic<long> bad{0};
            std::atomic<int> next{0};
            const size_t rb = sp.row_bytes();
            auto work = [&] {
                std::vector<uint8_t> r0(rb + 1), r1(rb + 1), r2(rb + 1);
                for (int y; (y = next++) < sp.h;) {
                    if (y == 0 || y == sp.h - 1 || sp.h < 3) {
                        for (int j = 0; j < 3; ++j) bad += std::memcmp(orow[j][y], zero_row.data(), rb) != 0;
                        continue;
                    }
                    ref_row_bytes(in_rows[y - 1], in_rows[y], in_rows[y + 1], r0.data(), r1.data(), r2.data(), sp.w, sp.C, sp.bps);
                    bad += std::memcmp(r0.data(), orow[0][y], rb) != 0;
                    bad += std::memcmp(r1.data(), orow[1][y], rb) != 0;
                    bad += std::memcmp(r2.data(), orow[2][y], rb) != 0;
                }
            };
            std::vector<std::thread> vt;
            for (int i = 1; i < threads; ++i) vt.emplace_back(work);
            work();
            for (auto& x : vt) x.join();
            rep.set("verify_bad_rows", bad.load());
            rep.set("verify", bad == 0 ? 1 : 0);
            if (bad) { ok = 0; std::fprintf(stderr, "VERIFY FAILED: %ld mismatching rows\n", bad.load()); }
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ERROR: %s\n", e.what());
        ok = 0;
    }
    rep.set("ok", ok);
    rep.set("t_total_s", bench::now_s() - t_start);
    rep.print();
    return ok ? 0 : 1;
}
