// hq_qem.cpp - double-precision QE and QE-M (Andersen 2008, QE with the martingale correction) for the Section 24
// sensitivity of protocol v0.6 / addendum v0.6.1. Calibration only: it is not run on the card or timed.
//   --scheme qe  : hqd::qe_side, the double-precision QE of hq_cpu --impl double (same draws, same results)
//   --scheme qem : the same draws; the drift constant K0 of each step is replaced by the martingale-corrected
//                  K0* = -ln M(A) - (K1 + K3/2) V,  A = K2 + K4/2, where M is the moment generating function of the
//                  QE draw of V(t + h) given V(t):
//                    quadratic branch (psi <= 1.5): ln M(A) = A b^2 a / (1 - 2 A a) - ln(1 - 2 A a) / 2, if A < 1/(2a)
//                    exponential branch:            M(A) = p + beta (1 - p) / (beta - A),            if A < beta
//                  so that E[exp(x(t + h)) | x(t), V(t)] = exp(x(t)) (x = ln S without the drift r t). If the condition
//                  fails, K0 is used for that step and counted (martingale_fallbacks).
// Cases I, II, III, P1 (single asset) and B (basket of 8 assets, each asset as one unit, as hb_double.hpp).
// Output keys as hq_cpu --impl double (impl=double, d1_k, dp_k, price, se, bias), so scripts/hqlib.py and hslib.py
// read it.  Usage: hq_qem --case I|II|III|P1|B --scheme qe|qem (--steps M | --steps-per-year S) --a0 A --a1 B --seed S
//                         [--threads T] [--impl double]
#include <omp.h>

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
#include "bench_common.hpp"
#include "hb_double.hpp"
#include "hq_double.hpp"
#include "mc_cli.hpp"

namespace {
using hqd::DPar;

inline void qem_side(const DPar& p, double& x, double& V, uint32_t uv, double z, uint64_t& bad) {
    const double m = p.theta + (V - p.theta) * p.E;
    const double s2 = V * p.QC1 + p.QC2;
    const double psi = s2 / (m * m), A = p.K2 + 0.5 * p.K4;
    double vn, k0;
    if (psi <= 1.5) {
        const double ip = 2.0 / psi, b2 = ip - 1.0 + std::sqrt(ip) * std::sqrt(ip - 1.0);
        const double a = m / (1.0 + b2), t = std::sqrt(b2) + hqd::zn(uv);
        vn = a * t * t;
        if (A < 0.5 / a) {
            const double q = 1.0 - 2.0 * A * a;
            k0 = -(A * b2 * a / q - 0.5 * std::log(q)) - (p.K1 + 0.5 * p.K3) * V;
        } else {
            k0 = p.K0; ++bad;
        }
    } else {
        const double pp = (psi - 1.0) / (psi + 1.0), beta = (1.0 - pp) / m;
        const double omu = std::ldexp((double)(~uv) + 0.5, -32);           // 1 - U, as hqd::qe_side
        vn = (1.0 - omu <= pp) ? 0.0 : std::log((1.0 - pp) / omu) / beta;
        if (A < beta) {
            k0 = -std::log(pp + beta * (1.0 - pp) / (beta - A)) - (p.K1 + 0.5 * p.K3) * V;
        } else {
            k0 = p.K0; ++bad;
        }
    }
    x += k0 + p.K1 * V + p.K2 * vn + std::sqrt(p.K3 * V + p.K4 * vn) * z;
    V = vn;
}

// one unit (single asset, or asset j of a basket pair): both antithetic sides, from x0 = ln S0
inline void unit(const DPar& p, bool qem, double x0, const mc::Keys& k, uint32_t M, uint32_t u, double& xA, double& xB, uint64_t& bad) {
    mc::Xo s = mc::pair_seed(k, u);
    double VA = p.U0, VB = p.U0;
    xA = xB = x0;
    for (uint32_t t = 0; t < M; ++t) {
        uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
        double z = hqd::zn(u2);
        if (qem) {
            qem_side(p, xA, VA, u1, z, bad);
            qem_side(p, xB, VB, ~u1, -z, bad);
        } else {
            hqd::qe_side(p, xA, VA, u1, z);
            hqd::qe_side(p, xB, VB, ~u1, -z);
        }
    }
}

struct Acc { hqd::DMom d; uint64_t bad = 0; char pad[64]; };
}  // namespace

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    int rc = 0;
    try {
        const std::string cn = A.str("case", "I"), scs = A.str("scheme", "qem");
        if (scs != "qe" && scs != "qem") throw std::runtime_error("--scheme must be qe or qem");
        if (A.has("impl") && A.str("impl", "double") != "double") throw std::runtime_error("--impl must be double");
        const bool qem = scs == "qem", basket = cn == "B";
        hq::Case c{};
        hb::BCase bc{};
        if (basket) { bc = hb::get_bcase(cn); c = bc.unit_case(); } else c = hq::get_case(cn);
        long long M = A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * c.T);
        if (M < 1 || M > HQ_M_MAX) throw std::runtime_error("number of steps must be 1..65535");
        uint64_t a0 = (uint64_t)A.i64("a0", 0);
        uint64_t a1 = A.has("a1") ? (uint64_t)A.i64("a1", 0) : a0 + (uint64_t)A.i64("pairs", 65536);
        if (a1 <= a0 || a1 > (basket ? (1ull << 29) : (1ull << 32))) throw std::runtime_error("bad pair range");
        uint64_t seed = (uint64_t)A.i64("seed", 20261011);
        int threads = (int)A.i64("threads", omp_get_max_threads());

        R.set("project", "heston_accuracy");
        R.set("platform", "cpu");
        R.set("impl", "double");
        R.set("program", "hq_qem");
        R.set("case", cn);
        R.set("scheme", scs);
        R.set("steps", (long long)M);
        R.set("steps_per_year", (double)M / c.T);
        R.set("a0", (unsigned long long)a0);
        R.set("a1", (unsigned long long)a1);
        R.set("pairs", (unsigned long long)(a1 - a0));
        R.set("seed", (unsigned long long)seed);
        R.set("threads", threads);

        const DPar dp = hqd::make_dpar(c, (uint32_t)M);
        const mc::Keys k = mc::run_keys(seed);
        std::vector<Acc> acc(threads);
        const uint64_t U = 64, nb = (a1 - a0 + U - 1) / U;
        const double t0 = bench::now_s();
