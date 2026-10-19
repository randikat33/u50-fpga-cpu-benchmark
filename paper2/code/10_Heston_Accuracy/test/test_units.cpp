// test_units.cpp - unit tests of the paper-2 model pieces. Exit 0 = all pass.
//   1. hq_ln / hq_lnf against long-double log (absolute error)
//   2. icdf(~u) == -icdf(u) (the antithetic side of QE relies on it)
//   3. fixed-point state: float -> int64 truncation and back is exact where expected
//   4. QE moment matching: one QE step from V reproduces E[V'] and Var[V'] (large sample)
//   5. FT and QE single steps: float model vs double model agree to float precision on a few draws
#include <cmath>
#include <cstdio>
#include <random>
#include "hq_model.hpp"
#include "hq_double.hpp"

static int fails = 0;
static void check(bool ok, const char* what) {
    std::printf("%s  %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) ++fails;
}

int main() {
    std::mt19937_64 g(42);
    // 1. logarithm
    {
        double worst = 0, worstf = 0;
        for (int i = 0; i < 2000000; ++i) {
            uint64_t n = (g() >> (g() % 63)) | 1ull;
            int sc = (int)(g() % 80) - 60;
            long double ref = std::log((long double)n) + sc * std::log(2.0L);
            double e = std::fabs((double)(hq::hq_ln(n, sc) - ref));
            double tol = 6e-10 + std::fabs((double)ref) * 6e-8;     // table error + one float rounding
            worst = std::max(worst, e / tol);
        }
        std::uniform_real_distribution<float> U(1e-6f, 1.0f);
        for (int i = 0; i < 2000000; ++i) {
            float x = U(g);
            long double ref = std::log((long double)x);
            double e = std::fabs((double)(hq::hq_lnf(x) - ref));
            worstf = std::max(worstf, e / (6e-10 + std::fabs((double)ref) * 6e-8));
        }
        std::printf("      hq_ln worst error / tolerance %.3f, hq_lnf %.3f\n", worst, worstf);
        check(worst <= 1.0 && worstf <= 1.0, "fixed-point log within 6e-10 + 6e-8 |ln x|");
        // the exact argument used by QE: ln((cu + 0.5) 2^-32) for cu = 0 and 2^32 - 1
        double l0 = hq::hq_ln(1, -33), l1 = hq::hq_ln(2ull * 0xFFFFFFFFull + 1, -33);
        check(std::fabs(l0 - std::log(0x1p-33)) < 1e-5 && std::fabs(l1 - std::log1p(-0x1p-33)) < 1e-7,
              "ln(1-U) at both ends of the uniform range");
    }
    // 2. antithetic symmetry of the inverse CDF
    {
        bool ok = true;
        for (uint64_t i = 0; i < 50000000ull && ok; ++i) {
            uint32_t u = (uint32_t)g();
            ok = mc::icdf(~u) == -mc::icdf(u);
        }
        uint32_t edge[] = {0u, 1u, 0x7FFFFFFFu, 0x80000000u, 0xFFFFFFFEu, 0xFFFFFFFFu};
        for (uint32_t u : edge) ok = ok && mc::icdf(~u) == -mc::icdf(u);
        check(ok, "icdf(~u) == -icdf(u) on 5e7 random words and the edges");
    }
    // 3. fixed point
    {
        bool ok = hq::to_q(1.0f, hq::XQ) == (1ll << 40) && hq::to_q(-1.5f, hq::XQ) == -(3ll << 39) &&
                  hq::to_q(0.04f / 1024, hq::UQ) == (int64_t)(0.04f / 1024 * 0x1p58f) &&
                  hq::to_q(31.0f, hq::UQ) == (31ll << 58);
        check(ok, "float -> int64 fixed point exact for representable values, no overflow at |U| = 31");
    }
    // 4. QE moment matching (one step from V, with the float model's arithmetic)
    {
        hq::Case c = hq::get_case("I");
        bool ok = true;
        for (uint32_t M : {10u, 80u, 640u}) {
            float fp[HQ_FPAR_N];
            hq::make_fpar(c, M, fp);
            double dt = c.T / M, E = std::exp(-c.kappa * dt);
            for (float V0 : {0.0f, 0.001f, 0.04f, 0.3f}) {
                long double s = 0, q = 0;
                const int N = 2000000;
                for (int i = 0; i < N; ++i) {
                    int64_t X = 0;
                    float V = V0;
                    hq::qe_side(fp, X, V, (uint32_t)g(), 0.0f);
                    s += V; q += (long double)V * V;
                }
                double m = (double)(s / N), var = (double)(q / N) - m * m;
                double mt = c.theta + (V0 - c.theta) * E;
                double vt = V0 * c.eps * c.eps * E * (1 - E) / c.kappa + c.theta * c.eps * c.eps * (1 - E) * (1 - E) / (2 * c.kappa);
                double se_m = std::sqrt(vt / N);
                bool okm = std::fabs(m - mt) < 5 * se_m + 1e-6 * mt;
                bool okv = std::fabs(var / vt - 1) < 0.05;
                if (!okm || !okv)
                    std::printf("      M=%u V0=%g: mean %.6g (target %.6g), var %.6g (target %.6g)\n", M, V0, m, mt, var, vt);
                ok = ok && okm && okv;
            }
        }
        check(ok, "QE step matches E[V'] (5 SE) and Var[V'] (5%) for 12 (dt, V) combinations, Case I");
    }
    // 5. float vs double on single steps
    {
        hq::Case c = hq::get_case("III");
        uint32_t M = 40;
        float fp[HQ_FPAR_N];
        hq::make_fpar(c, M, fp);
        hqd::DPar dp = hqd::make_dpar(c, M);
        double worst_ft = 0, worst_qe = 0;
        for (int i = 0; i < 100000; ++i) {
            uint32_t u1 = (uint32_t)g(), u2 = (uint32_t)g();
            float V0 = (float)(std::uniform_real_distribution<double>(0.0, 0.5)(g));
            // FT
            int64_t XA = 0, XB = 0, UA = hq::to_q(V0 * (float)(c.T / M), hq::UQ), UB = UA;
            hq::ft_step(fp, u1, u2, XA, UA, XB, UB);
            double xd = 0, Vd = V0;
            double z1 = hqd::zn(u1), zv = dp.rho * z1 + dp.srho * hqd::zn(u2);
            hqd::ft_side(dp, xd, Vd, z1, zv, dp.dt);
            worst_ft = std::max(worst_ft, std::fabs(XA * 0x1p-40 * 0.5 - xd));
            // QE
            int64_t XQ = 0;
            float VQ = V0;
            float zx = fp[hq::P_ZS] * (float)mc::icdf(u2);
            hq::qe_side(fp, XQ, VQ, u1, zx);
            double xq = 0, Vq = V0;
            hqd::qe_side(dp, xq, Vq, u1, hqd::zn(u2));
            if (std::fabs(VQ - Vq) < 1e-4 * (1 + Vq)) worst_qe = std::max(worst_qe, std::fabs(XQ * 0x1p-40 * 0.5 - xq));
        }
        std::printf("      single step |ln S float - ln S double|: FT %.2e, QE %.2e\n", worst_ft, worst_qe);
        check(worst_ft < 1e-5 && worst_qe < 1e-5, "one step of FT and QE: float model equals double model to 1e-5 in ln S");
    }
    {   // put-call parity on the same paths: (call - put)_k + K_k e^-rT is the same for every strike
        bool ok = true;
        double worst = 0;
        for (const char* cs : {"I", "II", "III", "P1"}) for (int sc = 0; sc < 2; ++sc) {
            hq::Case cc = hq::get_case(cs), cp = cc;
            cc.puts = 0; cp.puts = 7;
            float fc[HQ_FPAR_N], fpp[HQ_FPAR_N];
            hq::make_fpar(cc, 8, fc);
            hq::make_fpar(cp, 8, fpp);
            mc::Keys k = mc::run_keys(5);
            hq::Mom mc_, mp;
            hq::sim_range(fc, (hq::Scheme)sc, k, 8, 0, 3000, mc_);
            hq::sim_range(fpp, (hq::Scheme)sc, k, 8, 0, 3000, mp);
            double disc = std::exp(-cc.r * cc.T), f[HQ_NK];
            for (int j = 0; j < HQ_NK; ++j)
                f[j] = hq::finalize_k(cc, mc_, j).price - hq::finalize_k(cp, mp, j).pv + cc.K[j] * disc;  // = disc E[S]
            for (int j = 1; j < HQ_NK; ++j) worst = std::max(worst, std::fabs(f[j] - f[0]));
        }
        ok = worst < 4 * 0x1p-16;   // one payoff quantum per side
        std::printf("      parity: max spread of (call - put + K e^-rT) over strikes %.2e\n", worst);
        check(ok, "put-call parity holds path by path (call and put forms, all cases, both schemes)");
    }
    std::printf("%s\n", fails ? "SOME TESTS FAILED" : "ALL UNIT TESTS PASSED");
    return fails ? 1 : 0;
}
