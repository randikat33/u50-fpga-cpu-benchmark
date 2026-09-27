// mc_fpga.cpp - v2 FPGA host for the Monte Carlo Heston basket kernel (XRT native C++ API).
//
// Antithetic pairs [0, pairs) are split into contiguous ranges, one per CU; each CU splits its
// range across its lanes internally. All CUs run concurrently (async xrt::run). Buffers are
// allocated once; only the used bytes are synced (96 floats in, 8 x uint64 out per CU).
// The moments are exact integers, so the price is bit-identical to cpu/mc_cpu for the same
// --paths/--steps/--seed, independent of the CU/lane count.
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <cstdio>
#include <string>
#include <thread>
#include <vector>
#include "bench_common.hpp"
#include "mc_cli.hpp"
#include "mc_model.hpp"

#ifndef MC_SIM_CUS
#define MC_SIM_CUS 2
#endif
static const char* KNAME = "mc_heston_v2";
enum { A_FPAR = 0, A_OUT, A_BASE, A_NPAIRS, A_STEPS, A_FLAGS, A_KEYLO, A_KEYHI };

struct Cu {
    xrt::kernel k;
    xrt::bo fpar, out;
    xrt::run run;
    uint64_t base = 0, n = 0;
};

struct Phases { double h2d = 0, kernel = 0, d2h = 0, window = 0; mc::Mom mom; uint32_t lanes = 0, il = 0; };

static Phases run_all(std::vector<Cu>& cus, uint32_t steps, uint32_t flags, const mc::Keys& keys) {
    Phases p;
    double t0 = bench::now_s();
    for (auto& c : cus) c.fpar.sync(XCL_BO_SYNC_BO_TO_DEVICE, MC_FPAR_N * sizeof(float), 0);
    double t1 = bench::now_s();
    for (auto& c : cus) {
        c.run.set_arg(A_FPAR, c.fpar);
        c.run.set_arg(A_OUT, c.out);
        c.run.set_arg(A_BASE, (uint64_t)c.base);
        c.run.set_arg(A_NPAIRS, (uint64_t)c.n);
        c.run.set_arg(A_STEPS, (uint32_t)steps);
        c.run.set_arg(A_FLAGS, (uint32_t)flags);
        c.run.set_arg(A_KEYLO, (uint64_t)mc::keys_lo(keys));
        c.run.set_arg(A_KEYHI, (uint64_t)mc::keys_hi(keys));
        c.run.start();
    }
    for (auto& c : cus) c.run.wait();
    double t2 = bench::now_s();
    for (auto& c : cus) c.out.sync(XCL_BO_SYNC_BO_FROM_DEVICE, 8 * sizeof(uint64_t), 0);
    double t3 = bench::now_s();
    for (auto& c : cus) {
        const uint64_t* w = c.out.map<uint64_t*>();
        mc::Mom m = mc::mom_from_words(w);
        if (m.pairs != c.n) throw std::runtime_error("kernel returned a wrong pair count");
        p.mom.add(m);
        p.lanes = (uint32_t)((w[mc::O_INFO] >> 32) & 0xFF);
        p.il = (uint32_t)((w[mc::O_INFO] >> 40) & 0xFFFF);
    }
    p.h2d = t1 - t0; p.kernel = t2 - t1; p.d2h = t3 - t2; p.window = t3 - t0;
    return p;
}

