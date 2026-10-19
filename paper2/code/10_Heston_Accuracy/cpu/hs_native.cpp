// hs_native.cpp - CPU-native baseline of the secondary cases of paper 2 (protocol v0.5, Sections 4b and 6),
// written the way a CPU programmer would write them when bit-identity with the card is not required. Same
// vector math, generator and conventions as cpu/hq_native.cpp (Box-Muller normals, AS 241 inverse normal for
// QE, FMA, vector log/exp, float32 state relative to S0, double sums of the payoff moments), own streams.
//   --case B --scheme ft|qe : 8-asset basket. One zmm = 16 basket pairs; the 8 assets of a block are
//                             simulated one after the other (they are independent), each with its own stream
//                             (unit 8a + j of the native keys); the basket average is summed in double.
//   --case A                : Asian call under Black-Scholes, exact log step y += (r - s^2/2) h + s sqrt(h) z,
//                             normals by Box-Muller (one pair of words per two dates), S = S0 exp(y), the
//                             arithmetic sum in double; with --estimator cv also the sum of y (geometric
//                             average, control variate); --estimator plain skips it.
// Its output is NOT bit-identical to the card; its bias at the chosen settings is checked separately.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include <immintrin.h>
#if !defined(__AVX512F__)
#error "hs_native needs AVX-512 (build with -march=native on an AVX-512 CPU)"
#endif
#include "bench_common.hpp"
#include "hb_model.hpp"
#include "ha_model.hpp"
#include "mc_cli.hpp"

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


inline void add_pd(__m512d& lo, __m512d& hi, vf x) {
    lo = _mm512_add_pd(lo, _mm512_cvtps_pd(_mm512_castps512_ps256(x)));
    hi = _mm512_add_pd(hi, _mm512_cvtps_pd(_mm256_castpd_ps(_mm512_extractf64x4_pd(_mm512_castps_pd(x), 1))));
}

struct alignas(64) Acc {
    double s1[HQ_NK] = {0, 0, 0}, sp[HQ_NK] = {0, 0, 0};                         // basket, or Asian y
    double sg[HQ_NK] = {0, 0, 0}, sgg[HQ_NK] = {0, 0, 0}, syg[HQ_NK] = {0, 0, 0}; // Asian geometric
    uint64_t pairs = 0;
};

inline void load_streams(const mc::Keys& k, uint64_t first, uint32_t stride, uint32_t off,
                         __m512i& s0, __m512i& s1, __m512i& s2, __m512i& s3) {
    alignas(64) uint32_t t0[V], t1[V], t2[V], t3[V];
    for (int l = 0; l < V; ++l) {
        mc::Xo x = mc::pair_seed(k, (uint32_t)((first + l) * stride + off));
        t0[l] = x.s[0]; t1[l] = x.s[1]; t2[l] = x.s[2]; t3[l] = x.s[3];
    }
    s0 = _mm512_load_si512(t0); s1 = _mm512_load_si512(t1); s2 = _mm512_load_si512(t2); s3 = _mm512_load_si512(t3);
}

