// pf_model.hpp - scalar REFERENCE MODEL of the v2 portfolio Monte Carlo.
//
// This file defines the exact numerics shared by the FPGA kernel (bit-identical by test),
// the CPU scalar baseline and the CPU AVX-512 baseline.  Every floating-point expression is
// written with one operation per statement so that the evaluation order is fixed; all
// programs are built with -ffp-contract=off (no FMA contraction).
//
// Per path p of trade id (stage s, seed):
//   keys     = mix64-derived 4x32-bit words of (seed ^ (s<<32 | id))
//   rng      = xoshiro128** seeded with fmix32(p ^ key[j]), j = 0..3
//   step r   = 6 outputs -> ICDF -> z0..z5 (int, 2^-20)
//              zS  = ((W0'z0 + W1'z1) + (W2'z2 + W3'z3)) + R'z4        (W' = W*2^-20)
//              zVs = sr*zS + ss'*z5                                      (ss' = sigma*sqrt(1-rho^2)*2^-20)
//   Heston   = log-Euler for ln(S/S0), Euler for v with reflection (v1 kernel) or full truncation
//   Asian    = arithmetic average of S at steps 1..n (sequential float sum of fexp(logR))
//   payoff   = quantised to q = round(min(po, cap) * 2^16) and accumulated EXACTLY as integers,
//              so moments are independent of how paths are partitioned (lanes, CUs, threads).
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include "pf_config.hpp"
#include "pf_types.hpp"
#include "pf_tables.h"

namespace pf {

// ------------------------------------------------------------------ run parameters
struct RunParams {
    uint32_t n_under = PF_MAX_UNDER;   // underlying ids >= n_under map to 0 (v1 behaviour)
    uint32_t n_paths = 256;
    uint32_t n_steps = 32;
    uint64_t seed = 12345;
    uint32_t stage = 1;                // 1 = screening, 2 = refinement (separate RNG streams)
    uint32_t ft = 0;                   // 0 = reflection (v1 kernel), 1 = full truncation
    float dspot = 0.0f, drate = 0.0f, volscale = 1.0f;
};

// market blob accessors (7168 floats, v1 Market layout)
inline float mk_spot(const float* m, uint32_t u) { return m[u]; }
inline float mk_rate(const float* m, uint32_t u) { return m[PF_MAX_UNDER + u]; }
inline float mk_divq(const float* m, uint32_t u) { return m[2 * PF_MAX_UNDER + u]; }
inline float mk_fw(const float* m, uint32_t u, int k) { return m[3 * PF_MAX_UNDER + PF_NF * u + k]; }

// ------------------------------------------------------------------ hashing / RNG
inline uint64_t mix64(uint64_t z) {   // splitmix64 finaliser
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}
inline uint32_t fmix32(uint32_t h) {  // murmur3 finaliser (bijective)
    h ^= h >> 16; h *= 0x85ebca6bu;
    h ^= h >> 13; h *= 0xc2b2ae35u;
    h ^= h >> 16;
    return h;
}
struct Keys { uint32_t k[4]; };
inline Keys trade_keys(uint64_t seed, uint32_t stage, uint32_t id) {
    uint64_t base = seed ^ ((static_cast<uint64_t>(stage) << 32) | id);
    uint64_t a = mix64(base + PF_GOLDEN64);
    uint64_t b = mix64(base + 2 * PF_GOLDEN64);
    Keys k;
    k.k[0] = static_cast<uint32_t>(a); k.k[1] = static_cast<uint32_t>(a >> 32);
    k.k[2] = static_cast<uint32_t>(b); k.k[3] = static_cast<uint32_t>(b >> 32);
    return k;
}
struct Xo { uint32_t s[4]; };
inline Xo path_seed(const Keys& k, uint32_t path) {
    Xo x;
    for (int j = 0; j < 4; ++j) x.s[j] = fmix32(path ^ k.k[j]);
    if ((x.s[0] | x.s[1] | x.s[2] | x.s[3]) == 0) x.s[0] = 0x9E3779B9u;
    return x;
}
inline uint32_t rotl32(uint32_t x, int k) { return (x << k) | (x >> (32 - k)); }
inline uint32_t xo_next(Xo& x) {      // xoshiro128** (Blackman & Vigna)
    uint32_t* s = x.s;
    const uint32_t result = rotl32(s[1] * 5, 7) * 9;
    const uint32_t t = s[1] << 9;
    s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3];
    s[2] ^= t;
    s[3] = rotl32(s[3], 11);
    return result;
}

