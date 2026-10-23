// hs_gpu.cu - GPU-native pricing program of paper 2 (protocol v0.6, Section 22), NVIDIA V100 (sm_70), CUDA 12.8.
// All six cases, same settings, seeds and output conventions as the CPU programs (RESULT key=value lines, MARK
// start/end around the measurement window), so the measurement scripts can drive it.
//
//   hs_gpu --case I|II|III|P1|B|A --scheme ft|qe|plain|cv (--steps-per-year S | --steps M) (--pairs N | --a1 B)
//          [--a0 A] [--seed S] [--beta b0,b1,b2] [--tpb 256] [--blocks-per-sm 0] [--sync spin|block]
//          [--timed --runs R --warmup-s W --window-s S]
//
// Without --timed (functional mode, allowed before the GPU prediction is registered, protocol 22) a run is
// limited to 10^7 path-steps and no time is reported. With --timed the measurement window is as in phase 5.
// Build: nvcc -O3 -arch=sm_70 --use_fast_math -std=c++17 gpu/hs_gpu.cu -o build/hs_gpu   (see gpu/Makefile)
#include <cuda_runtime.h>
#include <curand_kernel.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <string>
#include <vector>
#include "hs_gpu_host.hpp"

#define CK(x) do { cudaError_t e_ = (x); if (e_ != cudaSuccess) throw std::runtime_error(std::string(#x) + ": " + cudaGetErrorString(e_)); } while (0)

struct GpuRNG {
    curandStatePhilox4_32_10_t s;
    __device__ __forceinline__ GpuRNG(unsigned long long seed, unsigned long long sub) { curand_init(seed, sub, 0ull, &s); }
    __device__ __forceinline__ uint32_t u32() { return curand(&s); }
    __device__ __forceinline__ void normal2(float& a, float& b) { const float2 z = curand_normal2(&s); a = z.x; b = z.y; }
};

template <int TPB, int KIND, int SCHEME, int CV>
__global__ void __launch_bounds__(TPB) price_kernel(const Job P, double* __restrict__ out) {
    constexpr int NA = NAcc<KIND, CV>::n;
    double acc[NA];
#pragma unroll
    for (int k = 0; k < NA; ++k) acc[k] = 0.0;
    const unsigned long long stride = (unsigned long long)gridDim.x * TPB;
    for (unsigned long long i = (unsigned long long)blockIdx.x * TPB + threadIdx.x; i < P.n; i += stride)
        one_pair<KIND, SCHEME, CV, GpuRNG>(P, P.a0 + i, acc);
    // block sums in a fixed tree order (deterministic for a given launch shape); unused accumulators are 0
    __shared__ double sh[TPB];
    for (int k = 0; k < HS_NACC; ++k) {
        sh[threadIdx.x] = k < NA ? acc[k < NA ? k : 0] : 0.0;
        __syncthreads();
        for (int w = TPB / 2; w > 0; w >>= 1) {
            if ((int)threadIdx.x < w) sh[threadIdx.x] += sh[threadIdx.x + w];
            __syncthreads();
        }
        if (threadIdx.x == 0) out[(size_t)blockIdx.x * HS_NACC + k] = sh[0];
        __syncthreads();
    }
}

typedef void (*KernelFn)(const Job, double*);

static double now_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static void mark(const char* w) { std::printf("MARK %s %.6f\n", w, now_s()); std::fflush(stdout); }

