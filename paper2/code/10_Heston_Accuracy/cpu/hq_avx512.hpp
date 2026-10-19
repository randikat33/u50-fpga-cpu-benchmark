// hq_avx512.hpp - AVX-512 implementation of common/hq_model.hpp, bit-identical by test
// (test/tb_kernel.cpp checks kernel == model == this code).
// One zmm = 16 antithetic pairs (32 paths); NB independent blocks are interleaved per step to hide
// the latency of the gathers, divides and square roots. The int64 state (X, and FT's U) is held as
// two zmm of 8 lanes; float <-> int64 conversions use vcvttps2qq / vcvtqq2ps (AVX512DQ), the
// fixed-point log uses vplzcntq (AVX512CD), vpmuludq, vpmullq and 32-bit gathers.
// Reuses the vector RNG and ICDF of 03_MC_Heston/cpu/mc_avx512.hpp. Build with -ffp-contract=off.
#pragma once
#include <immintrin.h>
#include "hq_model.hpp"
#include "mc_avx512.hpp"

#if MC_HAVE_AVX512 && defined(__AVX512VL__)
#define HQ_HAVE_AVX512 1
namespace hq {
namespace avx {

struct I64 { __m512i lo, hi; };   // 16 int64: lanes 0..7, 8..15

inline __m256 lo8(__m512 v) { return _mm512_castps512_ps256(v); }
inline __m256 hi8(__m512 v) { return _mm512_extractf32x8_ps(v, 1); }
inline __m512 join(__m256 a, __m256 b) { return _mm512_insertf32x8(_mm512_castps256_ps512(a), b, 1); }

// hq::to_q: v * 2^k (exact), truncation to int64
inline I64 to_q_v(__m512 v, float q) {
    __m512 s = _mm512_mul_ps(v, _mm512_set1_ps(q));
    return {_mm512_cvttps_epi64(lo8(s)), _mm512_cvttps_epi64(hi8(s))};
}
inline I64 add(I64 a, I64 b) { return {_mm512_add_epi64(a.lo, b.lo), _mm512_add_epi64(a.hi, b.hi)}; }
inline __m512 to_f(I64 a) { return join(_mm512_cvtepi64_ps(a.lo), _mm512_cvtepi64_ps(a.hi)); }
inline __m512 neg(__m512 v) { return _mm512_xor_ps(v, _mm512_set1_ps(-0.0f)); }

// mc::fexp_half on 8 lanes (as 08_MC_Asian/cpu/asian_avx512.hpp; vscalefps == P2 table, tested there)
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
    __m512i Mx = _mm512_min_epu64(_mm512_add_epi64(T, Tg), _mm512_set1_epi64((long long)EXP_MLIM));
    __m512i m24 = _mm512_srli_epi64(_mm512_add_epi64(Mx, _mm512_set1_epi64(128)), 8);
    __m256 mf = _mm512_cvtepi64_ps(m24);
    __m256 ex = _mm256_cvtepi32_ps(_mm512_cvtepi64_epi32(_mm512_sub_epi64(n, _mm512_set1_epi64(23))));
    return _mm256_scalef_ps(mf, ex);
}
inline __m512 fexp_half_v(__m512 X) { return join(fexp_half_v8s(lo8(X)), fexp_half_v8s(hi8(X))); }

// hq::hq_ln on 8 lanes: n (uint64, >= 1), sc (int64 per lane)
inline __m256 ln_v8(__m512i n, __m512i sc) {
    __m512i lz = _mm512_lzcnt_epi64(n);
    __m512i top = _mm512_sllv_epi64(n, lz);
    __m512i e = _mm512_add_epi64(_mm512_sub_epi64(_mm512_set1_epi64(63), lz), sc);
    __m512i fr = _mm512_and_si512(_mm512_srli_epi64(top, 31), _mm512_set1_epi64(0xFFFFFFFFll));
    __m512i i = _mm512_srli_epi64(fr, 22);
    __m512i rem = _mm512_and_si512(fr, _mm512_set1_epi64(0x3FFFFF));
    __m512i R = _mm512_cvtepu32_epi64(_mm512_i64gather_epi32(i, (const int*)ln_r(), 4));
    __m512i T = _mm512_cvtepu32_epi64(_mm512_i64gather_epi32(i, (const int*)ln_t(), 4));
    __m512i d = _mm512_srli_epi64(_mm512_add_epi64(_mm512_mul_epu32(rem, R), _mm512_set1_epi64(1ll << 30)), 31);
    __m512i d2 = _mm512_srli_epi64(_mm512_add_epi64(_mm512_mul_epu32(d, d), _mm512_set1_epi64(1ll << 32)), 33);
    __m512i Lq = _mm512_sub_epi64(_mm512_add_epi64(T, d), d2);
    __m512i tot = _mm512_add_epi64(_mm512_mullo_epi64(e, _mm512_set1_epi64(HQ_LN2_Q40)), _mm512_slli_epi64(Lq, 8));
    return _mm256_mul_ps(_mm512_cvtepi64_ps(tot), _mm256_set1_ps(0x1p-40f));
}
// hq::hq_lnf on 8 lanes (x > 0, normal)
inline __m256 lnf_v8(__m256 x) {
    __m512i b = _mm512_cvtepu32_epi64(_mm256_castps_si256(x));
    __m512i ex = _mm512_sub_epi64(_mm512_and_si512(_mm512_srli_epi64(b, 23), _mm512_set1_epi64(0xFF)),
                                  _mm512_set1_epi64(127 + 23));
    __m512i m = _mm512_or_si512(_mm512_and_si512(b, _mm512_set1_epi64(0x7FFFFF)), _mm512_set1_epi64(0x800000));
    return ln_v8(m, ex);
}

struct Par {   // broadcast constants
    __m512 KDT, KTH2, C1H, C2Q, L2, TH, EK, QC1, QC2, K0, K1, K2, K3, K4, ZS, ZV, IPC, X0, D2, QS, K[HQ_NK], NG[HQ_NK];
    explicit Par(const float* fp) {
        KDT = _mm512_set1_ps(fp[P_KDT]); KTH2 = _mm512_set1_ps(fp[P_KTH2]); C1H = _mm512_set1_ps(fp[P_C1H]);
        C2Q = _mm512_set1_ps(fp[P_C2Q]); L2 = _mm512_set1_ps(fp[P_L2]); TH = _mm512_set1_ps(fp[P_TH]);
        EK = _mm512_set1_ps(fp[P_EK]); QC1 = _mm512_set1_ps(fp[P_QC1]); QC2 = _mm512_set1_ps(fp[P_QC2]);
        K0 = _mm512_set1_ps(fp[P_K0]); K1 = _mm512_set1_ps(fp[P_K1]); K2 = _mm512_set1_ps(fp[P_K2]);
        K3 = _mm512_set1_ps(fp[P_K3]); K4 = _mm512_set1_ps(fp[P_K4]); ZS = _mm512_set1_ps(fp[P_ZS]);
        ZV = _mm512_set1_ps(fp[P_ZV]); IPC = _mm512_set1_ps(fp[P_IPC]); X0 = _mm512_set1_ps(fp[P_X0]);
        D2 = _mm512_set1_ps(fp[P_D2]); QS = _mm512_set1_ps(fp[P_QS]);
        for (int k = 0; k < HQ_NK; ++k) K[k] = _mm512_set1_ps(fp[P_K + k]);
        for (int k = 0; k < HQ_NK; ++k)   // sign bit set for a put strike: d = -(S - K)
            NG[k] = _mm512_castsi512_ps(_mm512_set1_epi32(((hq::put_mask(fp) >> k) & 1u) ? (int)0x80000000u : 0));
    }
};

// hq::ft_side, 16 lanes
inline void ft_side_v(const Par& p, I64& Xi, I64& Ui, __m512 zs, __m512 w) {
    __m512i z = _mm512_setzero_si512();
    I64 upi{_mm512_max_epi64(Ui.lo, z), _mm512_max_epi64(Ui.hi, z)};
    __m512 up = _mm512_mul_ps(to_f(upi), _mm512_set1_ps(UQI));
    __m512 P = _mm512_sqrt_ps(up);
    __m512 g = _mm512_mul_ps(up, p.KDT);
    __m512 h = _mm512_sub_ps(p.KTH2, g);
    __m512 e = _mm512_mul_ps(P, w);
    __m512 du = _mm512_add_ps(h, e);
    __m512 f = _mm512_mul_ps(P, zs);
    __m512 y = _mm512_sub_ps(f, up);
    Xi = add(Xi, to_q_v(y, XQ));
    Ui = add(Ui, to_q_v(du, UQ));
}

// hq::qe_side, 16 lanes; uv = variance words of this side, fzv = (float)icdf(uv)
inline void qe_side_v(const Par& p, I64& Xi, __m512& V, __m512i uv, __m512 fzv, __m512 zx) {
    const __m512 one = _mm512_set1_ps(1.0f), two = _mm512_set1_ps(2.0f), zero = _mm512_setzero_ps();
    __m512 m1 = _mm512_sub_ps(V, p.TH);
    __m512 m2 = _mm512_mul_ps(m1, p.EK);
    __m512 m = _mm512_add_ps(p.TH, m2);
    __m512 s2a = _mm512_mul_ps(V, p.QC1);
    __m512 s2 = _mm512_add_ps(s2a, p.QC2);
    __m512 mm = _mm512_mul_ps(m, m);
    __m512 t = _mm512_div_ps(mm, s2);
    __m512 ip = _mm512_add_ps(t, t);
    __m512 ipm1 = _mm512_sub_ps(ip, one);
    __m512 pr = _mm512_mul_ps(ip, ipm1);
    __m512 prc = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(pr, zero, _CMP_GT_OQ), zero, pr);
    __m512 sq = _mm512_sqrt_ps(prc);
    __m512 b2 = _mm512_add_ps(ipm1, sq);
    __m512 den = _mm512_add_ps(one, b2);
    __m512 a = _mm512_div_ps(m, den);
    __m512 b2c = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(b2, zero, _CMP_GT_OQ), zero, b2);
    __m512 b = _mm512_sqrt_ps(b2c);
    __m512 zv = _mm512_mul_ps(fzv, p.ZV);
    __m512 tq = _mm512_add_ps(b, zv);
    __m512 t2 = _mm512_mul_ps(tq, tq);
    __m512 vq = _mm512_mul_ps(a, t2);
    __m512 ip2 = _mm512_add_ps(ip, ip);
    __m512 dd = _mm512_add_ps(two, ip);
    __m512 opp = _mm512_div_ps(ip2, dd);
    __m512i cu = _mm512_xor_si512(uv, _mm512_set1_epi32(-1));
    __m512 omu = _mm512_mul_ps(_mm512_cvtepu32_ps(cu), _mm512_set1_ps(0x1p-32f));
    __mmask16 take = _mm512_cmp_ps_mask(omu, opp, _CMP_LT_OQ);
    __m512 ve = zero;
    if (take) {   // logs only where needed (same values as computing everywhere)
        __m512 lo = join(lnf_v8(lo8(opp)), lnf_v8(hi8(opp)));
        __m512i cl = _mm512_cvtepu32_epi64(_mm512_castsi512_si256(cu));
        __m512i ch = _mm512_cvtepu32_epi64(_mm512_extracti64x4_epi64(cu, 1));
        __m512i one64 = _mm512_set1_epi64(1), sc = _mm512_set1_epi64(-33);
        __m512 lu = join(ln_v8(_mm512_add_epi64(_mm512_slli_epi64(cl, 1), one64), sc),
                         ln_v8(_mm512_add_epi64(_mm512_slli_epi64(ch, 1), one64), sc));
        __m512 df = _mm512_sub_ps(lo, lu);
        __m512 ratio = _mm512_div_ps(m, opp);
        ve = _mm512_maskz_mul_ps(take, ratio, df);
    }
    __m512 vn = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(ip, p.IPC, _CMP_GE_OQ), ve, vq);
    __m512 c1 = _mm512_mul_ps(p.K1, V);
    __m512 c2 = _mm512_mul_ps(p.K2, vn);
    __m512 s = _mm512_add_ps(p.K0, c1);
    __m512 s3 = _mm512_add_ps(s, c2);
    __m512 d3 = _mm512_mul_ps(p.K3, V);
    __m512 d4 = _mm512_mul_ps(p.K4, vn);
    __m512 dv = _mm512_add_ps(d3, d4);
    __m512 sd = _mm512_sqrt_ps(dv);
    __m512 y = _mm512_mul_ps(sd, zx);
    __m512 inc = _mm512_add_ps(s3, y);
    Xi = add(Xi, to_q_v(inc, XQ));
    V = vn;
}

