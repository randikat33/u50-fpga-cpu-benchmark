// mc_native.cpp - "CPU-native" Monte Carlo Heston baseline (review round 6, Phase 3).
//
// Same financial model, discretisation, market, antithetic pairing and per-pair xoshiro128** streams
// as cpu/mc_cpu.cpp, but with the numerics a CPU programmer would choose when bit-identity with the
// card is NOT required:
//   * normals by Box-Muller (two uniforms -> two normals) with vectorised log/sqrt/sincos (explicit AVX-512
//     polynomials, ~1e-7 relative error), instead of the table ICDF (two gathers per normal) that mirrors the FPGA;
//   * exp by a vectorised polynomial; FMA contraction allowed; -ffast-math;
//   * payoff moments accumulated in double instead of exact integers.
// So results are NOT bit-identical to the card; they agree statistically (test/native_check.sh: |price -
// price_ref| <= 3 combined standard errors). It bounds how much faster the CPU side could be with
// CPU-friendly numerics, i.e. how conservative the paper's algorithm-matched MC baseline is.
// One zmm-sized block = 16 antithetic pairs; OpenMP over blocks (schedule static, as mc_cpu).
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include <immintrin.h>
#if !defined(__AVX512F__)
#error "mc_native needs AVX-512 (build with -march=native on an AVX-512 CPU)"
#endif
#include "bench_common.hpp"
#include "mc_cli.hpp"
#include "mc_model.hpp"

