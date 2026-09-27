// test_rng_icdf.cpp - quality/accuracy tests of the shared RNG, ICDF and exp (scalar vs AVX-512 too).
//   ./test_rng_icdf              (sampled, ~10 s)
//   ./test_rng_icdf --exhaustive (all 2^31 ICDF magnitudes, ~1-2 min)
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
#include "pf_engine.hpp"

static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { ++fails; std::printf("FAIL: " __VA_ARGS__); std::printf("\n"); } } while (0)

static double phi_inv(double p) {   // Acklam + 2 Halley steps
    static const double a[] = {-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
                               1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00};
    static const double b[] = {-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
                               6.680131188771972e+01, -1.328068155288572e+01};
    static const double c[] = {-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
                               -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00};
    static const double d[] = {7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
                               3.754408661907416e+00};
    double x, q, r;
    if (p < 0.02425) { q = std::sqrt(-2 * std::log(p)); x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    else if (p <= 1 - 0.02425) { q = p - 0.5; r = q*q; x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1); }
    else { q = std::sqrt(-2 * std::log(1 - p)); x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    for (int i = 0; i < 2; ++i) {
        double e = 0.5 * std::erfc(-x / std::sqrt(2.0)) - p;
        double u = e * std::sqrt(2 * M_PI) * std::exp(x * x / 2);
        x = x - u / (1 + x * u / 2);
    }
    return x;
}
// exact reference for the magnitude index (lower half), in z units
static double zref(uint32_t u) {
    if (u >> 31) return -phi_inv((double)((~u) + 0.5) / 4294967296.0);
    return phi_inv(((double)u + 0.5) / 4294967296.0);
}
static double Phi(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }

int main(int argc, char** argv) {
    bool exhaustive = argc > 1 && std::strcmp(argv[1], "--exhaustive") == 0;
    // ---- known answers
    CHECK(pf::mix64(0x9E3779B97F4A7C15ull) == 0xE220A8397B1DCDAFull, "splitmix64 known answer");
    pf::Xo x0 = {{1, 2, 3, 4}};
    CHECK(pf::xo_next(x0) == 11520u, "xoshiro128** first output for state {1,2,3,4}");
    CHECK(pf::fmix32(0) == 0, "fmix32(0)");
    // ---- ICDF accuracy (units of 1)
    std::mt19937_64 g(42);
    double maxerr = 0;
    auto chk = [&](uint32_t u) {
        double z = pf::icdf(u) * 0x1p-20;
        double e = std::fabs(z - zref(u));
        if (e > maxerr) maxerr = e;
        CHECK(pf::icdf(~u) == -pf::icdf(u), "ICDF symmetry u=%08x", u);
    };
    for (int lz = 0; lz <= 31; ++lz)
        for (int j = 0; j < 64; ++j) {
            uint64_t n0 = (1ull << 30) + ((uint64_t)j << 24);
            for (int64_t d = -2; d <= 2; ++d) {
                int64_t m = (int64_t)(n0 >> lz) + d;
                if (m >= 0 && m < (1ll << 31)) chk((uint32_t)m);
            }
        }
    for (int i = 0; i < (1 << 22); ++i) chk((uint32_t)g());
    if (exhaustive)
        for (uint64_t m = 0; m < (1ull << 31); ++m) chk((uint32_t)m);
    std::printf("icdf: max |z - PhiInv| = %.3g (%s)\n", maxerr, exhaustive ? "exhaustive" : "sampled");
    CHECK(maxerr < 1.2e-6, "ICDF max error %.3g", maxerr);
    // monotonicity
    std::vector<uint32_t> us(1 << 20);
    for (auto& u : us) u = (uint32_t)g();
    for (uint32_t v = 0; v < 64; ++v) { us.push_back(v); us.push_back(0x7FFFFFE0u + v); us.push_back(0x80000000u + v); us.push_back(0xFFFFFFC0u + v); }
    std::sort(us.begin(), us.end());
    long nonmono = 0;
    for (size_t i = 1; i < us.size(); ++i) nonmono += pf::icdf(us[i]) < pf::icdf(us[i - 1]);
    CHECK(nonmono == 0, "ICDF monotonicity violations: %ld", nonmono);
    std::printf("icdf: z(0)=%.6f z(max)=%.6f, monotone on %zu sorted points\n", pf::icdf(0) * 0x1p-20,
                pf::icdf(0xFFFFFFFFu) * 0x1p-20, us.size());

    // ---- exp accuracy
    double maxrel = 0;
    std::uniform_real_distribution<float> X(-20.0f, 20.0f);
    for (int i = 0; i < 2000000; ++i) {
        float x = X(g);
        double e = pf::fexp(x), r = std::exp((double)x);
        maxrel = std::max(maxrel, std::fabs(e / r - 1));
    }
    std::printf("fexp: max relative error on [-20,20] = %.3g\n", maxrel);
    CHECK(maxrel < 2.5e-7, "fexp error %.3g", maxrel);
    CHECK(pf::fexp(0.0f) == 1.0f, "fexp(0) = %.9g", pf::fexp(0.0f));
    CHECK(pf::fexp(NAN) == pf::fexp(-64.0f) && pf::fexp(1e30f) == pf::fexp(64.0f), "fexp clamp/NaN");

#if PF_HAVE_AVX512
    // ---- AVX-512 kernels vs scalar
    pfcpu::Avx512Engine eg(pfcpu::Lut::gather), es(pfcpu::Lut::scalar);
    long bad = 0;
    alignas(64) int32_t ui[16], zo[16], zo2[16];
    alignas(64) float xf[16], ef[16], ef2[16];
    const float specials[16] = {0.0f, -0.0f, NAN, INFINITY, -INFINITY, 64.0f, -64.0f, 63.99999f, -63.99999f,
                                1e-30f, -1e-30f, 0.6931472f, -0.6931472f, 100.0f, -100.0f, 3.0e38f};
    for (int it = 0; it < (1 << 20); ++it) {
        for (int k = 0; k < 16; ++k) {
            ui[k] = (int32_t)g();
            xf[k] = it == 0 ? specials[k] : X(g) * ((it & 3) ? 1.0f : 3.2f);
        }
        _mm512_store_si512(zo, eg.icdf_public(_mm512_load_si512(ui)));
        _mm512_store_si512(zo2, es.icdf_public(_mm512_load_si512(ui)));
        _mm512_store_ps(ef, eg.fexp(_mm512_load_ps(xf)));
        _mm512_store_ps(ef2, es.fexp(_mm512_load_ps(xf)));
        for (int k = 0; k < 16; ++k) {
            int32_t zs = pf::icdf((uint32_t)ui[k]);
            float es_ = pf::fexp(xf[k]);
            bad += zo[k] != zs || zo2[k] != zs || std::memcmp(&ef[k], &es_, 4) || std::memcmp(&ef2[k], &es_, 4);
        }
    }
    CHECK(bad == 0, "AVX-512 icdf/fexp differ from scalar in %ld cases", bad);
    std::printf("avx512: icdf and fexp bit-identical to scalar on 16M samples\n");
#endif

    // ---- statistical quality of the full stream (seed hash -> xoshiro -> icdf)
    const int NP = 1 << 18, NO = 48;
    pf::Keys K = pf::trade_keys(12345, 1, 7);
    double s1 = 0, s2 = 0, s3 = 0, s4 = 0, cadj = 0, clag = 0;
    const int NB = 100;
    std::vector<long> bins(NB, 0);
    std::vector<long> bitc(32, 0);
    long tail3 = 0, tail4 = 0, N = 0;
    std::vector<double> prev(NO, 0.0);
    for (int p = 0; p < NP; ++p) {
        pf::Xo s = pf::path_seed(K, (uint32_t)p);
        double last = 0;
        for (int o = 0; o < NO; ++o) {
            uint32_t u = pf::xo_next(s);
            for (int b = 0; b < 32; ++b) bitc[b] += (u >> b) & 1;
            double z = pf::icdf(u) * 0x1p-20;
            s1 += z; s2 += z * z; s3 += z * z * z; s4 += z * z * z * z;
            int bi = (int)(Phi(z) * NB); bins[std::min(NB - 1, std::max(0, bi))]++;
            tail3 += std::fabs(z) > 3; tail4 += std::fabs(z) > 4;
            if (o) clag += z * last;
            cadj += z * prev[o];          // same output index, adjacent path
            prev[o] = z; last = z; ++N;
        }
    }
    double mean = s1 / N, var = s2 / N - mean * mean, skew = s3 / N, kurt = s4 / N;
    double chi = 0, e = (double)N / NB;
    for (long c : bins) chi += (c - e) * (c - e) / e;
    double r_adj = cadj / N, r_lag = clag / (N - NP);
    double bitdev = 0;
    for (long c : bitc) bitdev = std::max(bitdev, std::fabs(c - N / 2.0) / std::sqrt(N / 4.0));
    std::printf("stream: N=%ld mean=%.2e var=%.6f skew=%.2e kurt=%.4f chi2(99)=%.1f corr_adjpath=%.2e corr_lag1=%.2e "
                "tail3=%.3e(%.3e) tail4=%.3e(%.3e) max bit z=%.2f\n", N, mean, var, skew, kurt, chi, r_adj, r_lag,
                (double)tail3 / N, 2 * (1 - Phi(3)), (double)tail4 / N, 2 * (1 - Phi(4)), bitdev);
    double se = 1 / std::sqrt((double)N);
    CHECK(std::fabs(mean) < 5 * se, "mean");
    CHECK(std::fabs(var - 1) < 5 * std::sqrt(2.0) * se, "variance");
    CHECK(std::fabs(skew) < 5 * std::sqrt(15.0) * se, "skewness");
    CHECK(std::fabs(kurt - 3) < 5 * std::sqrt(96.0) * se, "kurtosis");
    CHECK(chi < 160, "chi-square %.1f (99 dof, p~1e-5 threshold)", chi);
    CHECK(std::fabs(r_adj) < 5 * se && std::fabs(r_lag) < 5 * se, "correlations");
    CHECK(bitdev < 5, "bit frequency");
    std::printf(fails ? "test_rng_icdf FAIL (%d)\n" : "test_rng_icdf PASS\n", fails);
    return fails ? 1 : 0;
}