struct Runner {
    Job P;
    int tpb, blocks;
    double* d_out = nullptr;
    std::vector<double> h_out, sum;
    KernelFn kern = nullptr;
    void run() {   // one complete pricing run: kernel, copy of the block sums, host sum in a fixed order
        kern<<<blocks, tpb>>>(P, d_out);
        CK(cudaGetLastError());
        CK(cudaMemcpy(h_out.data(), d_out, h_out.size() * sizeof(double), cudaMemcpyDeviceToHost));
        std::fill(sum.begin(), sum.end(), 0.0);
        for (int b = 0; b < blocks; ++b)
            for (int k = 0; k < HS_NACC; ++k) sum[k] += h_out[(size_t)b * HS_NACC + k];
    }
};
template <int TPB> static KernelFn pick(int kind, int scheme, int cv) {
    if (kind == K_HESTON) return scheme == S_FT ? price_kernel<TPB, K_HESTON, S_FT, 0> : price_kernel<TPB, K_HESTON, S_QE, 0>;
    if (kind == K_BASKET) return scheme == S_FT ? price_kernel<TPB, K_BASKET, S_FT, 0> : price_kernel<TPB, K_BASKET, S_QE, 0>;
    return cv ? price_kernel<TPB, K_ASIAN, S_FT, 1> : price_kernel<TPB, K_ASIAN, S_FT, 0>;
}
static KernelFn pick_tpb(int tpb, int kind, int scheme, int cv) {
    switch (tpb) {
        case 64: return pick<64>(kind, scheme, cv);
        case 128: return pick<128>(kind, scheme, cv);
        case 256: return pick<256>(kind, scheme, cv);
        case 512: return pick<512>(kind, scheme, cv);
    }
    throw std::runtime_error("--tpb must be 64, 128, 256 or 512");
}

