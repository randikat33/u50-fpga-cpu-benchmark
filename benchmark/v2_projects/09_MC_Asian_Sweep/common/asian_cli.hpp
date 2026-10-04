// asian_cli.hpp - workload options shared by cpu/asian_cpu.cpp and host/asian_fpga.cpp
#pragma once
#include <algorithm>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>
#include "bench_common.hpp"
#include "asian_model.hpp"
#include "mc_cli.hpp"      // mc::median, mc::append_json

namespace asn {
struct Workload {
    uint64_t paths = 131072, pairs = 65536;
    uint32_t steps = 64;
    uint64_t seed = 12345;
    int runs = 1, warmup = 1;
    Market mk;
};
inline const char* workload_help() {
    return "  --paths N      Monte Carlo paths (rounded up to an even number: antithetic pairs) [131072]\n"
           "  --steps M      monitoring dates = time steps (1..65535) [64]\n"
           "  --seed S       RNG seed (64-bit) [12345]\n"
           "  --runs R       timed repetitions (t_compute_s = median) [1]\n"
           "  --warmup W     untimed warm-up repetitions [1]\n"
           "  --s0 --sigma --r --strike --T --put   GBM and option parameters [100 0.2 0.03 100 1.0 call]\n"
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
    long long m = a.i64("steps", 64);
    if (m < 1 || m > AS_M_MAX) throw std::runtime_error("--steps must be 1..65535");
    w.steps = (uint32_t)m;
    w.seed = (uint64_t)a.i64("seed", 12345);
    w.runs = std::max<int>(1, (int)a.i64("runs", 1));
    w.warmup = std::max<int>(0, (int)a.i64("warmup", 1));
    w.mk.S0 = a.f64("s0", 100.0);
    w.mk.sigma = a.f64("sigma", 0.2);
    w.mk.r = a.f64("r", 0.03);
    w.mk.K = a.f64("strike", 100.0);
    w.mk.T = a.f64("T", 1.0);
    w.mk.put = a.has("put");
    if (!(w.mk.K > 0) || !(w.mk.T > 0) || !(w.mk.S0 > 0) || !(w.mk.sigma >= 0))
        throw std::runtime_error("--s0, --strike and --T must be > 0, --sigma >= 0");
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
    R.set("sigma", w.mk.sigma);
    R.set("runs", w.runs);
}
inline void report_price(bench::Report& R, const Workload& w, const Mom& m) {
    mc::Price p = finalize(w.mk, w.steps, m);
    char b[64];
    std::snprintf(b, sizeof b, "%.10f", p.price); R.set("price", b);
    std::snprintf(b, sizeof b, "%.10f", p.se); R.set("stderr", b);
    std::snprintf(b, sizeof b, "%.10f", p.se_naive); R.set("stderr_naive", b);
    R.set("payoff_overflows", (unsigned long long)m.ovf);
    R.set("mom_hash", bench::hex64(mc::mom_hash(m)));
}
using mc::median;
using mc::append_json;
}  // namespace asn
