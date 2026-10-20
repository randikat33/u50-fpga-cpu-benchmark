// host_emul_test.cpp - runs the GPU-native step code (hs_gpu_core.cuh) on a CPU, with a host Philox4x32-10 and
// Box-Muller in place of cuRAND, to check the algorithms against the reference prices without a GPU.
// Its random numbers differ from cuRAND's, so its prices differ from the GPU's within the standard error.
//   g++ -O2 -fopenmp -std=c++17 -x c++ gpu/host_emul_test.cpp -o build/host_emul_test
//   build/host_emul_test --case I --scheme qe --steps-per-year 4 --pairs 200000
#define HD inline
#define __host__
#define __device__
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <omp.h>
#include "hs_gpu_host.hpp"

float host_icdf(float u) {     // Acklam's rational approximation in double + one Newton step (accurate to ~1e-15)
    const double p = u;
    static const double a[] = {-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00};
    static const double b[] = {-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01};
    static const double c[] = {-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00};
    static const double d[] = {7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00};
    double x;
    if (p < 0.02425) { const double q = std::sqrt(-2 * std::log(p)); x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    else if (p > 1 - 0.02425) { const double q = std::sqrt(-2 * std::log(1 - p)); x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    else { const double q = p - 0.5, r = q * q; x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1); }
    const double e = 0.5 * std::erfc(-x / std::sqrt(2.0)) - p;
    x -= e * std::sqrt(2 * M_PI) * std::exp(x * x / 2);
    return (float)x;
}

// Philox4x32-10 (Salmon et al. 2011), key = seed, counter = (i, 0, subsequence lo, hi)
struct HostRNG {
    uint32_t key[2], ctr[4], buf[4];
    int pos;
    HostRNG(unsigned long long seed, unsigned long long sub) {
        key[0] = (uint32_t)seed; key[1] = (uint32_t)(seed >> 32);
        ctr[0] = 0; ctr[1] = 0; ctr[2] = (uint32_t)sub; ctr[3] = (uint32_t)(sub >> 32);
        pos = 4;
    }
    static void round(uint32_t* c, const uint32_t* k) {
        const uint64_t p0 = (uint64_t)0xD2511F53u * c[0], p1 = (uint64_t)0xCD9E8D57u * c[2];
        const uint32_t n0 = (uint32_t)(p1 >> 32) ^ c[1] ^ k[0], n2 = (uint32_t)(p0 >> 32) ^ c[3] ^ k[1];
        c[0] = n0; c[1] = (uint32_t)p1; c[2] = n2; c[3] = (uint32_t)p0;
    }
    void refill() {
        uint32_t c[4] = {ctr[0], ctr[1], ctr[2], ctr[3]}, k[2] = {key[0], key[1]};
        for (int i = 0; i < 10; ++i) { round(c, k); k[0] += 0x9E3779B9u; k[1] += 0xBB67AE85u; }
        for (int i = 0; i < 4; ++i) buf[i] = c[i];
        if (++ctr[0] == 0) ++ctr[1];
        pos = 0;
    }
    uint32_t u32() { if (pos == 4) refill(); return buf[pos++]; }
    void normal2(float& a, float& b) {   // Box-Muller, u1 in (0, 1]
        const double u1 = ((double)u32() + 1.0) * 0x1p-32, u2 = (double)u32() * 0x1p-32;
        const double r = std::sqrt(-2.0 * std::log(u1));
        a = (float)(r * std::cos(2 * M_PI * u2));
        b = (float)(r * std::sin(2 * M_PI * u2));
    }
};

int main(int argc, char** argv) {
    Args A(argc, argv);
    const CaseDef c = get_case(A.str("case", "I"));
    const std::string sc = A.str("scheme", c.kind == K_ASIAN ? "cv" : "qe");
    const int scheme = sc == "ft" ? S_FT : S_QE, cv = sc == "cv";
    const int M = c.kind == K_ASIAN ? c.M_fixed : (int)std::llround(A.f64("steps-per-year", 4) * c.T);
    const unsigned long long n = (unsigned long long)A.i64("pairs", 100000);
    const Job P = make_job(c, scheme, M, cv, 0, n, (unsigned long long)A.i64("seed", 1));
    const int T = omp_get_max_threads();
    std::vector<std::vector<double>> acc(T, std::vector<double>(HS_NACC, 0.0));
    void (*f)(const Job&, unsigned long long, double*) =
        c.kind == K_HESTON ? (scheme == S_FT ? one_pair<K_HESTON, S_FT, 0, HostRNG> : one_pair<K_HESTON, S_QE, 0, HostRNG>)
      : c.kind == K_BASKET ? (scheme == S_FT ? one_pair<K_BASKET, S_FT, 0, HostRNG> : one_pair<K_BASKET, S_QE, 0, HostRNG>)
      : (cv ? one_pair<K_ASIAN, S_FT, 1, HostRNG> : one_pair<K_ASIAN, S_FT, 0, HostRNG>);
#pragma omp parallel for schedule(static)
    for (long long i = 0; i < (long long)n; ++i) f(P, (unsigned long long)i, acc[omp_get_thread_num()].data());
    std::vector<double> s(HS_NACC, 0.0);
    for (auto& a : acc) for (int k = 0; k < HS_NACC; ++k) s[k] += a[k];
    auto pr = finalize(c, M, cv, s.data(), (double)n, BETA_A);
    for (int k = 0; k < HS_NK; ++k)
        std::printf("RESULT case=%s scheme=%s M=%d pairs=%llu K=%.0f price=%.6f se=%.6f ref=%.6f z=%+.2f\n", c.name.c_str(), sc.c_str(), M, n,
                    c.K[k], pr[k].price, pr[k].se, c.ref[k], (pr[k].price - c.ref[k]) / pr[k].se);
    return 0;
}
