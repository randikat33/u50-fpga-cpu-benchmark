// pf_fpga.cpp - FPGA host (XRT native C++ API) for the v2 two-stage Heston portfolio kernel.
//
// Flow (all buffers allocated once, before the measured window):
//   read    : portfolio slices are read straight into each CU's trade BO map (one BO per <=1 GiB chunk),
//             market.bin straight into each CU's market BO map (+4 parameter words).
//   stage 1 : one host thread per CU: sync trades+market -> ONE kernel call per chunk -> sync moments
//   select  : host finalises (double) and picks top-K (deterministic, shared with the CPU baseline)
//   stage 2 : selected records gathered into each CU's stage-2 BO (+ global ids), ONE call per CU
// Only two kernel calls per CU in total for the paper workload (no per-batch round trips).
#include <atomic>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>
#include "bench_common.hpp"
#include "pf_io.hpp"
#include "pf_kernel.h"
#include "pf_verify.hpp"

#ifndef PF_SIM_CUS
#define PF_SIM_CUS 2
#endif
static const char* KNAME = "pf_kernel";

using namespace pf;

struct Chunk { uint32_t first = 0, count = 0; xrt::bo bo; Trade* map = nullptr; };

struct CU {
    xrt::kernel k;
    std::vector<Chunk> chunks;          // stage-1 trade slices (each <= max BO size)
    xrt::bo bo_mkt, bo_out, bo_tr2, bo_ids;
    float* mkt = nullptr;
    uint64_t* out = nullptr;
    Trade* tr2 = nullptr;
    uint32_t* ids = nullptr;
    uint32_t k2_first = 0, k2_count = 0;   // stage-2 slice of the selection
    double h2d = 0, ker = 0, d2h = 0;
    uint32_t calls = 0;
    bool mkt_on_dev = false;
};

static uint32_t round_il(uint64_t x) { return (uint32_t)std::min<uint64_t>(0xFFFFFF80u, (x + PF_IL - 1) / PF_IL * PF_IL); }

// paths per lane job: whole trade when there are enough trades to feed every lane,
// otherwise split trades so that all lanes are busy (never more than PF_LANES jobs per trade)
static uint32_t choose_chunk(uint64_t ntr, uint32_t P) {
    if (ntr >= PF_LANES || P <= PF_IL) return round_il(P);
    uint64_t nsub = std::min<uint64_t>((PF_LANES + ntr - 1) / ntr, (P + PF_IL - 1) / PF_IL);
    uint32_t c = round_il((P + nsub - 1) / nsub);
    while ((P + (uint64_t)c - 1) / c > PF_LANES) c += PF_IL;
    return c;
}

static void run_call(CU& cu, xrt::bo& trades, uint32_t n, uint32_t use_ids, uint32_t id_base,
                     const RunParams& rp, uint32_t chunk, bool sync_mkt) {
    double t0 = bench::now_s();
    trades.sync(XCL_BO_SYNC_BO_TO_DEVICE, std::max<size_t>(1, (size_t)n * sizeof(Trade)), 0);
    if (use_ids) cu.bo_ids.sync(XCL_BO_SYNC_BO_TO_DEVICE, (size_t)n * 4, 0);
    if (sync_mkt || !cu.mkt_on_dev) {
        cu.bo_mkt.sync(XCL_BO_SYNC_BO_TO_DEVICE, (PF_MARKET_WORDS + PF_PARAM_WORDS) * 4, 0);
        cu.mkt_on_dev = true;
    }
    double t1 = bench::now_s();
    xrt::run run(cu.k);
    run.set_arg(PF_A_TRADES_F, trades);
    run.set_arg(PF_A_TRADES_I, trades);
    run.set_arg(PF_A_IDS, cu.bo_ids);
    run.set_arg(PF_A_MARKET, cu.bo_mkt);
    run.set_arg(PF_A_OUT, cu.bo_out);
    run.set_arg(PF_A_N_TRADES, n);
    run.set_arg(PF_A_N_IDS, use_ids ? n : 0u);
    run.set_arg(PF_A_ID_BASE, id_base);
    run.set_arg(PF_A_USE_IDS, use_ids);
    run.set_arg(PF_A_N_UNDER, rp.n_under);
    run.set_arg(PF_A_N_PATHS, rp.n_paths);
    run.set_arg(PF_A_N_STEPS, rp.n_steps);
    run.set_arg(PF_A_CHUNK, chunk);
    run.set_arg(PF_A_SEED, rp.seed);
    run.set_arg(PF_A_STAGE, rp.stage);
    run.set_arg(PF_A_FLAGS, rp.ft);
    run.start();
    run.wait2();
    double t2 = bench::now_s();
    if (n) cu.bo_out.sync(XCL_BO_SYNC_BO_FROM_DEVICE, (size_t)n * sizeof(Moments), 0);
    double t3 = bench::now_s();
    cu.h2d += t1 - t0; cu.ker += t2 - t1; cu.d2h += t3 - t2; cu.calls++;
}

