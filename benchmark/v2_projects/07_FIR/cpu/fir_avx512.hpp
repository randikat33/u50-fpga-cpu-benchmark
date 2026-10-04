// fir_avx512.hpp - AVX-512 int16 FIR (bit-identical to common/fir_ref.hpp, checked by test).
//
// 32 outputs y[n..n+31] per iteration, as 16 even and 16 odd int32 accumulators. For a tap pair
// (k, k+1) one unaligned 64-byte load at &x[n-k-1] puts (x[n+2i-k-1], x[n+2i-k]) into 32-bit lane i,
// so one vpmaddwd (or vpdpwssd with AVX512-VNNI) against the broadcast pair (c[k+1], c[k]) adds both
// taps of even output 2i; the load at &x[n-k] does the same for the odd outputs. Per 32 outputs and
// tap pair: 2 loads + 2 multiply-accumulates; the int32 sums wrap exactly like the reference.
// Needs AVX512F+BW; uses VNNI (vpdpwssd, Ice Lake-SP) when compiled with it and --isa vnni is chosen.
#pragma once
#include <immintrin.h>
#include "fir_ref.hpp"

#if defined(__AVX512F__) && defined(__AVX512BW__)
#define FIR_HAVE_AVX512 1
#if defined(__AVX512VNNI__)
#define FIR_HAVE_VNNI 1
#else
#define FIR_HAVE_VNNI 0
#endif
namespace fir {
namespace avx {

template <bool VNNI>
inline __m512i mac(__m512i acc, __m512i a, __m512i b) {
#if FIR_HAVE_VNNI
    if (VNNI) return _mm512_dpwssd_epi32(acc, a, b);
#endif
    return _mm512_add_epi32(acc, _mm512_madd_epi16(a, b));
}

inline __m512i finish(__m512i e, __m512i o) {
    const __m512i rnd = _mm512_set1_epi32(16384), hi = _mm512_set1_epi32(32767), lo = _mm512_set1_epi32(-32768);
    e = _mm512_max_epi32(_mm512_min_epi32(_mm512_srai_epi32(_mm512_add_epi32(e, rnd), 15), hi), lo);
    o = _mm512_max_epi32(_mm512_min_epi32(_mm512_srai_epi32(_mm512_add_epi32(o, rnd), 15), hi), lo);
    return _mm512_or_si512(_mm512_and_si512(e, _mm512_set1_epi32(0xFFFF)), _mm512_slli_epi32(o, 16));
}

// y[i0..i1) with i1 - i0 a multiple of 32; x[i0 - T + 1 .. i1) readable. TT = T (compile time) or 0.
// Four 32-output blocks are interleaved (8 independent accumulator chains) to cover the latency of
// the multiply-accumulate (a single chain of T/2 dependent vpdpwssd reaches only ~0.4 outputs/cycle).
template <bool VNNI, int TT>
inline void filter_blocks(const int16_t* x, int16_t* y, size_t i0, size_t i1, const __m512i* cp, int T) {
    const int P = TT ? TT / 2 : T / 2;
#ifndef FIR_NBLK
#define FIR_NBLK 1
#endif
    constexpr int NB = FIR_NBLK;
    size_t n = i0;
    for (; n + 32 * NB <= i1; n += 32 * NB) {
        __m512i e[NB], o[NB];
        for (int b = 0; b < NB; ++b) { e[b] = _mm512_setzero_si512(); o[b] = _mm512_setzero_si512(); }
        for (int p = 0; p < P; ++p) {
            const int k = 2 * p;
            for (int b = 0; b < NB; ++b) {
                __m512i le = _mm512_loadu_si512((const void*)(x + n + 32 * b - k - 1));
                __m512i lo = _mm512_loadu_si512((const void*)(x + n + 32 * b - k));
                e[b] = mac<VNNI>(e[b], le, cp[p]);
                o[b] = mac<VNNI>(o[b], lo, cp[p]);
            }
        }
        for (int b = 0; b < NB; ++b) _mm512_storeu_si512((void*)(y + n + 32 * b), finish(e[b], o[b]));
    }
    for (; n < i1; n += 32) {
        __m512i e = _mm512_setzero_si512(), o = _mm512_setzero_si512();
        for (int p = 0; p < P; ++p) {
            const int k = 2 * p;
            e = mac<VNNI>(e, _mm512_loadu_si512((const void*)(x + n - k - 1)), cp[p]);
            o = mac<VNNI>(o, _mm512_loadu_si512((const void*)(x + n - k)), cp[p]);
        }
        _mm512_storeu_si512((void*)(y + n), finish(e, o));
    }
}

// Register-shift variant for T <= 32 (the benchmark): one aligned 64-byte load per 32 outputs.
// A0 = x[n..n+31], A1 = x[n-32..n-1]; S0 = x[n-1..n+30], S1 = x[n-33..n-2] (vpermt2w, one per block,
// carried to the next block). Every operand vector is a valignd of (A0, A1) or (S0, S1):
//   odd outputs,  pair p: x[n-2p ..]    = alignr(A0, A1, 16-p)
//   even outputs, pair p: x[n-2p-1 ..]  = alignr(S0, S1, 16-p)
// This removes the cache-line-split unaligned loads (2 per pair) that limit filter_blocks.
template <bool VNNI, int TT>
inline void filter_shift(const int16_t* x, int16_t* y, size_t i0, size_t i1, const __m512i* cp) {
    static_assert(TT >= 2 && TT <= 32 && TT % 2 == 0, "filter_shift needs T <= 32");
    constexpr int P = TT / 2;
    const __m512i idx = _mm512_setr_epi32(      // lane 0 <- a[31], lane j>0 <- b[j-1]   (16-bit lanes)
        (int)(31u | (32u << 16)), (int)(33u | (34u << 16)), (int)(35u | (36u << 16)), (int)(37u | (38u << 16)),
        (int)(39u | (40u << 16)), (int)(41u | (42u << 16)), (int)(43u | (44u << 16)), (int)(45u | (46u << 16)),
        (int)(47u | (48u << 16)), (int)(49u | (50u << 16)), (int)(51u | (52u << 16)), (int)(53u | (54u << 16)),
        (int)(55u | (56u << 16)), (int)(57u | (58u << 16)), (int)(59u | (60u << 16)), (int)(61u | (62u << 16)));
    __m512i A1 = _mm512_loadu_si512((const void*)(x + i0 - 32));
    __m512i A2 = _mm512_loadu_si512((const void*)(x + i0 - 64));
    __m512i S1 = _mm512_permutex2var_epi16(A2, idx, A1);
    for (size_t n = i0; n < i1; n += 32) {
        __m512i A0 = _mm512_load_si512((const void*)(x + n));
        __m512i S0 = _mm512_permutex2var_epi16(A1, idx, A0);
        __m512i e = _mm512_setzero_si512(), o = _mm512_setzero_si512();
#define FIR_ALIGNR_STEP(p) if ((p) < P) { \
            o = mac<VNNI>(o, (p) == 0 ? A0 : _mm512_alignr_epi32(A0, A1, 16 - (p)), cp[p]); \
            e = mac<VNNI>(e, (p) == 0 ? S0 : _mm512_alignr_epi32(S0, S1, 16 - (p)), cp[p]); }
        FIR_ALIGNR_STEP(0) FIR_ALIGNR_STEP(1) FIR_ALIGNR_STEP(2) FIR_ALIGNR_STEP(3)
        FIR_ALIGNR_STEP(4) FIR_ALIGNR_STEP(5) FIR_ALIGNR_STEP(6) FIR_ALIGNR_STEP(7)
        FIR_ALIGNR_STEP(8) FIR_ALIGNR_STEP(9) FIR_ALIGNR_STEP(10) FIR_ALIGNR_STEP(11)
        FIR_ALIGNR_STEP(12) FIR_ALIGNR_STEP(13) FIR_ALIGNR_STEP(14) FIR_ALIGNR_STEP(15)
#undef FIR_ALIGNR_STEP
        _mm512_storeu_si512((void*)(y + n), finish(e, o));
        A1 = A0;
        S1 = S0;
    }
}

// broadcast coefficient pairs: 32-bit lane = c[k+1] (low half) | c[k] (high half)
inline void pack_coef(const int16_t* c, int T, __m512i* cp) {
    for (int p = 0; p < T / 2; ++p) {
        uint32_t v = (uint32_t)(uint16_t)c[2 * p + 1] | ((uint32_t)(uint16_t)c[2 * p] << 16);
        cp[p] = _mm512_set1_epi32((int)v);
    }
}

// y[i0..i1) of the stream x (x[i] for i in [i0-T+1, i1+1) readable; the caller pads with zeros)
template <bool VNNI>
inline void filter_range(const int16_t* x, int16_t* y, size_t i0, size_t i1, const int16_t* c, int T) {
    __m512i cp[FIR_TMAX / 2];
    pack_coef(c, T, cp);
    size_t nb = (i1 - i0) / 32 * 32;
    switch (T) {
        case 16:
            if (((uintptr_t)(x + i0) & 63) == 0) filter_shift<VNNI, 16>(x, y, i0, i0 + nb, cp);
            else filter_blocks<VNNI, 16>(x, y, i0, i0 + nb, cp, T);
            break;
        case 32:
            if (((uintptr_t)(x + i0) & 63) == 0) filter_shift<VNNI, 32>(x, y, i0, i0 + nb, cp);
            else filter_blocks<VNNI, 32>(x, y, i0, i0 + nb, cp, T);
            break;
        case 64: filter_blocks<VNNI, 64>(x, y, i0, i0 + nb, cp, T); break;
        default: filter_blocks<VNNI, 0>(x, y, i0, i0 + nb, cp, T); break;
    }
    for (size_t i = i0 + nb; i < i1; ++i) y[i] = out1(x + i, c, T);
}

}  // namespace avx
}  // namespace fir
#else
#define FIR_HAVE_AVX512 0
#define FIR_HAVE_VNNI 0
#endif
