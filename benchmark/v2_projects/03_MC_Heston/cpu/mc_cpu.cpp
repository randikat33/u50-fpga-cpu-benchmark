// mc_cpu.cpp - v2 CPU baseline for the Monte Carlo Heston basket option.
//   --impl avx512 : 16 antithetic pairs per zmm (cpu/mc_avx512.hpp), OpenMP over 16-pair blocks
//   --impl scalar : the reference model (common/mc_model.hpp), OpenMP over the same blocks
// Same RNG streams, ICDF, recurrence and exact integer accumulation as the FPGA kernel, so the
// result (mom_hash, price) is bit-identical to the FPGA for the same --paths/--steps/--seed.
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include "bench_common.hpp"
#include "mc_cli.hpp"
#include "mc_model.hpp"
#include "mc_avx512.hpp"

namespace {
struct alignas(64) Acc { mc::Mom m; };

mc::Mom run_once(bool avx, const float* fp, const mc::Keys& k, uint32_t M, uint64_t pairs, bool put, int threads,
                 bench::AlignedBuf<Acc>& acc) {
    const uint64_t nb = (pairs + 15) / 16;
#pragma omp parallel num_threads(threads)
    {
        Acc& a = acc[omp_get_thread_num()];
        a.m = mc::Mom();
#pragma omp for schedule(static)
        for (uint64_t b = 0; b < nb; ++b) {
            uint64_t a0 = b * 16, a1 = std::min<uint64_t>(a0 + 16, pairs);
#if MC_HAVE_AVX512
            if (avx) { mc::avx::sim_block(fp, k, M, (uint32_t)a0, (int)(a1 - a0), put, a.m); continue; }
#endif
            mc::sim_range(fp, k, M, a0, a1, put, a.m);
        }
    }
    mc::Mom tot;
    for (int t = 0; t < threads; ++t) tot.add(acc[t].m);
    return tot;
}
}  // namespace

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h")) {
        std::printf("usage: %s [--impl avx512|scalar] [--threads N] [workload options]\n%s", argv[0], mc::workload_help());
        return 0;
    }
    int rc = 0;
    try {
        mc::Workload w = mc::parse_workload(A);
        std::string impl = A.str("impl", MC_HAVE_AVX512 ? "avx512" : "scalar");
        bool avx = impl == "avx512";
        if (avx && !MC_HAVE_AVX512) throw std::runtime_error("built without AVX-512");
        if (!avx && impl != "scalar") throw std::runtime_error("--impl must be avx512 or scalar");
        int threads = (int)A.i64("threads", omp_get_max_threads());
        R.set("project", "mc_heston");
        R.set("platform", "cpu");
        R.set("impl", avx ? "avx512_omp" : "scalar_omp");
        R.set("isa", avx ? "avx512" : "scalar");
        R.set("threads", threads);
        mc::report_workload(R, w);
        R.set("t_read_s", 0.0);

        float fp[MC_FPAR_N];
        mc::make_fpar(w.mk, w.steps, fp);
        mc::Keys k = mc::run_keys(w.seed);
        bench::AlignedBuf<Acc> acc(threads);
#pragma omp parallel num_threads(threads)
        { acc[omp_get_thread_num()].m = mc::Mom(); }   // first touch by the owning thread

        mc::Mom res;
        for (int i = 0; i < w.warmup; ++i) res = run_once(avx, fp, k, w.steps, w.pairs, w.mk.put, threads, acc);
        std::vector<double> ts;
        bench::mark("start");
        for (int i = 0; i < w.runs; ++i) {
            double t0 = bench::now_s();
            res = run_once(avx, fp, k, w.steps, w.pairs, w.mk.put, threads, acc);
            ts.push_back(bench::now_s() - t0);
        }
        bench::mark("end");
        double tc = mc::median(ts);
        R.set("t_compute_s", tc);
        R.set("t_compute_min_s", *std::min_element(ts.begin(), ts.end()));
        double psteps = 2.0 * w.pairs * w.steps;
        R.set("msteps_per_s", psteps / tc / 1e6);
        R.set("paths_per_s", 2.0 * w.pairs / tc);
        R.set("ns_per_path_step_core", tc * threads / psteps * 1e9);
        mc::report_price(R, w, res);

        int ok = res.ovf == 0 ? 1 : 0;
        if (A.has("verify")) {
            // scalar reference over all pairs when small, else over the first 2^16 pairs
            uint64_t vp = w.pairs * w.steps <= (1ull << 24) ? w.pairs : std::min<uint64_t>(w.pairs, 65536);
            if (A.has("verify-pairs")) vp = std::min<uint64_t>(w.pairs, (uint64_t)A.i64("verify-pairs", 65536));
            mc::Mom ref = run_once(false, fp, k, w.steps, vp, w.mk.put, threads, acc);
            mc::Mom chk = vp == w.pairs ? res : run_once(avx, fp, k, w.steps, vp, w.mk.put, threads, acc);
            bool same = ref == chk;
            R.set("verify_pairs", (unsigned long long)vp);
            R.set("verify_bit_identical", same ? 1 : 0);
            ok = ok && same;
        }
        R.set("ok", ok);
        double tw0 = bench::now_s();
        R.set("t_write_s", 0.0);
        R.set("t_total_s", bench::now_s() - t_start);
        mc::append_json(A.str("out"), R);
        if (A.has("out")) R.set("t_write_s", bench::now_s() - tw0);
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
