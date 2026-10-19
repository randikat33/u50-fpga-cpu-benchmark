// hq_fpga.cpp - FPGA host of the paper-2 Heston kernels (XRT native C++ API), same structure as
// paper 1's hosts (08_MC_Asian/host/asian_fpga.cpp). Command line and RESULT keys as build/hq_cpu,
// so scripts/hqlib.py reads both (impl=fpga, integer moments s1_k/sp_k).
//
// Pairs [a0, a1) are split into contiguous ranges, one per CU; each CU splits its range across its
// lanes. The moments are exact integers: the result is bit-identical to hq_cpu --impl scalar|avx512
// for the same case, scheme, steps, pairs and seed, whatever the CU or lane count.
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>
#include "bench_common.hpp"
#include "hq_model.hpp"
#include "mc_cli.hpp"      // mc::median, mc::append_json

#ifndef HQ_SIM_CUS
#define HQ_SIM_CUS 1
#endif
enum { A_FPAR = 0, A_OUT, A_BASE, A_NPAIRS, A_STEPS, A_KEYLO, A_KEYHI };

struct Cu {
    xrt::kernel k;
    xrt::bo fpar, out;
    xrt::run run;
    uint64_t base = 0, n = 0;
};
struct Phases { double h2d = 0, kernel = 0, d2h = 0, window = 0; hq::Mom mom; uint32_t lanes = 0, il = 0, scheme = 9; };

static Phases run_all(std::vector<Cu>& cus, uint32_t steps, const mc::Keys& keys) {
    Phases p;
    double t0 = bench::now_s();
    for (auto& c : cus) c.fpar.sync(XCL_BO_SYNC_BO_TO_DEVICE, HQ_FPAR_N * sizeof(float), 0);
    double t1 = bench::now_s();
    for (auto& c : cus) {
        c.run.set_arg(A_FPAR, c.fpar);
        c.run.set_arg(A_OUT, c.out);
        c.run.set_arg(A_BASE, (uint64_t)c.base);
        c.run.set_arg(A_NPAIRS, (uint64_t)c.n);
        c.run.set_arg(A_STEPS, (uint32_t)steps);
        c.run.set_arg(A_KEYLO, (uint64_t)mc::keys_lo(keys));
        c.run.set_arg(A_KEYHI, (uint64_t)mc::keys_hi(keys));
        c.run.start();
    }
    for (auto& c : cus) c.run.wait();
    double t2 = bench::now_s();
    for (auto& c : cus) c.out.sync(XCL_BO_SYNC_BO_FROM_DEVICE, HQ_OUT_N * sizeof(uint64_t), 0);
    double t3 = bench::now_s();
    for (auto& c : cus) {
        const uint64_t* w = c.out.map<uint64_t*>();
        hq::Mom m = hq::mom_from_words(w);
        if (m.pairs != c.n) throw std::runtime_error("kernel returned a wrong pair count");
        p.mom.add(m);
        p.lanes = (uint32_t)((w[HQ_W_INFO] >> 32) & 0xFF);
        p.il = (uint32_t)((w[HQ_W_INFO] >> 40) & 0xFFFF);
        p.scheme = (uint32_t)(w[HQ_W_INFO] >> 56);
    }
    p.h2d = t1 - t0; p.kernel = t2 - t1; p.d2h = t3 - t2; p.window = t3 - t0;
    return p;
}

