// fir_ref.hpp - REFERENCE MODEL of the int16 FIR filter (out-of-sample streaming workload).
//
// y[n] = sat16( (acc + 2^14) >> 15 ),  acc = sum_{k=0}^{T-1} c[k] * x[n-k]   (x[n] = 0 for n < 0)
// int16 samples and Q15 coefficients; products are exact int32; the sum is taken modulo 2^32
// (two's-complement wrap-around, as in the FPGA adder tree and in vpmaddwd/vpdpwssd), so the
// summation order does not matter and every implementation is bit-identical. With the default
// coefficients (sum |c| x 2^15 < 2^31) the sum never wraps anyway.
// Shared by kernel/fir_v2.cpp (HLS), cpu/fir_cpu.cpp and the host (--verify).
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

#include "fir_params.h"

namespace fir {

// default coefficients: Hamming-windowed sinc low-pass, cut-off 0.2 x sample rate, Q15, DC gain 1
inline std::vector<int16_t> lowpass(int T, double fc = 0.2) {
    std::vector<double> h(T);
    double s = 0;
    for (int k = 0; k < T; ++k) {
        double m = k - (T - 1) / 2.0;
        double sinc = m == 0 ? 2 * fc : std::sin(2 * M_PI * fc * m) / (M_PI * m);
        h[k] = sinc * (0.54 - 0.46 * std::cos(2 * M_PI * k / (T - 1)));
        s += h[k];
    }
    std::vector<int16_t> c(T);
    for (int k = 0; k < T; ++k) c[k] = (int16_t)std::lround(h[k] / s * 32767.0);
    return c;
}

// deterministic test signal: two tones plus hashed noise, int16 (sample index i >= 0)
inline uint64_t mix64(uint64_t z) {
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}
inline int16_t gen(uint64_t seed, uint64_t i) {
    int32_t noise = (int32_t)(int16_t)(mix64(seed * 0x9E3779B97F4A7C15ull + i) >> 48) / 4;   // |.| <= 8192
    int32_t t1 = (int32_t)(8000.0 * std::sin(0.01 * (double)(i % 628318)));
    int32_t t2 = (int32_t)(6000.0 * std::sin(1.9 * (double)(i % 331)));
    return (int16_t)(noise + t1 + t2);                                             // |.| < 22193
}
// fill x[0..n) (the signal from sample index i0) - used identically by every program
inline void gen_block(uint64_t seed, uint64_t i0, int16_t* x, size_t n) {
    for (size_t j = 0; j < n; ++j) x[j] = gen(seed, i0 + j);
}

inline int16_t sat_round(uint32_t acc) {
    int32_t a = (int32_t)(acc + 16384u);
    a >>= 15;                                      // arithmetic shift (two's complement)
    return (int16_t)(a > 32767 ? 32767 : (a < -32768 ? -32768 : a));
}
// one output; x points at sample n and x[-1..-(T-1)] must be readable
inline int16_t out1(const int16_t* x, const int16_t* c, int T) {
    uint32_t acc = 0;
    for (int k = 0; k < T; ++k) acc += (uint32_t)((int32_t)c[k] * (int32_t)x[-k]);
    return sat_round(acc);
}
// y[0..n) from x[0..n), where x[-1..-(T-1)] are the history (zeros at the start of a stream)
inline void filter_ref(const int16_t* x, int16_t* y, size_t n, const int16_t* c, int T) {
    for (size_t i = 0; i < n; ++i) y[i] = out1(x + i, c, T);
}

inline uint64_t fnv1a64(const void* p, size_t n, uint64_t h = 1469598103934665603ull) {
    const uint8_t* b = static_cast<const uint8_t*>(p);
    for (size_t i = 0; i < n; ++i) { h ^= b[i]; h *= 1099511628211ull; }
    return h;
}

}  // namespace fir