// payoffs of 16 pairs -> moments (first cnt pairs)
inline void settle_v(const Par& p, const I64& XA, const I64& XB, int cnt, Mom& m) {
    alignas(64) float pa[HQ_NK][16], pb[HQ_NK][16];
    __m512 SA, SB;
    {
        __m512 xr = _mm512_mul_ps(to_f(XA), _mm512_set1_ps(XQI));
        __m512 x = _mm512_add_ps(p.X0, xr);
        SA = fexp_half_v(_mm512_add_ps(x, p.D2));
        __m512 xrb = _mm512_mul_ps(to_f(XB), _mm512_set1_ps(XQI));
        __m512 xb = _mm512_add_ps(p.X0, xrb);
        SB = fexp_half_v(_mm512_add_ps(xb, p.D2));
    }
    const __m512 z = _mm512_setzero_ps();
    for (int k = 0; k < HQ_NK; ++k) {
        __m512 da = _mm512_xor_ps(_mm512_sub_ps(SA, p.K[k]), p.NG[k]);   // call S - K, or put -(S - K)
        __m512 db = _mm512_xor_ps(_mm512_sub_ps(SB, p.K[k]), p.NG[k]);
        da = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(da, z, _CMP_GT_OQ), z, da);
        db = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(db, z, _CMP_GT_OQ), z, db);
        _mm512_store_ps(pa[k], _mm512_mul_ps(da, p.QS));
        _mm512_store_ps(pb[k], _mm512_mul_ps(db, p.QS));
    }
    for (int l = 0; l < cnt; ++l) {
        uint64_t mA[HQ_NK], mB[HQ_NK];
        uint32_t ovf = 0;
        for (int k = 0; k < HQ_NK; ++k) {
            float a = pa[k][l], b = pb[k][l];
            if (!(a < M_SAT_F)) { ovf += 1; mA[k] = (1ull << 40) - 1; } else mA[k] = (uint64_t)a;
            if (!(b < M_SAT_F)) { ovf += 1; mB[k] = (1ull << 40) - 1; } else mB[k] = (uint64_t)b;
        }
        m.add(mA, mB);
        m.ovf += ovf;
    }
}

