// hb_model.hpp - scalar REFERENCE MODEL of secondary Case B (protocol v0.5, Section 4b): paper 1's
// 8-asset arithmetic basket under Heston, schemes FT and QE, three strikes. The card (kernel/hb_kernel.cpp)
// and the AVX-512 code (cpu/hb_avx512.hpp) must give bit-identical moments.
//
// Paper 1's market has independent assets with identical Heston parameters; they differ only in S0.
// Each asset of a basket pair is simulated exactly like one single-asset pair of hq_model.hpp (same
// statements), as a "unit" with its own stream: unit u = 8 a + j for basket pair a and asset j
// (stream mc::pair_seed(keys, u)), so basket pairs a < 2^29. The state X starts at 0 for every asset;
// the asset's 2 ln S0_j enters only at maturity (as P_X0 does in hq_model.hpp).
// Maturity, per side:   S_j  = fexp_half((X0_j + Xi_j 2^-40) + D2)                      (as hq::payoff)
//                       Sq_j = to_q(min(S_j, 2^28), 2^32)  - exact for S_j >= 2^-9 (fexp_half returns
//                              m24 2^e), so the integer sum below does not depend on the order
//                       B    = (float)(sum_j Sq_j) * 2^-35  (= mean of the 8 prices; the only rounding)
//                       m_k  = floor(max(+-(B - K_k), 0) 2^16) exactly as hq::payoff (put mask per strike)
// Moments: hq::Mom (s1, sp per strike) over basket pairs; parity for a put strike uses B(0) = mean S0_j.
#pragma once
#include "hq_model.hpp"

#define HB_NA 8
#define HB_FPAR_N 48

