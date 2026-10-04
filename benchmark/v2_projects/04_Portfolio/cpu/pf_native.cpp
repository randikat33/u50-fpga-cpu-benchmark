// pf_native.cpp - "CPU-native" baseline for the v2 two-stage Heston portfolio (review round 7).
//
// Same portfolio, market, Heston discretisation (log-Euler, variance reflection or full truncation),
// payoffs, two-stage screen-and-refine workflow and per-path xoshiro128** streams as cpu/pf_cpu.cpp,
// but with the numerics a CPU programmer would choose when bit-identity with the card is NOT needed:
//   * normals by Box-Muller (6 uniforms -> 6 normals per step) with explicit AVX-512 polynomials for
//     log, sin/cos(2 pi u) and exp (~1e-7 relative error), instead of the table inverse CDF and the
//     table exp that mirror the FPGA (gathers on the CPU);
//   * fused multiply-add;
//   * payoff moments in double precision instead of exact quantised integers.
// Results are therefore NOT bit-identical to the card; prices agree within their standard errors
// (checked by scripts/pf_native_check.py on the whole portfolio). It shows how conservative the
// paper's algorithm-matched portfolio baseline is, as mc_native does for MC Heston.
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include <immintrin.h>
#include "bench_common.hpp"
#include "pf_io.hpp"
#if !defined(__AVX512F__) || !defined(__AVX512DQ__)
#error "pf_native needs AVX-512F/DQ (build with -march=native on an AVX-512 CPU)"
#endif

using namespace pf;

