// hs_gpu_core.cuh - GPU-native code of paper 2 (protocol v0.6, Section 22): all six cases, the way a GPU
// programmer would write them. Not bit-identical to the card; its bias is checked at every setting.
//
// One thread simulates whole antithetic pairs (grid-stride loop over pairs). Each pair, and in Case B each
// (pair, asset), has its own Philox4_32_10 subsequence, so a pair's paths do not depend on the launch shape.
// Per step:
//   FT     : one curand_normal2 -> z1 (spot), z2 (variance, independent part); zv = rho z1 + sqrt(1-rho^2) z2
//   QE     : one raw 32-bit word -> U = ((w >> 9) + 1/2) 2^-23 in (0,1) (1 - U exact); Z_V = normcdfinvf(U);
//            every second step one curand_normal2 -> the spot normals of this step and the next
//   Asian  : every second date one curand_normal2 -> the normals of this date and the next
//   side B of a pair uses 1 - U and -z (antithetic).
// State: y = ln(S/S0) without the drift r t (float), as the CPU-native code. Payoff moments per thread in
// double; block sums in a fixed tree order; the host adds the block sums in a fixed order.
//
// The step functions are __host__ __device__ so that host_emul_test.cpp can run the same code on a CPU with a
// host Philox (the sandbox has no GPU); on the GPU, RNG wraps cuRAND.
#pragma once
#include <stdint.h>

#ifndef HD
#define HD __host__ __device__ __forceinline__
#endif

#define HS_NK 3            // strikes
#define HS_NA 8            // basket assets
#define HS_NACC 15         // accumulators per thread (Asian: 5 moments x 3 strikes; Heston: 2 x 3)

enum Kind : int { K_HESTON = 0, K_BASKET = 1, K_ASIAN = 2 };
enum Scheme : int { S_FT = 0, S_QE = 1 };

struct Job {
    int kind, scheme, M, cv;
    float v0;
    float S0[HS_NA];       // single asset: S0[0]
    float K[HS_NK];
    unsigned puts;         // bit k: strike k from the put payoff (parity on the host)
    // Heston, per step (Andersen 2008, gamma1 = gamma2 = 1/2; FT as Lord et al. 2010)
    float hdt, sdt, kdt, kthdt, er, es;
    float th, E, QC1, QC2, Q0, Q1, Q2, Q3, Q4;
    float rT;
    // Asian
    float mu, sd;
    unsigned long long a0, n;   // first pair, number of pairs
    unsigned long long seed;
};

// ------------------------------------------------------------------ math wrappers
#ifdef __CUDA_ARCH__
#define F_SQRT(x) sqrtf(x)
#define F_LOG(x) __logf(x)
#define F_EXP(x) __expf(x)
#define F_ICDF(u) normcdfinvf(u)
#else
#include <cmath>
float host_icdf(float u);   // defined in host_emul_test.cpp
#define F_SQRT(x) std::sqrt(x)
#define F_LOG(x) std::log(x)
#define F_EXP(x) std::exp(x)
#define F_ICDF(u) host_icdf(u)
#endif

HD float uniform_open(uint32_t w) { return ((float)(w >> 9) + 0.5f) * 0x1p-23f; }
HD float fmaxz(float a) { return a > 0.0f ? a : 0.0f; }

// one FT step of one side: V may be negative; zs = +-z1, zw = +-eps (rho z1 + sqrt(1-rho^2) z2)
HD void ft_side(const Job& P, float& y, float& V, float zs, float zw) {
    const float vp = fmaxz(V);
    const float sv = F_SQRT(vp) * P.sdt;
    y += sv * zs - vp * P.hdt;
    V += P.kthdt - vp * P.kdt + sv * zw;
}
// one QE step of one side. u: the side's uniform; zq: icdf(u); z: spot normal
HD void qe_side(const Job& P, float& y, float& V, float u, float zq, float z) {
    const float m = (V - P.th) * P.E + P.th;
    const float s2 = V * P.QC1 + P.QC2;
    const float psi = s2 / (m * m);
    float vn;
    if (psi <= 1.5f) {
        const float ip = 2.0f / psi;
        const float ipm = fmaxz(ip - 1.0f);
        const float b2 = ipm + F_SQRT(ip * ipm);
        const float a = m / (1.0f + b2);
        const float t = F_SQRT(b2) + zq;
        vn = a * t * t;
    } else {
        const float p = (psi - 1.0f) / (psi + 1.0f);
        const float omp = 1.0f - p;
        vn = u > p ? F_LOG(omp / (1.0f - u)) * (m / omp) : 0.0f;
    }
    const float dr = P.Q0 + P.Q1 * V + P.Q2 * vn;
    const float sd = F_SQRT(fmaxz(P.Q3 * V + P.Q4 * vn));
    y += dr + sd * z;
    V = vn;
}

