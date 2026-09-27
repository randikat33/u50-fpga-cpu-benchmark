// ref_double.cpp - independent double-precision Monte Carlo (mt19937_64 + Box-Muller, v1-style)
// of the same model and discretisation (Euler log-price, Euler variance truncated at 0, antithetic
// pairs, same Cholesky), compared with the v2 float/ICDF engine (AVX-512 core, else scalar).
// Pass criterion: |p_v2 - p_dbl| <= 3 * sqrt(se_v2^2 + se_dbl^2).
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>
#include "mc_model.hpp"
#include "../cpu/mc_avx512.hpp"

int main(int argc, char** argv) {
    uint64_t pairs = argc > 1 ? std::strtoull(argv[1], nullptr, 0) : 200000;
    uint32_t M = argc > 2 ? (uint32_t)std::atoi(argv[2]) : 32;
    double corr = argc > 3 ? std::atof(argv[3]) : 0.0;
    bool put = argc > 4 && std::atoi(argv[4]);
    mc::Market mk = mc::v1_market();
    mk.put = put;
    if (corr != 0) mc::set_equicorr(mk, corr);
    // v2 engine
    float fp[MC_FPAR_N];
    mc::make_fpar(mk, M, fp);
    mc::Mom m;
#if MC_HAVE_AVX512
    mc::avx::sim_range(fp, mc::run_keys(2024), M, 0, pairs, put, m);
#else
    mc::sim_range(fp, mc::run_keys(2024), M, 0, pairs, put, m);
#endif
    mc::Price p = mc::finalize(mk, M, m);
    // double reference
    std::mt19937_64 gen(987654321);
    auto uni = [&] { return (gen() >> 11) * 0x1p-53 + 0x1p-54; };
    double dt = mk.T / M, sdt = std::sqrt(dt);
    long double sum = 0, sum2 = 0;
    for (uint64_t a = 0; a < pairs; ++a) {
        double x[2][8], v[2][8];
        for (int s = 0; s < 2; ++s) for (int i = 0; i < 8; ++i) { x[s][i] = std::log(mk.S0[i]); v[s][i] = mk.v0[i]; }
        for (uint32_t t = 0; t < M; ++t) {
            double z[16];
            for (int k = 0; k < 16; k += 2) {
                double r = std::sqrt(-2 * std::log(uni())), th = 2 * M_PI * uni();
                z[k] = r * std::cos(th); z[k + 1] = r * std::sin(th);
            }
            for (int s = 0; s < 2; ++s) {
                double sg = s ? -1.0 : 1.0;
                for (int i = 0; i < 8; ++i) {
                    double zs = 0;
                    for (int j = 0; j <= i; ++j) zs += mk.L[i][j] * z[j];
                    zs *= sg;
                    double zv = mk.rho[i] * zs + std::sqrt(1 - mk.rho[i] * mk.rho[i]) * sg * z[8 + i];
                    double vv = v[s][i], sv = std::sqrt(vv);
                    x[s][i] += (mk.r - mk.q[i] - 0.5 * vv) * dt + sv * sdt * zs;
                    double vn = vv + mk.kappa[i] * (mk.theta[i] - vv) * dt + mk.sigma[i] * sv * sdt * zv;
                    v[s][i] = vn > 0 ? vn : 0;
                }
            }
        }
        double po[2];
        for (int s = 0; s < 2; ++s) {
            double b = 0;
            for (int i = 0; i < 8; ++i) b += std::exp(x[s][i]);
            b /= 8;
            po[s] = put ? std::max(mk.K - b, 0.0) : std::max(b - mk.K, 0.0);
        }
        double y = 0.5 * (po[0] + po[1]);
        sum += y; sum2 += y * y;
    }
    double disc = std::exp(-mk.r * mk.T);
    long double mean = sum / pairs, var = sum2 / pairs - mean * mean;
    double pd = disc * (double)mean, sed = disc * std::sqrt((double)var / pairs);
    double tol = 3 * std::sqrt(p.se * p.se + sed * sed);
    bool ok = std::fabs(p.price - pd) <= tol && m.ovf == 0;
    std::printf("ref_double pairs=%llu M=%u corr=%.2f put=%d: v2 %.6f +- %.6f | double %.6f +- %.6f | diff %.2e (tol %.2e) %s\n",
                (unsigned long long)pairs, M, corr, put, p.price, p.se, pd, sed, std::fabs(p.price - pd), tol, ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