static hq::Mom reference(const float* fp, hq::Scheme sc, const mc::Keys& k, uint32_t M, uint64_t a0, uint64_t a1) {
    unsigned nt = std::max(1u, std::thread::hardware_concurrency());
    std::vector<hq::Mom> part(nt);
    std::vector<std::thread> th;
    uint64_t n = a1 - a0;
    for (unsigned t = 0; t < nt; ++t)
        th.emplace_back([&, t] { hq::sim_range(fp, sc, k, M, a0 + n * t / nt, a0 + n * (t + 1) / nt, part[t]); });
    hq::Mom m;
    for (unsigned t = 0; t < nt; ++t) { th[t].join(); m.add(part[t]); }
    return m;
}

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h") || !A.has("xclbin")) {
        std::printf("usage: %s --xclbin PATH --case I|II|III|P1 --scheme ft|qe (--steps-per-year S | --steps M)\n"
                    "          [--a0 A] (--a1 B | --pairs N) [--seed S] [--runs R] [--warmup W] [--warmup-s S] [--window-s S] [--cus K]\n"
                    "          [--verify [--verify-pairs N]] [--out FILE] [--device N]\n", argv[0]);
        return A.has("xclbin") || A.has("help") || A.has("h") ? 0 : 1;
    }
    int rc = 0;
    try {
        hq::Case c = hq::get_case(A.str("case", "I"));
        std::string scs = A.str("scheme", "qe");
        if (scs != "ft" && scs != "qe") throw std::runtime_error("--scheme must be ft or qe");
        hq::Scheme sc = scs == "ft" ? hq::FT : hq::QE;
        const std::string kname = sc == hq::FT ? "hq_ft" : "hq_qe";
        long long M = A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * c.T);
        if (M < 1 || M > HQ_M_MAX) throw std::runtime_error("number of steps must be 1..65535");
        uint64_t a0 = (uint64_t)A.i64("a0", 0);
        uint64_t a1 = A.has("a1") ? (uint64_t)A.i64("a1", 0) : a0 + (uint64_t)A.i64("pairs", 65536);
        if (a1 <= a0 || a1 > (1ull << 32)) throw std::runtime_error("need a0 < a1 <= 2^32");
        uint64_t seed = (uint64_t)A.i64("seed", 20261011);
        int runs = std::max<int>(1, (int)A.i64("runs", 1)), warm = std::max<int>(0, (int)A.i64("warmup", 1));
        double warm_s = A.f64("warmup-s", 0.0), win_s = A.f64("window-s", 0.0);

        R.set("project", "heston_accuracy");
        R.set("platform", "fpga");
        R.set("impl", "fpga");
        R.set("case", c.name);
        R.set("scheme", scs);
        R.set("steps", (long long)M);
        R.set("steps_per_year", (double)M / c.T);
        R.set("a0", (unsigned long long)a0);
        R.set("a1", (unsigned long long)a1);
        R.set("pairs", (unsigned long long)(a1 - a0));
        R.set("seed", (unsigned long long)seed);

        double t0 = bench::now_s();
        xrt::device dev((unsigned int)A.i64("device", 0));
        auto uuid = dev.load_xclbin(A.str("xclbin"));
        R.set("t_xclbin_s", bench::now_s() - t0);

        int want = (int)A.i64("cus", 16);
        std::vector<Cu> cus;
#ifdef XRT_SIM
        int ncu_avail = (int)A.i64("cus", HQ_SIM_CUS);
#else
        int ncu_avail = 16;