namespace {
constexpr int V = 16;
struct alignas(64) Acc { double s1 = 0, s2 = 0, sp = 0; uint64_t pairs = 0; };

struct Par {
    float X0[MC_NA], U0[MC_NA], OMK[MC_NA], KTH2[MC_NA], C1H[MC_NA], C2[MC_NA], D2[MC_NA], L[MC_NL], K8;
};
Par make_par(const float* fp) {
    Par p;
    for (int i = 0; i < MC_NA; ++i) {
        p.X0[i] = fp[mc::F_X0 + i]; p.U0[i] = fp[mc::F_U0 + i]; p.OMK[i] = fp[mc::F_OMK + i];
        p.KTH2[i] = fp[mc::F_KTH2 + i]; p.C1H[i] = fp[mc::F_C1H + i];
        p.C2[i] = fp[mc::F_C2Q + i] * 1048576.0f;      // unit normals instead of 2^-20 units
        p.D2[i] = fp[mc::F_D2 + i];
    }
    for (int i = 0; i < MC_NL; ++i) p.L[i] = fp[mc::F_L2 + i] * 1048576.0f;
    p.K8 = fp[mc::F_K8];
    return p;
}

// ---- explicit AVX-512 math (no dependence on compiler auto-vectorisation or glibc libmvec; gcc 11 on
// Ubuntu 22.04 does not vectorise the libm calls in this loop, which made the first version ~6x slower)
inline __m512 v_log(__m512 x) {                 // x > 0, normal; |rel err| ~1e-7
    const __m512 one = _mm512_set1_ps(1.0f);
    __m512 e = _mm512_getexp_ps(x);                                           // floor(log2 x)
    __m512 m = _mm512_getmant_ps(x, _MM_MANT_NORM_1_2, _MM_MANT_SIGN_src);    // [1, 2)
    const __mmask16 big = _mm512_cmp_ps_mask(m, _mm512_set1_ps(1.41421356f), _CMP_GT_OQ);
    m = _mm512_mask_mul_ps(m, big, m, _mm512_set1_ps(0.5f));
    e = _mm512_mask_add_ps(e, big, e, one);
    const __m512 s = _mm512_div_ps(_mm512_sub_ps(m, one), _mm512_add_ps(m, one));   // |s| <= 0.1716
    const __m512 s2 = _mm512_mul_ps(s, s);
    __m512 p = _mm512_set1_ps(1.0f / 9.0f);
    p = _mm512_fmadd_ps(p, s2, _mm512_set1_ps(1.0f / 7.0f));
    p = _mm512_fmadd_ps(p, s2, _mm512_set1_ps(1.0f / 5.0f));
    p = _mm512_fmadd_ps(p, s2, _mm512_set1_ps(1.0f / 3.0f));
    p = _mm512_fmadd_ps(p, s2, one);
    const __m512 lm = _mm512_mul_ps(_mm512_add_ps(s, s), p);                  // 2 atanh(s) = log m
    return _mm512_fmadd_ps(e, _mm512_set1_ps(0.693147180559945f), lm);
}
inline void v_sincos_2pi(__m512 u, __m512& sn, __m512& cs) {   // sin/cos(2*pi*u), u in [0, 1); exact reduction
    const __m512 k = _mm512_roundscale_ps(_mm512_mul_ps(u, _mm512_set1_ps(4.0f)), _MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC);
    const __m512 a = _mm512_mul_ps(_mm512_fnmadd_ps(k, _mm512_set1_ps(0.25f), u), _mm512_set1_ps(6.28318530717958647692f));
    const __m512 a2 = _mm512_mul_ps(a, a);                                     // |a| <= pi/4
    __m512 ps = _mm512_set1_ps(1.0f / 362880.0f);
    ps = _mm512_fmadd_ps(ps, a2, _mm512_set1_ps(-1.0f / 5040.0f));
    ps = _mm512_fmadd_ps(ps, a2, _mm512_set1_ps(1.0f / 120.0f));
    ps = _mm512_fmadd_ps(ps, a2, _mm512_set1_ps(-1.0f / 6.0f));
    ps = _mm512_fmadd_ps(_mm512_mul_ps(ps, a2), a, a);
    __m512 pc = _mm512_set1_ps(-1.0f / 3628800.0f);
    pc = _mm512_fmadd_ps(pc, a2, _mm512_set1_ps(1.0f / 40320.0f));
    pc = _mm512_fmadd_ps(pc, a2, _mm512_set1_ps(-1.0f / 720.0f));
    pc = _mm512_fmadd_ps(pc, a2, _mm512_set1_ps(1.0f / 24.0f));
    pc = _mm512_fmadd_ps(pc, a2, _mm512_set1_ps(-0.5f));
    pc = _mm512_fmadd_ps(pc, a2, _mm512_set1_ps(1.0f));
    const __m512i q = _mm512_cvtps_epi32(k);                                   // quadrant 0..4
    const __mmask16 swap = _mm512_test_epi32_mask(q, _mm512_set1_epi32(1));
    const __mmask16 negs = _mm512_test_epi32_mask(q, _mm512_set1_epi32(2));
    const __mmask16 negc = _mm512_test_epi32_mask(_mm512_add_epi32(q, _mm512_set1_epi32(1)), _mm512_set1_epi32(2));
    const __m512 s0 = _mm512_mask_blend_ps(swap, ps, pc), c0 = _mm512_mask_blend_ps(swap, pc, ps);
    const __m512 zero = _mm512_setzero_ps();
    sn = _mm512_mask_sub_ps(s0, negs, zero, s0);
    cs = _mm512_mask_sub_ps(c0, negc, zero, c0);
}
inline __m512 v_exp(__m512 y) {                  // |rel err| ~1e-7 for |y| < 80
    const __m512 n = _mm512_roundscale_ps(_mm512_mul_ps(y, _mm512_set1_ps(1.44269504088896f)), _MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC);
    __m512 g = _mm512_fnmadd_ps(n, _mm512_set1_ps(0.693145751953125f), y);    // Cody-Waite ln2 split
    g = _mm512_fnmadd_ps(n, _mm512_set1_ps(1.428606765330187e-06f), g);        // |g| <= 0.347
    __m512 p = _mm512_set1_ps(1.0f / 5040.0f);
    p = _mm512_fmadd_ps(p, g, _mm512_set1_ps(1.0f / 720.0f));
    p = _mm512_fmadd_ps(p, g, _mm512_set1_ps(1.0f / 120.0f));
    p = _mm512_fmadd_ps(p, g, _mm512_set1_ps(1.0f / 24.0f));
    p = _mm512_fmadd_ps(p, g, _mm512_set1_ps(1.0f / 6.0f));
    p = _mm512_fmadd_ps(p, g, _mm512_set1_ps(0.5f));
    p = _mm512_fmadd_ps(p, g, _mm512_set1_ps(1.0f));
    p = _mm512_fmadd_ps(p, g, _mm512_set1_ps(1.0f));
    return _mm512_scalef_ps(p, n);
}
inline __m512i xo_next(__m512i& s0, __m512i& s1, __m512i& s2, __m512i& s3) {   // xoshiro128** (Blackman & Vigna)
    const __m512i r = _mm512_mullo_epi32(_mm512_rol_epi32(_mm512_mullo_epi32(s1, _mm512_set1_epi32(5)), 7), _mm512_set1_epi32(9));
    const __m512i t = _mm512_slli_epi32(s1, 9);
    s2 = _mm512_xor_si512(s2, s0); s3 = _mm512_xor_si512(s3, s1);
    s1 = _mm512_xor_si512(s1, s2); s0 = _mm512_xor_si512(s0, s3);
    s2 = _mm512_xor_si512(s2, t);
    s3 = _mm512_rol_epi32(s3, 11);
    return r;
}

// 16 pairs a0 .. a0+15 (the first cnt are accumulated); one zmm lane per antithetic pair
void block(const Par& P, const mc::Keys& k, uint32_t M, uint32_t a0, int cnt, bool put, Acc& acc) {
    alignas(64) uint32_t t0[V], t1[V], t2[V], t3[V];
    for (int l = 0; l < V; ++l) {
        mc::Xo x = mc::pair_seed(k, a0 + l);
        t0[l] = x.s[0]; t1[l] = x.s[1]; t2[l] = x.s[2]; t3[l] = x.s[3];
    }
    __m512i s0 = _mm512_load_si512(t0), s1 = _mm512_load_si512(t1), s2 = _mm512_load_si512(t2), s3 = _mm512_load_si512(t3);
    __m512 XA[MC_NA], UA[MC_NA], XB[MC_NA], UB[MC_NA], z[MC_NZ];
    for (int i = 0; i < MC_NA; ++i) {
        XA[i] = XB[i] = _mm512_set1_ps(P.X0[i]);
        UA[i] = UB[i] = _mm512_set1_ps(P.U0[i]);
    }
    const __m512 inv24 = _mm512_set1_ps(0x1p-24f), one = _mm512_set1_ps(1.0f), m2 = _mm512_set1_ps(-2.0f);
    const __m512 zero = _mm512_setzero_ps();
    for (uint32_t t = 0; t < M; ++t) {
        for (int q = 0; q < MC_NZ / 2; ++q) {          // Box-Muller: 2 uniforms -> 2 normals
            const __m512i r0 = xo_next(s0, s1, s2, s3);
            const __m512i r1 = xo_next(s0, s1, s2, s3);
            const __m512 u1 = _mm512_mul_ps(_mm512_add_ps(_mm512_cvtepi32_ps(_mm512_srli_epi32(r0, 8)), one), inv24);  // (0, 1]
            const __m512 u2 = _mm512_mul_ps(_mm512_cvtepi32_ps(_mm512_srli_epi32(r1, 8)), inv24);                      // [0, 1)
            const __m512 rad = _mm512_sqrt_ps(_mm512_mul_ps(m2, v_log(u1)));
            __m512 sn, cs;
            v_sincos_2pi(u2, sn, cs);
            z[2 * q] = _mm512_mul_ps(rad, cs);
            z[2 * q + 1] = _mm512_mul_ps(rad, sn);
        }
        for (int i = 0; i < MC_NA; ++i) {
            __m512 zs = _mm512_mul_ps(_mm512_set1_ps(P.L[mc::tri(i, 0)]), z[0]);
            for (int j = 1; j <= i; ++j) zs = _mm512_fmadd_ps(_mm512_set1_ps(P.L[mc::tri(i, j)]), z[j], zs);
            const __m512 w = _mm512_fmadd_ps(_mm512_set1_ps(P.C1H[i]), zs, _mm512_mul_ps(_mm512_set1_ps(P.C2[i]), z[MC_NA + i]));
            const __m512 omk = _mm512_set1_ps(P.OMK[i]), kth = _mm512_set1_ps(P.KTH2[i]);
            __m512 U = UA[i], PA = _mm512_sqrt_ps(U);
            __m512 un = _mm512_fmadd_ps(PA, w, _mm512_fmadd_ps(U, omk, kth));
            XA[i] = _mm512_sub_ps(_mm512_fmadd_ps(PA, zs, XA[i]), U);
            UA[i] = _mm512_max_ps(un, zero);
            U = UB[i]; PA = _mm512_sqrt_ps(U);
            un = _mm512_fnmadd_ps(PA, w, _mm512_fmadd_ps(U, omk, kth));
            XB[i] = _mm512_sub_ps(_mm512_fnmadd_ps(PA, zs, XB[i]), U);
            UB[i] = _mm512_max_ps(un, zero);
        }
    }
    __m512 ba = zero, bb = zero;
    const __m512 half = _mm512_set1_ps(0.5f);
    for (int i = 0; i < MC_NA; ++i) {
        const __m512 d2 = _mm512_set1_ps(P.D2[i]);
        ba = _mm512_add_ps(ba, v_exp(_mm512_mul_ps(half, _mm512_add_ps(XA[i], d2))));
        bb = _mm512_add_ps(bb, v_exp(_mm512_mul_ps(half, _mm512_add_ps(XB[i], d2))));
    }
    const __m512 k8 = _mm512_set1_ps(P.K8), eighth = _mm512_set1_ps(0.125f);
    const __m512 da = put ? _mm512_sub_ps(k8, ba) : _mm512_sub_ps(ba, k8);
    const __m512 db = put ? _mm512_sub_ps(k8, bb) : _mm512_sub_ps(bb, k8);
    alignas(64) float pa[V], pb[V];
    _mm512_store_ps(pa, _mm512_mul_ps(_mm512_max_ps(da, zero), eighth));
    _mm512_store_ps(pb, _mm512_mul_ps(_mm512_max_ps(db, zero), eighth));
    for (int l = 0; l < cnt; ++l) {
        const double a = pa[l], b = pb[l], m = 0.5 * (a + b);
        acc.s1 += a + b; acc.s2 += a * a + b * b; acc.sp += m * m; ++acc.pairs;
    }
}

Acc run_once(const Par& P, const mc::Keys& k, uint32_t M, uint64_t pairs, bool put, int threads, std::vector<Acc>& acc) {
    const uint64_t nb = (pairs + V - 1) / V;
#pragma omp parallel num_threads(threads)
    {
        Acc& a = acc[omp_get_thread_num()];
        a = Acc();
#pragma omp for schedule(static)
        for (uint64_t b = 0; b < nb; ++b) {
            const uint64_t a0 = b * V;
            block(P, k, M, (uint32_t)a0, (int)std::min<uint64_t>(V, pairs - a0), put, a);
        }
    }
    Acc t;
    for (int i = 0; i < threads; ++i) { t.s1 += acc[i].s1; t.s2 += acc[i].s2; t.sp += acc[i].sp; t.pairs += acc[i].pairs; }
    return t;
}
}  // namespace

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h")) {
        std::printf("usage: %s [--threads N] [workload options]\n%s", argv[0], mc::workload_help());
        return 0;
    }
    int rc = 0;
    try {
        mc::Workload w = mc::parse_workload(A);
        const int threads = (int)A.i64("threads", omp_get_max_threads());
        R.set("project", "mc_heston");
        R.set("platform", "cpu");
        R.set("impl", "native_boxmuller_fastmath_omp");
        R.set("isa", "avx512_intrinsics");
        R.set("threads", threads);
        mc::report_workload(R, w);
        R.set("t_read_s", 0.0);
        float fp[MC_FPAR_N];
        mc::make_fpar(w.mk, w.steps, fp);
        const Par P = make_par(fp);
        const mc::Keys k = mc::run_keys(w.seed);
        std::vector<Acc> acc(threads);
        Acc res;
        for (int i = 0; i < w.warmup; ++i) res = run_once(P, k, w.steps, w.pairs, w.mk.put, threads, acc);
        std::vector<double> ts;
        bench::mark("start");
        for (int i = 0; i < w.runs; ++i) {
            const double t0 = bench::now_s();
            res = run_once(P, k, w.steps, w.pairs, w.mk.put, threads, acc);
            ts.push_back(bench::now_s() - t0);
        }
        bench::mark("end");
        const double tc = mc::median(ts);
        const double psteps = 2.0 * w.pairs * w.steps;
        R.set("t_compute_s", tc);
        R.set("t_compute_min_s", *std::min_element(ts.begin(), ts.end()));
        R.set("msteps_per_s", psteps / tc / 1e6);
        R.set("paths_per_s", 2.0 * w.pairs / tc);
        R.set("ns_per_path_step_core", tc * threads / psteps * 1e9);
        const double n = 2.0 * res.pairs, np = (double)res.pairs, disc = std::exp(-w.mk.r * w.mk.T);
        const double mean = res.s1 / n, vp = std::max(res.sp / np - mean * mean, 0.0);
        char b[64];
        std::snprintf(b, sizeof b, "%.10f", disc * mean); R.set("price", b);
        std::snprintf(b, sizeof b, "%.10f", disc * std::sqrt(vp / np)); R.set("stderr", b);
        R.set("bit_identical_to_fpga", 0);
        R.set("ok", std::isfinite(mean) ? 1 : 0);
        R.set("t_write_s", 0.0);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        R.set("ok", 0);
        rc = 1;
    }
    R.set("t_total_s", bench::now_s() - t_start);
    R.print();
    return rc;
}