// ------------------------------------------------------------------ Case B: basket pairs a0 .. a0+15
void block_b(const Par& P, const hb::BCase& c, hq::Scheme sc, const mc::Keys& k, uint32_t M, uint64_t a0, int cnt, Acc& acc) {
    const vf zero = _mm512_setzero_ps(), one = S(1.0f);
    __m512d BAl = _mm512_setzero_pd(), BAh = BAl, BBl = BAl, BBh = BAl;
    for (int j = 0; j < HB_NA; ++j) {
        __m512i s0, s1, s2, s3;
        load_streams(k, a0, HB_NA, (uint32_t)j, s0, s1, s2, s3);
        Y yA, yB;
        yA.init(); yB.init();
        vf VA = S((float)c.v0), VB = VA;
        if (sc == hq::FT) {
            const vf er = S(P.er), es = S(P.es);
            for (uint32_t t = 0; t < M; ++t) {
                vf z1, z2;
                const __m512i r0 = xo_next(s0, s1, s2, s3), r1 = xo_next(s0, s1, s2, s3);
                box_muller(r0, r1, z1, z2);
                const vf zw = _mm512_fmadd_ps(er, z1, _mm512_mul_ps(es, z2));
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
        const vf S0j = S((float)c.S0[j]);
        add_pd(BAl, BAh, _mm512_mul_ps(S0j, v_exp(_mm512_add_ps(yA.get(), S(P.rT)))));
        add_pd(BBl, BBh, _mm512_mul_ps(S0j, v_exp(_mm512_add_ps(yB.get(), S(P.rT)))));
    }
    alignas(64) double ba[V], bb[V];
    _mm512_store_pd(ba, BAl); _mm512_store_pd(ba + 8, BAh);
    _mm512_store_pd(bb, BBl); _mm512_store_pd(bb + 8, BBh);
    for (int l = 0; l < cnt; ++l) {
        const double A = ba[l] / HB_NA, B = bb[l] / HB_NA;
        for (int j = 0; j < HQ_NK; ++j) {
            const bool put = (c.puts >> j) & 1u;
            const double pa = std::max(put ? c.K[j] - A : A - c.K[j], 0.0), pb = std::max(put ? c.K[j] - B : B - c.K[j], 0.0);
            const double s = pa + pb;
            acc.s1[j] += s; acc.sp[j] += s * s;
        }
    }
    acc.pairs += (uint64_t)cnt;
}

// ------------------------------------------------------------------ Case A: pairs a0 .. a0+15
struct AP { float mu, sd, S0; double K[HQ_NK]; uint32_t M; bool cv; };
void block_a(const AP& P, const mc::Keys& k, uint64_t a0, int cnt, Acc& acc) {
    __m512i s0, s1, s2, s3;
    load_streams(k, a0, 1, 0, s0, s1, s2, s3);
    const vf zero = _mm512_setzero_ps(), mu = S(P.mu), sd = S(P.sd);
    Y yA, yB;
    yA.init(); yB.init();
    __m512d aAl = _mm512_setzero_pd(), aAh = aAl, aBl = aAl, aBh = aAl;    // sums of S / S0
    __m512d gAl = aAl, gAh = aAl, gBl = aAl, gBh = aAl;                       // sums of y
    vf zn = zero;
    for (uint32_t t = 0; t < P.M; ++t) {
        vf z;
        if ((t & 1) == 0) {
            const __m512i r1 = xo_next(s0, s1, s2, s3), r2 = xo_next(s0, s1, s2, s3);
            box_muller(r1, r2, z, zn);
        } else {
            z = zn;
        }
        yA.add(_mm512_fmadd_ps(sd, z, mu));
        yB.add(_mm512_fnmadd_ps(sd, z, mu));
        const vf ya = yA.get(), yb = yB.get();
        add_pd(aAl, aAh, v_exp(ya));
        add_pd(aBl, aBh, v_exp(yb));
        if (P.cv) { add_pd(gAl, gAh, ya); add_pd(gBl, gBh, yb); }
    }
    alignas(64) double sa[V], sb[V], ga[V], gb[V];
    _mm512_store_pd(sa, aAl); _mm512_store_pd(sa + 8, aAh); _mm512_store_pd(sb, aBl); _mm512_store_pd(sb + 8, aBh);
    _mm512_store_pd(ga, gAl); _mm512_store_pd(ga + 8, gAh); _mm512_store_pd(gb, gBl); _mm512_store_pd(gb + 8, gBh);
    const double invM = 1.0 / P.M;
    for (int l = 0; l < cnt; ++l) {
        const double A = P.S0 * sa[l] * invM, B = P.S0 * sb[l] * invM;
        double GA = 0, GB = 0;
        if (P.cv) { GA = P.S0 * std::exp(ga[l] * invM); GB = P.S0 * std::exp(gb[l] * invM); }
        for (int j = 0; j < HQ_NK; ++j) {
            const double ys = std::max(A - P.K[j], 0.0) + std::max(B - P.K[j], 0.0);
            acc.s1[j] += ys; acc.sp[j] += ys * ys;
            if (P.cv) {
                const double gs = std::max(GA - P.K[j], 0.0) + std::max(GB - P.K[j], 0.0);
                acc.sg[j] += gs; acc.sgg[j] += gs * gs; acc.syg[j] += ys * gs;
            }
        }
    }
    acc.pairs += (uint64_t)cnt;
}

template <class F>
Acc run_once(uint64_t a0, uint64_t a1, int threads, std::vector<Acc>& acc, F blk) {
    const uint64_t nb = (a1 - a0 + V - 1) / V;
#pragma omp parallel num_threads(threads)
    {
        Acc& a = acc[omp_get_thread_num()];
        a = Acc();
#pragma omp for schedule(static)
        for (uint64_t b = 0; b < nb; ++b) {
            const uint64_t b0 = a0 + b * V;
            blk(b0, (int)std::min<uint64_t>(V, a1 - b0), a);
        }
    }
    Acc t;
    for (int i = 0; i < threads; ++i) {
        for (int j = 0; j < HQ_NK; ++j) {
            t.s1[j] += acc[i].s1[j]; t.sp[j] += acc[i].sp[j];
            t.sg[j] += acc[i].sg[j]; t.sgg[j] += acc[i].sgg[j]; t.syg[j] += acc[i].syg[j];
        }
        t.pairs += acc[i].pairs;
    }
    return t;
}

std::vector<double> parse_list(const std::string& s, int n, double dflt) {
    std::vector<double> v;
    size_t i = 0;
    while (i < s.size()) {
        size_t j = s.find(',', i);
        if (j == std::string::npos) j = s.size();
        v.push_back(std::stod(s.substr(i, j - i)));
        i = j + 1;
    }
    if (v.empty()) v.assign(n, dflt);
    if ((int)v.size() == 1) v.assign(n, v[0]);
    if ((int)v.size() != n) throw std::runtime_error("--beta needs 1 or 3 values");
    return v;
}

const char* HELP =
    "usage: hs_native --case B --scheme ft|qe (--steps-per-year S | --steps M)\n"
    "       hs_native --case A [--steps M] [--estimator plain|cv] [--beta b | b0,b1,b2]\n"
    "                 [--a0 A] (--a1 B | --pairs N) [--seed S] [--threads T] [--runs R] [--warmup W] [--warmup-s S] [--window-s S] [--out FILE]\n";
}  // namespace

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h")) { std::printf("%s", HELP); return 0; }
    int rc = 0;
    try {
        std::string cs = A.str("case", "B");
        const bool asian = cs == "A";
        std::string scs = A.str("scheme", asian ? "asian" : "qe");
        hb::BCase bc{};
        ha::ACase ac{};
        hq::Scheme sc = hq::QE;
        long long M;
        if (asian) {
            ac = ha::get_acase(cs);
            M = A.has("steps") ? A.i64("steps", 0) : (long long)ac.M;
            ac.M = (uint32_t)M;
        } else {
            bc = hb::get_bcase(cs);
            if (scs != "ft" && scs != "qe") throw std::runtime_error("--scheme must be ft or qe for the basket");
            sc = scs == "ft" ? hq::FT : hq::QE;
            M = A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * bc.T);
        }
        if (M < 1 || M > HQ_M_MAX) throw std::runtime_error("number of steps must be 1..65535");
        uint64_t a0 = (uint64_t)A.i64("a0", 0);
        uint64_t a1 = A.has("a1") ? (uint64_t)A.i64("a1", 0) : a0 + (uint64_t)A.i64("pairs", 65536);
        uint64_t amax = asian ? (1ull << 32) : (1ull << 29);
        if (a1 <= a0 || a1 > amax) throw std::runtime_error(asian ? "need a0 < a1 <= 2^32" : "need a0 < a1 <= 2^29 (basket)");
        uint64_t seed = (uint64_t)A.i64("seed", 20261016);
        int threads = (int)A.i64("threads", omp_get_max_threads());
        int runs = std::max<int>(1, (int)A.i64("runs", 1)), warm = std::max<int>(0, (int)A.i64("warmup", 0));
        double warm_s = A.f64("warmup-s", 0.0), win_s = A.f64("window-s", 0.0);
        std::vector<double> beta = parse_list(A.str("beta", ""), HQ_NK, 1.0);
        std::string est = A.str("estimator", "cv");
        if (asian && est != "plain" && est != "cv") throw std::runtime_error("--estimator must be plain or cv");
        const double T = asian ? ac.T : bc.T;

        R.set("project", "heston_accuracy");
        R.set("platform", "cpu");
        R.set("impl", "native");
        R.set("case", cs);
        R.set("scheme", scs);
        R.set("steps", (long long)M);
        R.set("steps_per_year", (double)M / T);
        R.set("a0", (unsigned long long)a0);
        R.set("a1", (unsigned long long)a1);
        R.set("pairs", (unsigned long long)(a1 - a0));
        R.set("seed", (unsigned long long)seed);
        R.set("threads", threads);
        if (asian) R.set("estimator", est);

        const mc::Keys k = mc::run_keys(seed ^ 0x6e61746976650000ull);   // own streams ("native")
        std::vector<Acc> acc(threads);
        Par Pb{};
        AP Pa{};
        if (asian) {
            const double h = ac.T / M;
            Pa.mu = (float)((ac.r - 0.5 * ac.sigma * ac.sigma) * h);
            Pa.sd = (float)(ac.sigma * std::sqrt(h));
            Pa.S0 = (float)ac.S0;
            for (int j = 0; j < HQ_NK; ++j) Pa.K[j] = ac.K[j];
            Pa.M = (uint32_t)M;
            Pa.cv = est == "cv";
        } else {
            Pb = make_par(bc.unit_case(), (uint32_t)M);
        }
        auto once = [&]() -> Acc {
            if (asian) return run_once(a0, a1, threads, acc, [&](uint64_t b0, int cnt, Acc& a) { block_a(Pa, k, b0, cnt, a); });
            return run_once(a0, a1, threads, acc, [&](uint64_t b0, int cnt, Acc& a) { block_b(Pb, bc, sc, k, (uint32_t)M, b0, cnt, a); });
        };
        Acc m;
        const double tw = bench::now_s();
        for (int i = 0; i < warm || bench::now_s() - tw < warm_s; ++i) m = once();
        std::vector<double> ts;
        bench::mark("start");
        const double tw0 = bench::now_s();
        for (int i = 0; i < runs || bench::now_s() - tw0 < win_s; ++i) {
            const double t0 = bench::now_s();
            m = once();
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
        if (!asian) R.set("asset_steps", psteps * HB_NA);
        R.set("ns_per_path_step_core", tc * threads / psteps * 1e9);

        const double np = (double)m.pairs, disc = std::exp(-(asian ? ac.r : bc.r) * T), dn = np > 1 ? np - 1 : 1;
        bool fin = true;
        char b[96];
        for (int j = 0; j < HQ_NK; ++j) {
            const std::string s = std::to_string(j);
            const double mean = m.s1[j] / (2 * np), vyy = std::max(m.sp[j] / 4 / np - mean * mean, 0.0);
            if (!asian) {
                const double pv = disc * mean, se = disc * std::sqrt(vyy / dn);
                const double price = pv + (((bc.puts >> j) & 1u) ? bc.B0() - bc.K[j] * disc : 0.0);
                fin = fin && std::isfinite(price);
                std::snprintf(b, sizeof b, "%.0f", bc.K[j]); R.set("K" + s, b);
                std::snprintf(b, sizeof b, "%.10f", price); R.set("price" + s, b);
                std::snprintf(b, sizeof b, "%.10f", pv); R.set("pv" + s, b);
                R.set("form" + s, ((bc.puts >> j) & 1u) ? "put" : "call");
                std::snprintf(b, sizeof b, "%.10f", se); R.set("se" + s, b);
                std::snprintf(b, sizeof b, "%.10f", bc.ref[j]); R.set("ref" + s, b);
                std::snprintf(b, sizeof b, "%.10f", price - bc.ref[j]); R.set("bias" + s, b);
            } else {
                const double plain = disc * mean, se_plain = disc * std::sqrt(vyy / dn);
                double cvp = plain, se_cv = se_plain, bh = 0, gcf = ha::geo_call(ac, ac.K[j]);
                if (Pa.cv) {
                    const double gm = m.sg[j] / (2 * np), vgg = std::max(m.sgg[j] / 4 / np - gm * gm, 0.0);
                    const double vyg = m.syg[j] / 4 / np - mean * gm;
                    bh = vgg > 0 ? vyg / vgg : 0;
                    cvp = plain - beta[j] * (disc * gm - gcf);
                    se_cv = disc * std::sqrt(std::max(vyy - 2 * beta[j] * vyg + beta[j] * beta[j] * vgg, 0.0) / dn);
                    std::snprintf(b, sizeof b, "%.10f", disc * gm); R.set("geo_mc" + s, b);
                }
                const double price = Pa.cv ? cvp : plain, se = Pa.cv ? se_cv : se_plain;
                fin = fin && std::isfinite(price);
                std::snprintf(b, sizeof b, "%.0f", ac.K[j]); R.set("K" + s, b);
                std::snprintf(b, sizeof b, "%.10f", price); R.set("price" + s, b);
                std::snprintf(b, sizeof b, "%.10f", se); R.set("se" + s, b);
                std::snprintf(b, sizeof b, "%.10f", plain); R.set("plain" + s, b);
                std::snprintf(b, sizeof b, "%.10f", se_plain); R.set("se_plain" + s, b);
                if (Pa.cv) {
                    std::snprintf(b, sizeof b, "%.10f", cvp); R.set("cv" + s, b);
                    std::snprintf(b, sizeof b, "%.10f", se_cv); R.set("se_cv" + s, b);
                    std::snprintf(b, sizeof b, "%.6f", beta[j]); R.set("beta" + s, b);
                    std::snprintf(b, sizeof b, "%.6f", bh); R.set("beta_hat" + s, b);
                    std::snprintf(b, sizeof b, "%.10f", gcf); R.set("geo_cf" + s, b);
                }
                std::snprintf(b, sizeof b, "%.10f", ac.ref[j]); R.set("ref" + s, b);
                std::snprintf(b, sizeof b, "%.10f", price - ac.ref[j]); R.set("bias" + s, b);
            }
            std::snprintf(b, sizeof b, "%.17g", m.s1[j]); R.set("d1_" + s, b);
            std::snprintf(b, sizeof b, "%.17g", m.sp[j]); R.set("dp_" + s, b);
            if (asian && Pa.cv) {   // sums of the geometric payoff, so that runs can be combined exactly
                std::snprintf(b, sizeof b, "%.17g", m.sg[j]); R.set("dg_" + s, b);
                std::snprintf(b, sizeof b, "%.17g", m.sgg[j]); R.set("dgg_" + s, b);
                std::snprintf(b, sizeof b, "%.17g", m.syg[j]); R.set("dyg_" + s, b);
            }
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
