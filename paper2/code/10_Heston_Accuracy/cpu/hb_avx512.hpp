// hb_avx512.hpp - AVX-512 version of common/hb_model.hpp (Case B basket), bit-identical by test.
// One zmm = 16 units = the 8 assets of 2 basket pairs (units 8a .. 8a+15); the step functions are those
// of cpu/hq_avx512.hpp. At maturity the 8 quantised prices of a basket are summed as exact integers.
#pragma once
#include "hb_model.hpp"
#include "hq_avx512.hpp"

#if HQ_HAVE_AVX512
namespace hb {
namespace avx {
using namespace hq::avx;

// basket payoffs of 2 basket pairs held in one block (first cnt of them)
inline void settle_b(const float* fp, const __m512& X0v, const __m512& D2, const I64& XA, const I64& XB, int cnt, Mom& m) {
    const __m512 clampv = _mm512_set1_ps(S_CLAMP);
    I64 q[2];
    uint32_t ovf = 0;
    const I64* X[2] = {&XA, &XB};
    for (int sd = 0; sd < 2; ++sd) {
        __m512 xr = _mm512_mul_ps(to_f(*X[sd]), _mm512_set1_ps(hq::XQI));
        __m512 x = _mm512_add_ps(X0v, xr);
        __m512 S = fexp_half_v(_mm512_add_ps(x, D2));
        __mmask16 big = _mm512_cmp_ps_mask(S, clampv, _CMP_NLT_UQ);      // !(S < 2^28)
        // count only the lanes of baskets that are used
        __mmask16 used = cnt >= 2 ? (__mmask16)0xFFFF : (__mmask16)0x00FF;
        ovf += (uint32_t)__builtin_popcount((unsigned)(big & used));
        S = _mm512_mask_blend_ps(big, S, clampv);
        q[sd] = to_q_v(S, SQ);
    }
    for (int b = 0; b < cnt; ++b) {
        int64_t sa = _mm512_reduce_add_epi64(b == 0 ? q[0].lo : q[0].hi);
        int64_t sb = _mm512_reduce_add_epi64(b == 0 ? q[1].lo : q[1].hi);
        uint64_t mA[HQ_NK], mB[HQ_NK];
        uint32_t o = 0;
        bpayoff(fp, sa, mA, o);
        bpayoff(fp, sb, mB, o);
        m.add(mA, mB);
        m.ovf += o;
    }
    m.ovf += ovf;
}

// basket pairs a0 .. a0 + 2 NB - 1 (units 8 a0 .. 8 a0 + 16 NB - 1); accumulate the first cnt pairs
template <int NB>
inline void sim_blocks(const float* fp, Scheme sc, const mc::Keys& k, uint32_t M, uint32_t a0, int cnt, Mom& m) {
    const Par p(fp);
    const __m512i iota = _mm512_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
    const uint32_t u0 = HB_NA * a0;
    mc::avx::XoV s[NB];
    I64 XA[NB], XB[NB], UA[NB], UB[NB];
    __m512 VA[NB], VB[NB];
    const int64_t uq0 = hq::to_q(fp[hq::P_U0], hq::UQ);
    for (int b = 0; b < NB; ++b) {
        s[b] = mc::avx::seed_v(k, _mm512_add_epi32(_mm512_set1_epi32((int)(u0 + 16 * b)), iota));
        XA[b] = XB[b] = {_mm512_setzero_si512(), _mm512_setzero_si512()};
        UA[b] = UB[b] = {_mm512_set1_epi64(uq0), _mm512_set1_epi64(uq0)};
        VA[b] = VB[b] = _mm512_set1_ps(fp[hq::P_V0]);
    }
    if (sc == hq::FT) {
        for (uint32_t t = 0; t < M; ++t) {
            for (int b = 0; b < NB; ++b) {
                __m512i u1 = mc::avx::xo_next_v(s[b]);
                __m512i u2 = mc::avx::xo_next_v(s[b]);
                __m512 fz1 = _mm512_cvtepi32_ps(mc::avx::icdf_v(u1));
                __m512 fz2 = _mm512_cvtepi32_ps(mc::avx::icdf_v(u2));
                __m512 zs = _mm512_mul_ps(p.L2, fz1);
                __m512 a1 = _mm512_mul_ps(p.C1H, zs);
                __m512 a2 = _mm512_mul_ps(p.C2Q, fz2);
                __m512 w = _mm512_add_ps(a1, a2);
                ft_side_v(p, XA[b], UA[b], zs, w);
                ft_side_v(p, XB[b], UB[b], neg(zs), neg(w));
            }
        }
    } else {
        for (uint32_t t = 0; t < M; ++t) {
            for (int b = 0; b < NB; ++b) {
                __m512i u1 = mc::avx::xo_next_v(s[b]);
                __m512i u2 = mc::avx::xo_next_v(s[b]);
                __m512 fzq = _mm512_cvtepi32_ps(mc::avx::icdf_v(u1));
                __m512 zx = _mm512_mul_ps(p.ZS, _mm512_cvtepi32_ps(mc::avx::icdf_v(u2)));
                qe_side_v(p, XA[b], VA[b], u1, fzq, zx);
                qe_side_v(p, XB[b], VB[b], _mm512_xor_si512(u1, _mm512_set1_epi32(-1)), neg(fzq), neg(zx));
            }
        }
    }
    alignas(64) float x0[16];
    for (int l = 0; l < 16; ++l) x0[l] = fp[P_BX0 + (l & 7)];
    const __m512 X0v = _mm512_load_ps(x0), D2 = _mm512_set1_ps(fp[hq::P_D2]);
    for (int b = 0; b < NB && cnt > 0; ++b) {
        int c = cnt < 2 ? cnt : 2;
        settle_b(fp, X0v, D2, XA[b], XB[b], c, m);
        cnt -= c;
    }
}
template <int NB>
inline void sim_range(const float* fp, Scheme sc, const mc::Keys& k, uint32_t M, uint64_t a0, uint64_t a1, Mom& m) {
    for (uint64_t a = a0; a < a1; a += 2 * NB) {
        int cnt = (int)((a1 - a) < 2u * NB ? (a1 - a) : 2u * NB);
        sim_blocks<NB>(fp, sc, k, M, (uint32_t)a, cnt, m);
    }
}

}  // namespace avx
}  // namespace hb
#endif
