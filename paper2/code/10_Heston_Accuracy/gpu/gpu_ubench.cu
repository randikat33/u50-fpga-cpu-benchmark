// gpu_ubench.cu - generic GPU microbenchmarks for the GPU prediction of paper 2 (protocol v0.6, Section 22a).
// They do not run any pricing kernel. Each benchmark runs back to back for a window (MARK start/end around it),
// so the harness (scripts/gpu_run.py) measures the GPU's power while that class of unit is busy.
//   fp32    : 8 independent FMA chains per thread                  -> FP32 instructions per SM per cycle
//   int     : 8 independent xorshift chains (SHF + LOP3 per step)  -> integer instructions per SM per cycle
//   sfu     : 8 independent chains of ex2.approx / lg2.approx      -> MUFU instructions per SM per cycle
//   startup : empty kernel + copy of the block sums + host sum      -> fixed cost of one pricing run
//   idle    : nothing (host sleeps)                                 -> idle power with the same host state
// Usage: gpu_ubench --bench fp32|int|sfu|startup|idle --window-s 10 [--warmup-s 3] [--tpb 256] [--blocks-per-sm 0]
//                   [--iters 4096] [--clock-mhz 1245] [--sync spin|block]
#include <cuda_runtime.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include "hs_gpu_host.hpp"

#define CK(x) do { cudaError_t e_ = (x); if (e_ != cudaSuccess) throw std::runtime_error(std::string(#x) + ": " + cudaGetErrorString(e_)); } while (0)

__global__ void k_fp32(int iters, float b, float c, float* out) {
    float a[8];
#pragma unroll
    for (int j = 0; j < 8; ++j) a[j] = threadIdx.x * 1e-7f + j;
    for (int i = 0; i < iters; ++i) {
#pragma unroll
        for (int j = 0; j < 8; ++j) a[j] = fmaf(a[j], b, c);
    }
    float s = 0.0f;
#pragma unroll
    for (int j = 0; j < 8; ++j) s += a[j];
    if (s == 1.2345f) out[0] = s;     // never true in practice; keeps the chains alive
}
__global__ void k_int(int iters, unsigned* out) {
    unsigned x[8];
#pragma unroll
    for (int j = 0; j < 8; ++j) x[j] = threadIdx.x * 2654435761u + j + 1u;
    for (int i = 0; i < iters; ++i) {
#pragma unroll
        for (int j = 0; j < 8; ++j) { x[j] ^= x[j] << 13; x[j] ^= x[j] >> 17; x[j] ^= x[j] << 5; }
    }
    unsigned s = 0;
#pragma unroll
    for (int j = 0; j < 8; ++j) s ^= x[j];
    if (s == 0x12345678u) out[0] = s;
}
__global__ void k_sfu(int iters, float* out) {
    float a[8];
#pragma unroll
    for (int j = 0; j < 8; ++j) a[j] = 1.5f + threadIdx.x * 1e-7f + j * 0.01f;
    for (int i = 0; i < iters; ++i) {
#pragma unroll
        for (int j = 0; j < 8; ++j) { a[j] = __log2f(a[j] + 2.0f); a[j] = exp2f(a[j]); }
    }
    float s = 0.0f;
#pragma unroll
    for (int j = 0; j < 8; ++j) s += a[j];
    if (s == 1.2345f) out[0] = s;
}
__global__ void k_startup(double* out) {   // writes the block sums, as a pricing kernel does at its end
    if (threadIdx.x < HS_NACC) out[(size_t)blockIdx.x * HS_NACC + threadIdx.x] = 0.0;
}

static double now_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static void mark(const char* w) { std::printf("MARK %s %.6f\n", w, now_s()); std::fflush(stdout); }

int main(int argc, char** argv) {
    try {
        Args A(argc, argv);
        const std::string bench = A.str("bench", "fp32"), sync = A.str("sync", "spin");
        CK(cudaSetDeviceFlags(sync == "block" ? cudaDeviceScheduleBlockingSync : cudaDeviceScheduleSpin));
        const int tpb = (int)A.i64("tpb", 256), iters = (int)A.i64("iters", 4096);
        const double win = A.f64("window-s", 10.0), warm = A.f64("warmup-s", 3.0), fmhz = A.f64("clock-mhz", 1245);
        cudaDeviceProp dp;
        CK(cudaGetDeviceProperties(&dp, 0));
        int occ = 1;
        if (bench == "fp32") CK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&occ, k_fp32, tpb, 0));
        if (bench == "int") CK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&occ, k_int, tpb, 0));
        if (bench == "sfu") CK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&occ, k_sfu, tpb, 0));
        if (bench == "startup") CK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&occ, k_startup, tpb, 0));
        const int bps = (int)A.i64("blocks-per-sm", 0);
        const int blocks = dp.multiProcessorCount * (bps > 0 ? bps : std::max(occ, 1));
        float* df; unsigned* du; double* dd;
        CK(cudaMalloc(&df, 64)); CK(cudaMalloc(&du, 64)); CK(cudaMalloc(&dd, (size_t)blocks * HS_NACC * sizeof(double)));
        std::vector<double> h((size_t)blocks * HS_NACC);
        double ops_per_launch = 0;
        auto once = [&]() {
            if (bench == "fp32") { k_fp32<<<blocks, tpb>>>(iters, 0.999f, 1e-3f, df); ops_per_launch = (double)blocks * tpb / 32 * iters * 8; }
            else if (bench == "int") { k_int<<<blocks, tpb>>>(iters, du); ops_per_launch = (double)blocks * tpb / 32 * iters * 8 * 6; }
            else if (bench == "sfu") { k_sfu<<<blocks, tpb>>>(iters, df); ops_per_launch = (double)blocks * tpb / 32 * iters * 8 * 2; }
            else if (bench == "startup") {
                k_startup<<<blocks, tpb>>>(dd);
                CK(cudaMemcpy(h.data(), dd, h.size() * sizeof(double), cudaMemcpyDeviceToHost));
                double s = 0; for (double v : h) s += v; if (s != 0) std::printf("#");
                return;
            } else if (bench == "idle") { std::this_thread::sleep_for(std::chrono::milliseconds(100)); return; }
            else throw std::runtime_error("unknown --bench");
            CK(cudaGetLastError());
            CK(cudaDeviceSynchronize());
        };
        once();
        const double tw = now_s();
        while (now_s() - tw < warm) once();
        long long n = 0;
        mark("start");
        const double t0 = now_s();
        while (now_s() - t0 < win) { once(); ++n; }
        const double t = now_s() - t0;
        mark("end");
        std::printf("RESULT project=heston_accuracy\nRESULT platform=gpu\nRESULT impl=ubench\nRESULT bench=%s\n", bench.c_str());
        std::printf("RESULT tpb=%d\nRESULT blocks=%d\nRESULT blocks_per_sm_occupancy=%d\nRESULT sync=%s\n", tpb, blocks, occ, sync.c_str());
        std::printf("RESULT runs=%lld\nRESULT t_window_s=%.6f\nRESULT t_per_run_s=%.9f\n", n, t, t / n);
        if (ops_per_launch > 0) {
            const double wops = ops_per_launch * n / t;    // warp instructions per second
            std::printf("RESULT warp_instr_per_s=%.6e\nRESULT clock_mhz_assumed=%.0f\n", wops, fmhz);
            std::printf("RESULT thread_instr_per_sm_per_clk=%.4f\n", wops * 32 / dp.multiProcessorCount / (fmhz * 1e6));
        }
        std::printf("RESULT ok=1\n");
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        std::printf("RESULT ok=0\n");
        return 1;
    }
}
