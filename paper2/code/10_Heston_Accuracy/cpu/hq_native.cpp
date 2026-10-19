// hq_native.cpp - "CPU-native" baseline of paper 2 (protocol Section 6): the same Heston schemes (FT, QE)
// and the same settings (time step, antithetic pairs, xoshiro128** stream per pair), written the way a
// CPU programmer would write them when bit-identity with the card is NOT required:
//   * normals by Box-Muller (two uniforms -> two normals), QE's Z_V by a rational inverse normal
//     (Wichura's AS 241, single precision), all with vector polynomials for log / exp / sin / cos (explicit AVX-512, ~1e-7 relative);
//   * float32 arithmetic with FMA; the log-price is kept relative to S0 (y = ln(S/S0), see struct Y);
//   * payoff moments summed in double.
// Its output is NOT bit-identical to the card. Its bias at the chosen settings is measured separately
// (protocol Section 10: "the CPU-native code is calibrated at the chosen steps only").
//
// Random words per time step (2, as in the matched code):
//   FT : r0, r1 -> Box-Muller -> z1 (spot), z2 (variance, independent part); zv = rho z1 + sqrt(1-rho^2) z2
//   QE : r0 -> uniform U of the variance step; on even steps two more words -> Box-Muller -> the spot
//        normals of this step and the next (so two words per step on average)
//   side B of a pair uses 1 - U and -z (antithetic).
// One zmm = 16 antithetic pairs; OpenMP over blocks of 16 pairs.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include <immintrin.h>
#if !defined(__AVX512F__)
#error "hq_native needs AVX-512 (build with -march=native on an AVX-512 CPU)"
#endif
#include "bench_common.hpp"
#include "hq_model.hpp"
#include "mc_cli.hpp"      // mc::append_json

