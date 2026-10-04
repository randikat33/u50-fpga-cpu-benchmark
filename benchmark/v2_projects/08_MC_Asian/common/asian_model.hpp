// asian_model.hpp - scalar REFERENCE MODEL of the Monte Carlo arithmetic-average Asian option (GBM).
//
// Out-of-sample workload for the time-energy model (paper Section 6.8): written, and its time and
// energy predicted, before it ever ran on the card. It reuses the building blocks of 03_MC_Heston
// (mc_model.hpp): xoshiro128** streams per antithetic pair, the segmented-quadratic ICDF, the
// table exp(X/2) and the exact integer moments, so every program gives bit-identical results.
// One floating-point operation per statement; every program is built with -ffp-contract=off.
//
// Financial model: one asset under geometric Brownian motion, discretely monitored arithmetic
// average over the M dates t_j = j*T/M (j = 1..M), call max(avg - K, 0) or put, antithetic pairs.
// Exact GBM step in log space (no discretisation bias except the monitoring):
//   X = 2 ln S;  per step  z = icdf(xo_next)  (int, units 2^-20),  e = VOL * z
//                          side A: XA = (XA + DR) + e      side B: XB = (XB + DR) - e
//                          SA = SA + fexp_half(XA)         SB = SB + fexp_half(XB)   (sums over dates)
//   maturity               m = floor(max(S - KM, 0) * QS)  (exact integer, saturating at 2^40-1)
//   DR = 2 (r - sigma^2/2) dt,  VOL = 2 sigma sqrt(dt) 2^-20,  KM = K*M,  QS = 2^-E, E = ilogb(KM)-24
// The price is disc * mean(m) * 2^E / M.  Accumulation: mc::Mom (exact 128-bit sums).
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include "mc_model.hpp"   // 03_MC_Heston/common: RNG, icdf, fexp_half, Mom, finalize helpers

#define AS_FPAR_N 16      // float parameter block (see AsIdx)
#define AS_M_MAX 65535    // max time steps (16-bit step counter in the kernel)

namespace asn {
using mc::Keys;
using mc::Mom;
using mc::Xo;

enum AsIdx : int { A_X0 = 0, A_DR = 1, A_VOL = 2, A_KM = 3, A_QS = 4 };

struct Market {
    double S0 = 100.0, sigma = 0.2, r = 0.03, K = 100.0, T = 1.0;
    bool put = false;
};

inline int payoff_exp(float KM) { return std::ilogb(KM) - 24; }

inline void make_fpar(const Market& m, uint32_t M, float fp[AS_FPAR_N]) {
    std::memset(fp, 0, sizeof(float) * AS_FPAR_N);
    double dt = m.T / M;
    fp[A_X0] = (float)(2.0 * std::log(m.S0));
    fp[A_DR] = (float)(2.0 * (m.r - 0.5 * m.sigma * m.sigma) * dt);
    fp[A_VOL] = (float)(2.0 * m.sigma * std::sqrt(dt) * 0x1p-20);
    float KM = (float)(m.K * (double)M);
    fp[A_KM] = KM;
    fp[A_QS] = std::ldexp(1.0f, -payoff_exp(KM));
}

// one time step of one antithetic pair (both sides)
inline void pair_step(const float* fp, int32_t z, float& XA, float& XB, float& SA, float& SB) {
    float fz = (float)z;                  // exact (|z| < 2^23)
    float e = fp[A_VOL] * fz;
    float ta = XA + fp[A_DR];
    float tb = XB + fp[A_DR];
    XA = ta + e;
    XB = tb - e;
    float sa = mc::fexp_half(XA);
    float sb = mc::fexp_half(XB);
    SA = SA + sa;
    SB = SB + sb;
}

constexpr float M_SAT_F = 1099511627776.0f;   // 2^40
inline uint64_t payoff_m(const float* fp, float S, bool put, bool* ovf) {
    float d = put ? (fp[A_KM] - S) : (S - fp[A_KM]);
    float po = d > 0.0f ? d : 0.0f;
    float pm = po * fp[A_QS];
    if (!(pm < M_SAT_F)) { *ovf = true; return (1ull << MC_MBITS) - 1; }
    return (uint64_t)pm;
}

struct PairOut { uint64_t mA, mB; };
inline PairOut sim_pair(const float* fp, const Keys& k, uint32_t M, uint32_t a, bool put, bool* ovf) {
    Xo s = mc::pair_seed(k, a);
    float XA = fp[A_X0], XB = fp[A_X0], SA = 0.0f, SB = 0.0f;
    for (uint32_t t = 0; t < M; ++t) asn::pair_step(fp, mc::icdf(mc::xo_next(s)), XA, XB, SA, SB);
    PairOut o;
    o.mA = payoff_m(fp, SA, put, ovf);
    o.mB = payoff_m(fp, SB, put, ovf);
    return o;
}

inline void sim_range(const float* fp, const Keys& k, uint32_t M, uint64_t a0, uint64_t a1, bool put, Mom& m) {
    for (uint64_t a = a0; a < a1; ++a) {
        bool ovf = false;
        PairOut o = asn::sim_pair(fp, k, M, (uint32_t)a, put, &ovf);
        m.add(o.mA, o.mB);
        m.ovf += ovf;
    }
}

inline mc::Price finalize(const Market& mk, uint32_t M, const Mom& m) {
    float fp[AS_FPAR_N];
    make_fpar(mk, M, fp);
    long double q = std::ldexp(1.0L, payoff_exp(fp[A_KM])) / (long double)M;   // payoff = m * 2^E / M
    long double np = (long double)m.pairs, n = 2 * np;
    long double disc = std::exp(-(long double)mk.r * mk.T);
    mc::Price p{};
    p.paths = 2 * m.pairs;
    if (m.pairs == 0) return p;
    long double mean = mc::u128_ld(m.s1) * q / n;
    long double var = mc::u128_ld(m.s2) * q * q / n - mean * mean;
    long double vp = mc::u128_ld(m.sp) * q * q / 4 / np - mean * mean;   // variance of pair averages
    if (var < 0) var = 0;
    if (vp < 0) vp = 0;
    p.mean_payoff = (double)mean;
    p.price = (double)(disc * mean);
    p.se = (double)(disc * std::sqrt(vp / np));
    p.se_naive = (double)(disc * std::sqrt(var / n));
    return p;
}

}  // namespace asn
