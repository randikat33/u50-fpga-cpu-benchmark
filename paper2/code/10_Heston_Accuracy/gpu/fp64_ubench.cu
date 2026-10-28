// fp64_ubench.cu - addendum v0.10, Part B: throughput and energy per thread instruction of the fp64 pipe (DFMA, DADD)
// and of float<->double conversions (F2F.F64.F32 + F2F.F32.F64) at a locked SM clock. Self-contained: GPU energy from
// NVML's total-energy counter (libnvidia-ml.so.1 opened at run time), idle power measured first.
// Build (RTX 4070 Ti SUPER):  nvcc -O3 -arch=sm_89 -o build/fp64_ubench gpu/fp64_ubench.cu -ldl      (V100: -arch=sm_70)
// Check the loops survived:    cuobjdump -sass build/fp64_ubench | grep -cE "DFMA|DADD|F2F"
// Run at the registered clock: sudo nvidia-smi -lgc 2340,2340 && ./build/fp64_ubench 2340 3
// Output: one RESULT line per test: thread ops per SM per clock, average GPU power, energy per thread op above idle.
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <dlfcn.h>
#include <unistd.h>
#include <cuda_runtime.h>

#define CK(x) do { cudaError_t e_ = (x); if (e_ != cudaSuccess) { \
    std::printf("CUDA error: %s (line %d)\n", cudaGetErrorString(e_), __LINE__); std::exit(1); } } while (0)

constexpr int CHAINS = 8;   // independent dependency chains per thread, so latency does not limit the rate

__global__ void k_dfma(long iters, double b, double c, double* out) {
    double a[CHAINS];
#pragma unroll
    for (int j = 0; j < CHAINS; ++j) a[j] = 1e-3 * (threadIdx.x + j);
    for (long i = 0; i < iters; ++i) {
#pragma unroll
        for (int j = 0; j < CHAINS; ++j) asm("fma.rn.f64 %0, %1, %2, %3;" : "=d"(a[j]) : "d"(a[j]), "d"(b), "d"(c));
    }
    double s = 0.0;
#pragma unroll
    for (int j = 0; j < CHAINS; ++j) s += a[j];
    if (s == -1.0) out[blockIdx.x] = s;   // never true; keeps the loop live
}

__global__ void k_dadd(long iters, double c, double* out) {
    double a[CHAINS];
#pragma unroll
    for (int j = 0; j < CHAINS; ++j) a[j] = 1e-3 * (threadIdx.x + j);
    for (long i = 0; i < iters; ++i) {
#pragma unroll
        for (int j = 0; j < CHAINS; ++j) a[j] = a[j] + c;
    }
    double s = 0.0;
#pragma unroll
    for (int j = 0; j < CHAINS; ++j) s += a[j];
    if (s == -1.0) out[blockIdx.x] = s;
}

// float -> double -> float, with one float add so the round trip cannot be folded away.
// Per chain and iteration: two conversions (F2F.F64.F32, F2F.F32.F64) and one FADD (fp32 pipe, not the limit).
__global__ void k_cvt(long iters, float c, float* out) {
    float x[CHAINS];
#pragma unroll
    for (int j = 0; j < CHAINS; ++j) x[j] = 1e-3f * (threadIdx.x + j);
    for (long i = 0; i < iters; ++i) {
#pragma unroll
        for (int j = 0; j < CHAINS; ++j) {
            double d;
            asm volatile("cvt.f64.f32 %0, %1;" : "=d"(d) : "f"(x[j]));
            float y;
            asm volatile("cvt.rn.f32.f64 %0, %1;" : "=f"(y) : "d"(d));
            x[j] = y + c;
        }
    }
    float s = 0.0f;
#pragma unroll
    for (int j = 0; j < CHAINS; ++j) s += x[j];
    if (s == -1.0f) out[blockIdx.x] = s;
}

// ---- NVML, opened at run time (no header or link dependency)
typedef int (*nvmlInit_t)();
typedef int (*nvmlHandle_t)(unsigned int, void**);
typedef int (*nvmlEnergy_t)(void*, unsigned long long*);
static nvmlEnergy_t nvml_energy = nullptr;
static void* nvml_dev = nullptr;

