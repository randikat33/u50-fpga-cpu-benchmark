// asian_cpu.cpp - CPU baseline for the Monte Carlo arithmetic-average Asian option (GBM).
//   --impl avx512 : NB x 16 antithetic pairs per step loop (cpu/asian_avx512.hpp), OpenMP over blocks
//   --impl scalar : the reference model (common/asian_model.hpp), OpenMP over the same blocks
// Same RNG streams, ICDF, exp and exact integer accumulation as the FPGA kernel, so the result
// (mom_hash, price) is bit-identical to the FPGA for the same --paths/--steps/--seed.
#include <cstdio>
#include <string>
#include <vector>
#include <omp.h>
#include "bench_common.hpp"
#include "asian_cli.hpp"
#include "asian_model.hpp"
#include "asian_avx512.hpp"

#ifndef AS_DEFAULT_NB
#define AS_DEFAULT_NB 2     // interleaved zmm blocks (sandbox + the server tuning: see README)
#endif

namespace {
struct alignas(64) Acc { asn::Mom m; };

void block(int impl_nb, const float* fp, const asn::Keys& k, uint32_t M, uint64_t a0, uint64_t a1, bool put,
           asn::Mom& m) {
#if AS_HAVE_AVX512
    switch (impl_nb) {
        case 1: asn::avx::sim_range<1>(fp, k, M, a0, a1, put, m); return;
        case 2: asn::avx::sim_range<2>(fp, k, M, a0, a1, put, m); return;
        case 4: asn::avx::sim_range<4>(fp, k, M, a0, a1, put, m); return;
        default: break;
    }
#endif
    asn::sim_range(fp, k, M, a0, a1, put, m);
}

// impl_nb: 0 = scalar, else AVX-512 with impl_nb interleaved blocks. Work unit = 64 pairs for every
// impl (so the thread partition, and therefore nothing in the result, depends on the impl).
asn::Mom run_once(int impl_nb, const float* fp, const asn::Keys& k, uint32_t M, uint64_t pairs, bool put,
                  int threads, bench::AlignedBuf<Acc>& acc) {
    const uint64_t U = 64;
    const uint64_t nb = (pairs + U - 1) / U;
#pragma omp parallel num_threads(threads)
    {
        Acc& a = acc[omp_get_thread_num()];
        a.m = asn::Mom();
#pragma omp for schedule(static)
        for (uint64_t b = 0; b < nb; ++b) {
            uint64_t a0 = b * U, a1 = std::min<uint64_t>(a0 + U, pairs);
            block(impl_nb, fp, k, M, a0, a1, put, a.m);
        }
    }
    asn::Mom tot;
    for (int t = 0; t < threads; ++t) tot.add(acc[t].m);
    return tot;
}
}  // namespace

int main(int argc, char** argv) {
    double t_start = bench::now_s();
    bench::Report R;
    bench::Args A(argc, argv);
    if (A.has("help") || A.has("h")) {
        std::printf("usage: %s [--impl avx512|scalar] [--interleave 1|2|4] [--threads N] [workload options]\n%s",
                    argv[0], asn::workload_help());
        return 0;
    }
    int rc = 0;
    try {
        asn::Workload w = asn::parse_workload(A);
        std::string impl = A.str("impl", AS_HAVE_AVX512 ? "avx512" : "scalar");
        bool avx = impl == "avx512";
        if (avx && !AS_HAVE_AVX512) throw std::runtime_error("built without AVX-512");
        if (!avx && impl != "scalar") throw std::runtime_error("--impl must be avx512 or scalar");
        int nbi = (int)A.i64("interleave", AS_DEFAULT_NB);
        if (nbi != 1 && nbi != 2 && nbi != 4) throw std::runtime_error("--interleave must be 1, 2 or 4");
        int impl_nb = avx ? nbi : 0;
        int threads = (int)A.i64("threads", omp_get_max_threads());
        R.set("project", "mc_asian");
        R.set("platform", "cpu");
        R.set("impl", avx ? "avx512_omp_x" + std::to_string(nbi) : std::string("scalar_omp"));
        R.set("isa", avx ? "avx512" : "scalar");
        R.set("threads", threads);
        asn::report_workload(R, w);
        R.set("t_read_s", 0.0);

        float fp[AS_FPAR_N];
        asn::make_fpar(w.mk, w.steps, fp);
        asn::Keys k = mc::run_keys(w.seed);
        bench::AlignedBuf<Acc> acc(threads);
#pragma omp parallel num_threads(threads)
        { acc[omp_get_thread_num()].m = asn::Mom(); }   // first touch by the owning thread

        asn::Mom res;
        for (int i = 0; i < w.warmup; ++i) res = run_once(impl_nb, fp, k, w.steps, w.pairs, w.mk.put, threads, acc);
        std::vector<double> ts;
        bench::mark("start");
        for (int i = 0; i < w.runs; ++i) {
            double t0 = bench::now_s();
            res = run_once(impl_nb, fp, k, w.steps, w.pairs, w.mk.put, threads, acc);
            ts.push_back(bench::now_s() - t0);
        }
        bench::mark("end");
        double tc = asn::median(ts);
        R.set("t_compute_s", tc);
        R.set("t_compute_min_s", *std::min_element(ts.begin(), ts.end()));
        double psteps = 2.0 * w.pairs * w.steps;
        R.set("msteps_per_s", psteps / tc / 1e6);
        R.set("paths_per_s", 2.0 * w.pairs / tc);
        R.set("ns_per_path_step_core", tc * threads / psteps * 1e9);
        asn::report_price(R, w, res);

        int ok = res.ovf == 0 ? 1 : 0;
        if (A.has("verify")) {
            uint64_t vp = w.pairs * w.steps <= (1ull << 24) ? w.pairs : std::min<uint64_t>(w.pairs, 65536);
            if (A.has("verify-pairs")) vp = std::min<uint64_t>(w.pairs, (uint64_t)A.i64("verify-pairs", 65536));
            asn::Mom ref = run_once(0, fp, k, w.steps, vp, w.mk.put, threads, acc);
            asn::Mom chk = vp == w.pairs ? res : run_once(impl_nb, fp, k, w.steps, vp, w.mk.put, threads, acc);
            bool same = ref == chk;
            R.set("verify_pairs", (unsigned long long)vp);
            R.set("verify_bit_identical", same ? 1 : 0);
            ok = ok && same;
        }
        R.set("ok", ok);
        double tw0 = bench::now_s();
        R.set("t_write_s", 0.0);
        R.set("t_total_s", bench::now_s() - t_start);
        asn::append_json(A.str("out"), R);
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
