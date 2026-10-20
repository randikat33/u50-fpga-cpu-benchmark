// hs_fpga.cpp - FPGA host of the secondary-case kernels of paper 2 (protocol v0.5), XRT native C++ API.
//   --case B --scheme ft|qe : basket kernels hb_ft / hb_qe (kernel/hb_kernel.cpp)
//   --case A                : Asian kernel ha_asian (kernel/ha_kernel.cpp)
// Same structure, command line and RESULT keys as host/hq_fpga.cpp and build/hs_cpu, so the scripts read
// all of them. Pairs [a0, a1) are split into contiguous ranges, one per CU; the moments are exact
// integers, bit-identical to hs_cpu --impl scalar|avx512 for the same arguments.
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>
#include "bench_common.hpp"
#include "wait_mode.hpp"
#include "hb_model.hpp"
#include "ha_model.hpp"
#include "mc_cli.hpp"

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

// the two kernel families
struct BasketK {
    using Mom = hq::Mom;
    static constexpr int FPAR_N = HB_FPAR_N, OUT_N = HQ_OUT_N, W_INFO = HQ_W_INFO;
    static Mom from_words(const uint64_t* w) { return hq::mom_from_words(w); }
    static uint64_t hash(const Mom& m) { return hq::mom_hash(m); }
};
struct AsianK {
    using Mom = ha::AMom;
    static constexpr int FPAR_N = HA_FPAR_N, OUT_N = HA_OUT_N, W_INFO = HA_W_INFO;
    static Mom from_words(const uint64_t* w) { return ha::mom_from_words(w); }
    static uint64_t hash(const Mom& m) { return ha::mom_hash(m); }
};

template <class KF>
struct Phases { double h2d = 0, kernel = 0, d2h = 0, window = 0; typename KF::Mom mom; uint32_t lanes = 0, il = 0, kind = 999; };

template <class KF>
static Phases<KF> run_all(std::vector<Cu>& cus, uint32_t steps, const mc::Keys& keys) {
    Phases<KF> p;
    double t0 = bench::now_s();
    for (auto& c : cus) c.fpar.sync(XCL_BO_SYNC_BO_TO_DEVICE, KF::FPAR_N * sizeof(float), 0);
    double t1 = bench::now_s();
    hw::arm();
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
    hw::wait_all(cus);
    double t2 = bench::now_s();
    for (auto& c : cus) c.out.sync(XCL_BO_SYNC_BO_FROM_DEVICE, KF::OUT_N * sizeof(uint64_t), 0);
    double t3 = bench::now_s();
    for (auto& c : cus) {
        const uint64_t* w = c.out.map<uint64_t*>();
        typename KF::Mom m = KF::from_words(w);
        if (m.pairs != c.n) throw std::runtime_error("kernel returned a wrong pair count");
        p.mom.add(m);
        p.lanes = (uint32_t)((w[KF::W_INFO] >> 32) & 0xFF);
        p.il = (uint32_t)((w[KF::W_INFO] >> 40) & 0xFFFF);
        p.kind = (uint32_t)(w[KF::W_INFO] >> 56);
    }
    p.h2d = t1 - t0; p.kernel = t2 - t1; p.d2h = t3 - t2; p.window = t3 - t0;
    return p;
}

template <class Mom, class F>
static Mom reference(uint64_t a0, uint64_t a1, F sim) {
    unsigned nt = std::max(1u, std::thread::hardware_concurrency());
    std::vector<Mom> part(nt);
    std::vector<std::thread> th;
    uint64_t n = a1 - a0;
    for (unsigned t = 0; t < nt; ++t) th.emplace_back([&, t] { sim(a0 + n * t / nt, a0 + n * (t + 1) / nt, part[t]); });
    Mom m;
    for (unsigned t = 0; t < nt; ++t) { th[t].join(); m.add(part[t]); }
    return m;
}

