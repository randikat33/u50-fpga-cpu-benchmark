// mc_model.hpp - scalar REFERENCE MODEL of the v2 Monte Carlo Heston basket option.
//
// Defines the exact numerics shared (bit-identically, checked by test) by
//   kernel/mc_heston_v2.cpp (HLS), cpu/mc_cpu.cpp --impl scalar|avx512, and host --verify.
// One floating-point operation per statement fixes the evaluation order; every program is
// built with -ffp-contract=off (no FMA contraction).
//
// Financial model (v1, krnl_heston_mc_p6.cpp): 8-asset arithmetic basket call/put, Heston per
// asset, spot shocks correlated by a lower-triangular Cholesky factor L, variance shock of asset
// i correlated with its own spot shock by rho_i, Euler scheme in log-price, Euler in variance with
// truncation at zero of the stored state (v1: v <- max(v_new, 0)), antithetic sampling.
//
// v2 numerics (see README section 3):
//   pair a (global index)   RNG stream: xoshiro128** seeded by fmix32(a ^ key[j]), j=0..3
//   per step                16 draws -> segmented-quadratic ICDF -> n_k (int, units 2^-20)
//                           zS2_i = tree-sum_j<=i (L2_ij * n_j)       L2 = 2*L*2^-20
//                           w_i   = c1h_i*zS2_i + c2q_i*n_{8+i}
//   side A (+) / B (-)      state X = 2 ln S (w/o drift), U = v*dt
//                           P = sqrt(U); U' = max((U*omk + kth2) + P*(+-w), 0)
//                           X' = X + (P*(+-zS2) - U)
//   maturity                S_i = fexp_half(X_i + D2_i);  B8 = tree-sum S_i  (= 8*basket)
//                           po8 = max(B8 - K8, 0) (call) ; m = po8 * 2^-E8  (exact integer)
//   accumulation            exact integers: S1 = sum(mA+mB), S2 = sum(mA^2+mB^2),
//                           SP = sum((mA+mB)^2)  -> independent of any partitioning.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include "mc_tables.h"

#define MC_NA 8            // assets
#define MC_NZ 16           // normals per time step (8 spot + 8 variance)
#define MC_NL 36           // lower-triangular Cholesky entries
#define MC_FPAR_N 96       // float parameter block (see FparIdx)
#define MC_M_MAX 65535     // max time steps (16-bit step counter in the kernel)
#define MC_MBITS 40        // payoff integer width (saturating)

