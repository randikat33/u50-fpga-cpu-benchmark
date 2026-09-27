// gen_tables.cpp - generates common/mc_tables.h
// (Adapted from 04_Portfolio/scripts/gen_tables.cpp: identical ICDF/exp numerics, MC_ prefix.)
//
//  1. Inverse-normal ROM (2048 entries, 54-bit words) for the segmented quadratic ICDF:
//       mag index mi (31 bits) -> lz = clz31(mi) (0..31), n = mi << lz,
//       seg = {lz, n[29:24]}, x = n[23:9] (15 bits)
//       a = (c2*x) >> 15 ; b = c1 + a ; p = (b*x) >> 15 ; zi = c0 + p  (units 2^-22)
//       z = (zi + 2) >> 2  (units 2^-20),  approximating f(mi) = -PhiInv((mi+0.5)/2^32)
//     word = c0[53:29] (25 bit unsigned) | c1[28:12] (17 bit signed) | c2[11:0] (12 bit signed)
//     Every product fits a signed 32-bit integer (checked here), so the CPU AVX-512 code can
//     use 16-lane vpmulld and the FPGA 2 DSP48E2 per normal.
//  2. exp tables: T[i] = round(2^(i/256) * 2^31), P2[k] = 2^(k-151) (float, exact).
//
// Build: g++ -O2 -std=c++17 gen_tables.cpp -o gen_tables && ./gen_tables > mc_tables.h
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <algorithm>

typedef long double LD;

// Acklam's rational approximation + two Halley refinements (long double) -> ~1e-18
static LD phi_inv(LD p) {
    static const double a[] = {-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
                               1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00};
    static const double b[] = {-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
                               6.680131188771972e+01, -1.328068155288572e+01};
    static const double c[] = {-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
                               -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00};
    static const double d[] = {7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
                               3.754408661907416e+00};
    LD x;
    const LD pl = 0.02425L;
    if (p < pl) {
        LD q = std::sqrt(-2 * std::log(p));
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    } else if (p <= 1 - pl) {
        LD q = p - 0.5L, r = q * q;
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
    } else {
        LD q = std::sqrt(-2 * std::log(1 - p));
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    }
    for (int it = 0; it < 2; ++it) {
        LD e = 0.5L * std::erfc(-x / std::sqrt(2.0L)) - p;
        LD u = e * std::sqrt(2 * M_PIl) * std::exp(x * x / 2);
        x = x - u / (1 + x * u / 2);
    }
    return x;
}
// target magnitude in units of 2^-22
static LD ftarget(uint32_t mi) { return -phi_inv(((LD)mi + 0.5L) / 4294967296.0L) * 4194304.0L; }

static inline int clz31(uint32_t m) { return m ? __builtin_clz(m) - 1 : 31; }

static inline int64_t eval_int(int64_t c0, int64_t c1, int64_t c2, int64_t x) {
    int64_t a = (c2 * x) >> 15;
    int64_t bb = c1 + a;
    int64_t p = (bb * x) >> 15;
    return c0 + p;
}

struct Pt { int32_t x; LD lo, hi; };  // target range of the bucket (units 2^-22)