namespace hb {
using hq::Mom;
using hq::Scheme;

struct BCase {
    std::string name;
    double S0[HB_NA], T, v0, kappa, theta, eps, rho, r;
    double K[HQ_NK];
    double ref[HQ_NK];   // reference prices (protocol 4b: long double-precision runs); NaN until computed
    unsigned puts;       // bit k: strike k priced from the put payoff and parity
    double B0() const { double s = 0; for (int j = 0; j < HB_NA; ++j) s += S0[j]; return s / HB_NA; }
    hq::Case unit_case() const {   // the per-asset dynamics as a single-asset case (S0 unused by the units)
        return {name, 100.0, T, v0, kappa, theta, eps, rho, r, {K[0], K[1], K[2]}, {ref[0], ref[1], ref[2]}, puts};
    }
};
// reference prices: filled in from results/reference_B.csv (scripts/reference_s.py) before the calibration
#ifndef HB_REF0
#define HB_REF0 NAN
#define HB_REF1 NAN
#define HB_REF2 NAN
#endif
inline BCase get_bcase(const std::string& n) {
    // paper 1's v1_market (03_MC_Heston/common/mc_model.hpp): S0_i = 100 + 0.1 i, v0 = theta = 0.04,
    // kappa = 1.5, eps = 0.3, rho = -0.6, q = 0, L = identity, r = 0.01, T = 1.
    // puts = 3: put payoff at K = 90 and 100, call at 110 (pilot SD of a pair average, call / put:
    // 1.96 / 0.61, 2.10 / 2.04, 0.88 / 2.02; protocol v0.5 Section 9c)
    if (n == "B") {
        BCase c{"B", {}, 1.0, 0.04, 1.5, 0.04, 0.3, -0.6, 0.01, {90, 100, 110}, {HB_REF0, HB_REF1, HB_REF2}, 3u};
        for (int j = 0; j < HB_NA; ++j) c.S0[j] = 100.0 + 0.1 * j;
        return c;
    }
    throw std::runtime_error("unknown basket case '" + n + "' (B)");
}

enum : int { P_BX0 = 32 };   // 2 ln S0_j, j = 0..7 (indices 32..39); 0..31 as hq::FparIdx

inline void make_fpar(const BCase& c, uint32_t M, float fp[HB_FPAR_N]) {
    std::memset(fp, 0, sizeof(float) * HB_FPAR_N);
    hq::make_fpar(c.unit_case(), M, fp);
    fp[hq::P_X0] = 0.0f;                               // not used by the basket
    for (int j = 0; j < HB_NA; ++j) fp[P_BX0 + j] = (float)(2.0 * std::log(c.S0[j]));
}

constexpr float SQ = 0x1p32f;          // price quantum 2^-32
constexpr float S_CLAMP = 0x1p28f;     // keeps 8 * S * 2^32 < 2^63
constexpr float BQI = 0x1p-35f;        // 2^-32 / 8

// one unit (asset j of basket pair a): the hq_model.hpp dynamics, both sides; returns the final Xi
inline void sim_unit(const float* fp, Scheme sc, const mc::Keys& k, uint32_t M, uint32_t u, int64_t& XA, int64_t& XB) {
    mc::Xo s = mc::pair_seed(k, u);
    XA = 0; XB = 0;
    if (sc == hq::FT) {
        int64_t UA = hq::to_q(fp[hq::P_U0], hq::UQ), UB = UA;
        for (uint32_t t = 0; t < M; ++t) {
            uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
            hq::ft_step(fp, u1, u2, XA, UA, XB, UB);
        }
    } else {
        float VA = fp[hq::P_V0], VB = fp[hq::P_V0];
        for (uint32_t t = 0; t < M; ++t) {
            uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
            hq::qe_step(fp, u1, u2, XA, VA, XB, VB);
        }
    }
}
// price of asset j at maturity, as an exact integer multiple of 2^-32 (clamped; clamp counted in ovf)
inline int64_t price_q(const float* fp, int j, int64_t Xi, uint32_t& ovf) {
    float xr = (float)Xi * hq::XQI;
    float x = fp[P_BX0 + j] + xr;
    float xt = x + fp[hq::P_D2];
    float S = mc::fexp_half(xt);
    if (!(S < S_CLAMP)) { ovf += 1; S = S_CLAMP; }
    return hq::to_q(S, SQ);
}
// basket payoffs from the summed quantised prices (sum of the 8 Sq_j), as hq::payoff
inline void bpayoff(const float* fp, int64_t sumq, uint64_t m[HQ_NK], uint32_t& ovf) {
    float bf = (float)sumq;
    float B = bf * BQI;                                // exact scale
    for (int k = 0; k < HQ_NK; ++k) {
        float d = B - fp[hq::P_K + k];
        if ((hq::put_mask(fp) >> k) & 1u) d = -d;
        float po = d > 0.0f ? d : 0.0f;
        float pm = po * fp[hq::P_QS];
        if (!(pm < hq::M_SAT_F)) { ovf += 1; m[k] = (1ull << 40) - 1; }
        else m[k] = (uint64_t)pm;
    }
}
inline void sim_pair(const float* fp, Scheme sc, const mc::Keys& k, uint32_t M, uint32_t a,
                     uint64_t mA[HQ_NK], uint64_t mB[HQ_NK], uint32_t& ovf) {
    int64_t sa = 0, sb = 0;
    for (int j = 0; j < HB_NA; ++j) {
        int64_t XA, XB;
        hb::sim_unit(fp, sc, k, M, (uint32_t)(HB_NA * a + j), XA, XB);
        sa += price_q(fp, j, XA, ovf);
        sb += price_q(fp, j, XB, ovf);
    }
    bpayoff(fp, sa, mA, ovf);
    bpayoff(fp, sb, mB, ovf);
}
inline void sim_range(const float* fp, Scheme sc, const mc::Keys& k, uint32_t M, uint64_t a0, uint64_t a1, Mom& m) {
    for (uint64_t a = a0; a < a1; ++a) {
        uint64_t mA[HQ_NK], mB[HQ_NK];
        uint32_t ovf = 0;
        hb::sim_pair(fp, sc, k, M, (uint32_t)a, mA, mB, ovf);
        m.add(mA, mB);
        m.ovf += ovf;
    }
}

inline hq::Price finalize_k(const BCase& c, const Mom& m, int k) {
    hq::Case u = c.unit_case();
    u.S0 = c.B0();                                     // parity: C = P + B(0) - K e^{-rT}
    return hq::finalize_k(u, m, k);
}

}  // namespace hb