static mc::Mom reference(const float* fp, const mc::Keys& k, uint32_t M, uint64_t pairs, bool put) {
    unsigned nt = std::max(1u, std::thread::hardware_concurrency());
    std::vector<mc::Mom> part(nt);
    std::vector<std::thread> th;
    for (unsigned t = 0; t < nt; ++t)
        th.emplace_back([&, t] { mc::sim_range(fp, k, M, pairs * t / nt, pairs * (t + 1) / nt, put, part[t]); });
    mc::Mom m;
    for (unsigned t = 0; t < nt; ++t) { th[t].join(); m.add(part[t]); }
    return m;
}

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h") || !A.has("xclbin")) {
        std::printf("usage: %s --xclbin PATH [--device N] [--cus K] [workload options]\n%s"
                    "  --cus K        use at most K compute units (default: all found)\n", argv[0], mc::workload_help());
        return A.has("xclbin") || A.has("help") || A.has("h") ? 0 : 1;
    }
    int rc = 0;
    try {
        mc::Workload w = mc::parse_workload(A);
        R.set("project", "mc_heston");
        R.set("platform", "fpga");
        mc::report_workload(R, w);

        double t0 = bench::now_s();
        xrt::device dev((unsigned int)A.i64("device", 0));
        auto uuid = dev.load_xclbin(A.str("xclbin"));
        R.set("t_xclbin_s", bench::now_s() - t0);

        // compute units: probe <top>:{<top>_i} until one fails (xrt_sim: --cus, default MC_SIM_CUS)
        int want = (int)A.i64("cus", 16);
        std::vector<Cu> cus;
#ifdef XRT_SIM
        int ncu_avail = (int)A.i64("cus", MC_SIM_CUS);
#else
        int ncu_avail = 16;
#endif
        for (int i = 1; i <= std::min(want, ncu_avail); ++i) {
            std::string nm = std::string(KNAME) + ":{" + KNAME + "_" + std::to_string(i) + "}";
            try {
                Cu c;
                c.k = xrt::kernel(dev, uuid, nm, xrt::kernel::cu_access_mode::exclusive);
                cus.push_back(std::move(c));
            } catch (const std::exception&) {
                break;
            }
        }
        if (cus.empty()) throw std::runtime_error("no compute unit named " + std::string(KNAME) + " in the xclbin");
        R.set("n_cu", (int)cus.size());

        t0 = bench::now_s();
        for (auto& c : cus) {
            try {
                c.fpar = xrt::bo(dev, 4096, xrt::bo::flags::normal, c.k.group_id(A_FPAR));
                c.out = xrt::bo(dev, 4096, xrt::bo::flags::normal, c.k.group_id(A_OUT));
            } catch (const std::exception& e) {
                throw std::runtime_error(std::string("buffer allocation failed (HBM bank full or not connected?): ") + e.what());
            }
            c.run = xrt::run(c.k);
        }
        R.set("t_alloc_s", bench::now_s() - t0);

        // "input": the parameter block, generated directly into the mapped buffers
        t0 = bench::now_s();
        float fp[MC_FPAR_N];
        mc::make_fpar(w.mk, w.steps, fp);
        for (auto& c : cus) std::memcpy(c.fpar.map<float*>(), fp, sizeof fp);
        mc::Keys keys = mc::run_keys(w.seed);
        uint64_t ncu = cus.size();
        for (uint64_t i = 0; i < ncu; ++i) {
            cus[i].base = w.pairs * i / ncu;
            cus[i].n = w.pairs * (i + 1) / ncu - cus[i].base;
        }
        R.set("t_read_s", bench::now_s() - t0);
        uint32_t flags = w.mk.put ? 1 : 0;

        Phases res;
        for (int i = 0; i < w.warmup; ++i) res = run_all(cus, w.steps, flags, keys);
        std::vector<Phases> ph;
        bench::mark("start");
        for (int i = 0; i < w.runs; ++i) ph.push_back(run_all(cus, w.steps, flags, keys));
        bench::mark("end");
        std::vector<double> win;
        for (auto& p : ph) win.push_back(p.window);
        double med = mc::median(win);
        size_t mi = 0;
        for (size_t i = 0; i < ph.size(); ++i) if (std::fabs(ph[i].window - med) < std::fabs(ph[mi].window - med)) mi = i;
        res = ph[mi];
        R.set("impl", "fpga_l" + std::to_string(res.lanes) + "_cu" + std::to_string(cus.size()));
        R.set("lanes", (unsigned)res.lanes);
        R.set("il", (unsigned)res.il);
        R.set("t_h2d_s", res.h2d);
        R.set("t_kernel_s", res.kernel);
        R.set("t_d2h_s", res.d2h);
        R.set("t_compute_s", res.window);          // one window: h2d + kernel + d2h (median run)
        R.set("t_compute_min_s", *std::min_element(win.begin(), win.end()));
        double psteps = 2.0 * w.pairs * w.steps;
        R.set("msteps_per_s", psteps / res.window / 1e6);
        R.set("msteps_per_s_kernel", psteps / res.kernel / 1e6);
        R.set("paths_per_s", 2.0 * w.pairs / res.window);
        mc::report_price(R, w, res.mom);

        int ok = res.mom.ovf == 0 ? 1 : 0;
        for (auto& p : ph) ok = ok && p.mom == res.mom;   // repeat runs must agree exactly
        if (A.has("verify")) {
            uint64_t vp = w.pairs * w.steps <= (1ull << 24) ? w.pairs : std::min<uint64_t>(w.pairs, 65536);
            if (A.has("verify-pairs")) vp = std::min<uint64_t>(w.pairs, (uint64_t)A.i64("verify-pairs", 65536));
            mc::Mom dev_m = res.mom;
            if (vp != w.pairs) {   // extra kernel call on CU 1 over the first vp pairs
                std::vector<Cu> one(1);
                one[0] = cus[0];
                one[0].base = 0;
                one[0].n = vp;
                dev_m = run_all(one, w.steps, flags, keys).mom;
            }
            mc::Mom ref = reference(fp, keys, w.steps, vp, w.mk.put);
            bool same = ref == dev_m;
            R.set("verify_pairs", (unsigned long long)vp);
            R.set("verify_bit_identical", same ? 1 : 0);
            ok = ok && same;
        }
        R.set("ok", ok);
        t0 = bench::now_s();
        mc::append_json(A.str("out"), R);
        R.set("t_write_s", A.has("out") ? bench::now_s() - t0 : 0.0);
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