int main() {
    std::vector<uint64_t> rom(2048, 0);
    double worst = 0;         // worst |zi - f| in units of 2^-22 (before final rounding)
    long long worst_b = 0, worst_c2 = 0, worst_c0 = 0;
    for (int lz = 0; lz <= 31; ++lz) {
        for (int j = 0; j < 64; ++j) {
            std::vector<Pt> pts;
            if (lz == 31) {
                if (j == 0) pts.push_back({0, ftarget(0), ftarget(0)});
            } else if (lz <= 8) {
                // each 15-bit x bucket holds 2^(9-lz) consecutive mi values
                uint32_t span = 1u << (9 - lz);
                for (int x = 0; x < 32768; ++x) {
                    uint64_t n = (1ull << 30) + ((uint64_t)j << 24) + ((uint64_t)x << 9);
                    uint32_t m0 = (uint32_t)(n >> lz);
                    uint32_t m1 = m0 + span - 1;
                    pts.push_back({x, ftarget(m1), ftarget(m0)});   // f decreasing
                }
            } else {
                // every mi is its own point
                uint64_t n0 = (1ull << 30) + ((uint64_t)j << 24);
                uint64_t n1 = n0 + (1ull << 24);
                uint64_t step = 1ull << lz;
                uint64_t first = (n0 + step - 1) / step * step;
                for (uint64_t n = first; n < n1; n += step) {
                    uint32_t m = (uint32_t)(n >> lz);
                    int x = (int)((n >> 9) & 0x7FFF);
                    LD f = ftarget(m);
                    pts.push_back({x, f, f});
                }
            }
            if (pts.empty()) continue;
            // least squares on midpoints, degree min(2, distinct x - 1)
            int distinct = 1;
            for (size_t k = 1; k < pts.size(); ++k) distinct += pts[k].x != pts[k - 1].x;
            int deg = std::min(2, distinct - 1);
            LD C[3] = {0, 0, 0};
            {
                LD A[3][3] = {{0}}, B[3] = {0};
                for (auto& p : pts) {
                    LD xn = p.x / 32768.0L, y = (p.lo + p.hi) / 2;
                    LD pw[3] = {1, xn, xn * xn};
                    for (int r = 0; r <= deg; ++r) {
                        B[r] += pw[r] * y;
                        for (int c = 0; c <= deg; ++c) A[r][c] += pw[r] * pw[c];
                    }
                }
                int nn = deg + 1;  // Gauss elimination
                for (int r = 0; r < nn; ++r) {
                    int piv = r;
                    for (int k = r + 1; k < nn; ++k) if (std::fabs(A[k][r]) > std::fabs(A[piv][r])) piv = k;
                    for (int c = 0; c < nn; ++c) std::swap(A[r][c], A[piv][c]);
                    std::swap(B[r], B[piv]);
                    for (int k = r + 1; k < nn; ++k) {
                        LD f = A[k][r] / A[r][r];
                        for (int c = r; c < nn; ++c) A[k][c] -= f * A[r][c];
                        B[k] -= f * B[r];
                    }
                }
                for (int r = nn - 1; r >= 0; --r) {
                    LD s = B[r];
                    for (int c = r + 1; c < nn; ++c) s -= A[r][c] * C[c];
                    C[r] = s / A[r][r];
                }
            }
            // quantise, then local minimax search over (c1, c2) with c0 re-centred
            auto maxerr = [&](int64_t c0, int64_t c1, int64_t c2, LD& emin, LD& emax) {
                emin = 1e30L; emax = -1e30L;
                for (auto& p : pts) {
                    int64_t z = eval_int(c0, c1, c2, p.x);
                    LD e1 = (LD)z - p.lo, e2 = (LD)z - p.hi;
                    emin = std::min(emin, std::min(e1, e2));
                    emax = std::max(emax, std::max(e1, e2));
                }
            };
            int64_t q1 = std::llround(C[1]), q2 = std::llround(C[2]);
            int64_t best0 = 0, best1 = q1, best2 = q2; LD bestE = 1e30L;
            int R1 = deg >= 1 ? 2 : 0, R2 = deg >= 2 ? 2 : 0;
            for (int d1 = -R1; d1 <= R1; ++d1)
                for (int d2 = -R2; d2 <= R2; ++d2) {
                    int64_t c1 = q1 + d1, c2 = q2 + d2;
                    int64_t c0 = std::llround(C[0]);
                    LD emin, emax;
                    maxerr(c0, c1, c2, emin, emax);
                    c0 -= std::llround((emin + emax) / 2);   // centre the error band
                    maxerr(c0, c1, c2, emin, emax);
                    LD e = std::max(-emin, emax);
                    if (e < bestE) { bestE = e; best0 = c0; best1 = c1; best2 = c2; }
                }
            // range checks for the fixed-point datapath
            if (best0 < 0 || best0 >= (1 << 25) || best1 < -(1 << 16) || best1 >= (1 << 16) ||
                best2 < -(1 << 11) || best2 >= (1 << 11)) {
                std::fprintf(stderr, "coefficient range violated lz=%d j=%d c0=%lld c1=%lld c2=%lld\n", lz, j,
                             (long long)best0, (long long)best1, (long long)best2);
                return 1;
            }
            for (int x = 0; x < 32768; x += 1) {
                int64_t bb = best1 + ((best2 * x) >> 15);
                if (bb < -65536 || bb > 65535) { std::fprintf(stderr, "b range lz=%d j=%d\n", lz, j); return 1; }
                worst_b = std::max<long long>(worst_b, std::llabs(bb));
            }
            worst = std::max<double>(worst, (double)bestE);
            worst_c2 = std::max<long long>(worst_c2, std::llabs(best2));
            worst_c0 = std::max<long long>(worst_c0, best0);
            uint64_t w = ((uint64_t)best0 << 29) | (((uint64_t)best1 & 0x1FFFF) << 12) | ((uint64_t)best2 & 0xFFF);
            rom[(lz << 6) | j] = w;
        }
    }
    std::fprintf(stderr, "ICDF: worst fit error %.3f units of 2^-22 (%.3g), max|b|=%lld max|c2|=%lld max c0=%lld\n",
                 worst, worst / 4194304.0, worst_b, worst_c2, worst_c0);

    std::printf("// mc_tables.h - GENERATED by scripts/gen_tables.cpp. Do not edit.\n");
    std::printf("// ICDF ROM: word = c0[53:29] | c1[28:12] (signed) | c2[11:0] (signed); worst fit error %.3f x 2^-22\n", worst);
    std::printf("#pragma once\n#include <cstdint>\n\n");
    std::printf("#define MC_ICDF_ROM_INIT {");
    for (int i = 0; i < 2048; ++i) std::printf("%s0x%014llxull", i ? (i % 4 ? "," : ", \\\n  ") : " \\\n  ", (unsigned long long)rom[i]);
    std::printf("}\n\n");
    std::printf("#define MC_EXP_T_INIT {");
    for (int i = 0; i < 256; ++i) {
        LD v = std::pow(2.0L, i / 256.0L) * 2147483648.0L;
        unsigned long long t = (unsigned long long)std::llround(v);
        std::printf("%s0x%08llxu", i ? (i % 6 ? "," : ", \\\n  ") : " \\\n  ", t);
    }
    std::printf("}\n\n");
    std::printf("// P2[k] = 2^(k-151); entries that are not normal floats are 0 (never addressed)\n");
    std::printf("#define MC_EXP_P2_INIT {");
    for (int k = 0; k < 256; ++k) {
        int e = k - 151;
        std::printf("%s", k ? (k % 6 ? "," : ", \\\n  ") : " \\\n  ");
        if (e < -126 || e > 127) std::printf("0.0f");
        else std::printf("0x1p%+df", e);
    }
    std::printf("}\n");
    return 0;
}
