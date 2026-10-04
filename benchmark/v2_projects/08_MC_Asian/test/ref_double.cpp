// ref_double.cpp - independent double-precision Monte Carlo (mt19937_64 + Box-Muller) of the same
// option (GBM, exact log step, arithmetic average over M dates, antithetic pairs), compared with the
// float/ICDF engine (AVX-512 core, else scalar). Also checks the geometric-average variant against its
// closed form (Kemna-Vorst, discrete monitoring) to validate the reference itself.
// Pass criterion: |p_engine - p_dbl| <= 3 * sqrt(se_engine^2 + se_dbl^2).
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>
#include "asian_model.hpp"
#include "../cpu/asian_avx512.hpp"

static double ncdf(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }

int main(int argc, char** argv) {
    uint64_t pairs = argc > 1 ? std::strtoull(argv[1], nullptr, 0) : 200000;
    uint32_t M = argc > 2 ? (uint32_t)std::atoi(argv[2]) : 64;
    double sigma = argc > 3 ? std::atof(argv[3]) : 0.2;
    bool put = argc > 4 && std::atoi(argv[4]);
    asn::Market mk;
    mk.put = put;
    mk.sigma = sigma;
    float fp[AS_FPAR_N];
    asn::make_fpar(mk, M, fp);
    asn::Mom m;
#if AS_HAVE_AVX512
    asn::avx::sim_range<2>(fp, mc::run_keys(2024), M, 0, pairs, put, m);
#else
    asn::sim_range(fp, mc::run_keys(2024), M, 0, pairs, put, m);
#endif
    mc::Price p = asn::finalize(mk, M, m);

    std::mt19937_64 g(99);
    std::uniform_real_distribution<double> U(0.0, 1.0);
    const double dt = mk.T / M, mu = (mk.r - 0.5 * sigma * sigma) * dt, sd = sigma * std::sqrt(dt);
    double s1 = 0, s2 = 0, g1 = 0, g2 = 0;
    for (uint64_t i = 0; i < pairs; ++i) {
        double xa = std::log(mk.S0), xb = xa, sa = 0, sb = 0, la = 0, lb = 0;
        for (uint32_t t = 0; t < M; ++t) {
            double u1 = U(g), u2 = U(g);
            double z = std::sqrt(-2.0 * std::log(1.0 - u1)) * std::cos(2 * M_PI * u2);
            xa += mu + sd * z; xb += mu - sd * z;
            sa += std::exp(xa); sb += std::exp(xb); la += xa; lb += xb;
        }
        auto pay = [&](double avg) { return put ? std::max(mk.K - avg, 0.0) : std::max(avg - mk.K, 0.0); };
        double pp = 0.5 * (pay(sa / M) + pay(sb / M));
        double gp = 0.5 * (pay(std::exp(la / M)) + pay(std::exp(lb / M)));
        s1 += pp; s2 += pp * pp; g1 += gp; g2 += gp * gp;
    }
    const double disc = std::exp(-mk.r * mk.T), n = (double)pairs;
    double pd = disc * s1 / n, sed = disc * std::sqrt(std::max(s2 / n - (s1 / n) * (s1 / n), 0.0) / n);
    double pg = disc * g1 / n, seg = disc * std::sqrt(std::max(g2 / n - (g1 / n) * (g1 / n), 0.0) / n);
    // closed form of the discretely monitored geometric-average option
    double mG = std::log(mk.S0) + (mk.r - 0.5 * sigma * sigma) * dt * (M + 1) / 2.0;
    double vG = sigma * sigma * dt * (M + 1) * (2.0 * M + 1) / (6.0 * M);
    double d1 = (mG - std::log(mk.K) + vG) / std::sqrt(vG), d2 = d1 - std::sqrt(vG);
    double cg = put ? disc * (mk.K * ncdf(-d2) - std::exp(mG + 0.5 * vG) * ncdf(-d1))
                    : disc * (std::exp(mG + 0.5 * vG) * ncdf(d1) - mk.K * ncdf(d2));
    double tol = 3 * std::sqrt(p.se * p.se + sed * sed);
    bool ok1 = std::fabs(p.price - pd) <= tol, ok2 = std::fabs(pg - cg) <= 3 * seg;
    std::printf("pairs=%llu M=%u sigma=%.2f %s: engine %.6f (se %.6f)  double %.6f (se %.6f)  |diff| %.6f <= %.6f : %s\n",
                (unsigned long long)pairs, M, sigma, put ? "put" : "call", p.price, p.se, pd, sed,
                std::fabs(p.price - pd), tol, ok1 ? "PASS" : "FAIL");
    std::printf("  reference check, geometric average: MC %.6f (se %.6f) vs closed form %.6f : %s\n", pg, seg, cg,
                ok2 ? "PASS" : "FAIL");
    return ok1 && ok2 ? 0 : 1;
}
