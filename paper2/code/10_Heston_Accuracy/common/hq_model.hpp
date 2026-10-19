// hq_model.hpp - scalar REFERENCE MODEL of the single-asset Heston European call, paper 2
// ("energy to a target accuracy"). Two schemes:
//   FT : Euler with full truncation (Lord et al. 2010): V+ = max(V,0) in drift and diffusion,
//        the stored V may be negative.
//   QE : Andersen (2008) quadratic-exponential scheme, psi_c = 1.5, central discretisation
//        gamma1 = gamma2 = 1/2, no martingale correction.
// Building blocks reused from paper 1 (03_MC_Heston/common/mc_model.hpp): xoshiro128** stream per
// antithetic pair, segmented-quadratic inverse CDF, table exp(X/2), exact 128-bit integer moments.
// New: a fixed-point natural logarithm (hq_ln, tables in hq_log_tables.h) for QE's exponential branch.
//
// Every floating-point operation is one statement, so the order is fixed; every program is built
// with -ffp-contract=off. The FPGA kernel (phase 3) and the AVX-512 code must give bit-identical
// moments to this model. Only float +, -, *, /, sqrt (IEEE, round to nearest), int64<->float
// conversions (float->int truncates) and integer operations are used.
//
// Two draws per time step for both schemes (shared by the two sides of an antithetic pair):
//   FT : u1 -> z1 = icdf(u1) (spot), u2 -> z2 = icdf(u2) (variance, independent part)
//   QE : u1 = uniform of the variance step (quadratic branch: Z_V = icdf(u1); exponential branch:
//        1 - U = (~u1 + 0.5) 2^-32), u2 -> z2 = icdf(u2) (spot)
//   side A uses (u1, z), side B uses (~u1, -z); icdf(~u) == -icdf(u) exactly.
// State: X = 2 ln S without the drift r*t (added at maturity, as in paper 1), kept as X0 + Xi 2^-40
// with Xi an exact int64 sum; FT's U = V dt likewise as Ui 2^-58 (see 'fixed-point state').
// Payoff: three strikes from the same terminal S; m_k = floor(max(d, 0) * 2^16) with d = S - K_k (call)
// or d = -(S - K_k) = K_k - S (put), exact integers saturating at 2^40 - 1. Since protocol v0.4 (13 Oct
// 2026) each strike is priced from the payoff form with the smaller variance among the forms with finite
// variance (Case::puts); a put gives the call by parity, C = P + S0 - K exp(-rT) (finalize_k). The call
// payoff has infinite variance in Case II (Heston moment explosion: E[S_T^2] = inf for T > 13.2 years).
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <stdexcept>
#include "mc_model.hpp"        // 03_MC_Heston/common
#include "hq_log_tables.h"

#define HQ_NK 3                // strikes per run
#define HQ_FPAR_N 32
#define HQ_M_MAX 65535         // max time steps (16-bit step counter in the kernel)
#define HQ_QBITS 16            // payoff quantum 2^-16

