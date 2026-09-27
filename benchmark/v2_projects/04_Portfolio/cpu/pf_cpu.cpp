// pf_cpu.cpp - CPU baseline for the v2 two-stage Heston portfolio benchmark.
//   --impl avx512 : 16 paths per zmm (hand-written intrinsics), identical numerics to the FPGA
//   --impl scalar : the scalar reference model
// Threading: OpenMP over (trade group | trade path-block) work items, schedule(dynamic,1) because
// Asian trades cost ~2x European ones on the CPU (per-step exp). Moments are exact integers, so
// results do not depend on the thread count or the work-item split.
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include "bench_common.hpp"
#include "pf_engine.hpp"
#include "pf_io.hpp"
#include "pf_verify.hpp"

using namespace pf;

struct Item { uint32_t t0, t1, p0, p1; };

struct Ctx {
    pfcpu::Impl impl;
    pfcpu::Lut lut;
    uint32_t grain;
};

static inline void sim_one(const Ctx& x, const TradeConst& c, uint32_t p0, uint32_t p1, Mom& m) {
#if PF_HAVE_AVX512
    if (x.impl == pfcpu::Impl::avx512) {
        static thread_local pfcpu::Avx512Engine eg(pfcpu::Lut::gather), es(pfcpu::Lut::scalar);
        (x.lut == pfcpu::Lut::gather ? eg : es).sim(c, p0, p1, m);
        return;
    }
#endif
    pfcpu::sim_scalar(c, p0, p1, m);
}

// Price `n` trades (records recs[j], ids ids[j]) with parameters rp; moments -> mom[j]
static void run_stage(const Ctx& x, uint32_t n, const Trade* const* recs, const uint32_t* ids,
                      const float* mkt, const RunParams& rp, Mom* mom) {
    CallConst cc = call_const(rp);
    uint32_t P = rp.n_paths;
    std::vector<Item> items;
    bool split = P > x.grain;
    if (!split) {
        uint32_t g = std::max<uint32_t>(1, x.grain / P);
        for (uint32_t t = 0; t < n; t += g) items.push_back({t, std::min(n, t + g), 0, P});
    } else {
        uint32_t b = (x.grain + 15) / 16 * 16;
        for (uint32_t t = 0; t < n; ++t)
            for (uint64_t p = 0; p < P; p += b) items.push_back({t, t + 1, (uint32_t)p, (uint32_t)std::min<uint64_t>(P, p + b)});
    }
    std::vector<Mom> part(split ? items.size() : 0);
    #pragma omp parallel for schedule(dynamic, 1)
    for (long i = 0; i < (long)items.size(); ++i) {
        const Item& it = items[i];
        for (uint32_t t = it.t0; t < it.t1; ++t) {
            TradeConst c = setup(*recs[t], ids[t], mkt, rp, cc);
            if (split) sim_one(x, c, it.p0, it.p1, part[i]);
            else { Mom m; sim_one(x, c, it.p0, it.p1, m); mom[t] = m; }
        }
    }
    if (split) {
        for (uint32_t t = 0; t < n; ++t) mom[t] = Mom();
        for (size_t i = 0; i < items.size(); ++i) mom[items[i].t0].add(part[i]);
    }
}

// Pick the faster table-lookup method on this machine (gather can be slow with the GDS microcode
// mitigation on Ice Lake). Alternating timed runs of 8192 paths x 32 steps, best of 3 each.
static pfcpu::Lut calibrate(const Ctx& x0, const Trade& t, const float* mkt) {
    Ctx x = x0;
    RunParams rp; rp.n_paths = 8192; rp.n_steps = 32;
    CallConst cc = call_const(rp);
    TradeConst c = setup(t, 0, mkt, rp, cc);
    double best[2] = {1e9, 1e9};
    for (int rep = 0; rep < 3; ++rep)
        for (int l = 0; l < 2; ++l) {
            x.lut = l ? pfcpu::Lut::scalar : pfcpu::Lut::gather;
            Mom m; double t0 = bench::now_s();
            sim_one(x, c, 0, rp.n_paths, m);
            volatile uint64_t sink = m.sum;   // keep the timed work alive (else it is dead code)
            (void)sink;
            best[l] = std::min(best[l], bench::now_s() - t0);
        }
    if (std::getenv("PF_DEBUG_LUT")) std::fprintf(stderr, "lut calib gather %.6g scalar %.6g\n", best[0], best[1]);
    return best[1] < best[0] ? pfcpu::Lut::scalar : pfcpu::Lut::gather;
}

