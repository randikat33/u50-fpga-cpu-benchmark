// hs_cpu.cpp - CPU program for the secondary cases of paper 2 (protocol v0.5, Section 4b).
//   --case B --scheme ft|qe : paper 1's 8-asset Heston basket (common/hb_model.hpp)
//   --case A                : arithmetic Asian call under Black-Scholes, plain and control-variate
//                             estimators from the same paths (common/ha_model.hpp)
//   --impl scalar  : the float reference model, OpenMP; bit-identical to the card and the AVX-512 code
//   --impl avx512  : AVX-512 (--interleave 1|2|4)
//   --impl double  : same draws, double precision (reference prices)
//   --impl diff    : float payoff minus double payoff on the same path
// Same command line, timing protocol (MARK lines) and RESULT keys as hq_cpu where they apply. Pairs [a0, a1)
// of one seed; runs over consecutive ranges add up exactly to one longer run.
// Work counted in path-steps: 2 x pairs x M (for the basket, a basket path-step = 8 asset-steps).
#include <algorithm>
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include "bench_common.hpp"
#include "hb_double.hpp"
#include "ha_double.hpp"
#include "hb_avx512.hpp"
#include "ha_avx512.hpp"
#include "mc_cli.hpp"

namespace {
enum Impl { SCALAR, DOUBLE, DIFF, AVX };
int g_nb = 1;
inline bool integer_moments(Impl i) { return i == SCALAR || i == AVX; }

struct alignas(64) Acc { hq::Mom m; hqd::DMom d; ha::AMom am; had::DAMom ad; };

struct Job {
    bool asian;
    hb::BCase bc;
    ha::ACase ac;
    hq::Scheme sc;
    uint32_t M;
    float fpb[HB_FPAR_N], fpa[HA_FPAR_N];
    hqd::DPar dpb;
    had::DPar dpa;
    mc::Keys k;
};

void work_block(Impl impl, const Job& J, uint64_t a0, uint64_t a1, Acc& acc) {
    if (!J.asian) {
        if (impl == SCALAR) { hb::sim_range(J.fpb, J.sc, J.k, J.M, a0, a1, acc.m); return; }
#if HQ_HAVE_AVX512
        if (impl == AVX) {
            if (g_nb == 1) hb::avx::sim_range<1>(J.fpb, J.sc, J.k, J.M, a0, a1, acc.m);
            else if (g_nb == 4) hb::avx::sim_range<4>(J.fpb, J.sc, J.k, J.M, a0, a1, acc.m);
            else hb::avx::sim_range<2>(J.fpb, J.sc, J.k, J.M, a0, a1, acc.m);
            return;
        }
#endif
        for (uint64_t a = a0; a < a1; ++a) {
            double A[HQ_NK], B[HQ_NK];
            hbd::sim_pair(J.dpb, J.bc, J.sc, J.k, J.M, (uint32_t)a, A, B);
            if (impl == DIFF) {
                uint64_t mA[HQ_NK], mB[HQ_NK];
                uint32_t ovf = 0;
                hb::sim_pair(J.fpb, J.sc, J.k, J.M, (uint32_t)a, mA, mB, ovf);
                for (int j = 0; j < HQ_NK; ++j) {
                    A[j] = std::ldexp((double)mA[j], -HQ_QBITS) - A[j];
                    B[j] = std::ldexp((double)mB[j], -HQ_QBITS) - B[j];
                }
                acc.m.ovf += ovf;
            }
            acc.d.add(A, B);
        }
        return;
    }
    if (impl == SCALAR) { ha::sim_range(J.fpa, J.k, J.M, a0, a1, acc.am); return; }
#if HQ_HAVE_AVX512
    if (impl == AVX) {
        if (g_nb == 1) ha::avx::sim_range<1>(J.fpa, J.k, J.M, a0, a1, acc.am);
        else if (g_nb == 4) ha::avx::sim_range<4>(J.fpa, J.k, J.M, a0, a1, acc.am);
        else ha::avx::sim_range<2>(J.fpa, J.k, J.M, a0, a1, acc.am);
        return;
    }
#endif
    for (uint64_t a = a0; a < a1; ++a) {
        double yA[HQ_NK], yB[HQ_NK], gA[HQ_NK], gB[HQ_NK];
        had::sim_pair(J.dpa, J.k, (uint32_t)a, yA, yB, gA, gB);
        if (impl == DIFF) {
            ha::AMom one;
            ha::sim_pair(J.fpa, J.k, J.M, (uint32_t)a, one);   // one pair: sums are the pair's own values
            // recover the float payoffs from the one-pair moments: ys = sy, gs = sg (pair sums)
            for (int j = 0; j < HQ_NK; ++j) {
                double fy = std::ldexp((double)(uint64_t)one.sy[j], -HQ_QBITS), fg = std::ldexp((double)(uint64_t)one.sg[j], -HQ_QBITS);
                double dy = fy - (yA[j] + yB[j]), dg = fg - (gA[j] + gB[j]);
                yA[j] = dy; yB[j] = 0; gA[j] = dg; gB[j] = 0;   // pair-sum differences
            }
            acc.am.ovf += one.ovf;
        }
        acc.ad.add(yA, yB, gA, gB);
    }
}

void run_once(Impl impl, const Job& J, uint64_t a0, uint64_t a1, int threads, std::vector<Acc>& acc, Acc& tot) {
    const uint64_t U = 64;
    const uint64_t nb = (a1 - a0 + U - 1) / U;
#pragma omp parallel num_threads(threads)
    {
        Acc& a = acc[omp_get_thread_num()];
        a.m = hq::Mom(); a.d = hqd::DMom(); a.am = ha::AMom(); a.ad = had::DAMom();
#pragma omp for schedule(dynamic, 16)
        for (uint64_t b = 0; b < nb; ++b) {
            uint64_t b0 = a0 + b * U, b1 = std::min<uint64_t>(b0 + U, a1);
            work_block(impl, J, b0, b1, a);
        }
    }
    tot.m = hq::Mom(); tot.d = hqd::DMom(); tot.am = ha::AMom(); tot.ad = had::DAMom();
    for (int t = 0; t < threads; ++t) { tot.m.add(acc[t].m); tot.d.add(acc[t].d); tot.am.add(acc[t].am); tot.ad.add(acc[t].ad); }
}

std::vector<double> parse_list(const std::string& s, int n, double dflt) {
    std::vector<double> v;
    size_t i = 0;
    while (i < s.size()) {
        size_t j = s.find(',', i);
        if (j == std::string::npos) j = s.size();
        v.push_back(std::stod(s.substr(i, j - i)));
        i = j + 1;
    }
    if (v.empty()) v.assign(n, dflt);
    if ((int)v.size() == 1) v.assign(n, v[0]);
    if ((int)v.size() != n) throw std::runtime_error("--beta needs 1 or 3 values");
    return v;
}

const char* HELP =
    "usage: hs_cpu --case B --scheme ft|qe (--steps-per-year S | --steps M)   (basket)\n"
    "       hs_cpu --case A [--steps M] [--estimator plain|cv] [--beta b | b0,b1,b2]   (Asian, M = 250 by default)\n"
    "              [--impl scalar|avx512|double|diff] [--interleave 1|2|4] [--a0 A] (--a1 B | --pairs N) [--seed S]\n"
    "              [--threads T] [--runs R] [--warmup W] [--warmup-s S] [--window-s S] [--verify] [--out FILE]\n";
}  // namespace

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h")) { std::printf("%s", HELP); return 0; }
    int rc = 0;
    try {
        Job J{};
        std::string cs = A.str("case", "B");
        J.asian = cs == "A";
        std::string scs = A.str("scheme", J.asian ? "asian" : "qe");
        long long M;
        if (J.asian) {
            J.ac = ha::get_acase(cs);
            M = A.has("steps") ? A.i64("steps", 0) : (long long)J.ac.M;
            J.ac.M = (uint32_t)M;
        } else {
            J.bc = hb::get_bcase(cs);
            if (scs != "ft" && scs != "qe") throw std::runtime_error("--scheme must be ft or qe for the basket");
            J.sc = scs == "ft" ? hq::FT : hq::QE;
            M = A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * J.bc.T);
        }
        if (M < 1 || M > HQ_M_MAX) throw std::runtime_error("number of steps must be 1..65535");
        J.M = (uint32_t)M;
        std::string is = A.str("impl", "scalar");
        Impl impl = is == "scalar" ? SCALAR : is == "avx512" ? AVX : is == "double" ? DOUBLE : is == "diff" ? DIFF
                  : throw std::runtime_error("--impl must be scalar, avx512, double or diff");
        if (impl == AVX && !HQ_HAVE_AVX512) throw std::runtime_error("built without AVX-512");
        g_nb = (int)A.i64("interleave", 1);
        if (g_nb != 1 && g_nb != 2 && g_nb != 4) throw std::runtime_error("--interleave must be 1, 2 or 4");
        uint64_t a0 = (uint64_t)A.i64("a0", 0);
        uint64_t a1 = A.has("a1") ? (uint64_t)A.i64("a1", 0) : a0 + (uint64_t)A.i64("pairs", 65536);
        uint64_t amax = J.asian ? (1ull << 32) : (1ull << 29);
        if (a1 <= a0 || a1 > amax) throw std::runtime_error(J.asian ? "need a0 < a1 <= 2^32" : "need a0 < a1 <= 2^29 (basket)");
        uint64_t seed = (uint64_t)A.i64("seed", 20261016);
        int threads = (int)A.i64("threads", omp_get_max_threads());
        int runs = std::max<int>(1, (int)A.i64("runs", 1)), warm = std::max<int>(0, (int)A.i64("warmup", 0));
        double warm_s = A.f64("warmup-s", 0.0), win_s = A.f64("window-s", 0.0);
        std::vector<double> beta = parse_list(A.str("beta", ""), HQ_NK, 1.0);
        std::string est = A.str("estimator", "cv");
        if (J.asian && est != "plain" && est != "cv") throw std::runtime_error("--estimator must be plain or cv");

        R.set("project", "heston_accuracy");
        R.set("platform", "cpu");
        R.set("impl", is);
        if (impl == AVX) R.set("interleave", g_nb);
        R.set("case", cs);
        R.set("scheme", scs);
        R.set("steps", (long long)M);
        R.set("steps_per_year", (double)M / (J.asian ? J.ac.T : J.bc.T));
        R.set("a0", (unsigned long long)a0);
        R.set("a1", (unsigned long long)a1);
        R.set("pairs", (unsigned long long)(a1 - a0));
        R.set("seed", (unsigned long long)seed);
        R.set("threads", threads);
        if (J.asian) R.set("estimator", est);

        J.k = mc::run_keys(seed);
        if (J.asian) { ha::make_fpar(J.ac, J.M, J.fpa); J.dpa = had::make_dpar(J.ac, J.M); }
        else { hb::make_fpar(J.bc, J.M, J.fpb); J.dpb = hqd::make_dpar(J.bc.unit_case(), J.M); }
        std::vector<Acc> acc(threads);
        Acc tot;
        const double tw = bench::now_s();
        for (int i = 0; i < warm || bench::now_s() - tw < warm_s; ++i) run_once(impl, J, a0, a1, threads, acc, tot);
        std::vector<double> ts;
        bench::mark("start");
        const double tw0 = bench::now_s();
        for (int i = 0; i < runs || bench::now_s() - tw0 < win_s; ++i) {
            double t0 = bench::now_s();
            run_once(impl, J, a0, a1, threads, acc, tot);
            ts.push_back(bench::now_s() - t0);
        }
        const double twin = bench::now_s() - tw0;
        bench::mark("end");
        R.set("runs", (long long)ts.size());
        R.set("t_window_s", twin);
        R.set("t_per_run_s", twin / (double)ts.size());
        std::sort(ts.begin(), ts.end());
        double tc = ts[ts.size() / 2];
        R.set("t_compute_s", tc);
        double psteps = 2.0 * (double)(a1 - a0) * (double)M;
        R.set("path_steps", psteps);
        if (!J.asian) R.set("asset_steps", psteps * HB_NA);
        R.set("ns_per_path_step_core", tc * threads / psteps * 1e9);

        char b[96];
        uint64_t ovf = J.asian ? tot.am.ovf : tot.m.ovf;
        for (int j = 0; j < HQ_NK; ++j) {
            std::string s = std::to_string(j);
            double K = J.asian ? J.ac.K[j] : J.bc.K[j], ref = J.asian ? J.ac.ref[j] : J.bc.ref[j];
            std::snprintf(b, sizeof b, "%.0f", K); R.set("K" + s, b);
            if (!J.asian) {
                hq::Price p = integer_moments(impl) ? hb::finalize_k(J.bc, tot.m, j) : hbd::finalize_k(J.bc, tot.d, j);
                if (impl == DIFF) { std::snprintf(b, sizeof b, "%.10f", p.pv); R.set("mean_diff" + s, b); }
                else {
                    std::snprintf(b, sizeof b, "%.10f", p.price); R.set("price" + s, b);
                    std::snprintf(b, sizeof b, "%.10f", p.pv); R.set("pv" + s, b);
                    R.set("form" + s, ((J.bc.puts >> j) & 1u) ? "put" : "call");
                    std::snprintf(b, sizeof b, "%.10f", ref); R.set("ref" + s, b);
                    std::snprintf(b, sizeof b, "%.10f", p.price - ref); R.set("bias" + s, b);
                }
                std::snprintf(b, sizeof b, "%.10f", p.se); R.set("se" + s, b);
                if (integer_moments(impl)) {
                    R.set("s1_" + s, hq::u128_hex(tot.m.s1[j]));
                    R.set("sp_" + s, hq::u128_hex(tot.m.sp[j]));
                } else {
                    std::snprintf(b, sizeof b, "%.21Lg", tot.d.s1[j]); R.set("d1_" + s, b);
                    std::snprintf(b, sizeof b, "%.21Lg", tot.d.sp[j]); R.set("dp_" + s, b);
                }
            } else {
                ha::APrice p = integer_moments(impl) ? ha::finalize_k(J.ac, tot.am, j, beta[j]) : had::finalize_k(J.ac, tot.ad, j, beta[j]);
                if (impl == DIFF) {   // pair-sum differences: mean of (float - double) per path = sy / (2 np)
                    long double np = (long double)tot.ad.pairs, disc = std::exp(-(long double)J.ac.r * J.ac.T);
                    std::snprintf(b, sizeof b, "%.10Lf", disc * tot.ad.sy[j] / (2 * np)); R.set("mean_diff" + s, b);
                    std::snprintf(b, sizeof b, "%.10Lf", disc * tot.ad.sg[j] / (2 * np)); R.set("mean_diff_geo" + s, b);
                    std::snprintf(b, sizeof b, "%.10f", p.se_plain); R.set("se" + s, b);
                    continue;
                }
                double price = est == "cv" ? p.cv : p.plain, se = est == "cv" ? p.se_cv : p.se_plain;
                std::snprintf(b, sizeof b, "%.10f", price); R.set("price" + s, b);
                std::snprintf(b, sizeof b, "%.10f", se); R.set("se" + s, b);
                std::snprintf(b, sizeof b, "%.10f", p.plain); R.set("plain" + s, b);
                std::snprintf(b, sizeof b, "%.10f", p.se_plain); R.set("se_plain" + s, b);
                std::snprintf(b, sizeof b, "%.10f", p.cv); R.set("cv" + s, b);
                std::snprintf(b, sizeof b, "%.10f", p.se_cv); R.set("se_cv" + s, b);
                std::snprintf(b, sizeof b, "%.6f", p.beta); R.set("beta" + s, b);
                std::snprintf(b, sizeof b, "%.6f", p.beta_hat); R.set("beta_hat" + s, b);
                std::snprintf(b, sizeof b, "%.10f", p.geo_mc); R.set("geo_mc" + s, b);
                std::snprintf(b, sizeof b, "%.10f", p.geo_cf); R.set("geo_cf" + s, b);
                std::snprintf(b, sizeof b, "%.3f", p.var_ratio); R.set("var_ratio" + s, b);
                std::snprintf(b, sizeof b, "%.10f", ref); R.set("ref" + s, b);
                std::snprintf(b, sizeof b, "%.10f", price - ref); R.set("bias" + s, b);
                if (integer_moments(impl)) {
                    R.set("sy_" + s, hq::u128_hex(tot.am.sy[j])); R.set("syy_" + s, hq::u128_hex(tot.am.syy[j]));
                    R.set("sg_" + s, hq::u128_hex(tot.am.sg[j])); R.set("sgg_" + s, hq::u128_hex(tot.am.sgg[j]));
                    R.set("syg_" + s, hq::u128_hex(tot.am.syg[j]));
                } else {
                    std::snprintf(b, sizeof b, "%.21Lg", tot.ad.sy[j]); R.set("dy_" + s, b);
                    std::snprintf(b, sizeof b, "%.21Lg", tot.ad.syy[j]); R.set("dyy_" + s, b);
                    std::snprintf(b, sizeof b, "%.21Lg", tot.ad.sg[j]); R.set("dg_" + s, b);
                    std::snprintf(b, sizeof b, "%.21Lg", tot.ad.sgg[j]); R.set("dgg_" + s, b);
                    std::snprintf(b, sizeof b, "%.21Lg", tot.ad.syg[j]); R.set("dyg_" + s, b);
                }
            }
        }
        R.set("payoff_overflows", (unsigned long long)ovf);
        int ok = ovf == 0 ? 1 : 0;
        if (integer_moments(impl)) {
            R.set("mom_hash", "h" + bench::hex64(J.asian ? ha::mom_hash(tot.am) : hq::mom_hash(tot.m)));
            if (A.has("verify")) {
                std::vector<Acc> acc1(1);
                Acc t1;
                run_once(SCALAR, J, a0, a1, 1, acc1, t1);
                bool same = J.asian ? (t1.am == tot.am) : (t1.m == tot.m);
                R.set("verify_identical_to_scalar_1thread", same ? 1 : 0);
                ok = ok && same;
            }
        }
        R.set("ok", ok);
        R.set("t_total_s", bench::now_s() - t_start);
        if (A.has("out")) mc::append_json(A.str("out"), R);
        rc = ok ? 0 : 2;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        R.set("ok", 0);
        rc = 1;
    }
    R.set("t_total_s", bench::now_s() - t_start);
    R.print();
    return rc;
}