static std::vector<double> parse_list(const std::string& s, int n, double dflt) {
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

template <class KF>
static int run(bench::Args& A, bench::Report& R) {
    const bool asian = std::is_same<KF, AsianK>::value;
    std::string cs = A.str("case", asian ? "A" : "B");
    hb::BCase bc{};
    ha::ACase ac{};
    hq::Scheme sc = hq::QE;
    std::string scs = A.str("scheme", asian ? "asian" : "qe");
    long long M;
    std::string kname;
    uint32_t want_kind;
    if (asian) {
        ac = ha::get_acase(cs);
        M = A.has("steps") ? A.i64("steps", 0) : (long long)ac.M;
        ac.M = (uint32_t)M;
        kname = "ha_asian";
        want_kind = 0x20;
    } else {
        bc = hb::get_bcase(cs);
        if (scs != "ft" && scs != "qe") throw std::runtime_error("--scheme must be ft or qe for the basket");
        sc = scs == "ft" ? hq::FT : hq::QE;
        M = A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * bc.T);
        kname = sc == hq::FT ? "hb_ft" : "hb_qe";
        want_kind = 0x10 | (uint32_t)sc;
    }
    if (M < 1 || M > HQ_M_MAX) throw std::runtime_error("number of steps must be 1..65535");
    uint64_t a0 = (uint64_t)A.i64("a0", 0);
    uint64_t a1 = A.has("a1") ? (uint64_t)A.i64("a1", 0) : a0 + (uint64_t)A.i64("pairs", 65536);
    uint64_t amax = asian ? (1ull << 32) : (1ull << 29);
    if (a1 <= a0 || a1 > amax) throw std::runtime_error(asian ? "need a0 < a1 <= 2^32" : "need a0 < a1 <= 2^29 (basket)");
    uint64_t seed = (uint64_t)A.i64("seed", 20261016);
    int runs = std::max<int>(1, (int)A.i64("runs", 1)), warm = std::max<int>(0, (int)A.i64("warmup", 1));
    double warm_s = A.f64("warmup-s", 0.0), win_s = A.f64("window-s", 0.0);
    hw::set_mode(A.str("wait", "xrt"));
    R.set("wait", std::string(hw::mode_name()));
    std::vector<double> beta = parse_list(A.str("beta", ""), HQ_NK, 1.0);
    std::string est = A.str("estimator", "cv");
    if (asian && est != "plain" && est != "cv") throw std::runtime_error("--estimator must be plain or cv");
    const double T = asian ? ac.T : bc.T;

    R.set("project", "heston_accuracy");
    R.set("platform", "fpga");
    R.set("impl", "fpga");
    R.set("case", cs);
    R.set("scheme", scs);
    R.set("steps", (long long)M);
    R.set("steps_per_year", (double)M / T);
    R.set("a0", (unsigned long long)a0);
    R.set("a1", (unsigned long long)a1);
    R.set("pairs", (unsigned long long)(a1 - a0));
    R.set("seed", (unsigned long long)seed);
    if (asian) R.set("estimator", est);

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
    if (cus.empty()) throw std::runtime_error("no compute unit named " + kname + " in the xclbin (wrong --case or --scheme?)");
    R.set("n_cu", (int)cus.size());

    t0 = bench::now_s();
    for (auto& cu : cus) {
        cu.fpar = xrt::bo(dev, 4096, xrt::bo::flags::normal, cu.k.group_id(A_FPAR));
        cu.out = xrt::bo(dev, 4096, xrt::bo::flags::normal, cu.k.group_id(A_OUT));
        cu.run = xrt::run(cu.k);
        hw::attach(cu.run);
    }
    R.set("t_alloc_s", bench::now_s() - t0);

    float fp[64] = {0};
    if (asian) ha::make_fpar(ac, (uint32_t)M, fp); else hb::make_fpar(bc, (uint32_t)M, fp);
    for (auto& cu : cus) std::memcpy(cu.fpar.map<float*>(), fp, KF::FPAR_N * sizeof(float));
    mc::Keys keys = mc::run_keys(seed);
    uint64_t n = a1 - a0, ncu = cus.size();
    for (uint64_t i = 0; i < ncu; ++i) {
        cus[i].base = a0 + n * i / ncu;
        cus[i].n = a0 + n * (i + 1) / ncu - cus[i].base;
    }

    Phases<KF> res;
    const double tw = bench::now_s();
    for (int i = 0; i < warm || bench::now_s() - tw < warm_s; ++i) res = run_all<KF>(cus, (uint32_t)M, keys);
    std::vector<Phases<KF>> ph;
    bench::mark("start");
    const double tw0 = bench::now_s();
    const double c0 = hw::host_cpu_s();
    for (int i = 0; i < runs || bench::now_s() - tw0 < win_s; ++i) ph.push_back(run_all<KF>(cus, (uint32_t)M, keys));
    const double twin = bench::now_s() - tw0;
    const double cpu_win = hw::host_cpu_s() - c0;
    bench::mark("end");
    R.set("runs", (long long)ph.size());
    R.set("t_window_s", twin);
    R.set("t_per_run_s", twin / (double)ph.size());
    R.set("host_cpu_s", cpu_win);
    R.set("host_cpu_frac", cpu_win / twin);
    std::vector<double> win;
    for (auto& p : ph) win.push_back(p.window);
    double med = mc::median(win);
    size_t mi = 0;
    for (size_t i = 0; i < ph.size(); ++i) if (std::fabs(ph[i].window - med) < std::fabs(ph[mi].window - med)) mi = i;
    res = ph[mi];
    if (res.kind != want_kind) throw std::runtime_error("the xclbin's kernel is not the requested case/scheme");
    R.set("lanes", (unsigned)res.lanes);
    R.set("il", (unsigned)res.il);
    R.set("t_h2d_s", res.h2d);
    R.set("t_kernel_s", res.kernel);
    R.set("t_d2h_s", res.d2h);
    R.set("t_compute_s", res.window);
    R.set("t_compute_min_s", *std::min_element(win.begin(), win.end()));
    double psteps = 2.0 * (double)n * (double)M;
    R.set("path_steps", psteps);
    if (!asian) R.set("asset_steps", psteps * HB_NA);
    R.set("gsteps_per_s", psteps / res.window / 1e9);
    R.set("gsteps_per_s_kernel", psteps / res.kernel / 1e9);

    char b[96];
    int ok = 1;
    if constexpr (std::is_same<KF, BasketK>::value) {
        for (int j = 0; j < HQ_NK; ++j) {
            std::string s = std::to_string(j);
            hq::Price p = hb::finalize_k(bc, res.mom, j);
            std::snprintf(b, sizeof b, "%.0f", bc.K[j]); R.set("K" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.price); R.set("price" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.pv); R.set("pv" + s, b);
            R.set("form" + s, ((bc.puts >> j) & 1u) ? "put" : "call");
            std::snprintf(b, sizeof b, "%.10f", p.se); R.set("se" + s, b);
            std::snprintf(b, sizeof b, "%.10f", bc.ref[j]); R.set("ref" + s, b);
            std::snprintf(b, sizeof b, "%.10f", p.price - bc.ref[j]); R.set("bias" + s, b);
            R.set("s1_" + s, hq::u128_hex(res.mom.s1[j]));
            R.set("sp_" + s, hq::u128_hex(res.mom.sp[j]));
        }
    } else {
        for (int j = 0; j < HQ_NK; ++j) {
            std::string s = std::to_string(j);
            ha::APrice p = ha::finalize_k(ac, res.mom, j, beta[j]);
            double price = est == "cv" ? p.cv : p.plain, se = est == "cv" ? p.se_cv : p.se_plain;
            std::snprintf(b, sizeof b, "%.0f", ac.K[j]); R.set("K" + s, b);
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
            std::snprintf(b, sizeof b, "%.10f", ac.ref[j]); R.set("ref" + s, b);
            std::snprintf(b, sizeof b, "%.10f", price - ac.ref[j]); R.set("bias" + s, b);
            R.set("sy_" + s, hq::u128_hex(res.mom.sy[j])); R.set("syy_" + s, hq::u128_hex(res.mom.syy[j]));
            R.set("sg_" + s, hq::u128_hex(res.mom.sg[j])); R.set("sgg_" + s, hq::u128_hex(res.mom.sgg[j]));
            R.set("syg_" + s, hq::u128_hex(res.mom.syg[j]));
        }
    }
    R.set("payoff_overflows", (unsigned long long)res.mom.ovf);
    R.set("mom_hash", "h" + bench::hex64(KF::hash(res.mom)));
    ok = res.mom.ovf == 0 ? 1 : 0;
    for (auto& p : ph) ok = ok && p.mom == res.mom;
    if (A.has("verify")) {
        uint64_t vn = n * (uint64_t)M * (asian ? 1 : HB_NA) <= (1ull << 24) ? n : std::min<uint64_t>(n, asian ? 65536 : 8192);
        if (A.has("verify-pairs")) vn = std::min<uint64_t>(n, (uint64_t)A.i64("verify-pairs", 65536));
        typename KF::Mom dev_m = res.mom;
        if (vn != n) {
            std::vector<Cu> one(1);
            one[0] = cus[0];
            one[0].base = a0;
            one[0].n = vn;
            dev_m = run_all<KF>(one, (uint32_t)M, keys).mom;
        }
        typename KF::Mom ref;
        if constexpr (std::is_same<KF, BasketK>::value)
            ref = reference<hq::Mom>(a0, a0 + vn, [&](uint64_t x0, uint64_t x1, hq::Mom& m) { hb::sim_range(fp, sc, keys, (uint32_t)M, x0, x1, m); });
        else
            ref = reference<ha::AMom>(a0, a0 + vn, [&](uint64_t x0, uint64_t x1, ha::AMom& m) { ha::sim_range(fp, keys, (uint32_t)M, x0, x1, m); });
        bool same = ref == dev_m;
        R.set("verify_pairs", (unsigned long long)vn);
        R.set("verify_bit_identical", same ? 1 : 0);
        ok = ok && same;
    }
    R.set("ok", ok);
    t0 = bench::now_s();
    mc::append_json(A.str("out"), R);
    R.set("t_write_s", A.has("out") ? bench::now_s() - t0 : 0.0);
    return ok ? 0 : 2;
}

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h") || !A.has("xclbin")) {
        std::printf("usage: %s --xclbin PATH --case B --scheme ft|qe (--steps-per-year S | --steps M)\n"
                    "       %s --xclbin PATH --case A [--steps M] [--estimator plain|cv] [--beta b | b0,b1,b2]\n"
                    "          [--a0 A] (--a1 B | --pairs N) [--seed S] [--runs R] [--warmup W] [--warmup-s S] [--window-s S] [--cus K]\n"
                    "          [--verify [--verify-pairs N]] [--out FILE] [--device N] [--wait xrt|callback]\n", argv[0], argv[0]);
        return A.has("xclbin") || A.has("help") || A.has("h") ? 0 : 1;
    }
    int rc = 0;
    try {
        rc = A.str("case", "B") == "A" ? run<AsianK>(A, R) : run<BasketK>(A, R);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        R.set("ok", 0);
        rc = 1;
    }
    R.set("t_total_s", bench::now_s() - t_start);
    R.print();
    return rc;
}