int main(int argc, char** argv) {
    double t_main = bench::now_s();
    bench::Args a(argc, argv);
    if (a.has("help")) {
        std::printf("pf_cpu - CPU baseline, two-stage Heston MC portfolio (v2)\n%s"
                    "  --impl avx512|scalar  (default avx512 when available)\n"
                    "  --lut auto|gather|scalar  table lookup method for AVX-512 (default auto = timed)\n"
                    "  --threads N           (default omp_get_max_threads())\n"
                    "  --grain N             paths per OpenMP work item (default 4096)\n",
                    pf::workload_usage());
        return 0;
    }
    bench::Report R;
    int ok = 1;
    try {
        Workload w = parse_workload(a);
        Ctx x;
        std::string impl = a.str("impl", pfcpu::have_avx512() ? "avx512" : "scalar");
        if (impl == "avx512") {
            if (!pfcpu::have_avx512()) throw std::runtime_error("avx512 not available in this build/CPU");
            x.impl = pfcpu::Impl::avx512;
        } else if (impl == "scalar") x.impl = pfcpu::Impl::scalar;
        else throw std::runtime_error("--impl must be avx512 or scalar");
        x.grain = (uint32_t)std::max<long long>(16, a.i64("grain", 4096));
        int threads = (int)a.i64("threads", omp_get_max_threads());
        omp_set_num_threads(threads);

        // ---------------- read (first touch in parallel, static schedule)
        double t0 = bench::now_s();
        uint32_t n = portfolio_count(w.portfolio);
        bench::AlignedBuf<Trade> trades(n);
        #pragma omp parallel for schedule(static)
        for (long i = 0; i < (long)n; ++i) std::memset(&trades[i], 0, sizeof(Trade));
        read_trades(w.portfolio, 0, n, trades.data());
        std::vector<float> mkt(PF_MARKET_WORDS);
        read_market(w.market, mkt.data());
        uint32_t nu = n_under_of(trades.data(), n);
        R.set("t_read_s", bench::now_s() - t0);

        double ts = bench::now_s();
        std::string lut = a.str("lut", "auto");
        x.lut = pfcpu::Lut::gather;
        if (x.impl == pfcpu::Impl::avx512) {
            if (lut == "scalar") x.lut = pfcpu::Lut::scalar;
            else if (lut == "auto" && n) x.lut = calibrate(x, trades[0], mkt.data());
        }
        std::vector<const Trade*> recs1(n);
        std::vector<uint32_t> ids1(n);
        bench::AlignedBuf<Mom> mom1(n);
        bench::AlignedBuf<Result> res1(n);
        #pragma omp parallel for schedule(static)
        for (long i = 0; i < (long)n; ++i) {
            recs1[i] = &trades[i]; ids1[i] = (uint32_t)i; mom1[i] = Mom(); res1[i] = Result{0, 0};
        }
        uint32_t k = std::min(w.topk, n);
        std::vector<Result> final_res(n);
        R.set("t_setup_s", bench::now_s() - ts);

        RunParams rp1 = stage_params(w, 1, nu), rp2 = stage_params(w, 2, nu);
        std::vector<uint32_t> sel;
        std::vector<const Trade*> recs2(k);
        std::vector<uint32_t> ids2(k);
        std::vector<Mom> mom2(k);
        std::vector<Result> res2(k);

        // ---------------- measured section: stage 1 + selection + stage 2
        bench::mark("start");
        double tc0 = bench::now_s();
        run_stage(x, n, recs1.data(), ids1.data(), mkt.data(), rp1, mom1.data());
        #pragma omp parallel for schedule(static)
        for (long i = 0; i < (long)n; ++i) res1[i] = finalize(trades[i], mkt.data(), rp1, mom1[i]);
        double tc1 = bench::now_s();
        sel = select_topk(res1.data(), n, k);
        for (uint32_t j = 0; j < k; ++j) { recs2[j] = &trades[sel[j]]; ids2[j] = sel[j]; }
        double tc2 = bench::now_s();
        run_stage(x, k, recs2.data(), ids2.data(), mkt.data(), rp2, mom2.data());
        for (uint32_t j = 0; j < k; ++j) res2[j] = finalize(*recs2[j], mkt.data(), rp2, mom2[j]);
        std::memcpy(final_res.data(), res1.data(), (size_t)n * sizeof(Result));
        for (uint32_t j = 0; j < k; ++j) final_res[sel[j]] = res2[j];
        double tc3 = bench::now_s();
        bench::mark("end");

        R.set("platform", "cpu");
        R.set("impl", std::string("pf_cpu_") + impl + (x.impl == pfcpu::Impl::avx512 ? (x.lut == pfcpu::Lut::gather ? "_gather" : "_lutscalar") : ""));
        R.set("isa", x.impl == pfcpu::Impl::avx512 ? "avx512" : "scalar");
        R.set("threads", threads);
        R.set("grain", (unsigned)x.grain);
        report_workload(R, w, n, nu, k);
        double steps = total_steps(n, w.paths1, w.steps1, k, w.paths2, w.steps2);
        R.set("t_compute_s", tc3 - tc0);
        R.set("t_stage1_s", tc1 - tc0);
        R.set("t_select_s", tc2 - tc1);
        R.set("t_stage2_s", tc3 - tc2);
        R.set("msteps_per_s", steps / (tc3 - tc0) / 1e6);
        R.set("checksum", bench::hex64(results_checksum(final_res.data(), n)));
        R.set("price_sum", price_sum(final_res.data(), n));

        // ---------------- write
        double tw = bench::now_s();
        if (!w.out.empty()) write_results(w.out, final_res.data(), n);
        if (!w.out_stage1.empty()) write_results(w.out_stage1, res1.data(), n);
        if (!w.out_topk.empty()) write_topk(w.out_topk, sel, res2);
        R.set("t_write_s", bench::now_s() - tw);

        // ---------------- verify (untimed)
        if (w.verify) {
            double tv = bench::now_s();
            auto s1 = verify_sample(n, w.verify_n, w.verify);
            auto s2 = verify_sample(k, w.verify == 2 ? k : std::max<uint32_t>(1, w.verify_n / 16), w.verify);
            long bad = verify_moments(s1, recs1.data(), ids1.data(), mom1.data(), mkt.data(), rp1);
            bad += verify_moments(s2, recs2.data(), ids2.data(), mom2.data(), mkt.data(), rp2);
            bad += (select_topk(res1.data(), n, k) != sel);
            R.set("verify_trades", (unsigned long)(s1.size() + s2.size()));
            R.set("verify_mismatch", bad);
            R.set("t_verify_s", bench::now_s() - tv);
            ok = bad == 0;
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "pf_cpu: error: %s\n", e.what());
        R.set("project", "portfolio"); R.set("platform", "cpu"); R.set("error", e.what());
        ok = 0;
    }
    R.set("ok", ok);
    R.set("t_total_s", bench::now_s() - t_main);
    R.print();
    return ok ? 0 : 1;
}
