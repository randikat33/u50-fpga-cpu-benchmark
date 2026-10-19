// ha_model.hpp - scalar REFERENCE MODEL of secondary Case A (protocol v0.5, Section 4b): arithmetic-average
// Asian call under Black-Scholes, M equally spaced monitoring dates, three strikes, antithetic pairs, with
// the geometric-average call on the same path as control variate. The card (kernel/ha_kernel.cpp) and the
// AVX-512 code (cpu/ha_avx512.hpp) must give bit-identical moments.
//
// Derived from paper 1's 08_MC_Asian (asian_model.hpp: one draw per step, exact GBM log step, table exp)
// with the fixed-point state of hq_model.hpp (an exact int64 sum instead of a float accumulator, whose
// rounding repeats in direction from step to step):
//   X = 2 ln S = X0 + Xi 2^-40;  per step  z = icdf(xo_next) (int, 2^-20),  e = VOL * z
//        side A: Xi += to_q(DR + e, 2^40)        side B: Xi += to_q(DR - e, 2^40)
//        S  = fexp_half(X0 + Xi 2^-40)          (exact-GBM date price; clamped at 2^22, clamp counted)
//        Aq += to_q(S, 2^24)                     exact for S >= 1/2 (fexp_half returns m24 2^e)
//        Gq += Xi                                exact
//   maturity:  A = (float)Aq * CAM,  CAM = (float)(2^-24 / M)          arithmetic average
//              G = fexp_half(X0 + (float)Gq * CGM),  CGM = (float)(2^-40 / M)   geometric average
//              y_k = floor(max(A - K_k, 0) 2^16),  g_k = floor(max(G - K_k, 0) 2^16)  (exact, as hq::payoff)
//   moments per strike over pairs (ys = yA + yB, gs = gA + gB): sum ys, ys^2, gs, gs^2, ys gs (u128)
// Estimators (host): plain  disc * mean(y);  CV  disc * mean(y) - beta (disc * mean(g) - E[G]), with
// E[G] the closed form for discrete monitoring (Kemna and Vorst 1990) and beta fixed per strike.
#pragma once
#include "hq_model.hpp"

#define HA_FPAR_N 16

