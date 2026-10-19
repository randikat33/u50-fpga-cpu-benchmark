// ha_avx512.hpp - AVX-512 version of common/ha_model.hpp (Case A, Asian), bit-identical by test.
// One zmm = 16 antithetic pairs; per step: one draw, one ICDF, per side an int64 state update, one
// exp(X/2) and two exact int64 sums. Step and exp functions from cpu/hq_avx512.hpp.
#pragma once
#include "ha_model.hpp"
#include "hq_avx512.hpp"

#if HQ_HAVE_AVX512
namespace ha {
namespace avx {
using namespace hq::avx;

struct SideV { I64 Xi, Aq, Gq; };

inline void side_step_v(SideV& s, __m512 inc, __m512 X0, __m512 clampv, __mmask16& big) {
    s.Xi = add(s.Xi, to_q_v(inc, hq::XQ));
    __m512 xr = _mm512_mul_ps(to_f(s.Xi), _mm512_set1_ps(hq::XQI));
    __m512 x = _mm512_add_ps(X0, xr);
    __m512 S = fexp_half_v(x);
    __mmask16 b = _mm512_cmp_ps_mask(S, clampv, _CMP_NLT_UQ);
    big |= b;
    S = _mm512_mask_blend_ps(b, S, clampv);
    s.Aq = add(s.Aq, to_q_v(S, AQ));
    s.Gq = add(s.Gq, s.Xi);
}

// payoffs of 16 pairs -> moments (first cnt pairs)
inline void settle_v(const float* fp, const SideV& A, const SideV& B, int cnt, uint32_t clamps, AMom& m) {
    alignas(64) float py[2][HQ_NK][16], pg[2][HQ_NK][16];
    const SideV* sd[2] = {&A, &B};
    const __m512 X0 = _mm512_set1_ps(fp[A_X0]), z = _mm512_setzero_ps(), QS = _mm512_set1_ps(fp[A_QS]);
    for (int h = 0; h < 2; ++h) {
        __m512 Av = _mm512_mul_ps(to_f(sd[h]->Aq), _mm512_set1_ps(fp[A_CAM]));
        __m512 gm = _mm512_mul_ps(to_f(sd[h]->Gq), _mm512_set1_ps(fp[A_CGM]));
        __m512 G = fexp_half_v(_mm512_add_ps(X0, gm));
        for (int k = 0; k < HQ_NK; ++k) {
            __m512 K = _mm512_set1_ps(fp[A_K + k]);
            __m512 dy = _mm512_sub_ps(Av, K), dg = _mm512_sub_ps(G, K);
            dy = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(dy, z, _CMP_GT_OQ), z, dy);
            dg = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(dg, z, _CMP_GT_OQ), z, dg);
            _mm512_store_ps(py[h][k], _mm512_mul_ps(dy, QS));
            _mm512_store_ps(pg[h][k], _mm512_mul_ps(dg, QS));
        }
    }
    auto toq = [](float v, uint32_t& o) -> uint64_t {
        if (!(v < hq::M_SAT_F)) { o += 1; return (1ull << 40) - 1; }
        return (uint64_t)v;
    };
    for (int l = 0; l < cnt; ++l) {
        uint64_t yA[HQ_NK], yB[HQ_NK], gA[HQ_NK], gB[HQ_NK];
        uint32_t o = 0;
        for (int k = 0; k < HQ_NK; ++k) {
            yA[k] = toq(py[0][k][l], o); gA[k] = toq(pg[0][k][l], o);
            yB[k] = toq(py[1][k][l], o); gB[k] = toq(pg[1][k][l], o);
        }
        m.add(yA, yB, gA, gB);
        m.ovf += o;
    }
    m.ovf += clamps;
}

template <int NB>
inline void sim_blocks(const float* fp, const mc::Keys& k, uint32_t M, uint32_t a0, int cnt, AMom& m) {
    const __m512i iota = _mm512_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
    mc::avx::XoV s[NB];
    SideV A[NB], B[NB];
    const I64 zero{_mm512_setzero_si512(), _mm512_setzero_si512()};
    for (int b = 0; b < NB; ++b) {
        s[b] = mc::avx::seed_v(k, _mm512_add_epi32(_mm512_set1_epi32((int)(a0 + 16 * b)), iota));
        A[b] = B[b] = {zero, zero, zero};
    }
    const __m512 DR = _mm512_set1_ps(fp[A_DR]), VOL = _mm512_set1_ps(fp[A_VOL]), X0 = _mm512_set1_ps(fp[A_X0]);
    const __m512 clampv = _mm512_set1_ps(S_CLAMP);
    // clamp events per (step, side, lane); counted per pair lane (only lanes < cnt are used pairs)
    uint32_t clamps[NB] = {};
    for (uint32_t t = 0; t < M; ++t) {
        for (int b = 0; b < NB; ++b) {
            __m512 fz = _mm512_cvtepi32_ps(mc::avx::icdf_v(mc::avx::xo_next_v(s[b])));
            __m512 e = _mm512_mul_ps(VOL, fz);
            __m512 ia = _mm512_add_ps(DR, e);
            __m512 ib = _mm512_sub_ps(DR, e);
            __mmask16 ba = 0, bb = 0;
            side_step_v(A[b], ia, X0, clampv, ba);
            side_step_v(B[b], ib, X0, clampv, bb);
            if (ba | bb) {
                int c = cnt - 16 * b;
                __mmask16 used = c >= 16 ? (__mmask16)0xFFFF : (__mmask16)((1u << (c > 0 ? c : 0)) - 1u);
                clamps[b] += (uint32_t)__builtin_popcount((unsigned)(ba & used)) + (uint32_t)__builtin_popcount((unsigned)(bb & used));
            }
        }
    }
    for (int b = 0; b < NB && cnt > 0; ++b) {
        int c = cnt < 16 ? cnt : 16;
        settle_v(fp, A[b], B[b], c, clamps[b], m);
        cnt -= c;
    }
}
template <int NB>
inline void sim_range(const float* fp, const mc::Keys& k, uint32_t M, uint64_t a0, uint64_t a1, AMom& m) {
    for (uint64_t a = a0; a < a1; a += 16 * NB) {
        int cnt = (int)((a1 - a) < 16u * NB ? (a1 - a) : 16u * NB);
        sim_blocks<NB>(fp, k, M, (uint32_t)a, cnt, m);
    }
}

}  // namespace avx
}  // namespace ha
#endif
