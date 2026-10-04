// fir_fpga.cpp - int16 FIR filter FPGA host (XRT native C++ API), streaming host class of the paper.
//
// Same schedule as the AES host (01_AES/host/aes_fpga.cpp): one worker thread per CU (per stream)
// pulls chunk indices from a shared counter and runs  [memcpy in] | sync H2D | run | sync D2H | [memcpy out].
// Each input chunk carries FIR_HW words of history (the samples before the chunk) in front of its data,
// so chunks are independent and the CUs overlap freely. Every BO is allocated once.
//   --no-io (default)  every chunk carries the same generated block (filled once, untimed), as AES --no-io
//   --mem              a real signal in host memory: history + data copied into the BO, output copied out
//   --streams S        S workers (buffer sets) per CU; the default chunk is then 256/S MiB
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <exception>
#include <string>
#include <thread>
#include <vector>

#include "bench_common.hpp"
#include "fir_ref.hpp"

namespace {
constexpr size_t MiB = size_t(1) << 20;
constexpr const char* KNAME = "fir_v2";
enum { ARG_SRC = 0, ARG_DST = 1, ARG_COEF = 2, ARG_NWORDS = 3 };
constexpr size_t HS = size_t(FIR_HW) * FIR_L;          // history samples per chunk
#if defined(XRT_SIM_KERNEL)
constexpr int DEFAULT_MAX_CU = 2;
#else
constexpr int DEFAULT_MAX_CU = 16;
#endif

void usage() {
    std::puts(
        "fir_fpga - int16 FIR filter on Alveo U50 (XRT native API)\n"
        "usage: fir_fpga --xclbin X [--size-mb 16384] [--no-io | --mem] [--chunk-mb 256] [--streams S]\n"
        "                [--cus N] [--device 0] [--seed 1] [--verify]\n"
        "  --size-mb   input bytes (int16 samples x 2); the output has the same size\n"
        "  --verify    --no-io: every chunk against the reference (memcmp inside the window, verify_in_window=1)\n"
        "              --mem: the whole output against the scalar reference after the run (untimed)");
}

struct Cu {
    xrt::kernel k;
    xrt::bo bin, bout, bcoef;
    int16_t* min = nullptr;
    int16_t* mout = nullptr;
    xrt::run run;
};
struct PhaseT { double read = 0, h2d = 0, kern = 0, d2h = 0, write = 0, verify = 0; size_t chunks = 0; bool bad = false; };
}  // namespace

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Args a(argc, argv);
    if (a.has("help") || argc == 1) { usage(); return argc == 1 ? 1 : 0; }
    bench::Report rep;
    rep.set("project", "fir");
    rep.set("platform", "fpga");
    rep.set("impl", "fir_v2-u50");
    int rc = 0;
    try {
        const std::string xclbin_path = a.str("xclbin");
        if (xclbin_path.empty()) throw std::runtime_error("--xclbin PATH is required");
        const bool mem = a.has("mem");
        const bool verify = a.has("verify");
        const uint64_t seed = (uint64_t)a.i64("seed", 1);
        const long long streams = std::max<long long>(1, a.i64("streams", 1));
        long long chunk_default = 256;
        while (chunk_default * streams > 256 && chunk_default > 1) chunk_default /= 2;
        const long long chunk_mb = a.i64("chunk-mb", chunk_default);
        if (chunk_mb < 1 || chunk_mb > 256) throw std::runtime_error("--chunk-mb must be in [1, 256]");
        const size_t C = size_t(chunk_mb) * MiB / 2;          // samples per chunk (multiple of 16)
        const size_t n = size_t(a.i64("size-mb", 16384)) * MiB / 2;
        if (n == 0) throw std::runtime_error("empty input");
        const size_t nchunks = (n + C - 1) / C;
        const size_t cs = std::min(C, (n + FIR_L - 1) / FIR_L * FIR_L);   // samples in a full BO
        const size_t in_bytes = (HS + cs) * 2, out_bytes = cs * 2;
        const std::vector<int16_t> coef = fir::lowpass(FIR_TAPS);

        double t0 = bench::now_s();
        xrt::device dev(static_cast<unsigned int>(a.i64("device", 0)));
        const xrt::uuid uuid = dev.load_xclbin(xclbin_path);
        rep.set("t_xclbin_s", bench::now_s() - t0);

        const int max_cu = static_cast<int>(a.i64("cus", DEFAULT_MAX_CU));
        std::vector<xrt::kernel> ks;
        for (int i = 1; i <= max_cu; ++i) {
            try { ks.emplace_back(dev, uuid, std::string(KNAME) + ":{" + KNAME + "_" + std::to_string(i) + "}"); }
            catch (const std::exception&) { break; }
        }
        if (ks.empty()) throw std::runtime_error(std::string("no ") + KNAME + " CU found in " + xclbin_path);
        const size_t n_cu_found = ks.size();
        const size_t ncu_used = std::min(n_cu_found, nchunks);
        std::vector<Cu> cus;                                   // worker slots, stream-major
        for (long long st = 0; st < streams; ++st)
            for (size_t i = 0; i < ncu_used; ++i) { Cu c; c.k = ks[i]; cus.push_back(std::move(c)); }
        const size_t nw = std::min(cus.size(), nchunks);
        cus.resize(nw);

        t0 = bench::now_s();
        for (size_t i = 0; i < nw; ++i) {
            Cu& c = cus[i];
            try {
                c.bin = xrt::bo(dev, in_bytes, xrt::bo::flags::normal, c.k.group_id(ARG_SRC));
                c.bout = xrt::bo(dev, out_bytes, xrt::bo::flags::normal, c.k.group_id(ARG_DST));
                c.bcoef = xrt::bo(dev, 4096, xrt::bo::flags::normal, c.k.group_id(ARG_COEF));
            } catch (const std::exception& e) {
                throw std::runtime_error("BO allocation failed for worker " + std::to_string(i + 1) + ": " + e.what() +
                                         " -- reduce --chunk-mb or --streams");
            }
            c.min = c.bin.map<int16_t*>();
            c.mout = c.bout.map<int16_t*>();
            int16_t* pc = c.bcoef.map<int16_t*>();
            std::memset(pc, 0, 4096);
            std::memcpy(pc, coef.data(), FIR_TAPS * 2);
            c.bcoef.sync(XCL_BO_SYNC_BO_TO_DEVICE, 4096, 0);
            c.run = xrt::run(c.k);
            c.run.set_arg(ARG_SRC, c.bin);
            c.run.set_arg(ARG_DST, c.bout);
            c.run.set_arg(ARG_COEF, c.bcoef);
        }
        rep.set("t_alloc_s", bench::now_s() - t0);

        // data: --no-io -> one generated block with zero history in every BO; --mem -> the whole signal
        t0 = bench::now_s();
        bench::AlignedBuf<int16_t> xs, ys, ref;
        int16_t* x = nullptr;
        if (mem) {
            xs.alloc(HS + n + FIR_L);
            ys.alloc(n + FIR_L);
            std::memset(xs.data(), 0, HS * 2);
            x = xs.data() + HS;
            const unsigned nt = std::max(1u, std::thread::hardware_concurrency());
            std::vector<std::thread> th;
            for (unsigned t = 0; t < nt; ++t)
                th.emplace_back([&, t] { size_t s0 = n * t / nt, s1 = n * (t + 1) / nt; fir::gen_block(seed, s0, x + s0, s1 - s0); });
            for (auto& z : th) z.join();
            std::memset(x + n, 0, FIR_L * 2);
        } else {
            for (auto& c : cus) {
                std::memset(c.min, 0, HS * 2);
                fir::gen_block(seed, 0, c.min + HS, cs);
            }
            if (verify) {   // reference output of the block (all chunks carry the same block)
                ref.alloc(cs);
                fir::filter_ref(cus[0].min + HS, ref.data(), cs, coef.data(), FIR_TAPS);
            }
        }
        rep.set("t_gen_s", bench::now_s() - t0);

        std::vector<PhaseT> ph(nw);
        std::vector<std::exception_ptr> errs(nw);
        std::vector<uint64_t> chunk0_hash(1, 0);
        std::atomic<size_t> next{0};
        auto worker = [&](size_t wi) {
            Cu& c = cus[wi];
            PhaseT& T = ph[wi];
            try {
                for (size_t k; (k = next++) < nchunks;) {
                    const size_t off = k * C, len = std::min(C, n - off);
                    const uint32_t words = static_cast<uint32_t>((len + FIR_L - 1) / FIR_L);
                    const size_t wsamp = size_t(words) * FIR_L;
                    double t = bench::now_s(), u;
                    if (mem) {
                        std::memcpy(c.min, x + off - HS, (HS + len) * 2);        // history + data
                        if (wsamp > len) std::memset(c.min + HS + len, 0, (wsamp - len) * 2);
                        u = bench::now_s(); T.read += u - t; t = u;
                    }
                    c.bin.sync(XCL_BO_SYNC_BO_TO_DEVICE, (HS + wsamp) * 2, 0);
                    u = bench::now_s(); T.h2d += u - t; t = u;
                    c.run.set_arg(ARG_NWORDS, words);
                    c.run.start();
                    c.run.wait2();
                    u = bench::now_s(); T.kern += u - t; t = u;
                    c.bout.sync(XCL_BO_SYNC_BO_FROM_DEVICE, wsamp * 2, 0);
                    u = bench::now_s(); T.d2h += u - t; t = u;
                    if (mem) {
                        std::memcpy(ys.data() + off, c.mout, len * 2);
                        u = bench::now_s(); T.write += u - t; t = u;
                    } else if (verify) {
                        if (std::memcmp(ref.data(), c.mout, len * 2) != 0) T.bad = true;
                        u = bench::now_s(); T.verify += u - t;
                    }
                    if (k == 0 && !mem) chunk0_hash[0] = fir::fnv1a64(c.mout, len * 2);   // (one chunk, negligible)
                    ++T.chunks;
                }
            } catch (...) { errs[wi] = std::current_exception(); next = nchunks; }
        };
        bench::mark("start");
        const double tw0 = bench::now_s();
        {
            std::vector<std::thread> th;
            for (size_t i = 0; i < nw; ++i) th.emplace_back(worker, i);
            for (auto& z : th) z.join();
        }
        const double t_window = bench::now_s() - tw0;
        bench::mark("end");
        for (auto& e : errs) if (e) std::rethrow_exception(e);

        PhaseT S;
        for (auto& p : ph) {
            S.read += p.read; S.h2d += p.h2d; S.kern += p.kern; S.d2h += p.d2h; S.write += p.write;
            S.verify += p.verify; S.chunks += p.chunks; S.bad = S.bad || p.bad;
        }
        int ok = 1, verified = 0;
        if (verify && !mem) { verified = 1; if (S.bad) { ok = 0; rc = 3; } }
        if (verify && mem) {
            const unsigned nt = std::max(1u, std::thread::hardware_concurrency());
            std::vector<std::thread> th;
            std::atomic<bool> bad{false};
            for (unsigned t = 0; t < nt; ++t)
                th.emplace_back([&, t] {
                    size_t s0 = n * t / nt, s1 = n * (t + 1) / nt;
                    for (size_t i = s0; i < s1 && !bad; ++i)
                        if (fir::out1(x + i, coef.data(), FIR_TAPS) != ys[i]) bad = true;
                });
            for (auto& z : th) z.join();
            verified = 1;
            if (bad) { ok = 0; rc = 3; }
        }
        if (mem) rep.set("out_fnv1a64", bench::hex64(fir::fnv1a64(ys.data(), n * 2)));
        else rep.set("chunk_out_fnv1a64", bench::hex64(chunk0_hash[0]));

        rep.set("taps", FIR_TAPS);
        rep.set("xclbin", xclbin_path);
        rep.set("n_cu", static_cast<unsigned long long>(ncu_used));
        rep.set("n_cu_found", static_cast<unsigned long long>(n_cu_found));
        rep.set("streams", static_cast<unsigned long long>(streams));
        rep.set("n_workers", static_cast<unsigned long long>(nw));
        rep.set("io", mem ? 1 : 0);
        rep.set("samples", static_cast<unsigned long long>(n));
        rep.set("bytes", static_cast<unsigned long long>(n * 2));
        rep.set("size_mb", double(n * 2) / MiB);
        rep.set("chunk_mb", chunk_mb);
        rep.set("nchunks", static_cast<unsigned long long>(nchunks));
        rep.set("t_read_s", S.read);
        rep.set("t_h2d_s", S.h2d);
        rep.set("t_kernel_s", S.kern);
        rep.set("t_d2h_s", S.d2h);
        rep.set("t_write_s", S.write);
        rep.set("t_accel_window_s", t_window);
        rep.set("t_compute_s", t_window);
        rep.set("compute_def", "window");
        rep.set("phase_sums_are_thread_sums", 1);
        rep.set("verify_in_window", (verify && !mem) ? 1 : 0);
        rep.set("gsps", double(n) / t_window / 1e9);
        rep.set("throughput_gbps", double(n) * 2 / t_window / 1e9);
        rep.set("kernel_gsps_per_cu", S.kern > 0 ? double(n) / S.kern / 1e9 : 0.0);
        rep.set("h2d_gbps_per_stream", S.h2d > 0 ? double(n) * 2 / S.h2d / 1e9 : 0.0);
        rep.set("d2h_gbps_per_stream", S.d2h > 0 ? double(n) * 2 / S.d2h / 1e9 : 0.0);
        rep.set("verified", verified);
        rep.set("ok", ok);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ERROR: %s\n", e.what());
        rep.set("error", e.what());
        rep.set("ok", 0);
        rc = 1;
    }
    rep.set("t_total_s", bench::now_s() - t_start);
    rep.print();
    return rc;
}
