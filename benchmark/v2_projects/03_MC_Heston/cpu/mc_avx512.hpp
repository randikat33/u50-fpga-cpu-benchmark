// mc_avx512.hpp - AVX-512 implementation of common/mc_model.hpp (bit-identical by test).
// One zmm = 16 antithetic pairs (32 paths). Per step: 16 xoshiro128** draws, 16 ICDFs
// (2 gathers + 2 vpmulld each), Cholesky tree, and the two per-side asset updates.
// Needs AVX512F/CD/DQ (Ice Lake-SP: yes). Build with -ffp-contract=off.
#pragma once
#include <immintrin.h>
#include "mc_model.hpp"

#if defined(__AVX512F__) && defined(__AVX512CD__) && defined(__AVX512DQ__)
#define MC_HAVE_AVX512 1
namespace mc {
namespace avx {

// c0 and packed (c1<<12 | c2) int32 tables: two 16-lane gathers per ICDF.
// (Measured alternatives on Xeon, all slower: one 64-bit gather per 8 lanes (+27 %), vpermi2d
//  lookups for the lz<=1 octaves plus masked tail gathers (+28 %).)
struct Tabs {
    int32_t c0[2048], c12[2048];
    Tabs() {
        const uint64_t* r = icdf_rom();
        for (int i = 0; i < 2048; ++i) {
            c0[i] = rom_c0(r[i]);
            c12[i] = (int32_t)((uint32_t)rom_c1(r[i]) << 12) | (rom_c2(r[i]) & 0xFFF);
        }
    }
};
inline const Tabs& tabs() { static const Tabs t; return t; }

inline __m512i fmix32_v(__m512i h) {
    h = _mm512_xor_si512(h, _mm512_srli_epi32(h, 16));
    h = _mm512_mullo_epi32(h, _mm512_set1_epi32((int)0x85ebca6bu));
    h = _mm512_xor_si512(h, _mm512_srli_epi32(h, 13));
    h = _mm512_mullo_epi32(h, _mm512_set1_epi32((int)0xc2b2ae35u));
    h = _mm512_xor_si512(h, _mm512_srli_epi32(h, 16));
    return h;
}
struct XoV { __m512i s[4]; };
inline XoV seed_v(const Keys& k, __m512i a) {
    XoV x;
    for (int j = 0; j < 4; ++j) x.s[j] = fmix32_v(_mm512_xor_si512(a, _mm512_set1_epi32((int)k.k[j])));
    __m512i o = _mm512_or_si512(_mm512_or_si512(x.s[0], x.s[1]), _mm512_or_si512(x.s[2], x.s[3]));
    __mmask16 z = _mm512_testn_epi32_mask(o, o);
    x.s[0] = _mm512_mask_blend_epi32(z, x.s[0], _mm512_set1_epi32((int)0x9E3779B9u));
    return x;
}
inline __m512i xo_next_v(XoV& x) {
    __m512i s1 = x.s[1];
    __m512i m5 = _mm512_add_epi32(s1, _mm512_slli_epi32(s1, 2));
    __m512i r7 = _mm512_rol_epi32(m5, 7);
    __m512i res = _mm512_add_epi32(r7, _mm512_slli_epi32(r7, 3));
    __m512i t = _mm512_slli_epi32(s1, 9);
    x.s[2] = _mm512_xor_si512(x.s[2], x.s[0]);
    x.s[3] = _mm512_xor_si512(x.s[3], x.s[1]);
    x.s[1] = _mm512_xor_si512(x.s[1], x.s[2]);
    x.s[0] = _mm512_xor_si512(x.s[0], x.s[3]);
    x.s[2] = _mm512_xor_si512(x.s[2], t);
    x.s[3] = _mm512_rol_epi32(x.s[3], 11);
    return res;
}
inline __m512i icdf_v(__m512i u) {
    const Tabs& T = tabs();
    __m512i m31 = _mm512_set1_epi32(0x7FFFFFFF);
    __mmask16 neg = _mm512_movepi32_mask(u);                       // sign bit
    __m512i mi = _mm512_and_si512(_mm512_xor_si512(u, _mm512_srai_epi32(u, 31)), m31);
    __m512i lz = _mm512_sub_epi32(_mm512_lzcnt_epi32(mi), _mm512_set1_epi32(1));
    __m512i n = _mm512_sllv_epi32(mi, lz);
    __m512i idx = _mm512_or_si512(_mm512_slli_epi32(lz, 6),
                                  _mm512_and_si512(_mm512_srli_epi32(n, 24), _mm512_set1_epi32(63)));
    __m512i x = _mm512_and_si512(_mm512_srli_epi32(n, 9), _mm512_set1_epi32(0x7FFF));
    __m512i c0 = _mm512_i32gather_epi32(idx, T.c0, 4);
    __m512i c12 = _mm512_i32gather_epi32(idx, T.c12, 4);
    __m512i c2 = _mm512_srai_epi32(_mm512_slli_epi32(c12, 20), 20);
    __m512i c1 = _mm512_srai_epi32(c12, 12);
    __m512i a = _mm512_srai_epi32(_mm512_mullo_epi32(c2, x), 15);
    __m512i b = _mm512_add_epi32(c1, a);
    __m512i p = _mm512_srai_epi32(_mm512_mullo_epi32(b, x), 15);
    __m512i zi = _mm512_add_epi32(c0, p);
    __m512i zr = _mm512_srai_epi32(_mm512_add_epi32(zi, _mm512_set1_epi32(2)), 2);
    return _mm512_mask_blend_epi32(neg, _mm512_sub_epi32(_mm512_setzero_si512(), zr), zr);
}
inline __m512 tree_v(const __m512* t, int n) {
    __m512 a[8], b[4];
    int na = (n + 1) / 2;
    for (int k = 0; k < na; ++k) a[k] = (2 * k + 1 < n) ? _mm512_add_ps(t[2 * k], t[2 * k + 1]) : t[2 * k];
    int nb = (na + 1) / 2;
    for (int k = 0; k < nb; ++k) b[k] = (2 * k + 1 < na) ? _mm512_add_ps(a[2 * k], a[2 * k + 1]) : a[2 * k];
    if (nb == 1) return b[0];
    __m512 c0 = _mm512_add_ps(b[0], b[1]);
    if (nb == 2) return c0;
    __m512 c1 = (nb == 4) ? _mm512_add_ps(b[2], b[3]) : b[2];
    return _mm512_add_ps(c0, c1);
}
inline __m256 fexp_half_v8(__m256 X) {
    __m256 hi = _mm256_set1_ps(128.0f), lo = _mm256_set1_ps(-128.0f);
    __m256 xc0 = _mm256_blendv_ps(hi, X, _mm256_cmp_ps(X, hi, _CMP_LE_OQ));
    __m256 xc = _mm256_blendv_ps(lo, xc0, _mm256_cmp_ps(X, lo, _CMP_GE_OQ));
    __m256 xs = _mm256_mul_ps(xc, _mm256_set1_ps(8388608.0f));
    __m256i xq = _mm256_cvtps_epi32(_mm256_floor_ps(xs));
    __m512i y = _mm512_mul_epi32(_mm512_cvtepi32_epi64(xq), _mm512_set1_epi64(INVLN2_Q30));
    __m512i n = _mm512_srai_epi64(y, 54);
    __m512i fr = _mm512_and_si512(_mm512_srli_epi64(y, 22), _mm512_set1_epi64(0xFFFFFFFFll));
    __m512i i = _mm512_srli_epi64(fr, 24);
    __m512i fl = _mm512_and_si512(fr, _mm512_set1_epi64(0xFFFFFF));
    __m512i d = _mm512_srli_epi64(_mm512_mul_epu32(fl, _mm512_set1_epi64((long long)LN2_Q32)), 32);
    __m512i dd = _mm512_srli_epi64(_mm512_mul_epu32(d, d), 33);
    __m512i g = _mm512_add_epi64(d, dd);
    __m512i T = _mm512_cvtepu32_epi64(_mm512_i64gather_epi32(i, (const int*)exp_t(), 4));
    __m512i Tg = _mm512_srli_epi64(_mm512_mul_epu32(T, g), 32);
    __m512i M = _mm512_min_epu64(_mm512_add_epi64(T, Tg), _mm512_set1_epi64((long long)EXP_MLIM));
    __m512i m24 = _mm512_srli_epi64(_mm512_add_epi64(M, _mm512_set1_epi64(128)), 8);
    __m256 mf = _mm512_cvtepi64_ps(m24);
    __m256 p2 = _mm512_i64gather_ps(_mm512_add_epi64(n, _mm512_set1_epi64(128)), exp_p2(), 4);
    return _mm256_mul_ps(mf, p2);
}
inline __m512 fexp_half_v(__m512 X) {
    __m256 a = fexp_half_v8(_mm512_castps512_ps256(X));
    __m256 b = fexp_half_v8(_mm512_extractf32x8_ps(X, 1));
    return _mm512_insertf32x8(_mm512_castps256_ps512(a), b, 1);
}
inline __m512 payoff_pm_v(const float* fp, const __m512 X[MC_NA], bool put) {
    __m512 S[MC_NA];
    for (int i = 0; i < MC_NA; ++i) S[i] = fexp_half_v(_mm512_add_ps(X[i], _mm512_set1_ps(fp[F_D2 + i])));
    __m512 B8 = tree_v(S, MC_NA);
    __m512 K8 = _mm512_set1_ps(fp[F_K8]);
    __m512 d = put ? _mm512_sub_ps(K8, B8) : _mm512_sub_ps(B8, K8);
    __m512 z = _mm512_setzero_ps();
    __m512 po = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(d, z, _CMP_GT_OQ), z, d);
    return _mm512_mul_ps(po, _mm512_set1_ps(fp[F_QS]));
}
inline uint64_t pm_to_m(float pm, bool* ovf) {
    if (!(pm < M_SAT_F)) { *ovf = true; return (1ull << MC_MBITS) - 1; }
    return (uint64_t)pm;
}

// simulate the 16 pairs a0..a0+15, accumulate the first `cnt` of them into m
inline void sim_block(const float* fp, const Keys& k, uint32_t M, uint32_t a0, int cnt, bool put, Mom& m) {
    __m512i a = _mm512_add_epi32(_mm512_set1_epi32((int)a0),
                                 _mm512_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15));
    XoV s = seed_v(k, a);
    __m512 XA[MC_NA], UA[MC_NA], XB[MC_NA], UB[MC_NA];
    __m512 L2[MC_NL], C1H[MC_NA], C2Q[MC_NA], OMK[MC_NA], KTH2[MC_NA];
    for (int i = 0; i < MC_NA; ++i) {
        XA[i] = XB[i] = _mm512_set1_ps(fp[F_X0 + i]);
        UA[i] = UB[i] = _mm512_set1_ps(fp[F_U0 + i]);
        C1H[i] = _mm512_set1_ps(fp[F_C1H + i]); C2Q[i] = _mm512_set1_ps(fp[F_C2Q + i]);
        OMK[i] = _mm512_set1_ps(fp[F_OMK + i]); KTH2[i] = _mm512_set1_ps(fp[F_KTH2 + i]);
    }
    for (int i = 0; i < MC_NL; ++i) L2[i] = _mm512_set1_ps(fp[F_L2 + i]);
    const __m512 z = _mm512_setzero_ps();
    const __m512i sgn = _mm512_set1_epi32((int)0x80000000u);
    for (uint32_t t = 0; t < M; ++t) {
        __m512 fz[MC_NZ];
        for (int j = 0; j < MC_NZ; ++j) fz[j] = _mm512_cvtepi32_ps(icdf_v(xo_next_v(s)));
        for (int i = 0; i < MC_NA; ++i) {
            __m512 tt[MC_NA];
            for (int j = 0; j <= i; ++j) tt[j] = _mm512_mul_ps(L2[tri(i, j)], fz[j]);
            __m512 zS2 = tree_v(tt, i + 1);
            __m512 w = _mm512_add_ps(_mm512_mul_ps(C1H[i], zS2), _mm512_mul_ps(C2Q[i], fz[MC_NA + i]));
            __m512 nz = _mm512_castsi512_ps(_mm512_xor_si512(_mm512_castps_si512(zS2), sgn));
            __m512 nw = _mm512_castsi512_ps(_mm512_xor_si512(_mm512_castps_si512(w), sgn));
            for (int sd = 0; sd < 2; ++sd) {
                __m512& X = sd ? XB[i] : XA[i];
                __m512& U = sd ? UB[i] : UA[i];
                __m512 zs = sd ? nz : zS2, ww = sd ? nw : w;
                __m512 P = _mm512_sqrt_ps(U);
                __m512 h = _mm512_add_ps(_mm512_mul_ps(U, OMK[i]), KTH2[i]);
                __m512 un = _mm512_add_ps(h, _mm512_mul_ps(P, ww));
                __m512 y = _mm512_sub_ps(_mm512_mul_ps(P, zs), U);
                X = _mm512_add_ps(X, y);
                U = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(un, z, _CMP_GT_OQ), z, un);
            }
        }
    }
    alignas(64) float pa[16], pb[16];
    _mm512_store_ps(pa, payoff_pm_v(fp, XA, put));
    _mm512_store_ps(pb, payoff_pm_v(fp, XB, put));
    for (int l = 0; l < cnt; ++l) {
        bool ovf = false;
        uint64_t mA = pm_to_m(pa[l], &ovf), mB = pm_to_m(pb[l], &ovf);
        m.add(mA, mB);
        m.ovf += ovf;
    }
}
inline void sim_range(const float* fp, const Keys& k, uint32_t M, uint64_t a0, uint64_t a1, bool put, Mom& m) {
    for (uint64_t a = a0; a < a1; a += 16) {
        int cnt = (int)((a1 - a) < 16 ? (a1 - a) : 16);
        sim_block(fp, k, M, (uint32_t)a, cnt, put, m);
    }
}

}  // namespace avx
}  // namespace mc
#else
#define MC_HAVE_AVX512 0
#endif