namespace {

// ---------------------------------------------------------------- AVX-512 math (as mc_native)
inline __m512 v_log(__m512 x) {                 // x > 0, normal; |rel err| ~1e-7
    const __m512 one = _mm512_set1_ps(1.0f);
    __m512 e = _mm512_getexp_ps(x);
    __m512 m = _mm512_getmant_ps(x, _MM_MANT_NORM_1_2, _MM_MANT_SIGN_src);
    const __mmask16 big = _mm512_cmp_ps_mask(m, _mm512_set1_ps(1.41421356f), _CMP_GT_OQ);
    m = _mm512_mask_mul_ps(m, big, m, _mm512_set1_ps(0.5f));
    e = _mm512_mask_add_ps(e, big, e, one);
    const __m512 s = _mm512_div_ps(_mm512_sub_ps(m, one), _mm512_add_ps(m, one));
    const __m512 s2 = _mm512_mul_ps(s, s);
    __m512 p = _mm512_set1_ps(1.0f / 9.0f);
    p = _mm512_fmadd_ps(p, s2, _mm512_set1_ps(1.0f / 7.0f));
    p = _mm512_fmadd_ps(p, s2, _mm512_set1_ps(1.0f / 5.0f));
    p = _mm512_fmadd_ps(p, s2, _mm512_set1_ps(1.0f / 3.0f));
    p = _mm512_fmadd_ps(p, s2, one);
    return _mm512_fmadd_ps(e, _mm512_set1_ps(0.693147180559945f), _mm512_mul_ps(_mm512_add_ps(s, s), p));
}
inline void v_sincos_2pi(__m512 u, __m512& sn, __m512& cs) {    // u in [0, 1)
    const __m512 k = _mm512_roundscale_ps(_mm512_mul_ps(u, _mm512_set1_ps(4.0f)), _MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC);
    const __m512 a = _mm512_mul_ps(_mm512_fnmadd_ps(k, _mm512_set1_ps(0.25f), u), _mm512_set1_ps(6.28318530717958647692f));
    const __m512 a2 = _mm512_mul_ps(a, a);
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
    const __m512i q = _mm512_cvtps_epi32(k);
    const __mmask16 swap = _mm512_test_epi32_mask(q, _mm512_set1_epi32(1));
    const __mmask16 negs = _mm512_test_epi32_mask(q, _mm512_set1_epi32(2));
    const __mmask16 negc = _mm512_test_epi32_mask(_mm512_add_epi32(q, _mm512_set1_epi32(1)), _mm512_set1_epi32(2));
    const __m512 s0 = _mm512_mask_blend_ps(swap, ps, pc), c0 = _mm512_mask_blend_ps(swap, pc, ps);
    const __m512 zero = _mm512_setzero_ps();
    sn = _mm512_mask_sub_ps(s0, negs, zero, s0);
    cs = _mm512_mask_sub_ps(c0, negc, zero, c0);
}
inline __m512 v_exp(__m512 y) {                  // clamped to [-64, 64] as the reference model
    y = _mm512_min_ps(_mm512_max_ps(y, _mm512_set1_ps(-64.0f)), _mm512_set1_ps(64.0f));
    const __m512 n = _mm512_roundscale_ps(_mm512_mul_ps(y, _mm512_set1_ps(1.44269504088896f)), _MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC);
    __m512 g = _mm512_fnmadd_ps(n, _mm512_set1_ps(0.693145751953125f), y);
    g = _mm512_fnmadd_ps(n, _mm512_set1_ps(1.428606765330187e-06f), g);
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
inline __m512i fmix(__m512i h) {
    h = _mm512_xor_si512(h, _mm512_srli_epi32(h, 16));
    h = _mm512_mullo_epi32(h, _mm512_set1_epi32(static_cast<int32_t>(0x85ebca6bu)));
    h = _mm512_xor_si512(h, _mm512_srli_epi32(h, 13));
    h = _mm512_mullo_epi32(h, _mm512_set1_epi32(static_cast<int32_t>(0xc2b2ae35u)));
    return _mm512_xor_si512(h, _mm512_srli_epi32(h, 16));
}
inline __m512i xo_next(__m512i s[4]) {           // xoshiro128** (Blackman & Vigna)
    const __m512i r = _mm512_mullo_epi32(_mm512_rol_epi32(_mm512_mullo_epi32(s[1], _mm512_set1_epi32(5)), 7), _mm512_set1_epi32(9));
    const __m512i t = _mm512_slli_epi32(s[1], 9);
    s[2] = _mm512_xor_si512(s[2], s[0]); s[3] = _mm512_xor_si512(s[3], s[1]);
    s[1] = _mm512_xor_si512(s[1], s[2]); s[0] = _mm512_xor_si512(s[0], s[3]);
    s[2] = _mm512_xor_si512(s[2], t);
    s[3] = _mm512_rol_epi32(s[3], 11);
    return r;
}
// one Box-Muller pair of unit normals from two xoshiro outputs
inline void bm_pair(__m512i s[4], __m512& za, __m512& zb) {
    const __m512 inv24 = _mm512_set1_ps(0x1p-24f);
    const __m512 u1 = _mm512_mul_ps(_mm512_add_ps(_mm512_cvtepi32_ps(_mm512_srli_epi32(xo_next(s), 8)), _mm512_set1_ps(1.0f)), inv24);
    const __m512 u2 = _mm512_mul_ps(_mm512_cvtepi32_ps(_mm512_srli_epi32(xo_next(s), 8)), inv24);
    const __m512 rad = _mm512_sqrt_ps(_mm512_mul_ps(_mm512_set1_ps(-2.0f), v_log(u1)));
    __m512 sn, cs;
    v_sincos_2pi(u2, sn, cs);
    za = _mm512_mul_ps(rad, cs);
    zb = _mm512_mul_ps(rad, sn);
}

struct DMom { double s1 = 0, s2 = 0; void add(const DMom& o) { s1 += o.s1; s2 += o.s2; } };

// paths [p0, p1) of one trade; 16 paths per zmm
void sim(const TradeConst& c, uint32_t p0, uint32_t p1, DMom& m) {
    const float sc = 1048576.0f;                  // TradeConst holds the 2^-20-scaled weights
    const __m512 W0 = _mm512_set1_ps(c.W[0] * sc), W1 = _mm512_set1_ps(c.W[1] * sc);
    const __m512 W2 = _mm512_set1_ps(c.W[2] * sc), W3 = _mm512_set1_ps(c.W[3] * sc);
    const __m512 Rq = _mm512_set1_ps(c.Rq * sc), ssq = _mm512_set1_ps(c.ssq * sc), sr = _mm512_set1_ps(c.sr);
    const __m512 sqdt = _mm512_set1_ps(c.sqdt), theta = _mm512_set1_ps(c.theta), kdt = _mm512_set1_ps(c.kdt);
    const __m512 hdt = _mm512_set1_ps(c.hdt), mu = _mm512_set1_ps(c.mu), v0 = _mm512_set1_ps(c.v0);
    const __m512 zero = _mm512_setzero_ps();
    const __m512i k0 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[0]));
    const __m512i k1 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[1]));
    const __m512i k2 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[2]));
    const __m512i k3 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[3]));
    const __m512i ramp = _mm512_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
    const __m512 S0 = _mm512_set1_ps(c.S0), K = _mm512_set1_ps(c.K), inv_n = _mm512_set1_ps(c.inv_n);
    const __m512 cap = _mm512_set1_ps(PF_PO_CAP);
    const bool ft = c.ft != 0, asian = c.asian != 0, put = c.put != 0;
    const uint32_t n = c.n_steps;
    alignas(64) float po[16];
    for (uint64_t p = p0; p < p1; p += 16) {
        const __m512i pv = _mm512_add_epi32(_mm512_set1_epi32(static_cast<int32_t>(p)), ramp);
        __m512i s[4] = {fmix(_mm512_xor_si512(pv, k0)), fmix(_mm512_xor_si512(pv, k1)),
                        fmix(_mm512_xor_si512(pv, k2)), fmix(_mm512_xor_si512(pv, k3))};
        const __m512i all = _mm512_or_si512(_mm512_or_si512(s[0], s[1]), _mm512_or_si512(s[2], s[3]));
        const __mmask16 z = _mm512_testn_epi32_mask(all, all);
        if (z) s[0] = _mm512_mask_blend_epi32(z, s[0], _mm512_set1_epi32(static_cast<int32_t>(0x9E3779B9u)));
        __m512 v = v0, lr = zero, sE = zero;
        for (uint32_t r = 1; r <= n; ++r) {
            __m512 z0, z1, z2, z3, z4, z5;
            bm_pair(s, z0, z1); bm_pair(s, z2, z3); bm_pair(s, z4, z5);
            const __m512 zS = _mm512_fmadd_ps(Rq, z4, _mm512_fmadd_ps(W3, z3, _mm512_fmadd_ps(W2, z2,
                              _mm512_fmadd_ps(W1, z1, _mm512_mul_ps(W0, z0)))));
            const __m512 zVs = _mm512_fmadd_ps(sr, zS, _mm512_mul_ps(ssq, z5));
            const __m512 sv = _mm512_mul_ps(_mm512_sqrt_ps(v), sqdt);
            const __m512 vn = _mm512_fmadd_ps(sv, zVs, _mm512_fmadd_ps(kdt, _mm512_sub_ps(theta, v), v));
            lr = _mm512_fmadd_ps(sv, zS, _mm512_add_ps(lr, _mm512_fnmadd_ps(hdt, v, mu)));
            if (ft) v = _mm512_max_ps(vn, zero);
            else v = _mm512_abs_ps(vn);
            if (asian) sE = _mm512_add_ps(sE, v_exp(lr));
        }
        const __m512 base = asian ? _mm512_mul_ps(sE, inv_n) : v_exp(lr);
        const __m512 Sp = _mm512_mul_ps(base, S0);
        const __m512 d = put ? _mm512_sub_ps(K, Sp) : _mm512_sub_ps(Sp, K);
        _mm512_store_ps(po, _mm512_min_ps(_mm512_max_ps(d, zero), cap));
        const uint32_t cnt = static_cast<uint32_t>(p1 - p < 16 ? p1 - p : 16);
        for (uint32_t k = 0; k < cnt; ++k) { const double x = po[k]; m.s1 += x; m.s2 += x * x; }
    }
}

