// hs_coexec.cu - serve mode of the GPU program for co-execution (addendum v0.7). The registered program
// (gpu/hs_gpu.cu, build/hs_gpu, sha256 fbddf8db...) is included unchanged with its main renamed, so the kernels,
// launch shape and host sum are the same source; this file only adds a command loop. build/hs_gpu is not rebuilt.
//
// Usage: hs_coexec --case I|II|III|P1|B|A --scheme ft|qe|plain|cv --steps M --seed S [--beta ...] [--sync spin|block] [--tpb 256]
// stdin : "R <id> <a0> <a1>" one pricing part over pairs [a0, a1) (a complete run: kernel, copy of the block sums, host
//         sum, prices);  "Q" quit
// stdout: "READY", then "D <id> <seconds> <pairs> <price0> <price1> <price2> <ok>"
#define main hs_gpu_main
#include "hs_gpu.cu"
#undef main

#include <iostream>

int main(int argc, char** argv) {
    int rc = 0;
    try {
        Args A(argc, argv);
        const std::string sync = A.str("sync", "block");
        CK(cudaSetDeviceFlags(sync == "block" ? cudaDeviceScheduleBlockingSync : cudaDeviceScheduleSpin));
        const CaseDef c = get_case(A.str("case", "I"));
        const std::string sc = A.str("scheme", c.kind == K_ASIAN ? "cv" : "qe");
        if (c.kind == K_ASIAN ? (sc != "plain" && sc != "cv") : (sc != "ft" && sc != "qe"))
            throw std::runtime_error("--scheme: ft|qe for I, II, III, P1, B; plain|cv for A");
        const int scheme = sc == "ft" ? S_FT : S_QE, cv = sc == "cv";
        long long M = c.kind == K_ASIAN ? c.M_fixed
                    : (A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * c.T));
        if (M < 1 || M > 1 << 20) throw std::runtime_error("bad number of steps");
        const unsigned long long seed = (unsigned long long)A.i64("seed", 20261011) ^ 0x6770750000000000ull;   // as hs_gpu
        const std::vector<double> beta = parse_list(A.str("beta", ""), HS_NK, BETA_A);
        Runner X;
        X.tpb = (int)A.i64("tpb", 256);
        X.kern = pick_tpb(X.tpb, c.kind, scheme, cv);
        cudaDeviceProp dp;
        CK(cudaGetDeviceProperties(&dp, 0));
        int occ = 0;
        CK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&occ, X.kern, X.tpb, 0));
        const int full = dp.multiProcessorCount * std::max(occ, 1);
        CK(cudaMalloc(&X.d_out, (size_t)full * HS_NACC * sizeof(double)));
        X.sum.assign(HS_NACC, 0.0);
        std::printf("READY\n");
        std::fflush(stdout);
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty()) continue;
            if (line[0] == 'Q') break;
            unsigned long long id = 0, x0 = 0, x1 = 0;
            if (std::sscanf(line.c_str(), "R %llu %llu %llu", &id, &x0, &x1) != 3 || x1 <= x0) {
                std::printf("ERROR bad command\n"); std::fflush(stdout); continue;
            }
            const unsigned long long nn = x1 - x0;
            const double t0 = now_s();
            X.P = make_job(c, scheme, (int)M, cv, x0, nn, seed);
            const unsigned long long maxb = (nn + X.tpb - 1) / X.tpb;
            X.blocks = (int)std::min<unsigned long long>((unsigned long long)full, maxb);
            X.h_out.assign((size_t)X.blocks * HS_NACC, 0.0);
            X.run();
            const auto pr = finalize(c, (int)M, cv, X.sum.data(), (double)nn, beta.data());
            const double ds = now_s() - t0;
            bool fin = true;
            std::printf("D %llu %.6f %llu", id, ds, nn);
            for (int k = 0; k < HS_NK; ++k) { std::printf(" %.10f", pr[k].price); fin = fin && std::isfinite(pr[k].price); }
            std::printf(" %d\n", fin ? 1 : 0);
            std::fflush(stdout);
        }
        cudaFree(X.d_out);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        rc = 1;
    }
    return rc;
}
