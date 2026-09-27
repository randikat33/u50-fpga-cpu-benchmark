// pf_engine.hpp - CPU Monte Carlo engines for the v2 portfolio benchmark.
//   scalar : pf::sim_path (the reference model itself, compiled -O3)
//   avx512 : 16 paths per zmm, identical RNG / ICDF / exp / Heston numerics (bit-identical)
// Build with -O3 -march=native -mprefer-vector-width=512 -ffp-contract=off.
#pragma once
#include <cstdint>
#include <cstring>
#include "pf_model.hpp"
#if defined(__AVX512F__)
#include <immintrin.h>
#endif

namespace pfcpu {

enum class Impl { scalar, avx512 };
enum class Lut { gather, scalar };

inline bool have_avx512() {
#if defined(__AVX512F__) && defined(__AVX512CD__) && defined(__AVX512DQ__)
    return __builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512cd") &&
           __builtin_cpu_supports("avx512dq");
#else
    return false;
#endif
}

// scalar engine: paths [p0, p1) of one trade
inline void sim_scalar(const pf::TradeConst& c, uint32_t p0, uint32_t p1, pf::Mom& m) {
    pf::sim_range(c, p0, p1, m);
}

#if defined(__AVX512F__) && defined(__AVX512CD__) && defined(__AVX512DQ__)
#define PF_HAVE_AVX512 1

struct Tables {
    alignas(64) int32_t ta[2048];     // c0
    alignas(64) int32_t tb[2048];     // c1 << 15 | c2 (15-bit field)
    alignas(64) uint64_t tp[2048];    // packed (c0 << 32) | tb  (one load per lookup)
    alignas(64) int64_t t64[256];     // exp mantissa table
    alignas(64) float p2[256];        // exp scale table
    Tables() {
        const uint64_t* rom = pf::icdf_rom();
        for (int i = 0; i < 2048; ++i) {
            ta[i] = pf::rom_c0(rom[i]);
            tb[i] = static_cast<int32_t>((static_cast<uint32_t>(pf::rom_c1(rom[i])) << 15) |
                                         (static_cast<uint32_t>(pf::rom_c2(rom[i])) & 0x7FFF));
            tp[i] = (static_cast<uint64_t>(static_cast<uint32_t>(ta[i])) << 32) | static_cast<uint32_t>(tb[i]);
        }
        for (int i = 0; i < 256; ++i) { t64[i] = pf::exp_t()[i]; p2[i] = pf::exp_p2()[i]; }
    }
};
inline const Tables& tables() { static const Tables t; return t; }

class Avx512Engine {
    const Tables& T = tables();
    Lut lut_;
    static __m512i fmix(__m512i h) {
        h = _mm512_xor_si512(h, _mm512_srli_epi32(h, 16));
        h = _mm512_mullo_epi32(h, _mm512_set1_epi32(static_cast<int32_t>(0x85ebca6bu)));
        h = _mm512_xor_si512(h, _mm512_srli_epi32(h, 13));
        h = _mm512_mullo_epi32(h, _mm512_set1_epi32(static_cast<int32_t>(0xc2b2ae35u)));
        return _mm512_xor_si512(h, _mm512_srli_epi32(h, 16));
    }
    static inline __m512i next(__m512i s[4]) {
        __m512i s1 = s[1];
        __m512i m5 = _mm512_add_epi32(_mm512_slli_epi32(s1, 2), s1);
        __m512i rr = _mm512_rol_epi32(m5, 7);
        __m512i res = _mm512_add_epi32(_mm512_slli_epi32(rr, 3), rr);
        __m512i t = _mm512_slli_epi32(s1, 9);
        s[2] = _mm512_xor_si512(s[2], s[0]);
        s[3] = _mm512_xor_si512(s[3], s[1]);
        s[1] = _mm512_xor_si512(s[1], s[2]);
        s[0] = _mm512_xor_si512(s[0], s[3]);
        s[2] = _mm512_xor_si512(s[2], t);
        s[3] = _mm512_rol_epi32(s[3], 11);
        return res;
    }
    inline __m512i icdf(__m512i u) const {
        __mmask16 pos = _mm512_movepi32_mask(u);                 // sign bit set -> z >= 0
        __m512i ui = _mm512_mask_xor_epi32(u, pos, u, _mm512_set1_epi32(-1));
        __m512i mi = _mm512_and_si512(ui, _mm512_set1_epi32(0x7FFFFFFF));
        __m512i lz = _mm512_sub_epi32(_mm512_lzcnt_epi32(mi), _mm512_set1_epi32(1));
        __m512i n = _mm512_sllv_epi32(mi, lz);
        __m512i idx = _mm512_or_si512(_mm512_slli_epi32(lz, 6),
                                      _mm512_and_si512(_mm512_srli_epi32(n, 24), _mm512_set1_epi32(63)));
        __m512i x = _mm512_and_si512(_mm512_srli_epi32(n, 9), _mm512_set1_epi32(0x7FFF));
        __m512i c0, cb;
        if (lut_ == Lut::gather) {
            c0 = _mm512_i32gather_epi32(idx, T.ta, 4);
            cb = _mm512_i32gather_epi32(idx, T.tb, 4);
        } else {
            alignas(64) int32_t ib[16], a[16], b[16];
            _mm512_store_si512(ib, idx);
            for (int k = 0; k < 16; ++k) {
                uint64_t w = T.tp[ib[k]];
                a[k] = static_cast<int32_t>(w >> 32);
                b[k] = static_cast<int32_t>(w);
            }
            c0 = _mm512_load_si512(a);
            cb = _mm512_load_si512(b);
        }
        __m512i c1 = _mm512_srai_epi32(cb, 15);
        __m512i c2 = _mm512_srai_epi32(_mm512_slli_epi32(cb, 17), 17);
        __m512i a = _mm512_srai_epi32(_mm512_mullo_epi32(c2, x), 15);
        __m512i b = _mm512_add_epi32(c1, a);
        __m512i p = _mm512_srai_epi32(_mm512_mullo_epi32(b, x), 15);
        __m512i zi = _mm512_add_epi32(c0, p);
        __m512i zr = _mm512_srai_epi32(_mm512_add_epi32(zi, _mm512_set1_epi32(2)), 2);
        return _mm512_mask_sub_epi32(zr, static_cast<__mmask16>(~pos), _mm512_setzero_si512(), zr);
    }
    struct TC { __m512 W0, W1, W2, W3, Rq, sr, ssq, sqdt, theta, kdt, hdt, mu; };
    inline void draw(__m512i s[4], const TC& t, __m512& zS, __m512& zVs) const {
        __m512i z0 = icdf(next(s));
        __m512i z1 = icdf(next(s));
        __m512i z2 = icdf(next(s));
        __m512i z3 = icdf(next(s));
        __m512i z4 = icdf(next(s));
        __m512i z5 = icdf(next(s));
        __m512 m0 = _mm512_mul_ps(t.W0, _mm512_cvtepi32_ps(z0));
        __m512 m1 = _mm512_mul_ps(t.W1, _mm512_cvtepi32_ps(z1));
        __m512 m2 = _mm512_mul_ps(t.W2, _mm512_cvtepi32_ps(z2));
        __m512 m3 = _mm512_mul_ps(t.W3, _mm512_cvtepi32_ps(z3));
        __m512 mr = _mm512_mul_ps(t.Rq, _mm512_cvtepi32_ps(z4));
        __m512 s01 = _mm512_add_ps(m0, m1);
        __m512 s23 = _mm512_add_ps(m2, m3);
        __m512 s03 = _mm512_add_ps(s01, s23);
        zS = _mm512_add_ps(s03, mr);
        __m512 a = _mm512_mul_ps(t.sr, zS);
        __m512 b = _mm512_mul_ps(t.ssq, _mm512_cvtepi32_ps(z5));
        zVs = _mm512_add_ps(a, b);
    }
    inline __m256 exp_half(__m256i xq32) const {
        __m512i xh = _mm512_cvtepi32_epi64(xq32);
        __m512i y = _mm512_mul_epi32(xh, _mm512_set1_epi64(pf::PF_INVLN2_Q30));
        __m512i n = _mm512_srai_epi64(y, 54);
        __m512i fr = _mm512_and_si512(_mm512_srli_epi64(y, 22), _mm512_set1_epi64(0xFFFFFFFFll));
        __m512i i = _mm512_srli_epi64(fr, 24);
        __m512i fl = _mm512_and_si512(fr, _mm512_set1_epi64(0xFFFFFF));
        __m512i d = _mm512_srli_epi64(_mm512_mul_epu32(fl, _mm512_set1_epi64(static_cast<int64_t>(pf::PF_LN2_Q32))), 32);
        __m512i dd = _mm512_srli_epi64(_mm512_mul_epu32(d, d), 33);
        __m512i g = _mm512_add_epi64(d, dd);
        __m512i k = _mm512_add_epi64(n, _mm512_set1_epi64(128));
        __m512i Tm;
        __m256 p2;
        if (lut_ == Lut::gather) {
            Tm = _mm512_i64gather_epi64(i, T.t64, 8);
            p2 = _mm512_i64gather_ps(k, T.p2, 4);
        } else {
            alignas(64) int64_t ib[8], kb[8], tv[8];
            alignas(32) float pv[8];
            _mm512_store_si512(ib, i);
            _mm512_store_si512(kb, k);
            for (int j = 0; j < 8; ++j) { tv[j] = T.t64[ib[j]]; pv[j] = T.p2[kb[j]]; }
            Tm = _mm512_load_si512(tv);
            p2 = _mm256_load_ps(pv);
        }
        __m512i M = _mm512_add_epi64(Tm, _mm512_srli_epi64(_mm512_mul_epu32(Tm, g), 32));
        M = _mm512_min_epu64(M, _mm512_set1_epi64(static_cast<int64_t>(pf::PF_EXP_MLIM)));
        __m512i m24 = _mm512_srli_epi64(_mm512_add_epi64(M, _mm512_set1_epi64(128)), 8);
        __m256 mf = _mm512_cvtepi64_ps(m24);
        return _mm256_mul_ps(mf, p2);
    }

public:
    explicit Avx512Engine(Lut lut) : lut_(lut) {}

