// fir_cpu.cpp - CPU baseline for the int16 FIR filter (out-of-sample streaming workload).
//
// The input signal (--size-mb of int16 samples, generated untimed) and the output live in DRAM,
// first-touched by the threads that use them (NUMA-local). Each timed pass filters the whole signal;
// OpenMP splits it into one contiguous segment per thread (the history before a segment is read from
// the neighbouring segment, so the result does not depend on the thread count).
//   --impl avx512 [--isa vnni|bw]  : cpu/fir_avx512.hpp (vpdpwssd with VNNI, else vpmaddwd + vpaddd)
//   --impl scalar                  : the reference model
// t_compute_s = time of --repeat passes; gsps = samples x repeat / t_compute_s.
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include "bench_common.hpp"
#include "fir_ref.hpp"
#include "fir_avx512.hpp"

namespace {
constexpr size_t MiB = size_t(1) << 20;
constexpr size_t PAD = 512;        // zero samples in front of the signal (>= T - 1) and behind it

enum Impl { SCALAR, BW, VNNI };
void filter_seg(Impl im, const int16_t* x, int16_t* y, size_t i0, size_t i1, const int16_t* c, int T) {
#if FIR_HAVE_AVX512
    if (im == BW) { fir::avx::filter_range<false>(x, y, i0, i1, c, T); return; }
#if FIR_HAVE_VNNI
    if (im == VNNI) { fir::avx::filter_range<true>(x, y, i0, i1, c, T); return; }
#endif
#endif
    for (size_t i = i0; i < i1; ++i) y[i] = fir::out1(x + i, c, T);
}
}  // namespace

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Args a(argc, argv);
    bench::Report rep;
    if (a.has("help")) {
        std::puts("fir_cpu [--size-mb 1024] [--repeat 10] [--taps 32] [--threads N] [--impl avx512|scalar]\n"
                  "        [--isa vnni|bw] [--seed 1] [--verify]");
        return 0;
    }
    rep.set("project", "fir");
    rep.set("platform", "cpu");
    int rc = 0;
    try {
        const size_t n = size_t(a.i64("size-mb", 1024)) * MiB / 2;      // samples
        const int repeat = std::max<int>(1, (int)a.i64("repeat", 10));
        const int T = (int)a.i64("taps", FIR_TAPS);
        if (T < 2 || T > FIR_TMAX || T % 2) throw std::runtime_error("--taps must be even, 2..256");
        const int threads = (int)a.i64("threads", omp_get_max_threads());
        const uint64_t seed = (uint64_t)a.i64("seed", 1);
        std::string impl = a.str("impl", FIR_HAVE_AVX512 ? "avx512" : "scalar");
        std::string isa = a.str("isa", FIR_HAVE_VNNI ? "vnni" : "bw");
        Impl im = SCALAR;
        if (impl == "avx512") {
            if (!FIR_HAVE_AVX512) throw std::runtime_error("built without AVX-512BW");
            if (isa == "vnni") { if (!FIR_HAVE_VNNI) throw std::runtime_error("built without AVX512-VNNI"); im = VNNI; }
            else if (isa == "bw") im = BW;
            else throw std::runtime_error("--isa must be vnni or bw");
        } else if (impl != "scalar") throw std::runtime_error("--impl must be avx512 or scalar");
        const std::vector<int16_t> c = fir::lowpass(T);

        bench::AlignedBuf<int16_t> xb(n + 2 * PAD), yb(n + PAD);
        int16_t* x = xb.data() + PAD;
        int16_t* y = yb.data();
        auto seg = [&](int t, size_t& s0, size_t& s1) {       // same split for first touch and compute
            size_t per = (n / threads + 31) / 32 * 32;
            s0 = std::min(n, per * t); s1 = std::min(n, per * (t + 1));
        };
        double tg = bench::now_s();
        std::memset(xb.data(), 0, PAD * 2);
        std::memset(x + n, 0, PAD * 2);
#pragma omp parallel num_threads(threads)
        {
            size_t s0, s1;
            seg(omp_get_thread_num(), s0, s1);
            fir::gen_block(seed, s0, x + s0, s1 - s0);
            std::memset(y + s0, 0, (s1 - s0) * 2);
        }
        rep.set("t_gen_s", bench::now_s() - tg);

        auto pass = [&] {
#pragma omp parallel num_threads(threads)
            {
                size_t s0, s1;
                seg(omp_get_thread_num(), s0, s1);
                if (s1 > s0) filter_seg(im, x, y, s0, s1, c.data(), T);
            }
        };
        pass();                                                    // warm-up (untimed)
        bench::mark("start");
        const double t0 = bench::now_s();
        for (int r = 0; r < repeat; ++r) pass();
        const double tc = bench::now_s() - t0;
        bench::mark("end");

        int ok = 1, verified = 0;
        const uint64_t h = fir::fnv1a64(y, n * 2);
        if (a.has("verify")) {       // scalar reference over the whole signal (untimed)
            bench::AlignedBuf<int16_t> r(n);
#pragma omp parallel for num_threads(threads) schedule(static)
            for (size_t i = 0; i < n; ++i) r[i] = fir::out1(x + i, c.data(), T);
            ok = std::memcmp(r.data(), y, n * 2) == 0;
            verified = 1;
            if (!ok) rc = 3;
        }
        rep.set("impl", impl == "avx512" ? "avx512_" + isa + "_omp" : std::string("scalar_omp"));
        rep.set("threads", threads);
        rep.set("taps", T);
        rep.set("samples", static_cast<unsigned long long>(n));
        rep.set("bytes", static_cast<unsigned long long>(n * 2));
        rep.set("size_mb", double(n * 2) / MiB);
        rep.set("repeat", repeat);
        rep.set("t_read_s", 0.0);
        rep.set("t_write_s", 0.0);
        rep.set("t_compute_s", tc);
        rep.set("gsps", double(n) * repeat / tc / 1e9);
        rep.set("throughput_gbps", double(n) * 2 * repeat / tc / 1e9);    // input bytes per second
        rep.set("out_fnv1a64", bench::hex64(h));
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