// simulate the NB*16 pairs a0 .. a0+NB*16-1, accumulate the first cnt into m
template <int NB>
inline void sim_blocks(const float* fp, Scheme sc, const Keys& k, uint32_t M, uint32_t a0, int cnt, Mom& m) {
    const Par p(fp);
    const __m512i iota = _mm512_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
    mc::avx::XoV s[NB];
    I64 XA[NB], XB[NB], UA[NB], UB[NB];
    __m512 VA[NB], VB[NB];
    const int64_t u0 = to_q(fp[P_U0], UQ);
    for (int b = 0; b < NB; ++b) {
        s[b] = mc::avx::seed_v(k, _mm512_add_epi32(_mm512_set1_epi32((int)(a0 + 16 * b)), iota));
        XA[b] = XB[b] = {_mm512_setzero_si512(), _mm512_setzero_si512()};
        UA[b] = UB[b] = {_mm512_set1_epi64(u0), _mm512_set1_epi64(u0)};
        VA[b] = VB[b] = _mm512_set1_ps(fp[P_V0]);
    }
    if (sc == FT) {
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
                __m512 fzq = _mm512_cvtepi32_ps(mc::avx::icdf_v(u1));   // icdf(~u1) == -icdf(u1)
                __m512 zx = _mm512_mul_ps(p.ZS, _mm512_cvtepi32_ps(mc::avx::icdf_v(u2)));
                qe_side_v(p, XA[b], VA[b], u1, fzq, zx);
                qe_side_v(p, XB[b], VB[b], _mm512_xor_si512(u1, _mm512_set1_epi32(-1)), neg(fzq), neg(zx));
            }
        }
    }
    for (int b = 0; b < NB && cnt > 0; ++b) {
        int c = cnt < 16 ? cnt : 16;
        settle_v(p, XA[b], XB[b], c, m);
        cnt -= c;
    }
}
template <int NB>
inline void sim_range(const float* fp, Scheme sc, const Keys& k, uint32_t M, uint64_t a0, uint64_t a1, Mom& m) {
    for (uint64_t a = a0; a < a1; a += 16 * NB) {
        int cnt = (int)((a1 - a) < 16u * NB ? (a1 - a) : 16u * NB);
        sim_blocks<NB>(fp, sc, k, M, (uint32_t)a, cnt, m);
    }
}

}  // namespace avx
}  // namespace hq
#else
#define HQ_HAVE_AVX512 0
#endif