    inline __m512 fexp(__m512 x) const {
        __m512 c64 = _mm512_set1_ps(64.0f), cm64 = _mm512_set1_ps(-64.0f);
        __m512 xc0 = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(x, c64, _CMP_LE_OQ), c64, x);
        __m512 xc = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(x, cm64, _CMP_GE_OQ), cm64, xc0);
        __m512 xs = _mm512_mul_ps(xc, _mm512_set1_ps(16777216.0f));
        __m512i xq = _mm512_cvt_roundps_epi32(xs, _MM_FROUND_TO_NEG_INF | _MM_FROUND_NO_EXC);
        __m256 lo = exp_half(_mm512_castsi512_si256(xq));
        __m256 hi = exp_half(_mm512_extracti64x4_epi64(xq, 1));
        return _mm512_insertf32x8(_mm512_castps256_ps512(lo), hi, 1);
    }
    __m512i icdf_public(__m512i u) const { return icdf(u); }

    // paths [p0, p1) of one trade
    void sim(const pf::TradeConst& c, uint32_t p0, uint32_t p1, pf::Mom& m) const {
        TC t;
        t.W0 = _mm512_set1_ps(c.W[0]); t.W1 = _mm512_set1_ps(c.W[1]);
        t.W2 = _mm512_set1_ps(c.W[2]); t.W3 = _mm512_set1_ps(c.W[3]);
        t.Rq = _mm512_set1_ps(c.Rq); t.sr = _mm512_set1_ps(c.sr); t.ssq = _mm512_set1_ps(c.ssq);
        t.sqdt = _mm512_set1_ps(c.sqdt); t.theta = _mm512_set1_ps(c.theta); t.kdt = _mm512_set1_ps(c.kdt);
        t.hdt = _mm512_set1_ps(c.hdt); t.mu = _mm512_set1_ps(c.mu);
        const __m512 zero = _mm512_setzero_ps();
        const __m512 v0 = _mm512_set1_ps(c.v0);
        const __m512i k0 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[0]));
        const __m512i k1 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[1]));
        const __m512i k2 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[2]));
        const __m512i k3 = _mm512_set1_epi32(static_cast<int32_t>(c.keys.k[3]));
        const __m512i ramp = _mm512_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
        const bool ft = c.ft != 0, asian = c.asian != 0;
        const uint32_t n = c.n_steps;
        alignas(64) float Ea[16], sEa[16];
        for (uint64_t p = p0; p < p1; p += 16) {
            __m512i pv = _mm512_add_epi32(_mm512_set1_epi32(static_cast<int32_t>(p)), ramp);
            __m512i s[4] = {fmix(_mm512_xor_si512(pv, k0)), fmix(_mm512_xor_si512(pv, k1)),
                            fmix(_mm512_xor_si512(pv, k2)), fmix(_mm512_xor_si512(pv, k3))};
            __m512i all = _mm512_or_si512(_mm512_or_si512(s[0], s[1]), _mm512_or_si512(s[2], s[3]));
            __mmask16 z = _mm512_testn_epi32_mask(all, all);
            if (z) s[0] = _mm512_mask_blend_epi32(z, s[0], _mm512_set1_epi32(static_cast<int32_t>(0x9E3779B9u)));
            __m512 zS, zVs;
            draw(s, t, zS, zVs);
            __m512 v = v0, lr = zero, sE = zero, E = zero;
            for (uint32_t r = 1; r <= n; ++r) {
                __m512 sq = _mm512_sqrt_ps(v);
                __m512 sv = _mm512_mul_ps(sq, t.sqdt);
                __m512 tv = _mm512_sub_ps(t.theta, v);
                __m512 d1 = _mm512_mul_ps(t.kdt, tv);
                __m512 va = _mm512_add_ps(v, d1);
                __m512 d2 = _mm512_mul_ps(sv, zVs);
                __m512 vn = _mm512_add_ps(va, d2);
                __m512 hv = _mm512_mul_ps(t.hdt, v);
                __m512 l1 = _mm512_sub_ps(t.mu, hv);
                __m512 la = _mm512_add_ps(lr, l1);
                __m512 l2 = _mm512_mul_ps(sv, zS);
                lr = _mm512_add_ps(la, l2);
                if (ft) {
                    v = _mm512_mask_blend_ps(_mm512_cmp_ps_mask(vn, zero, _CMP_GT_OQ), zero, vn);
                } else {
                    __mmask16 lt = _mm512_cmp_ps_mask(vn, zero, _CMP_LT_OQ);
                    v = _mm512_mask_sub_ps(vn, lt, zero, vn);
                }
                if (asian) {
                    E = fexp(lr);
                    sE = _mm512_add_ps(sE, E);
                }
                if (r < n) draw(s, t, zS, zVs);
            }
            if (!asian) E = fexp(lr);
            _mm512_store_ps(Ea, E);
            _mm512_store_ps(sEa, sE);
            uint32_t cnt = static_cast<uint32_t>(p1 - p < 16 ? p1 - p : 16);
            for (uint32_t k = 0; k < cnt; ++k) m.add(pf::payoff_q(Ea[k], sEa[k], c));
        }
    }
};
#else
#define PF_HAVE_AVX512 0
#endif  // AVX512

}  // namespace pfcpu