#endif
        for (int i = 1; i <= std::min(want, ncu_avail); ++i) {
            std::string nm = kname + ":{" + kname + "_" + std::to_string(i) + "}";
            try {
                Cu cu;
                cu.k = xrt::kernel(dev, uuid, nm, xrt::kernel::cu_access_mode::exclusive);
                cus.push_back(std::move(cu));
            } catch (const std::exception&) {
                break;
            }
        }
        if (cus.empty()) throw std::runtime_error("no compute unit named " + kname + " in the xclbin (wrong --scheme?)");
        R.set("n_cu", (int)cus.size());

        t0 = bench::now_s();
        for (auto& cu : cus) {
            cu.fpar = xrt::bo(dev, 4096, xrt::bo::flags::normal, cu.k.group_id(A_FPAR));
            cu.out = xrt::bo(dev, 4096, xrt::bo::flags::normal, cu.k.group_id(A_OUT));
            cu.run = xrt::run(cu.k);
        }
        R.set("t_alloc_s", bench::now_s() - t0);

        float fp[HQ_FPAR_N];
        hq::make_fpar(c, (uint32_t)M, fp);
        for (auto& cu : cus) std::memcpy(cu.fpar.map<float*>(), fp, sizeof fp);
        mc::Keys keys = mc::run_keys(seed);
        uint64_t n = a1 - a0, ncu = cus.size();
        for (uint64_t i = 0; i < ncu; ++i) {
            cus[i].base = a0 + n * i / ncu;
            cus[i].n = a0 + n * (i + 1) / ncu - cus[i].base;
        }

        // steady-clock protocol, as hq_cpu: untimed calls (>= --warmup and >= --warmup-s seconds), then timed
        // calls between the MARK lines (>= --runs and >= --window-s seconds); each call is one complete pricing run
        Phases res;
        const double tw = bench::now_s();
        for (int i = 0; i < warm || bench::now_s() - tw < warm_s; ++i) res = run_all(cus, (uint32_t)M, keys);
        std::vector<Phases> ph;
        bench::mark("start");
        const double tw0 = bench::now_s();
        for (int i = 0; i < runs || bench::now_s() - tw0 < win_s; ++i) ph.push_back(run_all(cus, (uint32_t)M, keys));
        const double twin = bench::now_s() - tw0;
        bench::mark("end");
        R.set("runs", (long long)ph.size());
        R.set("t_window_s", twin);
        R.set("t_per_run_s", twin / (double)ph.size());
        std::vector<double> win;
        for (auto& p : ph) win.push_back(p.window);
        double med = mc::median(win);
        size_t mi = 0;
        for (size_t i = 0; i < ph.size(); ++i) if (std::fabs(ph[i].window - med) < std::fabs(ph[mi].window - med)) mi = i;
        res = ph[mi];
        if (res.scheme != (uint32_t)sc) throw std::runtime_error("the xclbin's kernel runs the other scheme");
        R.set("lanes", (unsigned)res.lanes);
        R.set("il", (unsigned)res.il);
        R.set("t_h2d_s", res.h2d);
        R.set("t_kernel_s", res.kernel);
        R.set("t_d2h_s", res.d2h);
        R.set("t_compute_s", res.window);
        R.set("t_compute_min_s", *std::min_element(win.begin(), win.end()));
        double psteps = 2.0 * (double)n * (double)M;
        R.set("path_steps", psteps);
        R.set("gsteps_per_s", psteps / res.window / 1e9);
        R.set("gsteps_per_s_kernel", psteps / res.kernel / 1e9);

        char b[96];
        for (int j = 0; j < HQ_NK; ++j) {
            hq::Price p = hq::finalize_k(c, res.mom, j);
            std::string s = std::to_string(j);
            std::snprintf(b, sizeof b, "%.0f", c.K[j]); R.set("K" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.price); R.set("price" + s, b);   // call (= pv + S0 - K e^-rT for a put strike)
            std::snprintf(b, sizeof b, "%.10f", p.pv); R.set("pv" + s, b);
            R.set("form" + s, ((c.puts >> j) & 1u) ? "put" : "call");
            std::snprintf(b, sizeof b, "%.10f", p.se); R.set("se" + s, b);
            std::snprintf(b, sizeof b, "%.6f", c.ref[j]); R.set("ref" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.price - c.ref[j]); R.set("bias" + s, b);
            R.set("s1_" + s, hq::u128_hex(res.mom.s1[j]));
            R.set("sp_" + s, hq::u128_hex(res.mom.sp[j]));
        }
        R.set("payoff_overflows", (unsigned long long)res.mom.ovf);
        R.set("mom_hash", "h" + bench::hex64(hq::mom_hash(res.mom)));

        int ok = res.mom.ovf == 0 ? 1 : 0;
        for (auto& p : ph) ok = ok && p.mom == res.mom;   // repeated runs must agree exactly
        if (A.has("verify")) {
            uint64_t vn = n * (uint64_t)M <= (1ull << 24) ? n : std::min<uint64_t>(n, 65536);
            if (A.has("verify-pairs")) vn = std::min<uint64_t>(n, (uint64_t)A.i64("verify-pairs", 65536));
            hq::Mom dev_m = res.mom;
            if (vn != n) {   // extra kernel call on CU 1 over the first vn pairs
                std::vector<Cu> one(1);
                one[0] = cus[0];
                one[0].base = a0;
                one[0].n = vn;
                dev_m = run_all(one, (uint32_t)M, keys).mom;
            }
            hq::Mom ref = reference(fp, sc, keys, (uint32_t)M, a0, a0 + vn);
            bool same = ref == dev_m;
            R.set("verify_pairs", (unsigned long long)vn);
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