// simulate one asset of one antithetic pair; returns the two terminal values S/S0 = exp(y + rT)
template <int SCHEME, class RNG>
HD void heston_pair(const Job& P, RNG& r, float& eA, float& eB) {
    float yA = 0.0f, yB = 0.0f, VA = P.v0, VB = P.v0;
    if (SCHEME == S_FT) {
        for (int t = 0; t < P.M; ++t) {
            float z1, z2;
            r.normal2(z1, z2);
            const float zw = P.er * z1 + P.es * z2;
            ft_side(P, yA, VA, z1, zw);
            ft_side(P, yB, VB, -z1, -zw);
        }
    } else {
        float z = 0.0f, zn = 0.0f;
        for (int t = 0; t < P.M; ++t) {
            const float u = uniform_open(r.u32());
            if ((t & 1) == 0) r.normal2(z, zn); else z = zn;
            const float zq = F_ICDF(u);
            qe_side(P, yA, VA, u, zq, z);
            qe_side(P, yB, VB, 1.0f - u, -zq, -z);
        }
    }
    eA = F_EXP(yA + P.rT);
    eB = F_EXP(yB + P.rT);
}

HD float payoff(const Job& P, int k, double S) {
    const double d = ((P.puts >> k) & 1u) ? (double)P.K[k] - S : S - (double)P.K[k];
    return d > 0.0 ? (float)d : 0.0f;
}

// accumulators per thread: Heston, basket and Asian-plain 6 (sum, sum of squares per strike); Asian-CV 15
template <int KIND, int CV> struct NAcc { static const int n = (KIND == K_ASIAN && CV) ? 15 : 6; };

// one antithetic pair (global index pair) -> accumulators acc[NAcc::n]. KIND, SCHEME, CV are compile-time, so
// each case and method is its own kernel (as a GPU programmer would write it, and so the instruction counts
// of protocol 22a are per kernel).
template <int KIND, int SCHEME, int CV, class RNG>
HD void one_pair(const Job& P, unsigned long long pair, double* acc) {
    if (KIND == K_HESTON) {
        RNG r(P.seed, pair);
        float eA, eB;
        heston_pair<SCHEME>(P, r, eA, eB);
        const double SA = (double)P.S0[0] * eA, SB = (double)P.S0[0] * eB;
        for (int k = 0; k < HS_NK; ++k) {
            const double s = (double)payoff(P, k, SA) + (double)payoff(P, k, SB);
            acc[k] += s;
            acc[HS_NK + k] += s * s;
        }
    } else if (KIND == K_BASKET) {
        double BA = 0.0, BB = 0.0;
        for (int j = 0; j < HS_NA; ++j) {
            RNG r(P.seed, pair * HS_NA + (unsigned long long)j);
            float eA, eB;
            heston_pair<SCHEME>(P, r, eA, eB);
            BA += (double)P.S0[j] * eA;
            BB += (double)P.S0[j] * eB;
        }
        BA /= HS_NA;
        BB /= HS_NA;
        for (int k = 0; k < HS_NK; ++k) {
            const double s = (double)payoff(P, k, BA) + (double)payoff(P, k, BB);
            acc[k] += s;
            acc[HS_NK + k] += s * s;
        }
    } else {   // Asian: arithmetic average of S(t_1..t_M); CV: geometric average of the same prices
        RNG r(P.seed, pair);
        float yA = 0.0f, yB = 0.0f, z = 0.0f, zn = 0.0f;
        double aA = 0.0, aB = 0.0, gA = 0.0, gB = 0.0;
        for (int t = 0; t < P.M; ++t) {
            if ((t & 1) == 0) r.normal2(z, zn); else z = zn;
            yA += P.mu + P.sd * z;
            yB += P.mu - P.sd * z;
            aA += (double)F_EXP(yA);
            aB += (double)F_EXP(yB);
            if (CV) { gA += (double)yA; gB += (double)yB; }
        }
        const double S0 = (double)P.S0[0];
        const double avA = S0 * aA / P.M, avB = S0 * aB / P.M;
        double gmA = 0.0, gmB = 0.0;
        if (CV) { gmA = S0 * exp(gA / P.M); gmB = S0 * exp(gB / P.M); }
        for (int k = 0; k < HS_NK; ++k) {
            const double K = (double)P.K[k];
            const double ys = (avA > K ? avA - K : 0.0) + (avB > K ? avB - K : 0.0);
            acc[k] += ys;                       // sum y
            acc[3 + k] += ys * ys;              // sum y^2
            if (CV) {
                const double gs = (gmA > K ? gmA - K : 0.0) + (gmB > K ? gmB - K : 0.0);
                acc[6 + k] += gs;               // sum g
                acc[9 + k] += gs * gs;          // sum g^2
                acc[12 + k] += ys * gs;         // sum y g
            }
        }
    }
}