namespace ha {

struct ACase {
    std::string name;
    double S0, sigma, r, T;
    uint32_t M;                // monitoring dates (= time steps)
    double K[HQ_NK];
    double ref[HQ_NK];         // reference prices (CV run, protocol 4b); NaN until computed
};
#ifndef HA_REF0
#define HA_REF0 NAN
#define HA_REF1 NAN
#define HA_REF2 NAN
#endif
inline ACase get_acase(const std::string& n) {
    if (n == "A") return {"A", 100.0, 0.2, 0.05, 1.0, 250, {90, 100, 110}, {HA_REF0, HA_REF1, HA_REF2}};
    throw std::runtime_error("unknown Asian case '" + n + "' (A)");
}

enum AIdx : int { A_X0 = 0, A_DR = 1, A_VOL = 2, A_K = 3, A_QS = 6, A_CAM = 7, A_CGM = 8 };

inline void make_fpar(const ACase& c, uint32_t M, float fp[HA_FPAR_N]) {
    std::memset(fp, 0, sizeof(float) * HA_FPAR_N);
    double dt = c.T / M;
    fp[A_X0] = (float)(2.0 * std::log(c.S0));
    fp[A_DR] = (float)(2.0 * (c.r - 0.5 * c.sigma * c.sigma) * dt);
    fp[A_VOL] = (float)(2.0 * c.sigma * std::sqrt(dt) * 0x1p-20);
    for (int k = 0; k < HQ_NK; ++k) fp[A_K + k] = (float)c.K[k];
    fp[A_QS] = std::ldexp(1.0f, HQ_QBITS);
    fp[A_CAM] = (float)(0x1p-24 / (double)M);
    fp[A_CGM] = (float)(0x1p-40 / (double)M);
}

constexpr float AQ = 0x1p24f;          // price quantum of the arithmetic sum 2^-24
constexpr float S_CLAMP = 0x1p22f;     // keeps M * S * 2^24 < 2^62 for M < 2^16

struct Side { int64_t Xi = 0, Aq = 0, Gq = 0; };

// one date of one side: increment inc (units of X), then the date price into the sums
inline void side_step(const float* fp, Side& s, float inc, uint32_t& ovf) {
    s.Xi = s.Xi + hq::to_q(inc, hq::XQ);
    float xr = (float)s.Xi * hq::XQI;
    float x = fp[A_X0] + xr;
    float S = mc::fexp_half(x);
    if (!(S < S_CLAMP)) { ovf += 1; S = S_CLAMP; }
    s.Aq = s.Aq + hq::to_q(S, AQ);
    s.Gq = s.Gq + s.Xi;
}
inline void pair_step(const float* fp, int32_t z, Side& A, Side& B, uint32_t& ovf) {
    float fz = (float)z;                               // exact (|z| < 2^23)
    float e = fp[A_VOL] * fz;
    float ia = fp[A_DR] + e;
    float ib = fp[A_DR] - e;
    side_step(fp, A, ia, ovf);
    side_step(fp, B, ib, ovf);
}

inline uint64_t qpay(float d, float QS, uint32_t& ovf) {
    float po = d > 0.0f ? d : 0.0f;
    float pm = po * QS;
    if (!(pm < hq::M_SAT_F)) { ovf += 1; return (1ull << 40) - 1; }
    return (uint64_t)pm;
}
// payoffs of one side: y (arithmetic) and g (geometric) per strike
inline void payoff(const float* fp, const Side& s, uint64_t y[HQ_NK], uint64_t g[HQ_NK], uint32_t& ovf) {
    float af = (float)s.Aq;
    float A = af * fp[A_CAM];
    float gf = (float)s.Gq;
    float gm = gf * fp[A_CGM];
    float xg = fp[A_X0] + gm;
    float G = mc::fexp_half(xg);
    for (int k = 0; k < HQ_NK; ++k) {
        float dy = A - fp[A_K + k];
        float dg = G - fp[A_K + k];
        y[k] = qpay(dy, fp[A_QS], ovf);
        g[k] = qpay(dg, fp[A_QS], ovf);
    }
}

struct AMom {
    mc::u128 sy[HQ_NK] = {0, 0, 0}, syy[HQ_NK] = {0, 0, 0}, sg[HQ_NK] = {0, 0, 0}, sgg[HQ_NK] = {0, 0, 0},
             syg[HQ_NK] = {0, 0, 0};
    uint64_t pairs = 0, ovf = 0;
    void add(const uint64_t yA[HQ_NK], const uint64_t yB[HQ_NK], const uint64_t gA[HQ_NK], const uint64_t gB[HQ_NK]) {
        for (int k = 0; k < HQ_NK; ++k) {
            mc::u128 ys = (mc::u128)yA[k] + yB[k], gs = (mc::u128)gA[k] + gB[k];
            sy[k] += ys; syy[k] += ys * ys; sg[k] += gs; sgg[k] += gs * gs; syg[k] += ys * gs;
        }
        ++pairs;
    }
    void add(const AMom& o) {
        for (int k = 0; k < HQ_NK; ++k) {
            sy[k] += o.sy[k]; syy[k] += o.syy[k]; sg[k] += o.sg[k]; sgg[k] += o.sgg[k]; syg[k] += o.syg[k];
        }
        pairs += o.pairs; ovf += o.ovf;
    }
    bool operator==(const AMom& o) const {
        for (int k = 0; k < HQ_NK; ++k)
            if (sy[k] != o.sy[k] || syy[k] != o.syy[k] || sg[k] != o.sg[k] || sgg[k] != o.sgg[k] || syg[k] != o.syg[k]) return false;
        return pairs == o.pairs && ovf == o.ovf;
    }
    bool operator!=(const AMom& o) const { return !(*this == o); }
};

inline void sim_pair(const float* fp, const mc::Keys& k, uint32_t M, uint32_t a, AMom& m) {
    mc::Xo s = mc::pair_seed(k, a);
    Side A, B;
    uint32_t ovf = 0;
    for (uint32_t t = 0; t < M; ++t) pair_step(fp, mc::icdf(mc::xo_next(s)), A, B, ovf);
    uint64_t yA[HQ_NK], yB[HQ_NK], gA[HQ_NK], gB[HQ_NK];
    payoff(fp, A, yA, gA, ovf);
    payoff(fp, B, yB, gB, ovf);
    m.add(yA, yB, gA, gB);
    m.ovf += ovf;
}
inline void sim_range(const float* fp, const mc::Keys& k, uint32_t M, uint64_t a0, uint64_t a1, AMom& m) {
    for (uint64_t a = a0; a < a1; ++a) sim_pair(fp, k, M, (uint32_t)a, m);
}

// kernel output record: per strike 5 sums x 2 words (sy, syy, sg, sgg, syg; lo, hi), pairs, info
#define HA_OUT_N 32
#define HA_W_PAIRS 30
#define HA_W_INFO 31
inline void mom_to_words(const AMom& m, uint64_t w[HA_OUT_N]) {
    for (int k = 0; k < HQ_NK; ++k) {
        const mc::u128* v[5] = {&m.sy[k], &m.syy[k], &m.sg[k], &m.sgg[k], &m.syg[k]};
        for (int i = 0; i < 5; ++i) { w[10 * k + 2 * i] = (uint64_t)*v[i]; w[10 * k + 2 * i + 1] = (uint64_t)(*v[i] >> 64); }
    }
    w[HA_W_PAIRS] = m.pairs;
    w[HA_W_INFO] = m.ovf;
}
inline AMom mom_from_words(const uint64_t* w) {
    AMom m;
    for (int k = 0; k < HQ_NK; ++k) {
        mc::u128* v[5] = {&m.sy[k], &m.syy[k], &m.sg[k], &m.sgg[k], &m.syg[k]};
        for (int i = 0; i < 5; ++i) *v[i] = ((mc::u128)w[10 * k + 2 * i + 1] << 64) | w[10 * k + 2 * i];
    }
    m.pairs = w[HA_W_PAIRS];
    m.ovf = w[HA_W_INFO] & 0xFFFFFFFFull;
    return m;
}
inline uint64_t mom_hash(const AMom& m) {      // FNV-1a over all words
    uint64_t w[HA_OUT_N];
    mom_to_words(m, w);
    w[HA_W_INFO] = m.ovf;
    uint64_t h = 1469598103934665603ull;
    for (int i = 0; i < HA_OUT_N; ++i) for (int b = 0; b < 64; b += 8) { h ^= (w[i] >> b) & 0xFF; h *= 1099511628211ull; }
    return h;
}

// ------------------------------------------------------------------ closed form and estimators
inline double ncdf(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }
// discounted geometric-average call, dates t_j = j T / M, j = 1..M (Kemna and Vorst 1990, discrete form)
inline double geo_call(const ACase& c, double K) {
    double h = c.T / c.M;
    double mu = std::log(c.S0) + (c.r - 0.5 * c.sigma * c.sigma) * h * (c.M + 1) / 2.0;
    double s2 = c.sigma * c.sigma * h * (c.M + 1) * (2.0 * c.M + 1) / (6.0 * c.M);
    double d2 = (mu - std::log(K)) / std::sqrt(s2), d1 = d2 + std::sqrt(s2);
    return std::exp(-c.r * c.T) * (std::exp(mu + 0.5 * s2) * ncdf(d1) - K * ncdf(d2));
}
struct APrice {
    double plain, se_plain, cv, se_cv, beta, beta_hat, geo_mc, geo_cf, var_ratio;
};
// beta: the fixed coefficient (protocol: from the calibration); beta_hat: this run's own estimate
inline APrice finalize_k(const ACase& c, const AMom& m, int k, double beta) {
    APrice p{};
    if (m.pairs == 0) return p;
    long double q = std::ldexp(1.0L, -HQ_QBITS), np = (long double)m.pairs;
    long double disc = std::exp(-(long double)c.r * c.T);
    long double ym = mc::u128_ld(m.sy[k]) * q / (2 * np), gm = mc::u128_ld(m.sg[k]) * q / (2 * np);
    long double vyy = mc::u128_ld(m.syy[k]) * q * q / 4 / np - ym * ym;
    long double vgg = mc::u128_ld(m.sgg[k]) * q * q / 4 / np - gm * gm;
    long double vyg = mc::u128_ld(m.syg[k]) * q * q / 4 / np - ym * gm;
    if (vyy < 0) vyy = 0;
    if (vgg < 0) vgg = 0;
    long double dn = np > 1 ? np - 1 : 1;
    p.geo_cf = geo_call(c, c.K[k]);
    p.geo_mc = (double)(disc * gm);
    p.plain = (double)(disc * ym);
    p.se_plain = (double)(disc * std::sqrt(vyy / dn));
    p.beta = beta;
    p.beta_hat = vgg > 0 ? (double)(vyg / vgg) : 0.0;
    long double vcv = vyy - 2 * beta * vyg + (long double)beta * beta * vgg;
    if (vcv < 0) vcv = 0;
    p.cv = (double)(disc * ym - beta * (disc * gm - p.geo_cf));
    p.se_cv = (double)(disc * std::sqrt(vcv / dn));
    p.var_ratio = vcv > 0 ? (double)(vyy / vcv) : 0.0;
    return p;
}

}  // namespace ha