Result finalize_d(const Trade& t, const float* mkt, const RunParams& rp, const DMom& m) {
    const uint32_t uid = t.underlying_id < rp.n_under ? t.underlying_id : 0;
    const float rr = mk_rate(mkt, uid) + rp.drate;
    const double disc = std::exp(-static_cast<double>(rr) * static_cast<double>(t.T));
    const double invN = 1.0 / static_cast<double>(rp.n_paths);
    const double mean = m.s1 * invN;
    double var = m.s2 * invN - mean * mean;
    if (var < 0.0) var = 0.0;
    Result r;
    r.price = static_cast<float>(disc * mean * t.notional * t.position);
    r.std_err = static_cast<float>(disc * std::sqrt(var * invN) * t.notional);
    return r;
}

struct Item { uint32_t t0, t1, p0, p1; };

void run_stage(uint32_t grain, uint32_t n, const Trade* const* recs, const uint32_t* ids, const float* mkt,
               const RunParams& rp, DMom* mom) {
    const CallConst cc = call_const(rp);
    const uint32_t P = rp.n_paths;
    std::vector<Item> items;
    const bool split = P > grain;
    if (!split) {
        const uint32_t g = std::max<uint32_t>(1, grain / P);
        for (uint32_t t = 0; t < n; t += g) items.push_back({t, std::min(n, t + g), 0, P});
    } else {
        const uint32_t b = (grain + 15) / 16 * 16;
        for (uint32_t t = 0; t < n; ++t)
            for (uint64_t p = 0; p < P; p += b) items.push_back({t, t + 1, (uint32_t)p, (uint32_t)std::min<uint64_t>(P, p + b)});
    }
    std::vector<DMom> part(split ? items.size() : 0);
    #pragma omp parallel for schedule(dynamic, 1)
    for (long i = 0; i < (long)items.size(); ++i) {
        const Item& it = items[i];
        for (uint32_t t = it.t0; t < it.t1; ++t) {
            const TradeConst c = setup(*recs[t], ids[t], mkt, rp, cc);
            if (split) sim(c, it.p0, it.p1, part[i]);
            else { DMom m; sim(c, it.p0, it.p1, m); mom[t] = m; }
        }
    }
    if (split) {
        for (uint32_t t = 0; t < n; ++t) mom[t] = DMom();
        for (size_t i = 0; i < items.size(); ++i) mom[items[i].t0].add(part[i]);
    }
}

}  // namespace

