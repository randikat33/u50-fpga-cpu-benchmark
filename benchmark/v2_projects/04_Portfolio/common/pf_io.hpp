// pf_io.hpp - shared CLI parsing, file I/O and reporting for pf_fpga and pf_cpu.
#pragma once
#include <cstdio>
#include <string>
#include <vector>
#include "bench_common.hpp"
#include "pf_model.hpp"

namespace pf {

struct Workload {
    std::string portfolio, market, out, out_stage1, out_topk;
    uint32_t paths1 = 256, steps1 = 32, paths2 = 131072, steps2 = 32, topk = 10000;
    uint64_t seed = 12345;
    float dspot = 0.0f, drate = 0.0f, volscale = 1.0f;
    uint32_t ft = 0;
    int verify = 0;              // 0 off, 1 sampled, 2 full
    uint32_t verify_n = 64;      // sampled verification: trades per stage
};

inline const char* workload_usage() {
    return "  --portfolio FILE      portfolio.bin (v1 format)            [required]\n"
           "  --market FILE         market.bin (v1 format)               [required]\n"
           "  --out FILE            write results_final (uint32 n + n*(price,std_err))\n"
           "  --out-stage1 FILE     write stage-1 results (same format)\n"
           "  --out-topk FILE       write refined trades (uint32 k + k*idx + k*Result, v1 CPU format)\n"
           "  --paths1 N --steps1 M screening stage     (default 256, 32)\n"
           "  --paths2 N --steps2 M refinement stage    (default 131072, 32)\n"
           "  --topk K              trades refined in stage 2 (default 10000)\n"
           "  --seed S              RNG seed (default 12345)\n"
           "  --dspot X --drate X --volscale X   scenario shifts (default 0 0 1)\n"
           "  --scheme reflect|ft   variance scheme: v1 reflection (default) or full truncation\n"
           "  --verify [sample|full] check against the scalar reference model (untimed)\n"
           "  --verify-n N          trades per stage for sampled verification (default 64)\n"
           "  env fallback (v1 cpu_only): PATHS_SMALL PATHS_LARGE N_STEPS TOP_K SEED D_SPOT D_RATE VOL_SCALE\n";
}

inline Workload parse_workload(const bench::Args& a) {
    Workload w;
    auto envu = [](const char* n, uint64_t d) { const char* v = std::getenv(n); return v ? std::stoull(v) : d; };
    auto envf = [](const char* n, float d) { const char* v = std::getenv(n); return v ? std::stof(v) : d; };
    w.paths1 = (uint32_t)envu("PATHS_SMALL", w.paths1);
    w.paths2 = (uint32_t)envu("PATHS_LARGE", w.paths2);
    w.steps1 = w.steps2 = (uint32_t)envu("N_STEPS", w.steps1);
    w.topk = (uint32_t)envu("TOP_K", w.topk);
    w.seed = envu("SEED", w.seed);
    w.dspot = envf("D_SPOT", w.dspot);
    w.drate = envf("D_RATE", w.drate);
    w.volscale = envf("VOL_SCALE", w.volscale);
    w.portfolio = a.str("portfolio");
    w.market = a.str("market");
    w.out = a.str("out");
    w.out_stage1 = a.str("out-stage1");
    w.out_topk = a.str("out-topk");
    w.paths1 = (uint32_t)a.i64("paths1", w.paths1);
    w.steps1 = (uint32_t)a.i64("steps1", w.steps1);
    w.paths2 = (uint32_t)a.i64("paths2", w.paths2);
    w.steps2 = (uint32_t)a.i64("steps2", w.steps2);
    w.topk = (uint32_t)a.i64("topk", w.topk);
    w.seed = (uint64_t)a.i64("seed", (long long)w.seed);
    w.dspot = (float)a.f64("dspot", w.dspot);
    w.drate = (float)a.f64("drate", w.drate);
    w.volscale = (float)a.f64("volscale", w.volscale);
    std::string sch = a.str("scheme", "reflect");
    if (sch == "ft") w.ft = 1;
    else if (sch != "reflect") throw std::runtime_error("--scheme must be reflect or ft");
    if (a.has("verify")) {
        std::string v = a.str("verify");
        w.verify = (v == "full") ? 2 : 1;
    }
    w.verify_n = (uint32_t)a.i64("verify-n", w.verify_n);
    if (w.portfolio.empty() || w.market.empty()) throw std::runtime_error("--portfolio and --market are required");
    if (!w.paths1 || !w.paths2 || !w.steps1 || !w.steps2) throw std::runtime_error("paths/steps must be >= 1");
    if (w.steps1 > PF_MAX_STEPS || w.steps2 > PF_MAX_STEPS) throw std::runtime_error("steps must be <= 1024");
    if (w.paths1 > 0xFFFFFF00u || w.paths2 > 0xFFFFFF00u) throw std::runtime_error("paths too large");
    return w;
}

inline RunParams stage_params(const Workload& w, int stage, uint32_t n_under) {
    RunParams rp;
    rp.n_under = n_under;
    rp.n_paths = stage == 1 ? w.paths1 : w.paths2;
    rp.n_steps = stage == 1 ? w.steps1 : w.steps2;
    rp.seed = w.seed;
    rp.stage = (uint32_t)stage;
    rp.ft = w.ft;
    rp.dspot = w.dspot; rp.drate = w.drate; rp.volscale = w.volscale;
    return rp;
}

inline uint32_t portfolio_count(const std::string& path) {
    uint32_t n = 0;
    bench::read_into(path, &n, 4);
    size_t sz = bench::file_size(path);
    if (sz < 4 + (size_t)n * sizeof(Trade)) throw std::runtime_error("portfolio file truncated: " + path);
    return n;
}
inline void read_trades(const std::string& path, uint32_t first, uint32_t count, void* dst) {
    if (count) bench::read_into(path, dst, (size_t)count * sizeof(Trade), 4 + (size_t)first * sizeof(Trade));
}
inline void read_market(const std::string& path, float* dst) {
    if (bench::file_size(path) < sizeof(Market)) throw std::runtime_error("market file too small: " + path);
    bench::read_into(path, dst, sizeof(Market));
}
// v1: N_UNDER = min(MAX_UNDERLYINGS, max_uid + 1)
inline uint32_t n_under_of(const Trade* t, uint32_t n, uint32_t prev = 0) {
    uint32_t mx = prev;
    for (uint32_t i = 0; i < n; ++i) mx = std::max(mx, t[i].underlying_id + 1);
    return std::min<uint32_t>(PF_MAX_UNDER, std::max<uint32_t>(mx, 1));
}
inline void write_results(const std::string& path, const Result* r, uint32_t n) {
    bench::write_from(path, &n, 4);
    bench::write_from(path, r, (size_t)n * sizeof(Result), true);
}
inline void write_topk(const std::string& path, const std::vector<uint32_t>& idx, const std::vector<Result>& res) {
    uint32_t k = (uint32_t)idx.size();
    bench::write_from(path, &k, 4);
    bench::write_from(path, idx.data(), (size_t)k * 4, true);
    bench::write_from(path, res.data(), (size_t)k * sizeof(Result), true);
}
inline uint64_t results_checksum(const Result* r, uint32_t n) {
    return bench::fnv1a64(reinterpret_cast<const uint8_t*>(r), (size_t)n * sizeof(Result));
}
inline double price_sum(const Result* r, uint32_t n) {
    double s = 0;
    for (uint32_t i = 0; i < n; ++i) s += r[i].price;
    return s;
}

inline void report_workload(bench::Report& R, const Workload& w, uint32_t n, uint32_t nu, uint32_t k) {
    R.set("project", "portfolio");
    R.set("n_trades", (unsigned)n);
    R.set("n_under", (unsigned)nu);
    R.set("paths1", (unsigned)w.paths1);
    R.set("steps1", (unsigned)w.steps1);
    R.set("paths2", (unsigned)w.paths2);
    R.set("steps2", (unsigned)w.steps2);
    R.set("topk", (unsigned)k);
    R.set("seed", (unsigned long long)w.seed);
    R.set("scheme", w.ft ? "ft" : "reflect");
    R.set("path_steps", total_steps(n, w.paths1, w.steps1, k, w.paths2, w.steps2));
}

}  // namespace pf