static bool nvml_open() {
    void* h = dlopen("libnvidia-ml.so.1", RTLD_NOW);
    if (!h) return false;
    auto init = (nvmlInit_t)dlsym(h, "nvmlInit_v2");
    auto get = (nvmlHandle_t)dlsym(h, "nvmlDeviceGetHandleByIndex_v2");
    nvml_energy = (nvmlEnergy_t)dlsym(h, "nvmlDeviceGetTotalEnergyConsumption");
    if (!init || !get || !nvml_energy || init() != 0 || get(0, &nvml_dev) != 0) return false;
    return true;
}

static double energy_J() {            // GPU energy since driver load (mJ counter)
    unsigned long long mj = 0;
    if (nvml_energy(nvml_dev, &mj) != 0) { std::printf("NVML energy read failed\n"); std::exit(1); }
    return mj * 1e-3;
}

int main(int argc, char** argv) {
    const double clock_mhz = argc > 1 ? std::atof(argv[1]) : 2340.0;   // the locked SM clock
    const double target_s = argc > 2 ? std::atof(argv[2]) : 3.0;       // seconds per timed run
    if (!nvml_open()) { std::printf("cannot open NVML (libnvidia-ml.so.1)\n"); return 1; }
    cudaDeviceProp p;
    CK(cudaGetDeviceProperties(&p, 0));
    const int tpb = 256, blocks = p.multiProcessorCount * 8;
    double* dd; float* df;
    CK(cudaMalloc(&dd, blocks * sizeof(double)));
    CK(cudaMalloc(&df, blocks * sizeof(float)));
    cudaEvent_t e0, e1;
    CK(cudaEventCreate(&e0)); CK(cudaEventCreate(&e1));
    // idle power: 3 s with the context open and nothing running
    CK(cudaDeviceSynchronize());
    double ei = energy_J(); CK(cudaEventRecord(e0)); CK(cudaEventSynchronize(e0));
    sleep(3);
    CK(cudaEventRecord(e1)); CK(cudaEventSynchronize(e1));
    float msi = 0.0f; CK(cudaEventElapsedTime(&msi, e0, e1));
    const double p_idle = (energy_J() - ei) / (msi * 1e-3);
    std::printf("%s, %d SMs, clock %.0f MHz, %d blocks x %d threads, idle %.2f W\n", p.name, p.multiProcessorCount, clock_mhz, blocks, tpb, p_idle);
    const char* names[3] = {"DFMA", "DADD", "F2F64"};
    const double ops_per_iter[3] = {CHAINS, CHAINS, 2.0 * CHAINS};    // counted thread operations per thread per iteration
    for (int k = 0; k < 3; ++k) {
        long iters = 1 << 12;
        for (int rep = 0; rep < 5; ++rep) {          // rep 0 sizes the run to target_s; reps 1-4 are measured
            CK(cudaDeviceSynchronize());
            const double ea = energy_J();
            CK(cudaEventRecord(e0));
            if (k == 0) k_dfma<<<blocks, tpb>>>(iters, 0.9999999, 1e-9, dd);
            else if (k == 1) k_dadd<<<blocks, tpb>>>(iters, 1e-9, dd);
            else k_cvt<<<blocks, tpb>>>(iters, 1e-7f, df);
            CK(cudaGetLastError());
            CK(cudaEventRecord(e1));
            CK(cudaEventSynchronize(e1));
            const double eb = energy_J();
            float ms = 0.0f;
            CK(cudaEventElapsedTime(&ms, e0, e1));
            if (rep == 0) {
                iters = (long)(iters * target_s / (ms * 1e-3));
                if (iters < 1) iters = 1;
                continue;
            }
            const double sec = ms * 1e-3;
            const double ops = (double)blocks * tpb * iters * ops_per_iter[k];
            const double per_sm_clk = ops / sec / (p.multiProcessorCount * clock_mhz * 1e6);
            const double pw = (eb - ea) / sec;
            const double pj = (pw - p_idle) / (ops / sec) * 1e12;
            std::printf("RESULT %s rep=%d time_s=%.3f ops_per_sm_per_clk=%.3f gpu_W=%.2f idle_W=%.2f pJ_per_op_above_idle=%.2f\n",
                        names[k], rep, sec, per_sm_clk, pw, p_idle, pj);
        }
    }
    CK(cudaFree(dd)); CK(cudaFree(df));
    return 0;
}