int main(int argc, char** argv) {
    const double t_main = bench::now_s();
    bench::Args a(argc, argv);
    if (a.has("help")) {
        std::printf("pf_native - CPU-native baseline (Box-Muller, AVX-512 polynomials, FMA, double moments)\n%s"
                    "  --threads N  (default omp_get_max_threads())\n  --grain N    paths per work item (default 4096)\n",
                    pf::workload_usage());
        return 0;
    }
    bench::Report R;
    int ok = 1;
    try {
        const Workload w = parse_workload(a);
        const uint32_t grain = (uint32_t)std::max<long long>(16, a.i64("grain", 4096));
        const int threads = (int)a.i64("threads", omp_get_max_threads());
        omp_set_num_threads(threads);
        double t0 = bench::now_s();
        const uint32_t n = portfolio_count(w.portfolio);
        bench::AlignedBuf<Trade> trades(n);
        #pragma omp parallel for schedule(static)
        for (long i = 0; i < (long)n; ++i) std::memset(&trades[i], 0, sizeof(Trade));
        read_trades(w.portfolio, 0, n, trades.data());
        std::vector<float> mkt(PF_MARKET_WORDS);
        read_market(w.market, mkt.data());
        const uint32_t nu = n_under_of(trades.data(), n);
        R.set("t_read_s", bench::now_s() - t0);
        std::vector<const Trade*> recs1(n);
        std::vector<uint32_t> ids1(n);
        std::vector<DMom> mom1(n);
        std::vector<Result> res1(n);
        for (uint32_t i = 0; i < n; ++i) { recs1[i] = &trades[i]; ids1[i] = i; }
        const uint32_t k = std::min(w.topk, n);
        std::vector<Result> final_res(n);
        const RunParams rp1 = stage_params(w, 1, nu), rp2 = stage_params(w, 2, nu);
        std::vector<uint32_t> sel;
        std::vector<const Trade*> recs2(k);
        std::vector<uint32_t> ids2(k);
        std::vector<DMom> mom2(k);
        std::vector<Result> res2(k);

        bench::mark("start");
        const double tc0 = bench::now_s();
        run_stage(grain, n, recs1.data(), ids1.data(), mkt.data(), rp1, mom1.data());
        #pragma omp parallel for schedule(static)
        for (long i = 0; i < (long)n; ++i) res1[i] = finalize_d(trades[i], mkt.data(), rp1, mom1[i]);
        const double tc1 = bench::now_s();
        sel = select_topk(res1.data(), n, k);
        for (uint32_t j = 0; j < k; ++j) { recs2[j] = &trades[sel[j]]; ids2[j] = sel[j]; }
        const double tc2 = bench::now_s();
        run_stage(grain, k, recs2.data(), ids2.data(), mkt.data(), rp2, mom2.data());
        for (uint32_t j = 0; j < k; ++j) res2[j] = finalize_d(*recs2[j], mkt.data(), rp2, mom2[j]);
        std::memcpy(final_res.data(), res1.data(), (size_t)n * sizeof(Result));
        for (uint32_t j = 0; j < k; ++j) final_res[sel[j]] = res2[j];
        const double tc3 = bench::now_s();
        bench::mark("end");

        R.set("platform", "cpu");
        R.set("impl", "pf_native_boxmuller_fma");
        R.set("isa", "avx512_intrinsics");
        R.set("threads", threads);
        R.set("grain", (unsigned)grain);
        report_workload(R, w, n, nu, k);
        const double steps = total_steps(n, w.paths1, w.steps1, k, w.paths2, w.steps2);
        R.set("t_compute_s", tc3 - tc0);
        R.set("t_stage1_s", tc1 - tc0);
        R.set("t_select_s", tc2 - tc1);
        R.set("t_stage2_s", tc3 - tc2);
        R.set("msteps_per_s", steps / (tc3 - tc0) / 1e6);
        R.set("checksum", bench::hex64(results_checksum(final_res.data(), n)));
        R.set("price_sum", price_sum(final_res.data(), n));
        R.set("bit_identical_to_fpga", 0);
        const double tw = bench::now_s();
        if (!w.out.empty()) write_results(w.out, final_res.data(), n);
        if (!w.out_stage1.empty()) write_results(w.out_stage1, res1.data(), n);
        if (!w.out_topk.empty()) write_topk(w.out_topk, sel, res2);
        R.set("t_write_s", bench::now_s() - tw);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "pf_native: error: %s\n", e.what());
        R.set("project", "portfolio"); R.set("platform", "cpu"); R.set("error", e.what());
        ok = 0;
    }
    R.set("ok", ok);
    R.set("t_total_s", bench::now_s() - t_main);
    R.print();
    return ok ? 0 : 1;
}