int main(int argc, char** argv) {
    const double t_start = now_s();
    std::vector<std::pair<std::string, std::string>> R;
    auto set = [&](const std::string& k, const std::string& v) { R.push_back({k, v}); };
    auto setf = [&](const std::string& k, double v, const char* f = "%.10g") { char b[64]; std::snprintf(b, sizeof b, f, v); set(k, b); };
    int rc = 0;
    try {
        Args A(argc, argv);
        const std::string sync = A.str("sync", "spin");
        CK(cudaSetDeviceFlags(sync == "block" ? cudaDeviceScheduleBlockingSync : cudaDeviceScheduleSpin));
        const CaseDef c = get_case(A.str("case", "I"));
        const std::string sc = A.str("scheme", c.kind == K_ASIAN ? "cv" : "qe");
        if (c.kind == K_ASIAN ? (sc != "plain" && sc != "cv") : (sc != "ft" && sc != "qe"))
            throw std::runtime_error("--scheme: ft|qe for I, II, III, P1, B; plain|cv for A");
        const int scheme = sc == "ft" ? S_FT : S_QE, cv = sc == "cv";
        long long M = c.kind == K_ASIAN ? c.M_fixed
                    : (A.has("steps") ? A.i64("steps", 0) : (long long)std::llround(A.f64("steps-per-year", 8) * c.T));
        if (M < 1 || M > 1 << 20) throw std::runtime_error("bad number of steps");
        const unsigned long long a0 = (unsigned long long)A.i64("a0", 0);
        const unsigned long long a1 = A.has("a1") ? (unsigned long long)A.i64("a1", 0) : a0 + (unsigned long long)A.i64("pairs", 65536);
        if (a1 <= a0) throw std::runtime_error("need a0 < a1");
        const unsigned long long n = a1 - a0;
        const double psteps = 2.0 * (double)n * (double)M;   // basket: basket path-steps (8 asset-steps each)
        const bool timed = A.has("timed");
        if (!timed && psteps > 1e7) throw std::runtime_error("functional mode: at most 1e7 path-steps (protocol v0.6, 22); use --timed after the GPU prediction is registered");
        const unsigned long long seed = (unsigned long long)A.i64("seed", 20261011) ^ 0x6770750000000000ull;   // own streams ("gpu")
        const std::vector<double> beta = parse_list(A.str("beta", ""), HS_NK, BETA_A);

        Runner X;
        X.P = make_job(c, scheme, (int)M, cv, a0, n, seed);
        X.tpb = (int)A.i64("tpb", 256);
        X.kern = pick_tpb(X.tpb, c.kind, scheme, cv);
        cudaDeviceProp dp;
        CK(cudaGetDeviceProperties(&dp, 0));
        int occ = 0;
        cudaFuncAttributes fa;
        CK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&occ, X.kern, X.tpb, 0));
        CK(cudaFuncGetAttributes(&fa, X.kern));
        const int bps = (int)A.i64("blocks-per-sm", 0);
        X.blocks = dp.multiProcessorCount * (bps > 0 ? bps : std::max(occ, 1));
        const unsigned long long maxb = (n + X.tpb - 1) / X.tpb;            // no block without a pair
        if ((unsigned long long)X.blocks > maxb) X.blocks = (int)maxb;
        CK(cudaMalloc(&X.d_out, (size_t)X.blocks * HS_NACC * sizeof(double)));
        X.h_out.assign((size_t)X.blocks * HS_NACC, 0.0);
        X.sum.assign(HS_NACC, 0.0);

        set("project", "heston_accuracy"); set("platform", "gpu"); set("impl", "native");
        set("device", std::string(dp.name).substr(0, 40).c_str());
        for (auto& ch : R.back().second) if (ch == ' ') ch = '_';
        set("case", c.name); set("scheme", sc);
        if (c.kind == K_ASIAN) set("estimator", sc);
        setf("steps", (double)M, "%.0f"); setf("steps_per_year", (double)M / c.T);
        setf("a0", (double)a0, "%.0f"); setf("a1", (double)a1, "%.0f"); setf("pairs", (double)n, "%.0f");
        setf("seed", (double)A.i64("seed", 20261011), "%.0f");
        setf("tpb", X.tpb, "%.0f"); setf("blocks", X.blocks, "%.0f"); setf("blocks_per_sm_occupancy", occ, "%.0f");
        setf("regs_per_thread", fa.numRegs, "%.0f"); setf("local_bytes", (double)fa.localSizeBytes, "%.0f");
        set("sync", sync); set("timed", timed ? "1" : "0");

        if (timed) {
            const int runs = std::max<int>(1, (int)A.i64("runs", 1));
            const double warm_s = A.f64("warmup-s", 0.0), win_s = A.f64("window-s", 0.0);
            X.run();                                                     // first run: context, module load
            const double tw = now_s();
            while (now_s() - tw < warm_s) X.run();
            std::vector<double> ts;
            mark("start");
            const double tw0 = now_s();
            for (int i = 0; i < runs || now_s() - tw0 < win_s; ++i) {
                const double t0 = now_s();
                X.run();
                ts.push_back(now_s() - t0);
            }
            const double twin = now_s() - tw0;
            mark("end");
            std::vector<double> so = ts;
            std::sort(so.begin(), so.end());
            setf("runs", (double)ts.size(), "%.0f");
            setf("t_window_s", twin); setf("t_per_run_s", twin / (double)ts.size());
            setf("t_compute_s", so[so.size() / 2]); setf("t_compute_min_s", so.front());
        } else {
            X.run();
        }
        setf("path_steps", psteps, "%.0f");
        const auto pr = finalize(c, (int)M, cv, X.sum.data(), (double)n, beta.data());
        bool fin = true;
        for (int k = 0; k < HS_NK; ++k) {
            const std::string s = std::to_string(k);
            fin = fin && std::isfinite(pr[k].price);
            setf("K" + s, c.K[k], "%.0f");
            setf("price" + s, pr[k].price, "%.10f");
            setf("se" + s, pr[k].se, "%.10f");
            setf("ref" + s, c.ref[k], "%.8f");
            setf("bias" + s, pr[k].price - c.ref[k], "%.10f");
            set("form" + s, ((c.puts >> k) & 1u) ? "put" : "call");
            if (c.kind == K_ASIAN) {
                setf("plain" + s, pr[k].plain, "%.10f"); setf("se_plain" + s, pr[k].se_plain, "%.10f");
                if (cv) { setf("beta" + s, beta[k], "%.9f"); setf("beta_hat" + s, pr[k].beta_hat, "%.6f"); }
            }
            setf("d1_" + s, X.sum[k], "%.17g");
            setf("dp_" + s, X.sum[3 + k], "%.17g");
        }
        set("bit_identical_to_fpga", "0");
        set("ok", fin ? "1" : "0");
        rc = fin ? 0 : 2;
        cudaFree(X.d_out);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        set("ok", "0");
        rc = 1;
    }
    setf("t_total_s", now_s() - t_start);
    for (auto& kv : R) std::printf("RESULT %s=%s\n", kv.first.c_str(), kv.second.c_str());
    return rc;
}