static void collect(CU& cu, uint32_t n, uint32_t first_slot, Mom* dst, const uint32_t* expect_ids, uint32_t id_base,
                    std::atomic<long>& bad_tags) {
    for (uint32_t j = 0; j < n; ++j) {
        Moments r;
        std::memcpy(&r, cu.out + (size_t)j * PF_MOM_WORDS, sizeof r);
        uint32_t want = expect_ids ? expect_ids[j] : id_base + j;
        if (r.tag != ((1ull << 63) | want)) bad_tags++;
        dst[first_slot + j] = from_record(r);
    }
}

int main(int argc, char** argv) {
    double t_main = bench::now_s();
    bench::Args a(argc, argv);
    if (a.has("help")) {
        std::printf("pf_fpga - FPGA host, two-stage Heston MC portfolio (v2)\n"
                    "  --xclbin FILE         [required]\n  --device N            (default 0)\n"
                    "  --cus K               use at most K compute units (default: all found%s)\n"
                    "  --max-bo-mb M         largest single BO (default 1024)\n%s",
#ifdef XRT_SIM
                    ", xrt_sim default 2",
#else
                    "",
#endif
                    pf::workload_usage());
        return 0;
    }
    bench::Report R;
    int ok = 1;
    try {
        Workload w = parse_workload(a);
        std::string xclbin = a.str("xclbin");
        if (xclbin.empty()) throw std::runtime_error("--xclbin is required");
        int devidx = (int)a.i64("device", 0);
        size_t max_bo = (size_t)(a.f64("max-bo-mb", 1024.0) * 1048576.0);

        // ---------------- device + CUs
        double tx = bench::now_s();
        xrt::device dev(devidx);
        auto uuid = dev.load_xclbin(xclbin);
        int want = (int)a.i64("cus", 32);
        std::vector<CU> cus;
#ifdef XRT_SIM
        want = (int)a.i64("cus", PF_SIM_CUS);
#endif
        for (int i = 1; i <= want && i <= 32; ++i) {
            try {
                std::string nm = std::string(KNAME) + ":{" + KNAME + "_" + std::to_string(i) + "}";
                CU c;
                c.k = xrt::kernel(dev, uuid, nm, xrt::kernel::cu_access_mode::exclusive);
                cus.push_back(std::move(c));
            } catch (const std::exception&) {
                break;
            }
        }
        if (cus.empty()) throw std::runtime_error("no pf_kernel compute unit found in " + xclbin);
        uint32_t ncu = (uint32_t)cus.size();
        R.set("t_xclbin_s", bench::now_s() - tx);

        // ---------------- plan + allocate (once)
        double ta = bench::now_s();
        uint32_t n = portfolio_count(w.portfolio);
        if (n > (1u << 26) * ncu) throw std::runtime_error("too many trades (max 2^26 per CU)");
        uint32_t k = std::min(w.topk, n);
        uint32_t per_chunk_max = (uint32_t)std::max<size_t>(1, std::min<size_t>(max_bo / sizeof(Trade), 1u << 26));
        uint32_t max_out = 1;
        std::vector<const Trade*> rec(n);
        try {
            for (uint32_t c = 0; c < ncu; ++c) {
                CU& cu = cus[c];
                uint32_t f = (uint32_t)((uint64_t)n * c / ncu), l = (uint32_t)((uint64_t)n * (c + 1) / ncu);
                for (uint32_t s = f; s < l; s += per_chunk_max) {
                    Chunk ch;
                    ch.first = s;
                    ch.count = std::min(per_chunk_max, l - s);
                    ch.bo = xrt::bo(dev, std::max<size_t>(1, (size_t)ch.count * sizeof(Trade)), xrt::bo::flags::normal,
                                    cu.k.group_id(PF_A_TRADES_F));
                    ch.map = ch.bo.map<Trade*>();
                    max_out = std::max(max_out, ch.count);
                    cu.chunks.push_back(ch);
                }
                cu.k2_first = (uint32_t)((uint64_t)k * c / ncu);
                cu.k2_count = (uint32_t)((uint64_t)k * (c + 1) / ncu) - cu.k2_first;
                max_out = std::max(max_out, cu.k2_count);
                cu.bo_tr2 = xrt::bo(dev, std::max<size_t>(1, (size_t)cu.k2_count * sizeof(Trade)), xrt::bo::flags::normal,
                                    cu.k.group_id(PF_A_TRADES_F));
                cu.bo_ids = xrt::bo(dev, std::max<size_t>(4, (size_t)cu.k2_count * 4), xrt::bo::flags::normal,
                                    cu.k.group_id(PF_A_IDS));
                cu.bo_mkt = xrt::bo(dev, (PF_MARKET_WORDS + PF_PARAM_WORDS) * 4, xrt::bo::flags::normal,
                                    cu.k.group_id(PF_A_MARKET));
                cu.tr2 = cu.bo_tr2.map<Trade*>();
                cu.ids = cu.bo_ids.map<uint32_t*>();
                cu.mkt = cu.bo_mkt.map<float*>();
            }
            for (auto& cu : cus) {
                cu.bo_out = xrt::bo(dev, (size_t)max_out * sizeof(Moments), xrt::bo::flags::normal, cu.k.group_id(PF_A_OUT));
                cu.out = cu.bo_out.map<uint64_t*>();
            }
        } catch (const std::exception& e) {
            throw std::runtime_error(std::string("device buffer allocation failed (reduce --max-bo-mb or --cus): ") + e.what());
        }
        std::vector<Mom> mom1(n), mom2(k);
        std::vector<Result> res1(n), res2(k), final_res(n);
        std::vector<uint32_t> ids1(n);
        R.set("t_alloc_s", bench::now_s() - ta);

        // ---------------- read directly into BO maps
        double tr = bench::now_s();
        for (auto& cu : cus) {
            for (auto& ch : cu.chunks) {
                read_trades(w.portfolio, ch.first, ch.count, ch.map);
                for (uint32_t j = 0; j < ch.count; ++j) rec[ch.first + j] = ch.map + j;
            }
            read_market(w.market, cu.mkt);
            cu.mkt[PF_MARKET_WORDS] = w.dspot;
            cu.mkt[PF_MARKET_WORDS + 1] = w.drate;
            cu.mkt[PF_MARKET_WORDS + 2] = w.volscale;
            cu.mkt[PF_MARKET_WORDS + 3] = 0.0f;
        }
        uint32_t nu = 1;
        for (auto& cu : cus)
            for (auto& ch : cu.chunks) nu = n_under_of(ch.map, ch.count, nu);
        for (uint32_t i = 0; i < n; ++i) ids1[i] = i;
        R.set("t_read_s", bench::now_s() - tr);
        const float* mkt = cus[0].mkt;
        RunParams rp1 = stage_params(w, 1, nu), rp2 = stage_params(w, 2, nu);
        std::vector<uint32_t> sel;
        std::atomic<long> bad_tags{0};

        // ---------------- measured window
        bench::mark("start");
        double tc0 = bench::now_s();
        {
            std::vector<std::thread> th;
            for (auto& cu : cus)
                th.emplace_back([&, pc = &cu] {
                    bool first = true;
                    for (auto& ch : pc->chunks) {
                        run_call(*pc, ch.bo, ch.count, 0, ch.first, rp1, choose_chunk(ch.count, rp1.n_paths), first);
                        first = false;
                        collect(*pc, ch.count, ch.first, mom1.data(), nullptr, ch.first, bad_tags);
                    }
                });
            for (auto& t : th) t.join();
        }
        for (uint32_t i = 0; i < n; ++i) res1[i] = finalize(*rec[i], mkt, rp1, mom1[i]);
        double tc1 = bench::now_s();
        sel = select_topk(res1.data(), n, k);
        double tc2 = bench::now_s();
        {
            std::vector<std::thread> th;
            for (auto& cu : cus)
                th.emplace_back([&, pc = &cu] {
                    CU& c = *pc;
                    for (uint32_t j = 0; j < c.k2_count; ++j) {
                        uint32_t g = sel[c.k2_first + j];
                        std::memcpy(c.tr2 + j, rec[g], sizeof(Trade));
                        c.ids[j] = g;
                    }
                    if (c.k2_count == 0) return;
                    run_call(c, c.bo_tr2, c.k2_count, 1, 0, rp2, choose_chunk(c.k2_count, rp2.n_paths), false);
                    collect(c, c.k2_count, c.k2_first, mom2.data(), sel.data() + c.k2_first, 0, bad_tags);
                });
            for (auto& t : th) t.join();
        }
        for (uint32_t j = 0; j < k; ++j) res2[j] = finalize(*rec[sel[j]], mkt, rp2, mom2[j]);
        std::memcpy(final_res.data(), res1.data(), (size_t)n * sizeof(Result));
        for (uint32_t j = 0; j < k; ++j) final_res[sel[j]] = res2[j];
        double tc3 = bench::now_s();
        bench::mark("end");

        double h2d = 0, ker = 0, d2h = 0;
        unsigned calls = 0;
        for (auto& cu : cus) { h2d += cu.h2d; ker += cu.ker; d2h += cu.d2h; calls += cu.calls; }
        R.set("platform", "fpga");
        R.set("impl", "pf_v2_L" + std::to_string(PF_LANES) + "_IL" + std::to_string(PF_IL));
#ifdef XRT_SIM
        R.set("xrt", "sim");
#endif
        R.set("n_cu", (unsigned)ncu);
        R.set("lanes_per_cu", PF_LANES);
        R.set("il", (unsigned)PF_IL);
        R.set("kernel_calls", calls);
        report_workload(R, w, n, nu, k);
        double steps = total_steps(n, w.paths1, w.steps1, k, w.paths2, w.steps2);
        double win = tc3 - tc0;
        R.set("t_accel_window_s", win);
        R.set("t_compute_s", win);
        R.set("t_h2d_s", h2d);                 // summed over CU threads
        R.set("t_kernel_s", ker);
        R.set("t_d2h_s", d2h);
        R.set("t_stage1_s", tc1 - tc0);
        R.set("t_select_s", tc2 - tc1);
        R.set("t_stage2_s", tc3 - tc2);
        R.set("msteps_per_s", steps / win / 1e6);
        R.set("msteps_per_s_kernel", steps / (ker / ncu) / 1e6);
        R.set("checksum", bench::hex64(results_checksum(final_res.data(), n)));
        R.set("price_sum", price_sum(final_res.data(), n));
        R.set("bad_tags", bad_tags.load());
        if (bad_tags.load()) ok = 0;

        double tw = bench::now_s();
        if (!w.out.empty()) write_results(w.out, final_res.data(), n);
        if (!w.out_stage1.empty()) write_results(w.out_stage1, res1.data(), n);
        if (!w.out_topk.empty()) write_topk(w.out_topk, sel, res2);
        R.set("t_write_s", bench::now_s() - tw);

        if (w.verify) {
            double tv = bench::now_s();
            std::vector<const Trade*> rec2(k);
            for (uint32_t j = 0; j < k; ++j) rec2[j] = rec[sel[j]];
            auto s1 = verify_sample(n, w.verify_n, w.verify);
            auto s2 = verify_sample(k, w.verify == 2 ? k : std::max<uint32_t>(1, w.verify_n / 16), w.verify);
            long bad = verify_moments(s1, rec.data(), ids1.data(), mom1.data(), mkt, rp1);
            bad += verify_moments(s2, rec2.data(), sel.data(), mom2.data(), mkt, rp2);
            bad += (select_topk(res1.data(), n, k) != sel);
            R.set("verify_trades", (unsigned long)(s1.size() + s2.size()));
            R.set("verify_mismatch", bad);
            R.set("t_verify_s", bench::now_s() - tv);
            if (bad) ok = 0;
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "pf_fpga: error: %s\n", e.what());
        R.set("project", "portfolio"); R.set("platform", "fpga"); R.set("error", e.what());
        ok = 0;
    }
    R.set("ok", ok);
    R.set("t_total_s", bench::now_s() - t_main);
    R.print();
    return ok ? 0 : 1;
}
