// asian_avx512.hpp - AVX-512 implementation of common/asian_model.hpp (bit-identical by test).
// One zmm = 16 antithetic pairs (32 paths); NB independent zmm blocks are interleaved per step to
// hide the latency of the ICDF and exp gathers. Per step and block: one xoshiro128** draw, one ICDF
// (2 gathers), two exp(X/2) (each: 2 x 8-lane 64-bit fixed-point pipelines, 1 table gather; the power
// of two is applied with vscalefps, which is exact and equal to the P2 table: checked by the test).
// Reuses the vector RNG/ICDF of 03_MC_Heston/cpu/mc_avx512.hpp. Build with -ffp-contract=off.
#pragma once
#include <immintrin.h>
#include "asian_model.hpp"
#include "mc_avx512.hpp"

#if MC_HAVE_AVX512 && defined(__AVX512VL__)
#define AS_HAVE_AVX512 1
namespace asn {
namespace avx {

inline __m256 fexp_half_v8s(__m256 X) {
    using namespace mc;
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
    __m256 ex = _mm256_cvtepi32_ps(_mm512_cvtepi64_epi32(_mm512_sub_epi64(n, _mm512_set1_epi64(23))));
    return _mm256_scalef_ps(mf, ex);        // mf * 2^(n-23) = mf * P2[n+128], exact
}
inline __m512 fexp_half_v(__m512 X) {
    __m256 a = fexp_half_v8s(_mm512_castps512_ps256(X));
    __m256 b = fexp_half_v8s(_mm512_extractf32x8_ps(X, 1));
    return _mm512_insertf32x8(_mm512_castps256_ps512(a), b, 1);
}

// simulate NB*16 pairs a0 .. a0+NB*16-1, accumulate the first `cnt` of them into m
template <int NB>
inline void sim_blocks(const float* fp, const Keys& k, uint32_t M, uint32_t a0, int cnt, bool put, Mom& m) {
    const __m512i iota = _mm512_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
    mc::avx::XoV s[NB];
    __m512 XA[NB], XB[NB], SA[NB], SB[NB];
    for (int b = 0; b < NB; ++b) {
        s[b] = mc::avx::seed_v(k, _mm512_add_epi32(_mm512_set1_epi32((int)(a0 + 16 * b)), iota));
        XA[b] = XB[b] = _mm512_set1_ps(fp[A_X0]);
        SA[b] = SB[b] = _mm512_setzero_ps();
    }
    const __m512 DR = _mm512_set1_ps(fp[A_DR]), VOL = _mm512_set1_ps(fp[A_VOL]);
    for (uint32_t t = 0; t < M; ++t) {
        for (int b = 0; b < NB; ++b) {
            __m512 fz = _mm512_cvtepi32_ps(mc::avx::icdf_v(mc::avx::xo_next_v(s[b])));
            __m512 e = _mm512_mul_ps(VOL, fz);
            XA[b] = _mm512_add_ps(_mm512_add_ps(XA[b], DR), e);
            XB[b] = _mm512_sub_ps(_mm512_add_ps(XB[b], DR), e);
            SA[b] = _mm512_add_ps(SA[b], fexp_half_v(XA[b]));
            SB[b] = _mm512_add_ps(SB[b], fexp_half_v(XB[b]));
        }
    }
    const __m512 KM = _mm512_set1_ps(fp[A_KM]), QS = _mm512_set1_ps(fp[A_QS]), z = _mm512_setzero_ps();
    for (int b = 0; b < NB && cnt > 0; ++b) {
        alignas(64) float pa[16], pb[16];
        __m512 da = put ? _mm512_sub_ps(KM, SA[b]) : _mm512_sub_ps(SA[b], KM);
        __m512 db = put ? _mm512_sub_ps(KM, SB[b]) : _mm512_sub_ps(SB[b], KM);
        da = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(da, z, _CMP_GT_OQ), z, da);
        db = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(db, z, _CMP_GT_OQ), z, db);
        _mm512_store_ps(pa, _mm512_mul_ps(da, QS));
        _mm512_store_ps(pb, _mm512_mul_ps(db, QS));
        int c = cnt < 16 ? cnt : 16;
        for (int l = 0; l < c; ++l) {
            bool ovf = false;
            uint64_t mA = mc::avx::pm_to_m(pa[l], &ovf), mB = mc::avx::pm_to_m(pb[l], &ovf);
            m.add(mA, mB);
            m.ovf += ovf;
        }
        cnt -= c;
    }
}
template <int NB>
inline void sim_range(const float* fp, const Keys& k, uint32_t M, uint64_t a0, uint64_t a1, bool put, Mom& m) {
    for (uint64_t a = a0; a < a1; a += 16 * NB) {
        int cnt = (int)((a1 - a) < 16u * NB ? (a1 - a) : 16u * NB);
        asn::avx::sim_blocks<NB>(fp, k, M, (uint32_t)a, cnt, put, m);
    }
}

}  // namespace avx
}  // namespace asn
#else
#define AS_HAVE_AVX512 0
#endif