#pragma omp parallel num_threads(threads)
        {
            Acc& my = acc[omp_get_thread_num()];
#pragma omp for schedule(dynamic, 16)
            for (uint64_t b = 0; b < nb; ++b) {
                const uint64_t b0 = a0 + b * U, b1 = std::min<uint64_t>(b0 + U, a1);
                for (uint64_t a = b0; a < b1; ++a) {
                    double pA[HQ_NK], pB[HQ_NK];
                    if (basket) {
                        double BA = 0, BB = 0, g = dp.D2 * 0.5;
                        for (int j = 0; j < HB_NA; ++j) {
                            double xA, xB;
                            unit(dp, qem, std::log(bc.S0[j]), k, (uint32_t)M, (uint32_t)(HB_NA * a + j), xA, xB, my.bad);
                            BA += std::exp(xA + g);
                            BB += std::exp(xB + g);
                        }
                        hbd::bpayoff(dp, BA / HB_NA, pA);
                        hbd::bpayoff(dp, BB / HB_NA, pB);
                    } else {
                        double xA, xB;
                        unit(dp, qem, 0.5 * dp.X0, k, (uint32_t)M, (uint32_t)a, xA, xB, my.bad);
                        hqd::payoff(dp, xA, pA);
                        hqd::payoff(dp, xB, pB);
                    }
                    my.d.add(pA, pB);
                }
            }
        }
        const double tc = bench::now_s() - t0;
        hqd::DMom d;
        uint64_t bad = 0;
        for (auto& x : acc) { d.add(x.d); bad += x.bad; }
        const double psteps = 2.0 * (double)(a1 - a0) * (double)M;
        R.set("t_compute_s", tc);
        R.set("path_steps", psteps);
        R.set("martingale_fallbacks", (unsigned long long)bad);
        char b[96];
        for (int j = 0; j < HQ_NK; ++j) {
            hq::Price p = basket ? hbd::finalize_k(bc, d, j) : hqd::finalize_k(c, d, j);
            const double ref = basket ? bc.ref[j] : c.ref[j];
            std::string s = std::to_string(j);
            std::snprintf(b, sizeof b, "%.0f", c.K[j]); R.set("K" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.price); R.set("price" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.se); R.set("se" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.pv); R.set("pv" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.price - ref); R.set("bias" + s, b);
            std::snprintf(b, sizeof b, "%.21Lg", d.s1[j]); R.set("d1_" + s, b);
            std::snprintf(b, sizeof b, "%.21Lg", d.sp[j]); R.set("dp_" + s, b);
        }
        R.set("payoff_overflows", 0);
        R.set("ok", 1);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        R.set("ok", 0);
        rc = 1;
    }
    R.set("t_total_s", bench::now_s() - t_start);
    R.print();
    return rc;
}