// ------------------------------------------------------------------ inverse normal CDF
inline const uint64_t* icdf_rom() { static const uint64_t rom[2048] = PF_ICDF_ROM_INIT; return rom; }
inline int32_t rom_c0(uint64_t w) { return static_cast<int32_t>(w >> 29); }
inline int32_t rom_c1(uint64_t w) { return static_cast<int32_t>(static_cast<int64_t>(w << 35) >> 47); }
inline int32_t rom_c2(uint64_t w) { return static_cast<int32_t>(static_cast<int64_t>(w << 52) >> 52); }

// z = PhiInv((u + 0.5) / 2^32) in units of 2^-20 (|z| < 2^23, so (float)z is exact)
inline int32_t icdf(uint32_t u) {
    uint32_t sgn = u >> 31;
    uint32_t mi = (sgn ? ~u : u) & 0x7FFFFFFFu;
    uint32_t lz = mi ? static_cast<uint32_t>(__builtin_clz(mi)) - 1 : 31;
    uint32_t n = mi << lz;
    uint32_t idx = (lz << 6) | ((n >> 24) & 63);
    int32_t x = static_cast<int32_t>((n >> 9) & 0x7FFF);
    uint64_t w = icdf_rom()[idx];
    int32_t a = (rom_c2(w) * x) >> 15;
    int32_t b = rom_c1(w) + a;
    int32_t p = (b * x) >> 15;
    int32_t zi = rom_c0(w) + p;
    int32_t zr = (zi + 2) >> 2;
    return sgn ? zr : -zr;
}

// ------------------------------------------------------------------ exp (hardware form)
constexpr int64_t PF_INVLN2_Q30 = 1549082005;          // round(2^30 / ln 2)
constexpr uint64_t PF_LN2_Q32 = 2977044472ull;         // round(2^32 * ln 2)
constexpr uint64_t PF_EXP_MLIM = 4294967167ull;        // 2^32 - 129 (keeps m24 < 2^24)
inline const uint32_t* exp_t() { static const uint32_t t[256] = PF_EXP_T_INIT; return t; }
inline const float* exp_p2() { static const float t[256] = PF_EXP_P2_INIT; return t; }

// clamp(x, -64, 64) with NaN -> -64 (ordered compares, identical on all targets)
inline float exp_clamp(float x) { return (x >= -64.0f) ? ((x <= 64.0f) ? x : 64.0f) : -64.0f; }

inline float fexp(float x) {
    float xc = exp_clamp(x);
    float xs = xc * 16777216.0f;                                  // exact (power of two)
    int32_t xq = static_cast<int32_t>(std::floor(xs));           // Q7.24, |xq| <= 2^30
    int64_t y = static_cast<int64_t>(xq) * PF_INVLN2_Q30;         // x/ln2, Q54
    int32_t n = static_cast<int32_t>(y >> 54);                    // floor
    uint32_t fr = static_cast<uint32_t>(y >> 22);                 // fraction, Q32
    uint32_t i = fr >> 24;
    uint32_t fl = fr & 0xFFFFFFu;
    uint32_t d = static_cast<uint32_t>((static_cast<uint64_t>(fl) * PF_LN2_Q32) >> 32);
    uint32_t dd = static_cast<uint32_t>((static_cast<uint64_t>(d) * d) >> 33);
    uint32_t g = d + dd;                                          // e^d - 1 (2nd order), Q32
    uint64_t T = exp_t()[i];
    uint64_t M = T + ((T * g) >> 32);                             // Q31 mantissa
    if (M > PF_EXP_MLIM) M = PF_EXP_MLIM;
    int32_t m24 = static_cast<int32_t>((M + 128) >> 8);          // < 2^24 -> exact float
    float mf = static_cast<float>(m24);
    return mf * exp_p2()[n + 128];                                 // exact (normal result)
}

// ------------------------------------------------------------------ per-trade constants
struct TradeConst {
    float S0, K, v0, theta, kdt, sqdt, mu, hdt, sr, ssq, Rq, inv_n;
    float W[PF_NF];
    uint32_t put, asian, ft, n_steps, id;
    Keys keys;
};

struct CallConst {        // per kernel call (computed once)
    float nf, inv_n, one_p_dspot;
};
inline CallConst call_const(const RunParams& rp) {
    CallConst c;
    c.nf = static_cast<float>(rp.n_steps);
    c.inv_n = 1.0f / c.nf;
    c.one_p_dspot = 1.0f + rp.dspot;
    return c;
}

