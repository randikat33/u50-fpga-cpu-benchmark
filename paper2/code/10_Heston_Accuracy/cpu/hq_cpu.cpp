// hq_cpu.cpp - CPU program of paper 2 (single-asset Heston call, FT and QE schemes).
//   --impl scalar  : the float reference model (common/hq_model.hpp), OpenMP; bit-identical to the
//                    FPGA kernel and the AVX-512 code (phase 3). Exact integer moments.
//   --impl double  : same draws, double precision and library exp/log/sqrt (calibration only).
//   --impl diff    : float payoff - double payoff on the same path (float32 + table error).
//   --impl coupled : FT only, payoff(step h) - payoff(step 2h) on the same Brownian path.
// Pairs [a0, a1) of one seed: runs over consecutive ranges add up exactly to one longer run, so the
// calibration controller (scripts/calibrate.py) extends a run in batches.
#include <algorithm>
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include "bench_common.hpp"
#include "hq_model.hpp"
#include "hq_double.hpp"
#include "hq_avx512.hpp"
#include "mc_cli.hpp"      // mc::append_json

namespace {
struct alignas(64) Acc { hq::Mom m; hqd::DMom d; };

enum Impl { SCALAR, DOUBLE, DIFF, COUPLED, AVX };
int g_nb = 1;   // AVX-512: interleaved zmm blocks (1, 2 or 4)
inline bool integer_moments(Impl i) { return i == SCALAR || i == AVX; }

void work_block(Impl impl, const float* fp, const hqd::DPar& dp, hq::Scheme sc, const mc::Keys& k, uint32_t M,
                uint64_t a0, uint64_t a1, Acc& acc) {
    if (impl == SCALAR) { hq::sim_range(fp, sc, k, M, a0, a1, acc.m); return; }
#if HQ_HAVE_AVX512
    if (impl == AVX) {
        if (g_nb == 1) hq::avx::sim_range<1>(fp, sc, k, M, a0, a1, acc.m);
        else if (g_nb == 4) hq::avx::sim_range<4>(fp, sc, k, M, a0, a1, acc.m);
        else hq::avx::sim_range<2>(fp, sc, k, M, a0, a1, acc.m);
        return;
    }
#endif
    for (uint64_t a = a0; a < a1; ++a) {
        double A[HQ_NK], B[HQ_NK];
        if (impl == DOUBLE) {
            hqd::sim_pair(dp, sc, k, M, (uint32_t)a, A, B);
        } else if (impl == COUPLED) {
            hqd::ft_coupled_pair(dp, k, M, (uint32_t)a, A, B);
        } else {   // DIFF
            uint64_t mA[HQ_NK], mB[HQ_NK];
            uint32_t ovf = 0;
            hq::sim_pair(fp, sc, k, M, (uint32_t)a, mA, mB, ovf);
            hqd::sim_pair(dp, sc, k, M, (uint32_t)a, A, B);
            for (int j = 0; j < HQ_NK; ++j) {
                A[j] = std::ldexp((double)mA[j], -HQ_QBITS) - A[j];
                B[j] = std::ldexp((double)mB[j], -HQ_QBITS) - B[j];
            }
            acc.m.ovf += ovf;
        }
        acc.d.add(A, B);
    }
}

void run_once(Impl impl, const float* fp, const hqd::DPar& dp, hq::Scheme sc, const mc::Keys& k, uint32_t M,
              uint64_t a0, uint64_t a1, int threads, std::vector<Acc>& acc, hq::Mom& m, hqd::DMom& d) {
    const uint64_t U = 64;                               // work unit: 64 pairs (independent of threads)
    const uint64_t nb = (a1 - a0 + U - 1) / U;
#pragma omp parallel num_threads(threads)
    {
        Acc& a = acc[omp_get_thread_num()];
        a.m = hq::Mom();
        a.d = hqd::DMom();
#pragma omp for schedule(dynamic, 16)
        for (uint64_t b = 0; b < nb; ++b) {
            uint64_t b0 = a0 + b * U, b1 = std::min<uint64_t>(b0 + U, a1);
            work_block(impl, fp, dp, sc, k, M, b0, b1, a);
        }
    }
    m = hq::Mom();
    d = hqd::DMom();
    for (int t = 0; t < threads; ++t) { m.add(acc[t].m); d.add(acc[t].d); }
}

const char* HELP =
    "usage: hq_cpu --case I|II|III|P1 --scheme ft|qe (--steps-per-year S | --steps M)\n"
    "              [--impl scalar|avx512|double|diff|coupled] [--interleave 1|2|4] [--a0 A] (--a1 B | --pairs N) [--seed S]\n"
    "              [--threads T] [--runs R] [--warmup W] [--warmup-s S] [--window-s S] [--verify] [--out FILE]\n"
    "  timing: untimed passes (>= W and >= S seconds), then timed passes between MARK lines (>= R and >= S s)\n"
    "  pairs [a0, a1) of the seed's streams are simulated (2 paths each, antithetic)\n"
    "  --verify : scalar/avx512; checks against the scalar model on 1 thread (identical moments)\n";
}  // namespace

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h")) { std::printf("%s", HELP); return 0; }
    int rc = 0;
    try {
        hq::Case c = hq::get_case(A.str("case", "I"));
        std::string scs = A.str("scheme", "qe");
        if (scs != "ft" && scs != "qe") throw std::runtime_error("--scheme must be ft or qe");
        hq::Scheme sc = scs == "ft" ? hq::FT : hq::QE;
        long long M = A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * c.T);
        if (M < 1 || M > HQ_M_MAX) throw std::runtime_error("number of steps must be 1..65535");
        std::string is = A.str("impl", "scalar");
        Impl impl = is == "scalar" ? SCALAR : is == "avx512" ? AVX : is == "double" ? DOUBLE : is == "diff" ? DIFF : is == "coupled" ? COUPLED
                  : throw std::runtime_error("--impl must be scalar, avx512, double, diff or coupled");
        if (impl == AVX && !HQ_HAVE_AVX512) throw std::runtime_error("built without AVX-512");
        g_nb = (int)A.i64("interleave", 1);
        if (g_nb != 1 && g_nb != 2 && g_nb != 4) throw std::runtime_error("--interleave must be 1, 2 or 4");
        if (impl == COUPLED && (sc != hq::FT || M % 2)) throw std::runtime_error("--impl coupled needs --scheme ft and an even number of steps");
        uint64_t a0 = (uint64_t)A.i64("a0", 0);
        uint64_t a1 = A.has("a1") ? (uint64_t)A.i64("a1", 0) : a0 + (uint64_t)A.i64("pairs", 65536);
        if (a1 <= a0 || a1 > (1ull << 32)) throw std::runtime_error("need a0 < a1 <= 2^32");
        uint64_t seed = (uint64_t)A.i64("seed", 20261011);
        int threads = (int)A.i64("threads", omp_get_max_threads());
        int runs = std::max<int>(1, (int)A.i64("runs", 1)), warm = std::max<int>(0, (int)A.i64("warmup", 0));
        double warm_s = A.f64("warmup-s", 0.0), win_s = A.f64("window-s", 0.0);

        R.set("project", "heston_accuracy");
        R.set("platform", "cpu");
        R.set("impl", is);
        if (impl == AVX) R.set("interleave", g_nb);
        R.set("case", c.name);
        R.set("scheme", scs);
        R.set("steps", (long long)M);
        R.set("steps_per_year", (double)M / c.T);
        R.set("a0", (unsigned long long)a0);
        R.set("a1", (unsigned long long)a1);
        R.set("pairs", (unsigned long long)(a1 - a0));
        R.set("seed", (unsigned long long)seed);
        R.set("threads", threads);

        float fp[HQ_FPAR_N];
        hq::make_fpar(c, (uint32_t)M, fp);
        hqd::DPar dp = hqd::make_dpar(c, (uint32_t)M);
        mc::Keys k = mc::run_keys(seed);
        std::vector<Acc> acc(threads);
        hq::Mom m;
        hqd::DMom d;
        // steady-clock protocol (paper 1): untimed passes for at least --warmup passes and --warmup-s seconds,
        // then timed passes between the MARK lines for at least --runs passes and --window-s seconds
        const double tw = bench::now_s();
        for (int i = 0; i < warm || bench::now_s() - tw < warm_s; ++i)
            run_once(impl, fp, dp, sc, k, (uint32_t)M, a0, a1, threads, acc, m, d);
        std::vector<double> ts;
        bench::mark("start");
        const double tw0 = bench::now_s();
        for (int i = 0; i < runs || bench::now_s() - tw0 < win_s; ++i) {
            double t0 = bench::now_s();
            run_once(impl, fp, dp, sc, k, (uint32_t)M, a0, a1, threads, acc, m, d);
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
        R.set("ns_per_path_step_core", tc * threads / psteps * 1e9);

        char b[96];
        for (int j = 0; j < HQ_NK; ++j) {
            hq::Price p = integer_moments(impl) ? hq::finalize_k(c, m, j) : hqd::finalize_k(c, d, j);
            std::string s = std::to_string(j);
            std::snprintf(b, sizeof b, "%.0f", c.K[j]); R.set("K" + s, b);
            if (integer_moments(impl) || impl == DOUBLE) { std::snprintf(b, sizeof b, "%.10f", p.price); R.set("price" + s, b); }
            else { std::snprintf(b, sizeof b, "%.10f", p.pv); R.set("mean_diff" + s, b); }   // discounted mean difference (no parity term)
            std::snprintf(b, sizeof b, "%.10f", p.se); R.set("se" + s, b);
            if (integer_moments(impl) || impl == DOUBLE) {
                std::snprintf(b, sizeof b, "%.10f", p.pv); R.set("pv" + s, b);   // price = pv (+ S0 - K e^-rT for a put)
                R.set("form" + s, ((c.puts >> j) & 1u) ? "put" : "call");
                std::snprintf(b, sizeof b, "%.6f", c.ref[j]); R.set("ref" + s, b);
                std::snprintf(b, sizeof b, "%.10f", p.price - c.ref[j]); R.set("bias" + s, b);
            }
            if (integer_moments(impl)) {
                R.set("s1_" + s, hq::u128_hex(m.s1[j]));
                R.set("sp_" + s, hq::u128_hex(m.sp[j]));
            } else {
                std::snprintf(b, sizeof b, "%.21Lg", d.s1[j]); R.set("d1_" + s, b);
                std::snprintf(b, sizeof b, "%.21Lg", d.sp[j]); R.set("dp_" + s, b);
            }
        }
        R.set("payoff_overflows", (unsigned long long)m.ovf);
        int ok = m.ovf == 0 ? 1 : 0;
        if (integer_moments(impl)) {
            R.set("mom_hash", "h" + bench::hex64(hq::mom_hash(m)));
            if (A.has("verify")) {   // scalar model on 1 thread must give the same moments
                hq::Mom m1;
                hqd::DMom d1;
                std::vector<Acc> acc1(1);
                run_once(SCALAR, fp, dp, sc, k, (uint32_t)M, a0, a1, 1, acc1, m1, d1);
                bool same = m1 == m;
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
