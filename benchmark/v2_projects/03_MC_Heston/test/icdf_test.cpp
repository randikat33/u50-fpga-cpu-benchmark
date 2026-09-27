// icdf_test.cpp - distribution quality of the v2 normal generator (xoshiro128** per-pair streams +
// segmented quadratic ICDF, units 2^-20) and accuracy of fexp_half.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>
#include "mc_model.hpp"

static double Phi(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }
static long double phi_inv(long double p) {   // bisection on erfc (reference only)
    long double lo = -40, hi = 40;
    for (int i = 0; i < 200; ++i) {
        long double m = (lo + hi) / 2;
        if (0.5L * std::erfc(-m / std::sqrt(2.0L)) < p) lo = m; else hi = m;
    }
    return (lo + hi) / 2;
}

int main(int argc, char** argv) {
    size_t N = argc > 1 ? std::strtoull(argv[1], nullptr, 0) : 4000000;
    int fails = 0;
    // samples exactly as the simulation draws them: 16 per step from per-pair streams
    std::vector<double> z(N);
    mc::Keys k = mc::run_keys(12345);
    size_t i = 0;
    for (uint32_t a = 0; i < N; ++a) {
        mc::Xo s = mc::pair_seed(k, a);
        for (int t = 0; t < 64 && i < N; ++t) z[i++] = mc::icdf(mc::xo_next(s)) * 0x1p-20;
    }
    long double s1 = 0, s2 = 0, s3 = 0, s4 = 0;
    size_t tail3 = 0;
    for (double v : z) { s1 += v; s2 += v * v; s3 += v * v * v; s4 += v * v * v * v; tail3 += std::fabs(v) > 3.0; }
    long double mean = s1 / N, var = s2 / N - mean * mean;
    long double skew = (s3 / N - 3 * mean * var - mean * mean * mean) / std::pow(var, 1.5L);
    long double kurt = (s4 / N) / (var * var) - 3;   // mean ~ 0
    std::sort(z.begin(), z.end());
    double D = 0;
    for (size_t j = 0; j < N; ++j) {
        double F = Phi(z[j]);
        D = std::max(D, std::max(std::fabs(F - (double)j / N), std::fabs((double)(j + 1) / N - F)));
    }
    double ks_crit = 1.3581 / std::sqrt((double)N);   // 5 % level
    double se_m = 1 / std::sqrt((double)N), se_v = std::sqrt(2.0 / N), se_s = std::sqrt(6.0 / N), se_k = std::sqrt(24.0 / N);
    double tail_exp = 2 * (1 - Phi(3.0)) * N, tail_se = std::sqrt(tail_exp);
    std::printf("ICDF samples N=%zu\n", N);
    std::printf("  mean      = %+.3e  (|.| < 4 se = %.1e)\n", (double)mean, 4 * se_m);
    std::printf("  variance  = %.6f   (|.-1| < 4 se = %.1e)\n", (double)var, 4 * se_v);
    std::printf("  skewness  = %+.3e  (|.| < 4 se = %.1e)\n", (double)skew, 4 * se_s);
    std::printf("  ex.kurt   = %+.3e  (|.| < 4 se = %.1e)\n", (double)kurt, 4 * se_k);
    std::printf("  KS D      = %.3e   sqrt(N)*D = %.3f (5%% crit 1.358)\n", D, D * std::sqrt((double)N));
    std::printf("  P(|z|>3)  = %zu (expected %.0f +- %.0f)\n", tail3, tail_exp, tail_se);
    fails += std::fabs((double)mean) > 4 * se_m;
    fails += std::fabs((double)var - 1) > 4 * se_v;
    fails += std::fabs((double)skew) > 4 * se_s;
    fails += std::fabs((double)kurt) > 4 * se_k;
    fails += D > ks_crit;
    fails += std::fabs(tail3 - tail_exp) > 4 * tail_se;

    // deterministic accuracy of the ICDF vs Phi^-1((u+0.5)/2^32): random u + exhaustive extreme tail
    double maxerr = 0;
    uint64_t st = 0x1234567;
    auto chk = [&](uint32_t u) {
        long double p = ((long double)u + 0.5L) / 4294967296.0L;
        long double e = std::fabs(mc::icdf(u) * 0x1p-20L - phi_inv(p));
        if (e > maxerr) maxerr = (double)e;
    };
    for (int j = 0; j < 200000; ++j) { st = mc::mix64(st); chk((uint32_t)st); }
    for (uint32_t u = 0; u < 4096; ++u) { chk(u); chk(~u); }
    std::printf("  max |icdf - PhiInv| = %.3e  (fit 5.0e-7 + rounding 2^-21 = 4.8e-7)\n", maxerr);
    fails += maxerr > 1.2e-6;
    std::printf("  extremes: icdf(0) = %.6f, icdf(2^31-1) = %+.3e\n", mc::icdf(0) * 0x1p-20, mc::icdf(0x7FFFFFFF) * 0x1p-20);

    // adjacent-stream correlation (first draws of pairs a and a+1)
    {
        size_t n = 1000000;
        long double sxy = 0, sx = 0, sy = 0, sxx = 0, syy = 0;
        for (uint32_t a = 0; a < n; ++a) {
            mc::Xo s = mc::pair_seed(k, a), q = mc::pair_seed(k, a + 1);
            double x = mc::icdf(mc::xo_next(s)) * 0x1p-20, y = mc::icdf(mc::xo_next(q)) * 0x1p-20;
            sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y;
        }
        long double c = (sxy / n - sx / n * sy / n) / std::sqrt((sxx / n - sx / n * sx / n) * (syy / n - sy / n * sy / n));
        std::printf("  corr(first draw of pair a, pair a+1) = %+.2e (|.| < 4/sqrt(n) = %.1e)\n", (double)c, 4 / std::sqrt((double)n));
        fails += std::fabs((double)c) > 4 / std::sqrt((double)n);
    }
    // fexp_half accuracy
    {
        double me = 0;
        for (double x = -120; x < 120; x += 0.000731) {
            double r = std::fabs(mc::fexp_half((float)x) / std::exp((double)(float)x / 2) - 1);
            me = std::max(me, r);
        }
        std::printf("  fexp_half max rel err on [-120,120] = %.2e\n", me);
        fails += me > 2e-7;
    }
    std::printf("icdf_test: %s\n", fails ? "FAILED" : "ALL PASS");
    return fails ? 1 : 0;
}