namespace {
constexpr int V = 16;
typedef __m512 vf;

inline vf S(float x) { return _mm512_set1_ps(x); }

// ------------------------------------------------------------------ vector math (paper 1's mc_native)
inline vf v_log(vf x) {                 // x > 0, normal; |rel err| ~1e-7
    const vf one = S(1.0f);
    vf e = _mm512_getexp_ps(x);
    vf m = _mm512_getmant_ps(x, _MM_MANT_NORM_1_2, _MM_MANT_SIGN_src);
    const __mmask16 big = _mm512_cmp_ps_mask(m, S(1.41421356f), _CMP_GT_OQ);
    m = _mm512_mask_mul_ps(m, big, m, S(0.5f));
    e = _mm512_mask_add_ps(e, big, e, one);
    const vf s = _mm512_div_ps(_mm512_sub_ps(m, one), _mm512_add_ps(m, one));
    const vf s2 = _mm512_mul_ps(s, s);
    vf p = S(1.0f / 9.0f);
    p = _mm512_fmadd_ps(p, s2, S(1.0f / 7.0f));
    p = _mm512_fmadd_ps(p, s2, S(1.0f / 5.0f));
    p = _mm512_fmadd_ps(p, s2, S(1.0f / 3.0f));
    p = _mm512_fmadd_ps(p, s2, one);
    const vf lm = _mm512_mul_ps(_mm512_add_ps(s, s), p);
    return _mm512_fmadd_ps(e, S(0.693147180559945f), lm);
}
inline void v_sincos_2pi(vf u, vf& sn, vf& cs) {   // sin/cos(2 pi u), u in [0, 1)
    const vf k = _mm512_roundscale_ps(_mm512_mul_ps(u, S(4.0f)), _MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC);
    const vf a = _mm512_mul_ps(_mm512_fnmadd_ps(k, S(0.25f), u), S(6.28318530717958647692f));
    const vf a2 = _mm512_mul_ps(a, a);
    vf ps = S(1.0f / 362880.0f);
    ps = _mm512_fmadd_ps(ps, a2, S(-1.0f / 5040.0f));
    ps = _mm512_fmadd_ps(ps, a2, S(1.0f / 120.0f));
    ps = _mm512_fmadd_ps(ps, a2, S(-1.0f / 6.0f));
    ps = _mm512_fmadd_ps(_mm512_mul_ps(ps, a2), a, a);
    vf pc = S(-1.0f / 3628800.0f);
    pc = _mm512_fmadd_ps(pc, a2, S(1.0f / 40320.0f));
    pc = _mm512_fmadd_ps(pc, a2, S(-1.0f / 720.0f));
    pc = _mm512_fmadd_ps(pc, a2, S(1.0f / 24.0f));
    pc = _mm512_fmadd_ps(pc, a2, S(-0.5f));
    pc = _mm512_fmadd_ps(pc, a2, S(1.0f));
    const __m512i q = _mm512_cvtps_epi32(k);
    const __mmask16 swap = _mm512_test_epi32_mask(q, _mm512_set1_epi32(1));
    const __mmask16 negs = _mm512_test_epi32_mask(q, _mm512_set1_epi32(2));
    const __mmask16 negc = _mm512_test_epi32_mask(_mm512_add_epi32(q, _mm512_set1_epi32(1)), _mm512_set1_epi32(2));
    const vf s0 = _mm512_mask_blend_ps(swap, ps, pc), c0 = _mm512_mask_blend_ps(swap, pc, ps);
    const vf zero = _mm512_setzero_ps();
    sn = _mm512_mask_sub_ps(s0, negs, zero, s0);
    cs = _mm512_mask_sub_ps(c0, negc, zero, c0);
}
inline vf v_exp(vf y) {                  // |rel err| ~1e-7 for |y| < 80
    const vf n = _mm512_roundscale_ps(_mm512_mul_ps(y, S(1.44269504088896f)), _MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC);
    vf g = _mm512_fnmadd_ps(n, S(0.693145751953125f), y);
    g = _mm512_fnmadd_ps(n, S(1.428606765330187e-06f), g);
    vf p = S(1.0f / 5040.0f);
    p = _mm512_fmadd_ps(p, g, S(1.0f / 720.0f));
    p = _mm512_fmadd_ps(p, g, S(1.0f / 120.0f));
    p = _mm512_fmadd_ps(p, g, S(1.0f / 24.0f));
    p = _mm512_fmadd_ps(p, g, S(1.0f / 6.0f));
    p = _mm512_fmadd_ps(p, g, S(0.5f));
    p = _mm512_fmadd_ps(p, g, S(1.0f));
    p = _mm512_fmadd_ps(p, g, S(1.0f));
    return _mm512_scalef_ps(p, n);
}
// inverse normal CDF: Wichura's AS 241 PPND7 (the single-precision version, about 1e-7 relative error),
// u in (0, 1) with min(u, 1-u) >= 2^-24, so the second tail region of AS 241 (r > 5) is never needed.
// (Acklam's double-precision approximation lost up to 2e-4 when evaluated in float.)
inline vf v_invnorm(vf u) {
    const vf half = S(0.5f), one = S(1.0f);
    const vf q = _mm512_sub_ps(u, half);
    const vf r = _mm512_fnmadd_ps(q, q, S(0.180625f));
    vf n = S(59.109374720f);
    n = _mm512_fmadd_ps(n, r, S(159.29113202f));
    n = _mm512_fmadd_ps(n, r, S(50.434271938f));
    n = _mm512_fmadd_ps(n, r, S(3.3871327179f));
    vf d = S(67.187563600f);
    d = _mm512_fmadd_ps(d, r, S(78.757757664f));
    d = _mm512_fmadd_ps(d, r, S(17.895169469f));
    d = _mm512_fmadd_ps(d, r, one);
    vf z = _mm512_div_ps(_mm512_mul_ps(q, n), d);
    const __mmask16 tail = _mm512_cmp_ps_mask(_mm512_abs_ps(q), S(0.425f), _CMP_GT_OQ);
    if (tail) {
        const vf t = _mm512_min_ps(u, _mm512_sub_ps(one, u));              // distance to the nearer end
        const vf w = _mm512_sub_ps(_mm512_sqrt_ps(_mm512_sub_ps(_mm512_setzero_ps(), v_log(t))), S(1.6f));
        vf c = S(0.17023821103f);
        c = _mm512_fmadd_ps(c, w, S(1.3067284816f));
        c = _mm512_fmadd_ps(c, w, S(2.7568153900f));
        c = _mm512_fmadd_ps(c, w, S(1.4234372777f));
        vf e = S(0.12021132975f);
        e = _mm512_fmadd_ps(e, w, S(0.73700164250f));
        e = _mm512_fmadd_ps(e, w, one);
        vf zt = _mm512_div_ps(c, e);                                        // positive
        const __mmask16 lo = _mm512_cmp_ps_mask(q, _mm512_setzero_ps(), _CMP_LT_OQ);
        zt = _mm512_mask_sub_ps(zt, lo, _mm512_setzero_ps(), zt);
        z = _mm512_mask_blend_ps(tail, z, zt);
    }
    return z;
}
inline __m512i xo_next(__m512i& s0, __m512i& s1, __m512i& s2, __m512i& s3) {   // xoshiro128**
    const __m512i r = _mm512_mullo_epi32(_mm512_rol_epi32(_mm512_mullo_epi32(s1, _mm512_set1_epi32(5)), 7), _mm512_set1_epi32(9));
    const __m512i t = _mm512_slli_epi32(s1, 9);
    s2 = _mm512_xor_si512(s2, s0); s3 = _mm512_xor_si512(s3, s1);
    s1 = _mm512_xor_si512(s1, s2); s0 = _mm512_xor_si512(s0, s3);
    s2 = _mm512_xor_si512(s2, t);
    s3 = _mm512_rol_epi32(s3, 11);
    return r;
}
inline void box_muller(__m512i r0, __m512i r1, vf& za, vf& zb) {
    const vf u1 = _mm512_mul_ps(_mm512_add_ps(_mm512_cvtepi32_ps(_mm512_srli_epi32(r0, 8)), S(1.0f)), S(0x1p-24f));  // (0, 1]
    const vf u2 = _mm512_mul_ps(_mm512_cvtepi32_ps(_mm512_srli_epi32(r1, 8)), S(0x1p-24f));                       // [0, 1)
    const vf rad = _mm512_sqrt_ps(_mm512_mul_ps(S(-2.0f), v_log(u1)));
    vf sn, cs;
    v_sincos_2pi(u2, sn, cs);
    za = _mm512_mul_ps(rad, cs);
    zb = _mm512_mul_ps(rad, sn);
}
inline vf uniform_open(__m512i r) {      // (k + 1/2) 2^-23, k = r >> 9: in (0, 1), and 1 - u is exact
    return _mm512_mul_ps(_mm512_add_ps(_mm512_cvtepi32_ps(_mm512_srli_epi32(r, 9)), S(0.5f)), S(0x1p-23f));
}

// ------------------------------------------------------------------ parameters in natural units
struct Par {
    float dt, hdt, sdt, kdt, kthdt, er, es;               // FT
    float th, E, QC1, QC2, K0, K1, K2, K3, K4;            // QE (Andersen 2008, gamma1 = gamma2 = 1/2)
    float rT, S0, K[HQ_NK];
    unsigned puts;                                        // bit k: strike k from the put payoff
};
Par make_par(const hq::Case& c, uint32_t M) {
    Par p{};
    const double dt = c.T / M, E = std::exp(-c.kappa * dt), a = c.kappa * c.rho / c.eps - 0.5;
    p.dt = (float)dt; p.hdt = (float)(0.5 * dt); p.sdt = (float)std::sqrt(dt);
    p.kdt = (float)(c.kappa * dt); p.kthdt = (float)(c.kappa * c.theta * dt);
    p.er = (float)(c.eps * c.rho); p.es = (float)(c.eps * std::sqrt(1.0 - c.rho * c.rho));
    p.th = (float)c.theta; p.E = (float)E;
    p.QC1 = (float)(c.eps * c.eps * E * (1.0 - E) / c.kappa);
    p.QC2 = (float)(c.theta * c.eps * c.eps * (1.0 - E) * (1.0 - E) / (2.0 * c.kappa));
    p.K0 = (float)(-c.rho * c.kappa * c.theta * dt / c.eps);
    p.K1 = (float)(0.5 * dt * a - c.rho / c.eps);
    p.K2 = (float)(0.5 * dt * a + c.rho / c.eps);
    p.K3 = (float)(0.5 * dt * (1.0 - c.rho * c.rho));
    p.K4 = p.K3;
    p.rT = (float)(c.r * c.T); p.S0 = (float)c.S0;
    for (int k = 0; k < HQ_NK; ++k) p.K[k] = (float)c.K[k];
    p.puts = c.puts;
    return p;
}

// log-price y = ln(S/S0) without drift, 16 lanes, a float sum. Because y starts at 0 (not at ln S0), its
// rounding error stays small. Checked on the same paths against a double sum (build with
// -DHQN_DOUBLE_STATE): price differences 5e-7 (Case II FT, 30,720 steps) and 2e-5 (Case I QE, 160 steps),
// far below the smallest target (0.008). The double sum costs about 13% speed, so float is kept.
struct Y {
#ifndef HQN_DOUBLE_STATE
    vf f;
    void init() { f = _mm512_setzero_ps(); }
    void add(vf d) { f = _mm512_add_ps(f, d); }
    vf get() const { return f; }
#else
    __m512d lo, hi;
    void init() { lo = hi = _mm512_setzero_pd(); }
    void add(vf d) {
        lo = _mm512_add_pd(lo, _mm512_cvtps_pd(_mm512_castps512_ps256(d)));
        hi = _mm512_add_pd(hi, _mm512_cvtps_pd(_mm256_castpd_ps(_mm512_extractf64x4_pd(_mm512_castps_pd(d), 1))));
    }
    vf get() const {
        return _mm512_castpd_ps(_mm512_insertf64x4(_mm512_castps_pd(_mm512_castps256_ps512(_mm512_cvtpd_ps(lo))),
                                                   _mm256_castps_pd(_mm512_cvtpd_ps(hi)), 1));
    }
#endif
};

// one FT step of one side: V may be negative; zs = +-z1, zw = +-eps (rho z1 + sqrt(1-rho^2) z2)
inline void ft_side(const Par& P, Y& y, vf& Vv, vf zs, vf zw) {
    const vf vp = _mm512_max_ps(Vv, _mm512_setzero_ps());
    const vf sv = _mm512_mul_ps(_mm512_sqrt_ps(vp), S(P.sdt));                 // sqrt(V+ dt)
    y.add(_mm512_fmadd_ps(sv, zs, _mm512_mul_ps(vp, S(-P.hdt))));              // - V+ dt/2 + sv z1
    const vf dr = _mm512_fnmadd_ps(vp, S(P.kdt), S(P.kthdt));                   // kappa (theta - V+) dt
    Vv = _mm512_add_ps(Vv, _mm512_fmadd_ps(sv, zw, dr));                        // + eps sv zv (eps folded in zw)
}
// one QE step of one side. u: the side's uniform; zq: the side's icdf(u) (quadratic branch); z: spot normal
inline void qe_side(const Par& P, Y& y, vf& Vv, vf u, vf zq, vf z) {
    const vf one = S(1.0f), zero = _mm512_setzero_ps();
    const vf m = _mm512_fmadd_ps(_mm512_sub_ps(Vv, S(P.th)), S(P.E), S(P.th));
    const vf s2 = _mm512_fmadd_ps(Vv, S(P.QC1), S(P.QC2));
    const vf psi = _mm512_div_ps(s2, _mm512_mul_ps(m, m));
    const __mmask16 quad = _mm512_cmp_ps_mask(psi, S(1.5f), _CMP_LE_OQ);
    vf vn;
    // quadratic branch: b2 = 2/psi - 1 + sqrt(2/psi) sqrt(2/psi - 1), a = m / (1 + b2), V' = a (b + Z_V)^2
    vf vq = zero, ve = zero;
    if (quad) {
        const vf ip = _mm512_div_ps(S(2.0f), psi);
        const vf ipm = _mm512_max_ps(_mm512_sub_ps(ip, one), zero);
        const vf b2 = _mm512_add_ps(ipm, _mm512_sqrt_ps(_mm512_mul_ps(ip, ipm)));
        const vf a = _mm512_div_ps(m, _mm512_add_ps(one, b2));
        const vf t = _mm512_add_ps(_mm512_sqrt_ps(b2), zq);
        vq = _mm512_mul_ps(a, _mm512_mul_ps(t, t));
    }
    if (quad != 0xFFFF) {
        // exponential branch: p = (psi-1)/(psi+1), beta = (1-p)/m, V' = 0 if U <= p else ln((1-p)/(1-U))/beta
        const vf pp = _mm512_div_ps(_mm512_sub_ps(psi, one), _mm512_add_ps(psi, one));
        const vf omp = _mm512_sub_ps(one, pp);
        const vf omu = _mm512_sub_ps(one, u);
        const vf l = v_log(_mm512_div_ps(omp, omu));
        const vf e = _mm512_mul_ps(l, _mm512_div_ps(m, omp));
        const __mmask16 pos = _mm512_cmp_ps_mask(u, pp, _CMP_GT_OQ);
        ve = _mm512_maskz_mov_ps(pos, e);
    }
    vn = _mm512_mask_blend_ps(quad, ve, vq);
    // y += K0 + K1 V + K2 V' + sqrt(K3 V + K4 V') z
    const vf dr = _mm512_fmadd_ps(S(P.K2), vn, _mm512_fmadd_ps(S(P.K1), Vv, S(P.K0)));
    const vf sd = _mm512_sqrt_ps(_mm512_max_ps(_mm512_fmadd_ps(S(P.K4), vn, _mm512_mul_ps(S(P.K3), Vv)), zero));
    y.add(_mm512_fmadd_ps(sd, z, dr));
    Vv = vn;
}

struct alignas(64) Acc { double s1[HQ_NK] = {0, 0, 0}, sp[HQ_NK] = {0, 0, 0}; uint64_t pairs = 0; };

// pairs a0 .. a0+15 (the first cnt are counted)
void block(const Par& P, hq::Scheme sc, float v0, const mc::Keys& k, uint32_t M, uint64_t a0, int cnt, Acc& acc) {
    alignas(64) uint32_t t0[V], t1[V], t2[V], t3[V];
    for (int l = 0; l < V; ++l) {
        mc::Xo x = mc::pair_seed(k, (uint32_t)(a0 + l));
        t0[l] = x.s[0]; t1[l] = x.s[1]; t2[l] = x.s[2]; t3[l] = x.s[3];
    }
    __m512i s0 = _mm512_load_si512(t0), s1 = _mm512_load_si512(t1), s2 = _mm512_load_si512(t2), s3 = _mm512_load_si512(t3);
    const vf zero = _mm512_setzero_ps(), one = S(1.0f);
    Y yA, yB;
    yA.init(); yB.init();
    vf VA = S(v0), VB = S(v0);
    if (sc == hq::FT) {
        const vf er = S(P.er), es = S(P.es);
        for (uint32_t t = 0; t < M; ++t) {
            vf z1, z2;
            const __m512i r0 = xo_next(s0, s1, s2, s3), r1 = xo_next(s0, s1, s2, s3);
            box_muller(r0, r1, z1, z2);
            const vf zw = _mm512_fmadd_ps(er, z1, _mm512_mul_ps(es, z2));          // eps zv
            ft_side(P, yA, VA, z1, zw);
            ft_side(P, yB, VB, _mm512_sub_ps(zero, z1), _mm512_sub_ps(zero, zw));
        }
    } else {
        vf zn = zero;
        for (uint32_t t = 0; t < M; ++t) {
            const vf u = uniform_open(xo_next(s0, s1, s2, s3));
            vf z;
            if ((t & 1) == 0) {
                const __m512i r1 = xo_next(s0, s1, s2, s3), r2 = xo_next(s0, s1, s2, s3);
                box_muller(r1, r2, z, zn);
            } else {
                z = zn;
            }
            const vf zq = v_invnorm(u);
            qe_side(P, yA, VA, u, zq, z);
            qe_side(P, yB, VB, _mm512_sub_ps(one, u), _mm512_sub_ps(zero, zq), _mm512_sub_ps(zero, z));
        }
    }
    // S_T = S0 exp(y + r T)
    const vf SA = _mm512_mul_ps(S(P.S0), v_exp(_mm512_add_ps(yA.get(), S(P.rT))));
    const vf SB = _mm512_mul_ps(S(P.S0), v_exp(_mm512_add_ps(yB.get(), S(P.rT))));
    alignas(64) float pa[HQ_NK][V], pb[HQ_NK][V];
    for (int j = 0; j < HQ_NK; ++j) {
        const bool put = (P.puts >> j) & 1u;                                       // put K - S, or call S - K
        const vf da = put ? _mm512_sub_ps(S(P.K[j]), SA) : _mm512_sub_ps(SA, S(P.K[j]));
        const vf db = put ? _mm512_sub_ps(S(P.K[j]), SB) : _mm512_sub_ps(SB, S(P.K[j]));
        _mm512_store_ps(pa[j], _mm512_max_ps(da, zero));
        _mm512_store_ps(pb[j], _mm512_max_ps(db, zero));
    }
    for (int l = 0; l < cnt; ++l)
        for (int j = 0; j < HQ_NK; ++j) {
            const double s = (double)pa[j][l] + (double)pb[j][l];
            acc.s1[j] += s; acc.sp[j] += s * s;
        }
    acc.pairs += (uint64_t)cnt;
}

Acc run_once(const Par& P, hq::Scheme sc, float v0, const mc::Keys& k, uint32_t M, uint64_t a0, uint64_t a1,
             int threads, std::vector<Acc>& acc) {
    const uint64_t nb = (a1 - a0 + V - 1) / V;
#pragma omp parallel num_threads(threads)
    {
        Acc& a = acc[omp_get_thread_num()];
        a = Acc();
#pragma omp for schedule(static)
        for (uint64_t b = 0; b < nb; ++b) {
            const uint64_t b0 = a0 + b * V;
            block(P, sc, v0, k, M, b0, (int)std::min<uint64_t>(V, a1 - b0), a);
        }
    }
    Acc t;
    for (int i = 0; i < threads; ++i) {
        for (int j = 0; j < HQ_NK; ++j) { t.s1[j] += acc[i].s1[j]; t.sp[j] += acc[i].sp[j]; }
        t.pairs += acc[i].pairs;
    }
    return t;
}

const char* HELP =
    "usage: hq_native --case I|II|III|P1 --scheme ft|qe (--steps-per-year S | --steps M)\n"
    "                 [--a0 A] (--a1 B | --pairs N) [--seed S] [--threads T] [--runs R] [--warmup W] [--warmup-s S] [--window-s S] [--out FILE]\n"
    "  CPU-native baseline (Box-Muller, FMA, vector log/exp, double sums); not bit-identical to the card\n";
}  // namespace

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h")) { std::printf("%s", HELP); return 0; }
    int rc = 0;
    try {
        hq::Case c = hq::get_case(A.str("case", "I"));
        std::string scs = A.str("scheme", "qe");
        if (scs != "ft" && scs != "qe") throw std::runtime_error("--scheme must be ft or qe");
        hq::Scheme sc = scs == "ft" ? hq::FT : hq::QE;
        long long M = A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * c.T);
        if (M < 1 || M > HQ_M_MAX) throw std::runtime_error("number of steps must be 1..65535");
        uint64_t a0 = (uint64_t)A.i64("a0", 0);
        uint64_t a1 = A.has("a1") ? (uint64_t)A.i64("a1", 0) : a0 + (uint64_t)A.i64("pairs", 65536);
        if (a1 <= a0 || a1 > (1ull << 32)) throw std::runtime_error("need a0 < a1 <= 2^32");
        uint64_t seed = (uint64_t)A.i64("seed", 20261011);
        int threads = (int)A.i64("threads", omp_get_max_threads());
        int runs = std::max<int>(1, (int)A.i64("runs", 1)), warm = std::max<int>(0, (int)A.i64("warmup", 0));
        double warm_s = A.f64("warmup-s", 0.0), win_s = A.f64("window-s", 0.0);

        R.set("project", "heston_accuracy");
        R.set("platform", "cpu");
        R.set("impl", "native");
        R.set("case", c.name);
        R.set("scheme", scs);
        R.set("steps", (long long)M);
        R.set("steps_per_year", (double)M / c.T);
        R.set("a0", (unsigned long long)a0);
        R.set("a1", (unsigned long long)a1);
        R.set("pairs", (unsigned long long)(a1 - a0));
        R.set("seed", (unsigned long long)seed);
        R.set("threads", threads);

        const Par P = make_par(c, (uint32_t)M);
        const mc::Keys k = mc::run_keys(seed ^ 0x6e61746976650000ull);   // own streams ("native"), not the card's
        std::vector<Acc> acc(threads);
        Acc m;
        const double tw = bench::now_s();
        for (int i = 0; i < warm || bench::now_s() - tw < warm_s; ++i)
            m = run_once(P, sc, (float)c.v0, k, (uint32_t)M, a0, a1, threads, acc);
        std::vector<double> ts;
        bench::mark("start");
        const double tw0 = bench::now_s();
        for (int i = 0; i < runs || bench::now_s() - tw0 < win_s; ++i) {
            const double t0 = bench::now_s();
            m = run_once(P, sc, (float)c.v0, k, (uint32_t)M, a0, a1, threads, acc);
            ts.push_back(bench::now_s() - t0);
        }
        const double twin = bench::now_s() - tw0;
        bench::mark("end");
        R.set("t_window_s", twin);
        R.set("t_per_run_s", twin / (double)ts.size());
        std::vector<double> so = ts;
        std::sort(so.begin(), so.end());
        const double tc = so[so.size() / 2];
        R.set("runs", (long long)ts.size());
        R.set("t_compute_s", tc);
        R.set("t_compute_min_s", so.front());
        const double psteps = 2.0 * (double)(a1 - a0) * (double)M;
        R.set("path_steps", psteps);
        R.set("ns_per_path_step_core", tc * threads / psteps * 1e9);

        const double np = (double)m.pairs, disc = std::exp(-c.r * c.T);
        bool fin = true;
        char b[96];
        for (int j = 0; j < HQ_NK; ++j) {
            const double mean = m.s1[j] / (2 * np);
            const double vp = std::max(m.sp[j] / 4 / np - mean * mean, 0.0);
            const double pv = disc * mean, se = disc * std::sqrt(vp / (np > 1 ? np - 1 : 1));
            const double price = pv + (((c.puts >> j) & 1u) ? c.S0 - c.K[j] * disc : 0.0);   // call (parity for puts)
            fin = fin && std::isfinite(price);
            const std::string s = std::to_string(j);
            std::snprintf(b, sizeof b, "%.0f", c.K[j]); R.set("K" + s, b);
            std::snprintf(b, sizeof b, "%.10f", price); R.set("price" + s, b);
            std::snprintf(b, sizeof b, "%.10f", pv); R.set("pv" + s, b);
            R.set("form" + s, ((c.puts >> j) & 1u) ? "put" : "call");
            std::snprintf(b, sizeof b, "%.10f", se); R.set("se" + s, b);
            std::snprintf(b, sizeof b, "%.6f", c.ref[j]); R.set("ref" + s, b);
            std::snprintf(b, sizeof b, "%.10f", price - c.ref[j]); R.set("bias" + s, b);
            std::snprintf(b, sizeof b, "%.17g", m.s1[j]); R.set("d1_" + s, b);
            std::snprintf(b, sizeof b, "%.17g", m.sp[j]); R.set("dp_" + s, b);
        }
        R.set("bit_identical_to_fpga", 0);
        R.set("ok", fin ? 1 : 0);
        R.set("t_total_s", bench::now_s() - t_start);
        if (A.has("out")) mc::append_json(A.str("out"), R);
        rc = fin ? 0 : 2;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        R.set("ok", 0);
        rc = 1;
    }
    R.set("t_total_s", bench::now_s() - t_start);
    R.print();
    return rc;
}
