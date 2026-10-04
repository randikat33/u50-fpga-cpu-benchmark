// membw.cpp - achievable DRAM bandwidth of this server (STREAM-style copy / triad / read, OpenMP).
// Three arrays of --mib MiB each, first-touched by the threads that use them (NUMA-local), best of --reps.
// Bytes are counted as in STREAM (no write-allocate traffic): copy 16 B, triad 24 B, read 8 B per element.
// Used to state how close the streaming CPU baselines (AES, convolution, FIR) come to the memory limit.
#include <omp.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <chrono>
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int main(int argc, char** argv) {
    size_t mib = 1024; int reps = 10;
    for (int i = 1; i + 1 < argc; ++i) {
        if (!std::strcmp(argv[i], "--mib")) mib = std::strtoull(argv[i + 1], nullptr, 10);
        if (!std::strcmp(argv[i], "--reps")) reps = std::atoi(argv[i + 1]);
    }
    const size_t n = mib * 1024 * 1024 / sizeof(double);
    double *a, *b, *c;
    if (posix_memalign((void**)&a, 2 << 20, n * 8) || posix_memalign((void**)&b, 2 << 20, n * 8) ||
        posix_memalign((void**)&c, 2 << 20, n * 8)) { std::puts("alloc failed"); return 1; }
#pragma omp parallel for schedule(static)
    for (size_t i = 0; i < n; ++i) { a[i] = 1.0; b[i] = 2.0; c[i] = 0.5; }
    double bc = 0, bt = 0, br = 0, sink = 0;
    std::printf("MARK start %.6f\n", now()); std::fflush(stdout);
    for (int r = 0; r < reps; ++r) {
        double t = now();
#pragma omp parallel for schedule(static)
        for (size_t i = 0; i < n; ++i) a[i] = b[i];
        bc = std::max(bc, 16.0 * n / (now() - t) / 1e9);
        t = now();
#pragma omp parallel for schedule(static)
        for (size_t i = 0; i < n; ++i) a[i] = b[i] + 3.0 * c[i];
        bt = std::max(bt, 24.0 * n / (now() - t) / 1e9);
        t = now();
        double s = 0;
#pragma omp parallel for schedule(static) reduction(+ : s)
        for (size_t i = 0; i < n; ++i) s += a[i];
        br = std::max(br, 8.0 * n / (now() - t) / 1e9);
        sink += s;
    }
    std::printf("MARK end %.6f\n", now());
    std::printf("RESULT threads=%d\nRESULT mib_per_array=%zu\nRESULT copy_gbps=%.2f\nRESULT triad_gbps=%.2f\n"
                "RESULT read_gbps=%.2f\nRESULT t_compute_s=0\nRESULT ok=%d\n",
                omp_get_max_threads(), mib, bc, bt, br, sink > 0 ? 1 : 0);
    return 0;
}