inline TradeConst setup(const Trade& t, uint32_t id, const float* mkt, const RunParams& rp, const CallConst& cc) {
    TradeConst c;
    uint32_t uid = t.underlying_id < rp.n_under ? t.underlying_id : 0;
    float dt = t.T / cc.nf;
    c.sqdt = std::sqrt(dt);
    c.hdt = 0.5f * dt;
    float rr = mk_rate(mkt, uid) + rp.drate;
    float rmq = rr - mk_divq(mkt, uid);
    c.mu = rmq * dt;
    c.kdt = t.kappa * dt;
    c.theta = t.theta;
    float sig = t.sigma * rp.volscale;
    float rho2 = t.rho * t.rho;
    float omr = 1.0f - rho2;
    omr = omr < 0.0f ? 0.0f : omr;
    float srho = std::sqrt(omr);
    c.sr = sig * t.rho;
    float ss = sig * srho;
    c.ssq = ss * 0x1p-20f;
    float w2 = 0.0f;
    for (int k = 0; k < PF_NF; ++k) {
        float w = mk_fw(mkt, uid, k);
        float wsq = w * w;
        w2 = w2 + wsq;
        c.W[k] = w * 0x1p-20f;
    }
    float res = 1.0f - w2;
    res = res < 0.0f ? 0.0f : res;
    float R = std::sqrt(res);
    c.Rq = R * 0x1p-20f;
    c.S0 = mk_spot(mkt, uid) * cc.one_p_dspot;
    c.K = t.K;
    c.v0 = t.v0 > 0.0f ? t.v0 : 1e-4f;
    c.inv_n = cc.inv_n;
    c.put = t.option_type != 0;
    c.asian = t.option_kind == 1;
    c.ft = rp.ft;
    c.n_steps = rp.n_steps;
    c.id = id;
    c.keys = trade_keys(rp.seed, rp.stage, id);
    return c;
}

// ------------------------------------------------------------------ path kernel pieces
inline void mix_z(const int32_t z[6], const TradeConst& c, float& zS, float& zVs) {
    float f0 = static_cast<float>(z[0]), f1 = static_cast<float>(z[1]), f2 = static_cast<float>(z[2]);
    float f3 = static_cast<float>(z[3]), f4 = static_cast<float>(z[4]), f5 = static_cast<float>(z[5]);
    float m0 = c.W[0] * f0;
    float m1 = c.W[1] * f1;
    float m2 = c.W[2] * f2;
    float m3 = c.W[3] * f3;
    float mr = c.Rq * f4;
    float s01 = m0 + m1;
    float s23 = m2 + m3;
    float s03 = s01 + s23;
    zS = s03 + mr;
    float a = c.sr * zS;
    float b = c.ssq * f5;
    zVs = a + b;
}
inline void draw_z(Xo& s, const TradeConst& c, float& zS, float& zVs) {
    int32_t z[6];
    for (int k = 0; k < 6; ++k) z[k] = icdf(xo_next(s));
    mix_z(z, c, zS, zVs);
}
inline void heston_step(float& v, float& lr, float zS, float zVs, const TradeConst& c) {
    float sq = std::sqrt(v);
    float s = sq * c.sqdt;
    float tv = c.theta - v;
    float d1 = c.kdt * tv;
    float va = v + d1;
    float d2 = s * zVs;
    float vn = va + d2;
    float hv = c.hdt * v;
    float l1 = c.mu - hv;
    float la = lr + l1;
    float l2 = s * zS;
    lr = la + l2;
    if (c.ft) v = vn > 0.0f ? vn : 0.0f;
    else      v = vn < 0.0f ? -vn : vn;
}
inline uint32_t payoff_q(float E, float sE, const TradeConst& c) {
    float base;
    if (c.asian) base = sE * c.inv_n;
    else         base = E;
    float Sp = base * c.S0;
    float d = c.put ? (c.K - Sp) : (Sp - c.K);
    float po = d > 0.0f ? d : 0.0f;
    float pc = po < PF_PO_CAP ? po : PF_PO_CAP;
    float ps = pc * 131072.0f;                          // exact, < 2^31
    uint32_t q17 = static_cast<uint32_t>(ps);           // truncation == floor (ps >= 0)
    return (q17 + 1) >> 1;
}

