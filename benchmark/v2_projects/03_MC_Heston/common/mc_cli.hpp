// mc_cli.hpp - workload options shared by cpu/mc_cpu.cpp and host/mc_fpga.cpp
#pragma once
#include <algorithm>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>
#include "bench_common.hpp"
#include "mc_model.hpp"

namespace mc {
struct Workload {
    uint64_t paths = 131072, pairs = 65536;
    uint32_t steps = 32;
    uint64_t seed = 12345;
    int runs = 1, warmup = 1;
    Market mk = v1_market();
    double corr = 0.0;
};
inline const char* workload_help() {
    return "  --paths N      Monte Carlo paths (rounded up to an even number: antithetic pairs) [131072]\n"
           "  --steps M      time steps (1..65535) [32]\n"
           "  --seed S       RNG seed (64-bit) [12345]\n"
           "  --runs R       timed repetitions (t_compute_s = median) [1]\n"
           "  --warmup W     untimed warm-up repetitions [1]\n"
           "  --strike K --r R --T T --put   option parameters [100 0.01 1.0 call]\n"
           "  --corr C       equicorrelated basket instead of v1's identity Cholesky [0]\n"
           "  --verify       check against the scalar reference model (sets ok): all pairs if\n"
           "                 pairs*steps <= 2^24, else the first --verify-pairs [65536] pairs\n"
           "  --out FILE     append the RESULT_JSON line to FILE\n";
}
inline Workload parse_workload(const bench::Args& a) {
    Workload w;
    w.paths = (uint64_t)a.i64("paths", 131072);
    if (w.paths < 2) w.paths = 2;
    w.pairs = (w.paths + 1) / 2;
    if (w.pairs > 0xFFFFFFFFull) throw std::runtime_error("--paths too large (max 2^33 - 2)");
    long long m = a.i64("steps", 32);
    if (m < 1 || m > MC_M_MAX) throw std::runtime_error("--steps must be 1..65535");
    w.steps = (uint32_t)m;
    w.seed = (uint64_t)a.i64("seed", 12345);
    w.runs = std::max<int>(1, (int)a.i64("runs", 1));
    w.warmup = std::max<int>(0, (int)a.i64("warmup", 1));
    w.mk.K = a.f64("strike", 100.0);
    w.mk.r = a.f64("r", 0.01);
    w.mk.T = a.f64("T", 1.0);
    w.mk.put = a.has("put");
    w.corr = a.f64("corr", 0.0);
    if (w.corr != 0.0) set_equicorr(w.mk, w.corr);
    if (!(w.mk.K > 0) || !(w.mk.T > 0)) throw std::runtime_error("--strike and --T must be > 0");
    return w;
}
inline void report_workload(bench::Report& R, const Workload& w) {
    R.set("paths", (unsigned long long)w.paths);
    R.set("paths_eff", (unsigned long long)(2 * w.pairs));
    R.set("pairs", (unsigned long long)w.pairs);
    R.set("steps", (unsigned)w.steps);
    R.set("seed", (unsigned long long)w.seed);
    R.set("option", w.mk.put ? "put" : "call");
    R.set("strike", w.mk.K);
    R.set("corr", w.corr);
    R.set("runs", w.runs);
}
inline void report_price(bench::Report& R, const Workload& w, const Mom& m) {
    Price p = finalize(w.mk, w.steps, m);
    char b[64];
    std::snprintf(b, sizeof b, "%.10f", p.price); R.set("price", b);
    std::snprintf(b, sizeof b, "%.10f", p.se); R.set("stderr", b);
    std::snprintf(b, sizeof b, "%.10f", p.se_naive); R.set("stderr_naive", b);
    R.set("payoff_overflows", (unsigned long long)m.ovf);
    R.set("mom_hash", bench::hex64(mom_hash(m)));
}
inline double median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    size_t n = v.size();
    return n % 2 ? v[n / 2] : 0.5 * (v[n / 2 - 1] + v[n / 2]);
}
inline void append_json(const std::string& path, bench::Report& R) {
    if (path.empty()) return;
    FILE* f = std::fopen(path.c_str(), "a");
    if (!f) throw std::runtime_error("cannot open " + path);
    R.print(f);
    std::fclose(f);
}
}  // namespace mc