namespace hq {
using mc::Keys;
using mc::Xo;
using mc::u128;

enum Scheme : int { FT = 0, QE = 1 };

// ------------------------------------------------------------------ test cases (protocol Section 4)
struct Case {
    std::string name;
    double S0, T, v0, kappa, theta, eps, rho, r;
    double K[HQ_NK];
    double ref[HQ_NK];         // semi-closed-form prices (Heston 1993 / Lewis), protocol Table 4
    unsigned puts;             // bit k set: strike k is priced from the PUT payoff and put-call parity
};
inline Case get_case(const std::string& n) {
    // puts (protocol v0.4): per strike the payoff form with the smaller variance, among forms with finite
    // variance (pilot SD of a pair average, call / put: I 15.4/11.1, 9.2/17.7, 1.8/23.1; P1 5.6/2.0, 5.6/6.3,
    // 1.1/5.9; III 40/11, 36/18, 31/24; II: call variance infinite, E[S_T^2] = inf for T > 13.2 years)
    if (n == "I")   return {"I",   100, 10, 0.04, 0.5, 0.04, 1.0, -0.9, 0.0,  {70, 100, 140}, {35.849770, 13.084670, 0.295774}, 1u};
    if (n == "II")  return {"II",  100, 15, 0.04, 0.3, 0.04, 0.9, -0.5, 0.0,  {70, 100, 140}, {37.169665, 16.649223, 5.138190}, 7u};
    if (n == "III") return {"III", 100, 5,  0.09, 1.0, 0.09, 1.0, -0.3, 0.0,  {70, 100, 140}, {38.772044, 21.795288, 9.983068}, 7u};
    if (n == "P1")  return {"P1",  100, 1,  0.04, 1.5, 0.04, 0.3, -0.6, 0.01, {70, 100, 140}, {31.260968, 8.095646, 0.141170}, 1u};
    throw std::runtime_error("unknown case '" + n + "' (I, II, III, P1)");
}

// ------------------------------------------------------------------ float parameter block
enum FparIdx : int {
    P_X0 = 0,                  // 2 ln S0
    P_D2 = 1,                  // 2 r T
    P_QS = 2,                  // 2^16
    P_K = 3,                   // strikes K0, K1, K2 (3..5)
    // FT (state U = V dt)
    P_U0 = 6,                  // v0 dt
    P_KDT = 7,                 // kappa dt
    P_KTH2 = 8,                // kappa theta dt^2
    P_C1H = 9,                 // eps rho dt / 2
    P_C2Q = 10,                // eps sqrt(1-rho^2) dt 2^-20
    P_L2 = 11,                 // 2 * 2^-20
    // QE (state V)
    P_V0 = 12,                 // v0
    P_TH = 13,                 // theta
    P_EK = 14,                 // exp(-kappa dt)
    P_QC1 = 15,                // eps^2 E (1-E) / kappa
    P_QC2 = 16,                // theta eps^2 (1-E)^2 / (2 kappa)
    P_K0 = 17,                 // 2 K0 = -2 rho kappa theta dt / eps
    P_K1 = 18,                 // 2 K1 = 2 [g1 dt (kappa rho/eps - 1/2) - rho/eps]
    P_K2 = 19,                 // 2 K2 = 2 [g2 dt (kappa rho/eps - 1/2) + rho/eps]
    P_K3 = 20,                 // K3 = g1 dt (1 - rho^2)
    P_K4 = 21,                 // K4 = g2 dt (1 - rho^2)
    P_ZS = 22,                 // 2 * 2^-20   (spot normal, X = 2 ln S)
    P_ZV = 23,                 // 2^-20       (variance normal)
    P_IPC = 24,                // 2/psi_c = 4/3 (quadratic branch if 2/psi >= 4/3)
    P_PUTS = 25,               // put mask as a float 0..7 (bit k: strike k uses the put payoff)
};

inline void make_fpar(const Case& c, uint32_t M, float fp[HQ_FPAR_N]) {
    std::memset(fp, 0, sizeof(float) * HQ_FPAR_N);
    double dt = c.T / M;
    fp[P_X0] = (float)(2.0 * std::log(c.S0));
    fp[P_D2] = (float)(2.0 * c.r * c.T);
    fp[P_QS] = std::ldexp(1.0f, HQ_QBITS);
    for (int k = 0; k < HQ_NK; ++k) fp[P_K + k] = (float)c.K[k];
    fp[P_U0] = (float)(c.v0 * dt);
    fp[P_KDT] = (float)(c.kappa * dt);
    fp[P_KTH2] = (float)(c.kappa * c.theta * dt * dt);
    fp[P_C1H] = (float)(c.eps * c.rho * dt * 0.5);
    fp[P_C2Q] = (float)(c.eps * std::sqrt(1.0 - c.rho * c.rho) * dt * 0x1p-20);
    fp[P_L2] = 0x1p-19f;
    double E = std::exp(-c.kappa * dt), g1 = 0.5, g2 = 0.5, a = c.kappa * c.rho / c.eps - 0.5;
    fp[P_V0] = (float)c.v0;
    fp[P_TH] = (float)c.theta;
    fp[P_EK] = (float)E;
    fp[P_QC1] = (float)(c.eps * c.eps * E * (1.0 - E) / c.kappa);
    fp[P_QC2] = (float)(c.theta * c.eps * c.eps * (1.0 - E) * (1.0 - E) / (2.0 * c.kappa));
    fp[P_K0] = (float)(2.0 * (-c.rho * c.kappa * c.theta * dt / c.eps));
    fp[P_K1] = (float)(2.0 * (g1 * dt * a - c.rho / c.eps));
    fp[P_K2] = (float)(2.0 * (g2 * dt * a + c.rho / c.eps));
    fp[P_K3] = (float)(g1 * dt * (1.0 - c.rho * c.rho));
    fp[P_K4] = (float)(g2 * dt * (1.0 - c.rho * c.rho));
    fp[P_ZS] = 0x1p-19f;
    fp[P_ZV] = 0x1p-20f;
    fp[P_IPC] = 4.0f / 3.0f;
    fp[P_PUTS] = (float)(c.puts & 7u);
}

// ------------------------------------------------------------------ fixed-point natural log
inline const uint32_t* ln_t() { static const uint32_t t[1024] = HQ_LN_T_INIT; return t; }
inline const uint32_t* ln_r() { static const uint32_t t[1024] = HQ_LN_R_INIT; return t; }

// ln(n * 2^sc) for n >= 1, as float. Mantissa: 10-bit table index, then ln(1+d) ~ d - d^2/2 with
// d < 2^-10 (series error < d^3/3 < 3.2e-10), result in Q40 before one int64 -> float rounding.
// Absolute error before the final rounding < 6e-10 (test/test_units.cpp).
inline float hq_ln(uint64_t n, int32_t sc) {
    int lz = __builtin_clzll(n);
    uint64_t top = n << lz;                                   // bit 63 set
    int64_t e = (int64_t)(63 - lz + sc);
    uint32_t fr = (uint32_t)(top >> 31);                      // mantissa fraction, Q32
    uint32_t i = fr >> 22;                                    // 10-bit index
    uint64_t rem = fr & 0x3FFFFFu;                            // < 2^22 (Q32)
    uint64_t d = (rem * ln_r()[i] + (1ull << 30)) >> 31;      // d = rem / (1 + i/1024), Q32, rounded
    uint64_t d2 = (d * d + (1ull << 32)) >> 33;               // d^2 / 2, Q32, rounded
    int64_t L = (int64_t)ln_t()[i] + (int64_t)d - (int64_t)d2; // Q32
    int64_t tot = e * HQ_LN2_Q40 + L * 256;                   // Q40
    float tf = (float)tot;                                    // the only rounding
    return tf * 0x1p-40f;                                     // exact
}
inline float hq_lnf(float x) {                                // x > 0, normal
    uint32_t b;
    std::memcpy(&b, &x, 4);
    int32_t ex = (int32_t)((b >> 23) & 0xFF) - 127;
    uint64_t m = (b & 0x7FFFFFu) | 0x800000u;
    return hq_ln(m, ex - 23);
}

// ------------------------------------------------------------------ fixed-point state
// The log-price X and the FT variance U are accumulated as exact int64 fixed-point numbers. A float
// accumulator X = X + inc rounds every step in a direction that repeats from step to step (inc is
// small against X ~ 9 and changes little), which biased the price by about 7e-5 relative in Case I;
// an integer sum has no such bias. Increments are computed in float and truncated to the grid
// (error < 2^-40, resp. 2^-58, per step). float<->int64 conversions are exact operations on all targets.
constexpr float XQ = 0x1p40f, XQI = 0x1p-40f;        // X = X0 + Xi * 2^-40
constexpr float UQ = 0x1p58f, UQI = 0x1p-58f;        // U = Ui * 2^-58  (U = V dt, |U| < 32)
inline int64_t to_q(float v, float q) { float s = v * q; return (int64_t)s; }   // exact scale, truncation

// ------------------------------------------------------------------ FT: one side of one step
// U = V dt (may be negative), zs = 2 Z1 (units of X), w = eps dt (rho Z1 + sqrt(1-rho^2) Z2)
inline void ft_side(const float* fp, int64_t& Xi, int64_t& Ui, float zs, float w) {
    int64_t upi = Ui > 0 ? Ui : 0;
    float up = (float)upi * UQI;                      // V+ dt
    float P = std::sqrt(up);
    float g = up * fp[P_KDT];
    float h = fp[P_KTH2] - g;                         // kappa (theta - V+) dt^2
    float e = P * w;
    float du = h + e;
    float f = P * zs;
    float y = f - up;                                 // -V+ dt + 2 sqrt(V+ dt) Z1
    Xi = Xi + to_q(y, XQ);
    Ui = Ui + to_q(du, UQ);
}
inline void ft_step(const float* fp, uint32_t u1, uint32_t u2, int64_t& XA, int64_t& UA, int64_t& XB, int64_t& UB) {
    float fz1 = (float)mc::icdf(u1);
    float fz2 = (float)mc::icdf(u2);
    float zs = fp[P_L2] * fz1;                                // exact
    float a1 = fp[P_C1H] * zs;
    float a2 = fp[P_C2Q] * fz2;
    float w = a1 + a2;
    float nzs = -zs, nw = -w;
    ft_side(fp, XA, UA, zs, w);
    ft_side(fp, XB, UB, nzs, nw);
}

// ------------------------------------------------------------------ QE: one side of one step
// uv: the variance uniform of this side (A: u1, B: ~u1); zx = 2 Z (units of X, sign of this side)
inline void qe_side(const float* fp, int64_t& Xi, float& V, uint32_t uv, float zx) {
    float m1 = V - fp[P_TH];
    float m2 = m1 * fp[P_EK];
    float m = fp[P_TH] + m2;                                  // E[V'] > 0
    float s2a = V * fp[P_QC1];
    float s2 = s2a + fp[P_QC2];                               // Var[V'] > 0
    float mm = m * m;
    float t = mm / s2;
    float ip = t + t;                                         // 2 / psi
    // quadratic branch: V' = a (b + Z_V)^2
    float ipm1 = ip - 1.0f;
    float pr = ip * ipm1;
    float sq = std::sqrt(pr > 0.0f ? pr : 0.0f);
    float b2 = ipm1 + sq;
    float den = 1.0f + b2;
    float a = m / den;
    float b = std::sqrt(b2 > 0.0f ? b2 : 0.0f);
    float zv = (float)mc::icdf(uv) * fp[P_ZV];
    float tq = b + zv;
    float t2 = tq * tq;
    float vq = a * t2;
    // exponential branch: 1-p = 2 ip / (2 + ip); V' = 0 if U <= p, else m/(1-p) ln((1-p)/(1-U))
    float ip2 = ip + ip;
    float dd = 2.0f + ip;
    float opp = ip2 / dd;
    uint32_t cu = ~uv;
    float omu = (float)cu * 0x1p-32f;                         // ~ 1 - U
    float ve = 0.0f;
    if (omu < opp) {
        float lo = hq_lnf(opp);
        float lu = hq_ln(2ull * cu + 1ull, -33);              // ln((cu + 0.5) 2^-32), exact argument
        float df = lo - lu;
        float ratio = m / opp;
        ve = ratio * df;
    }
    float vn = ip >= fp[P_IPC] ? vq : ve;
    // log-price, central discretisation
    float c1 = fp[P_K1] * V;
    float c2 = fp[P_K2] * vn;
    float s = fp[P_K0] + c1;
    float s3 = s + c2;
    float d3 = fp[P_K3] * V;
    float d4 = fp[P_K4] * vn;
    float dv = d3 + d4;
    float sd = std::sqrt(dv);
    float y = sd * zx;
    float inc = s3 + y;
    Xi = Xi + to_q(inc, XQ);
    V = vn;
}
inline void qe_step(const float* fp, uint32_t u1, uint32_t u2, int64_t& XA, float& VA, int64_t& XB, float& VB) {
    float zx = fp[P_ZS] * (float)mc::icdf(u2);               // exact
    float nzx = -zx;
    qe_side(fp, XA, VA, u1, zx);
    qe_side(fp, XB, VB, ~u1, nzx);
}

// ------------------------------------------------------------------ payoff and moments
constexpr float M_SAT_F = 1099511627776.0f;   // 2^40
inline unsigned put_mask(const float* fp) { return (unsigned)fp[P_PUTS] & 7u; }
inline void payoff(const float* fp, int64_t Xi, uint64_t m[HQ_NK], uint32_t& ovf) {
    float xr = (float)Xi * XQI;
    float x = fp[P_X0] + xr;
    float xt = x + fp[P_D2];
    float S = mc::fexp_half(xt);
    for (int k = 0; k < HQ_NK; ++k) {
        float d = S - fp[P_K + k];
        if ((put_mask(fp) >> k) & 1u) d = -d;         // put payoff K - S (exact negation)
        float po = d > 0.0f ? d : 0.0f;
        float pm = po * fp[P_QS];
        if (!(pm < M_SAT_F)) { ovf += 1; m[k] = (1ull << 40) - 1; }
        else m[k] = (uint64_t)pm;
    }
}

struct Mom {
    // s1 = sum over pairs of (mA + mB), sp = sum of (mA + mB)^2; the price and its SE need only these
    // two (a third sum of mA^2 + mB^2 was dropped in phase 3: it cost a third of the kernel's settle DSPs)
    u128 s1[HQ_NK] = {0, 0, 0}, sp[HQ_NK] = {0, 0, 0};
    uint64_t pairs = 0, ovf = 0;
    void add(const uint64_t mA[HQ_NK], const uint64_t mB[HQ_NK]) {
        for (int k = 0; k < HQ_NK; ++k) {
            u128 a = mA[k], b = mB[k], s = a + b;
            s1[k] += s; sp[k] += s * s;
        }
        ++pairs;
    }
    void add(const Mom& o) {
        for (int k = 0; k < HQ_NK; ++k) { s1[k] += o.s1[k]; sp[k] += o.sp[k]; }
        pairs += o.pairs; ovf += o.ovf;
    }
    bool operator==(const Mom& o) const {
        for (int k = 0; k < HQ_NK; ++k) if (s1[k] != o.s1[k] || sp[k] != o.sp[k]) return false;
        return pairs == o.pairs && ovf == o.ovf;
    }
    bool operator!=(const Mom& o) const { return !(*this == o); }
};

inline void sim_pair(const float* fp, Scheme sc, const Keys& k, uint32_t M, uint32_t a,
                     uint64_t mA[HQ_NK], uint64_t mB[HQ_NK], uint32_t& ovf) {
    Xo s = mc::pair_seed(k, a);
    int64_t XA = 0, XB = 0;
    if (sc == FT) {
        int64_t UA = to_q(fp[P_U0], UQ), UB = UA;
        for (uint32_t t = 0; t < M; ++t) {
            uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
            ft_step(fp, u1, u2, XA, UA, XB, UB);
        }
    } else {
        float VA = fp[P_V0], VB = fp[P_V0];
        for (uint32_t t = 0; t < M; ++t) {
            uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
            qe_step(fp, u1, u2, XA, VA, XB, VB);
        }
    }
    payoff(fp, XA, mA, ovf);
    payoff(fp, XB, mB, ovf);
}
inline void sim_range(const float* fp, Scheme sc, const Keys& k, uint32_t M, uint64_t a0, uint64_t a1, Mom& m) {
    for (uint64_t a = a0; a < a1; ++a) {
        uint64_t mA[HQ_NK], mB[HQ_NK];
        uint32_t ovf = 0;
        sim_pair(fp, sc, k, M, (uint32_t)a, mA, mB, ovf);
        m.add(mA, mB);
        m.ovf += ovf;
    }
}

// kernel output record: 14 x uint64 = per strike (s1 lo, hi, sp lo, hi), pairs, info
// (info: bits 31:0 payoff overflows, 39:32 lanes, 55:40 slots per lane, 63:56 scheme)
#define HQ_OUT_N 14
#define HQ_W_PAIRS 12
#define HQ_W_INFO 13
inline void mom_to_words(const Mom& m, uint64_t w[HQ_OUT_N]) {
    for (int k = 0; k < HQ_NK; ++k) {
        w[4 * k + 0] = (uint64_t)m.s1[k]; w[4 * k + 1] = (uint64_t)(m.s1[k] >> 64);
        w[4 * k + 2] = (uint64_t)m.sp[k]; w[4 * k + 3] = (uint64_t)(m.sp[k] >> 64);
    }
    w[HQ_W_PAIRS] = m.pairs;
    w[HQ_W_INFO] = m.ovf;
}
inline Mom mom_from_words(const uint64_t* w) {
    Mom m;
    for (int k = 0; k < HQ_NK; ++k) {
        m.s1[k] = ((u128)w[4 * k + 1] << 64) | w[4 * k + 0];
        m.sp[k] = ((u128)w[4 * k + 3] << 64) | w[4 * k + 2];
    }
    m.pairs = w[HQ_W_PAIRS];
    m.ovf = w[HQ_W_INFO] & 0xFFFFFFFFull;
    return m;
}

inline uint64_t mom_hash(const Mom& m) {      // FNV-1a over all words (for logs)
    uint64_t h = 1469598103934665603ull;
    auto eat = [&](uint64_t w) { for (int b = 0; b < 64; b += 8) { h ^= (w >> b) & 0xFF; h *= 1099511628211ull; } };
    for (int k = 0; k < HQ_NK; ++k) {
        eat((uint64_t)m.s1[k]); eat((uint64_t)(m.s1[k] >> 64));
        eat((uint64_t)m.sp[k]); eat((uint64_t)(m.sp[k] >> 64));
    }
    eat(m.pairs); eat(m.ovf);
    return h;
}

// ------------------------------------------------------------------ finalisation (price per strike)
struct Price { double price, se, mean_payoff, pv; };   // price: the call; pv: discounted mean of the summed payoff (call or put)
inline Price finalize_k(const Case& c, const Mom& m, int k) {
    long double q = std::ldexp(1.0L, -HQ_QBITS);
    long double np = (long double)m.pairs;
    long double disc = std::exp(-(long double)c.r * c.T);
    Price p{};
    if (m.pairs == 0) return p;
    long double mean = mc::u128_ld(m.s1[k]) * q / (2 * np);
    long double vp = mc::u128_ld(m.sp[k]) * q * q / 4 / np - mean * mean;   // variance of pair averages
    if (vp < 0) vp = 0;
    p.mean_payoff = (double)mean;
    p.pv = (double)(disc * mean);
    p.price = p.pv + (((c.puts >> k) & 1u) ? (double)(c.S0 - c.K[k] * disc) : 0.0);   // C = P + S0 - K exp(-rT)
    p.se = (double)(disc * std::sqrt(vp / (np > 1 ? np - 1 : 1)));
    return p;
}
inline std::string u128_hex(u128 v) {
    char b[48];
    // leading 'h': the report's JSON writer prints values that parse as numbers without quotes
    std::snprintf(b, sizeof b, "h%016llx%016llx", (unsigned long long)(uint64_t)(v >> 64), (unsigned long long)(uint64_t)v);
    return b;
}

}  // namespace hq