// scalar per-path simulation
inline uint32_t sim_path(const TradeConst& c, uint32_t p) {
    Xo s = path_seed(c.keys, p);
    float zS, zVs;
    draw_z(s, c, zS, zVs);
    float v = c.v0, lr = 0.0f, sE = 0.0f, E = 0.0f;
    for (uint32_t r = 1; r <= c.n_steps; ++r) {
        heston_step(v, lr, zS, zVs, c);
        if (c.asian) { E = fexp(lr); sE = sE + E; }
        if (r < c.n_steps) draw_z(s, c, zS, zVs);
    }
    if (!c.asian) E = fexp(lr);
    return payoff_q(E, sE, c);
}

struct Mom {
    uint64_t sum = 0;
    unsigned __int128 sum2 = 0;
    void add(uint32_t q) { sum += q; sum2 += static_cast<uint64_t>(q) * q; }
    void add(const Mom& o) { sum += o.sum; sum2 += o.sum2; }
    bool operator==(const Mom& o) const { return sum == o.sum && sum2 == o.sum2; }
    bool operator!=(const Mom& o) const { return !(*this == o); }
};
inline void sim_range(const TradeConst& c, uint32_t p0, uint32_t p1, Mom& m) {
    for (uint32_t p = p0; p < p1; ++p) m.add(sim_path(c, p));
}
inline Moments to_record(const Mom& m, uint32_t id) {
    Moments r;
    r.sum = m.sum;
    r.s2lo = static_cast<uint64_t>(m.sum2);
    r.s2hi = static_cast<uint64_t>(m.sum2 >> 64);
    r.tag = id | (1ull << 63);
    return r;
}
inline Mom from_record(const Moments& r) {
    Mom m;
    m.sum = r.sum;
    m.sum2 = (static_cast<unsigned __int128>(r.s2hi) << 64) | r.s2lo;
    return m;
}

// ------------------------------------------------------------------ finalisation (double)
inline Result finalize(const Trade& t, const float* mkt, const RunParams& rp, const Mom& m) {
    uint32_t uid = t.underlying_id < rp.n_under ? t.underlying_id : 0;
    float rr = mk_rate(mkt, uid) + rp.drate;
    double disc = std::exp(-static_cast<double>(rr) * static_cast<double>(t.T));
    double sum = static_cast<double>(m.sum) * 0x1p-16;
    double hi = static_cast<double>(static_cast<uint64_t>(m.sum2 >> 64)) * 0x1p64;
    double lo = static_cast<double>(static_cast<uint64_t>(m.sum2));
    double s2 = (hi + lo) * 0x1p-32;
    double invN = 1.0 / static_cast<double>(rp.n_paths);
    double mean = sum * invN;
    double var = s2 * invN - mean * mean;
    if (var < 0.0) var = 0.0;
    Result r;
    r.price = static_cast<float>(disc * mean * static_cast<double>(t.notional) * static_cast<double>(t.position));
    r.std_err = static_cast<float>(disc * std::sqrt(var * invN) * static_cast<double>(t.notional));
    return r;
}

// ------------------------------------------------------------------ top-K (deterministic)
// v1 score |price| + 2|se|; ties broken by index so every program selects the same set.
inline std::vector<uint32_t> select_topk(const Result* r, uint32_t n, uint32_t k) {
    k = std::min(k, n);
    std::vector<std::pair<float, uint32_t>> s(n);
    for (uint32_t i = 0; i < n; ++i) {
        float a = std::fabs(r[i].price);
        float b = std::fabs(r[i].std_err);
        float b2 = 2.0f * b;
        float sc = a + b2;
        if (sc != sc) sc = -INFINITY;
        s[i] = {sc, i};
    }
    auto cmp = [](const std::pair<float, uint32_t>& a, const std::pair<float, uint32_t>& b) {
        return a.first > b.first || (a.first == b.first && a.second < b.second);
    };
    if (k < n) std::nth_element(s.begin(), s.begin() + k, s.end(), cmp);
    std::vector<uint32_t> idx(k);
    for (uint32_t i = 0; i < k; ++i) idx[i] = s[i].second;
    std::sort(idx.begin(), idx.end());
    return idx;
}

// total logical path-steps of the two-stage workload
inline double total_steps(uint64_t n, uint64_t p1, uint64_t s1, uint64_t k, uint64_t p2, uint64_t s2) {
    return static_cast<double>(n) * p1 * s1 + static_cast<double>(k) * p2 * s2;
}

}  // namespace pf