namespace mc {

enum FparIdx : int {
    F_X0 = 0,     // 2*ln(S0_i)
    F_U0 = 8,     // v0_i*dt
    F_OMK = 16,   // 1 - kappa_i*dt
    F_KTH2 = 24,  // kappa_i*theta_i*dt^2
    F_C1H = 32,   // sigma_i*rho_i*dt/2
    F_C2Q = 40,   // sigma_i*sqrt(1-rho_i^2)*dt*2^-20
    F_D2 = 48,    // 2*(r-q_i)*T
    F_L2 = 56,    // 2*L_ij*2^-20, row-major lower triangle (36)
    F_K8 = 92,    // 8*K
    F_QS = 93,    // 2^-E8 (payoff quantum inverse)
};
inline int tri(int i, int j) { return i * (i + 1) / 2 + j; }

// ------------------------------------------------------------------ market (host side, double)
struct Market {
    double S0[MC_NA], v0[MC_NA], kappa[MC_NA], theta[MC_NA], sigma[MC_NA], rho[MC_NA], q[MC_NA];
    double L[MC_NA][MC_NA];
    double r = 0.01, K = 100.0, T = 1.0;
    bool put = false;
};
// v1 workload (host_heston_mc_p6.cpp / cpu_heston_mc_unified.cpp)
inline Market v1_market() {
    Market m;
    for (int i = 0; i < MC_NA; ++i) {
        m.S0[i] = 100.0 + i * 0.1; m.v0[i] = 0.04; m.kappa[i] = 1.5; m.theta[i] = 0.04;
        m.sigma[i] = 0.3; m.rho[i] = -0.6; m.q[i] = 0.0;
        for (int j = 0; j < MC_NA; ++j) m.L[i][j] = (i == j) ? 1.0 : 0.0;
    }
    return m;
}
// optional correlated market for tests: equicorrelation c, Cholesky computed here
inline void set_equicorr(Market& m, double c) {
    double A[MC_NA][MC_NA];
    for (int i = 0; i < MC_NA; ++i) for (int j = 0; j < MC_NA; ++j) A[i][j] = (i == j) ? 1.0 : c;
    for (int i = 0; i < MC_NA; ++i) for (int j = 0; j < MC_NA; ++j) m.L[i][j] = 0.0;
    for (int j = 0; j < MC_NA; ++j) {
        double s = A[j][j];
        for (int k = 0; k < j; ++k) s -= m.L[j][k] * m.L[j][k];
        m.L[j][j] = std::sqrt(s);
        for (int i = j + 1; i < MC_NA; ++i) {
            double t = A[i][j];
            for (int k = 0; k < j; ++k) t -= m.L[i][k] * m.L[j][k];
            m.L[i][j] = t / m.L[j][j];
        }
    }
}

inline int payoff_exp(float K8) { return std::ilogb(K8) - 24; }   // E8: all payoffs are multiples of 2^E8

inline void make_fpar(const Market& m, uint32_t M, float fp[MC_FPAR_N]) {
    std::memset(fp, 0, sizeof(float) * MC_FPAR_N);
    double dt = m.T / M;
    for (int i = 0; i < MC_NA; ++i) {
        fp[F_X0 + i] = (float)(2.0 * std::log(m.S0[i]));
        fp[F_U0 + i] = (float)(m.v0[i] * dt);
        fp[F_OMK + i] = (float)(1.0 - m.kappa[i] * dt);
        fp[F_KTH2 + i] = (float)(m.kappa[i] * m.theta[i] * dt * dt);
        fp[F_C1H + i] = (float)(m.sigma[i] * m.rho[i] * dt * 0.5);
        double sr = 1.0 - m.rho[i] * m.rho[i];
        fp[F_C2Q + i] = (float)(m.sigma[i] * std::sqrt(sr > 0 ? sr : 0.0) * dt * 0x1p-20);
        fp[F_D2 + i] = (float)(2.0 * (m.r - m.q[i]) * m.T);
        for (int j = 0; j <= i; ++j) fp[F_L2 + tri(i, j)] = (float)(2.0 * m.L[i][j] * 0x1p-20);
    }
    float K8 = (float)(8.0 * m.K);
    fp[F_K8] = K8;
    fp[F_QS] = std::ldexp(1.0f, -payoff_exp(K8));
}

// ------------------------------------------------------------------ hashing / RNG
constexpr uint64_t GOLDEN64 = 0x9E3779B97F4A7C15ull;
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
inline Keys run_keys(uint64_t seed) {
    uint64_t a = mix64(seed + GOLDEN64), b = mix64(seed + 2 * GOLDEN64);
    Keys k;
    k.k[0] = (uint32_t)a; k.k[1] = (uint32_t)(a >> 32); k.k[2] = (uint32_t)b; k.k[3] = (uint32_t)(b >> 32);
    return k;
}
inline uint64_t keys_lo(const Keys& k) { return k.k[0] | ((uint64_t)k.k[1] << 32); }
inline uint64_t keys_hi(const Keys& k) { return k.k[2] | ((uint64_t)k.k[3] << 32); }

struct Xo { uint32_t s[4]; };
inline Xo pair_seed(const Keys& k, uint32_t a) {
    Xo x;
    for (int j = 0; j < 4; ++j) x.s[j] = fmix32(a ^ k.k[j]);
    if ((x.s[0] | x.s[1] | x.s[2] | x.s[3]) == 0) x.s[0] = 0x9E3779B9u;   // never all-zero
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
inline const uint64_t* icdf_rom() { static const uint64_t rom[2048] = MC_ICDF_ROM_INIT; return rom; }
inline int32_t rom_c0(uint64_t w) { return (int32_t)(w >> 29); }
inline int32_t rom_c1(uint64_t w) { return (int32_t)((int64_t)(w << 35) >> 47); }
inline int32_t rom_c2(uint64_t w) { return (int32_t)((int64_t)(w << 52) >> 52); }

// z = PhiInv((u + 0.5) / 2^32) in units of 2^-20 (|z| < 2^23, so (float)z is exact)
inline int32_t icdf(uint32_t u) {
    uint32_t sgn = u >> 31;
    uint32_t mi = (sgn ? ~u : u) & 0x7FFFFFFFu;
    uint32_t lz = mi ? (uint32_t)__builtin_clz(mi) - 1 : 31;
    uint32_t n = mi << lz;
    uint32_t idx = (lz << 6) | ((n >> 24) & 63);
    int32_t x = (int32_t)((n >> 9) & 0x7FFF);
    uint64_t w = icdf_rom()[idx];
    int32_t a = (rom_c2(w) * x) >> 15;
    int32_t b = rom_c1(w) + a;
    int32_t p = (b * x) >> 15;
    int32_t zi = rom_c0(w) + p;
    int32_t zr = (zi + 2) >> 2;
    return sgn ? zr : -zr;
}

// ------------------------------------------------------------------ exp(X/2), hardware form
constexpr int64_t INVLN2_Q30 = 1549082005;          // round(2^30 / ln 2)
constexpr uint64_t LN2_Q32 = 2977044472ull;         // round(2^32 * ln 2)
constexpr uint64_t EXP_MLIM = 4294967167ull;        // 2^32 - 129 (keeps m24 < 2^24)
inline const uint32_t* exp_t() { static const uint32_t t[256] = MC_EXP_T_INIT; return t; }
inline const float* exp_p2() { static const float t[256] = MC_EXP_P2_INIT; return t; }

// clamp(X, -128, 128) with NaN -> -128 (ordered compares, identical on all targets)
inline float xclamp(float x) { return (x >= -128.0f) ? ((x <= 128.0f) ? x : 128.0f) : -128.0f; }

inline float fexp_half(float X) {                                // ~ exp(X/2), |rel err| < 1e-7
    float xc = xclamp(X);
    float xs = xc * 8388608.0f;                                  // exact: (X/2) * 2^24
    int32_t xq = (int32_t)std::floor(xs);                        // Q7.24 of X/2, |xq| <= 2^30
    int64_t y = (int64_t)xq * INVLN2_Q30;                        // (X/2)/ln2, Q54
    int32_t n = (int32_t)(y >> 54);                              // floor
    uint32_t fr = (uint32_t)(y >> 22);                           // fraction, Q32
    uint32_t i = fr >> 24;
    uint32_t fl = fr & 0xFFFFFFu;
    uint32_t d = (uint32_t)(((uint64_t)fl * LN2_Q32) >> 32);
    uint32_t dd = (uint32_t)(((uint64_t)d * d) >> 33);
    uint32_t g = d + dd;                                         // e^d - 1 (2nd order), Q32
    uint64_t T = exp_t()[i];
    uint64_t Mm = T + ((T * g) >> 32);                           // Q31 mantissa
    if (Mm > EXP_MLIM) Mm = EXP_MLIM;
    int32_t m24 = (int32_t)((Mm + 128) >> 8);                    // < 2^24 -> exact float
    float mf = (float)m24;
    return mf * exp_p2()[n + 128];                               // exact (normal result)
}

// ------------------------------------------------------------------ path pieces
// pairwise tree sum of t[0..n-1] (fixed order, used for Cholesky rows and the basket)
inline float tree_sum(const float* t, int n) {
    float a[8], b[4];
    int na = (n + 1) / 2;
    for (int k = 0; k < na; ++k) {
        if (2 * k + 1 < n) a[k] = t[2 * k] + t[2 * k + 1];
        else a[k] = t[2 * k];
    }
    int nb = (na + 1) / 2;
    for (int k = 0; k < nb; ++k) {
        if (2 * k + 1 < na) b[k] = a[2 * k] + a[2 * k + 1];
        else b[k] = a[2 * k];
    }
    if (nb == 1) return b[0];
    float c0 = b[0] + b[1];
    if (nb == 2) return c0;
    float c1 = (nb == 4) ? (b[2] + b[3]) : b[2];
    return c0 + c1;
}

inline void draw_step(Xo& s, int32_t z[MC_NZ]) {
    for (int k = 0; k < MC_NZ; ++k) z[k] = icdf(xo_next(s));
}
// shared part of one time step: correlated spot shock (x2) and variance shock
inline void mix_step(const float* fp, const int32_t z[MC_NZ], float zS2[MC_NA], float w[MC_NA]) {
    float fz[MC_NZ];
    for (int k = 0; k < MC_NZ; ++k) fz[k] = (float)z[k];
    for (int i = 0; i < MC_NA; ++i) {
        float t[MC_NA];
        for (int j = 0; j <= i; ++j) t[j] = fp[F_L2 + tri(i, j)] * fz[j];
        zS2[i] = tree_sum(t, i + 1);
        float a1 = fp[F_C1H + i] * zS2[i];
        float a2 = fp[F_C2Q + i] * fz[MC_NA + i];
        w[i] = a1 + a2;
    }
}
// one side (sgn=+1 path A, -1 antithetic path B) of one asset
inline void side_step(float& X, float& U, float zs, float ww, float omk, float kth2) {
    float P = std::sqrt(U);
    float g = U * omk;
    float h = g + kth2;
    float e = P * ww;
    float un = h + e;
    float f = P * zs;
    float y = f - U;
    X = X + y;
    U = un > 0.0f ? un : 0.0f;
}
inline void full_step(const float* fp, const int32_t z[MC_NZ], float XA[MC_NA], float UA[MC_NA],
                      float XB[MC_NA], float UB[MC_NA]) {
    float zS2[MC_NA], w[MC_NA];
    mix_step(fp, z, zS2, w);
    for (int i = 0; i < MC_NA; ++i) {
        float nz = -zS2[i];
        float nw = -w[i];
        side_step(XA[i], UA[i], zS2[i], w[i], fp[F_OMK + i], fp[F_KTH2 + i]);
        side_step(XB[i], UB[i], nz, nw, fp[F_OMK + i], fp[F_KTH2 + i]);
    }
}

// payoff as an exact integer multiple of 2^E8 (saturating at 2^40-1, *ovf set)
constexpr float M_SAT_F = 1099511627776.0f;   // 2^40
inline uint64_t payoff_m(const float* fp, const float X[MC_NA], bool put, bool* ovf) {
    float S[MC_NA];
    for (int i = 0; i < MC_NA; ++i) {
        float xt = X[i] + fp[F_D2 + i];
        S[i] = fexp_half(xt);
    }
    float B8 = tree_sum(S, MC_NA);
    float d = put ? (fp[F_K8] - B8) : (B8 - fp[F_K8]);
    float po = d > 0.0f ? d : 0.0f;
    float pm = po * fp[F_QS];
    if (!(pm < M_SAT_F)) { *ovf = true; return (1ull << MC_MBITS) - 1; }
    return (uint64_t)pm;
}

struct PairOut { uint64_t mA, mB; };
inline PairOut sim_pair(const float* fp, const Keys& k, uint32_t M, uint32_t a, bool put, bool* ovf) {
    Xo s = pair_seed(k, a);
    float XA[MC_NA], UA[MC_NA], XB[MC_NA], UB[MC_NA];
    for (int i = 0; i < MC_NA; ++i) { XA[i] = XB[i] = fp[F_X0 + i]; UA[i] = UB[i] = fp[F_U0 + i]; }
    int32_t z[MC_NZ];
    for (uint32_t t = 0; t < M; ++t) {
        draw_step(s, z);
        full_step(fp, z, XA, UA, XB, UB);
    }
    PairOut o;
    o.mA = payoff_m(fp, XA, put, ovf);
    o.mB = payoff_m(fp, XB, put, ovf);
    return o;
}

// ------------------------------------------------------------------ exact moments
typedef unsigned __int128 u128;
struct Mom {
    u128 s1 = 0, s2 = 0, sp = 0;
    uint64_t pairs = 0, ovf = 0;
    void add(uint64_t mA, uint64_t mB) {
        u128 a = mA, b = mB, s = a + b;
        s1 += s; s2 += a * a + b * b; sp += s * s; ++pairs;
    }
    void add(const Mom& o) { s1 += o.s1; s2 += o.s2; sp += o.sp; pairs += o.pairs; ovf += o.ovf; }
    bool operator==(const Mom& o) const {
        return s1 == o.s1 && s2 == o.s2 && sp == o.sp && pairs == o.pairs && ovf == o.ovf;
    }
    bool operator!=(const Mom& o) const { return !(*this == o); }
};
inline void sim_range(const float* fp, const Keys& k, uint32_t M, uint64_t a0, uint64_t a1, bool put, Mom& m) {
    for (uint64_t a = a0; a < a1; ++a) {
        bool ovf = false;
        PairOut o = sim_pair(fp, k, M, (uint32_t)a, put, &ovf);
        m.add(o.mA, o.mB);
        m.ovf += ovf;
    }
}
// kernel out record: 8 x uint64
enum OutIdx { O_S1L = 0, O_S1H, O_S2L, O_S2H, O_SPL, O_SPH, O_PAIRS, O_INFO };
inline void mom_to_words(const Mom& m, uint64_t w[8]) {
    w[O_S1L] = (uint64_t)m.s1; w[O_S1H] = (uint64_t)(m.s1 >> 64);
    w[O_S2L] = (uint64_t)m.s2; w[O_S2H] = (uint64_t)(m.s2 >> 64);
    w[O_SPL] = (uint64_t)m.sp; w[O_SPH] = (uint64_t)(m.sp >> 64);
    w[O_PAIRS] = m.pairs; w[O_INFO] = m.ovf;
}
inline Mom mom_from_words(const uint64_t* w) {
    Mom m;
    m.s1 = ((u128)w[O_S1H] << 64) | w[O_S1L];
    m.s2 = ((u128)w[O_S2H] << 64) | w[O_S2L];
    m.sp = ((u128)w[O_SPH] << 64) | w[O_SPL];
    m.pairs = w[O_PAIRS];
    m.ovf = w[O_INFO] & 0xFFFFFFFFull;     // high bits: kernel build info (lanes, IL)
    return m;
}
inline uint64_t mom_hash(const Mom& m) {   // FNV-1a over the words (for logs)
    uint64_t w[8]; mom_to_words(m, w);
    uint64_t h = 1469598103934665603ull;
    for (int i = 0; i < 8; ++i) for (int b = 0; b < 64; b += 8) { h ^= (w[i] >> b) & 0xFF; h *= 1099511628211ull; }
    return h;
}

// ------------------------------------------------------------------ finalisation
struct Price { double price, se, se_naive, mean_payoff; uint64_t paths; };
inline long double u128_ld(u128 v) { return (long double)(uint64_t)(v >> 64) * 18446744073709551616.0L + (long double)(uint64_t)v; }
inline Price finalize(const Market& mk, uint32_t M, const Mom& m) {
    float fp[MC_FPAR_N];
    make_fpar(mk, M, fp);
    long double q = std::ldexp(1.0L, payoff_exp(fp[F_K8])) / 8.0L;   // payoff = m * 2^E8 / 8
    long double np = (long double)m.pairs, n = 2 * np;
    long double disc = std::exp(-(long double)mk.r * mk.T);
    Price p{};
    p.paths = 2 * m.pairs;
    if (m.pairs == 0) return p;
    long double mean = u128_ld(m.s1) * q / n;
    long double var = u128_ld(m.s2) * q * q / n - mean * mean;
    long double vp = u128_ld(m.sp) * q * q / 4 / np - mean * mean;     // variance of pair averages
    if (var < 0) var = 0;
    if (vp < 0) vp = 0;
    p.mean_payoff = (double)mean;
    p.price = (double)(disc * mean);
    p.se = (double)(disc * std::sqrt(vp / np));
    p.se_naive = (double)(disc * std::sqrt(var / n));
    return p;
}

}  // namespace mc
