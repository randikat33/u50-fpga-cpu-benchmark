// conv_avx512.hpp - hand-written AVX-512BW row kernels (all three filters in one pass).
// 8-bit data: 32 lanes of int16 per iteration; 16-bit data: 16 lanes of int32.
// Bit-exact with conv_ref.hpp (the tail < one vector is done by the scalar reference).
#pragma once
#include <cstdint>
#include <cstring>
#include <immintrin.h>
#include "conv_ref.hpp"

namespace conv {

#if defined(__AVX512F__) && defined(__AVX512BW__)
inline const char* row_isa() { return "avx512bw"; }

inline void row_u8_avx512(const uint8_t* up, const uint8_t* mid, const uint8_t* dn,
                          uint8_t* sh, uint8_t* ed, uint8_t* bl, int W, int C) {
    const int n = W * C;
    if (W < 3) { ref_row<uint8_t>(up, mid, dn, sh, ed, bl, W, C); return; }
    const int end = n - C;              // samples [C, end) are interior
    const __m512i z = _mm512_setzero_si512();
    auto L = [](const uint8_t* p) { return _mm512_cvtepu8_epi16(_mm256_loadu_si256((const __m256i*)p)); };
    int i = C;
    for (; i + 32 <= end; i += 32) {
        const __m512i nw = L(up + i - C), no = L(up + i), ne = L(up + i + C);
        const __m512i we = L(mid + i - C), c = L(mid + i), ea = L(mid + i + C);
        const __m512i sw = L(dn + i - C), so = L(dn + i), se = L(dn + i + C);
        const __m512i s4 = _mm512_add_epi16(_mm512_add_epi16(no, so), _mm512_add_epi16(we, ea));
        const __m512i cr = _mm512_add_epi16(_mm512_add_epi16(nw, ne), _mm512_add_epi16(sw, se));
        const __m512i c4 = _mm512_slli_epi16(c, 2);
        const __m512i vs = _mm512_sub_epi16(_mm512_add_epi16(c4, c), s4);
        const __m512i ve = _mm512_sub_epi16(_mm512_sub_epi16(_mm512_slli_epi16(c, 3), s4), cr);
        const __m512i vb = _mm512_srli_epi16(_mm512_add_epi16(_mm512_add_epi16(c4, _mm512_slli_epi16(s4, 1)), cr), 4);
        // clamp: max(x,0) then unsigned saturation 16->8 (vpmovuswb)
        _mm256_storeu_si256((__m256i*)(sh + i), _mm512_cvtusepi16_epi8(_mm512_max_epi16(vs, z)));
        _mm256_storeu_si256((__m256i*)(ed + i), _mm512_cvtusepi16_epi8(_mm512_max_epi16(ve, z)));
        _mm256_storeu_si256((__m256i*)(bl + i), _mm512_cvtepi16_epi8(vb));
    }
    // borders + tail (scalar reference on the remaining span)
    for (int k = 0; k < C; ++k) { sh[k] = ed[k] = bl[k] = 0; sh[n - 1 - k] = ed[n - 1 - k] = bl[n - 1 - k] = 0; }
    for (; i < end; ++i) {
        const int c = mid[i], s4 = up[i] + dn[i] + mid[i - C] + mid[i + C];
        const int cr = up[i - C] + up[i + C] + dn[i - C] + dn[i + C];
        sh[i] = clamp_px<uint8_t>(5 * c - s4, 255);
        ed[i] = clamp_px<uint8_t>(8 * c - s4 - cr, 255);
        bl[i] = clamp_px<uint8_t>((4 * c + 2 * s4 + cr) >> 4, 255);
    }
}

inline void row_u16_avx512(const uint16_t* up, const uint16_t* mid, const uint16_t* dn,
                           uint16_t* sh, uint16_t* ed, uint16_t* bl, int W, int C) {
    const int n = W * C;
    if (W < 3) { ref_row<uint16_t>(up, mid, dn, sh, ed, bl, W, C); return; }
    const int end = n - C;
    const __m512i z = _mm512_setzero_si512();
    auto L = [](const uint16_t* p) { return _mm512_cvtepu16_epi32(_mm256_loadu_si256((const __m256i*)p)); };
    int i = C;
    for (; i + 16 <= end; i += 16) {
        const __m512i nw = L(up + i - C), no = L(up + i), ne = L(up + i + C);
        const __m512i we = L(mid + i - C), c = L(mid + i), ea = L(mid + i + C);
        const __m512i sw = L(dn + i - C), so = L(dn + i), se = L(dn + i + C);
        const __m512i s4 = _mm512_add_epi32(_mm512_add_epi32(no, so), _mm512_add_epi32(we, ea));
        const __m512i cr = _mm512_add_epi32(_mm512_add_epi32(nw, ne), _mm512_add_epi32(sw, se));
        const __m512i c4 = _mm512_slli_epi32(c, 2);
        const __m512i vs = _mm512_sub_epi32(_mm512_add_epi32(c4, c), s4);
        const __m512i ve = _mm512_sub_epi32(_mm512_sub_epi32(_mm512_slli_epi32(c, 3), s4), cr);
        const __m512i vb = _mm512_srli_epi32(_mm512_add_epi32(_mm512_add_epi32(c4, _mm512_slli_epi32(s4, 1)), cr), 4);
        _mm256_storeu_si256((__m256i*)(sh + i), _mm512_cvtusepi32_epi16(_mm512_max_epi32(vs, z)));
        _mm256_storeu_si256((__m256i*)(ed + i), _mm512_cvtusepi32_epi16(_mm512_max_epi32(ve, z)));
        _mm256_storeu_si256((__m256i*)(bl + i), _mm512_cvtepi32_epi16(vb));
    }
    for (int k = 0; k < C; ++k) { sh[k] = ed[k] = bl[k] = 0; sh[n - 1 - k] = ed[n - 1 - k] = bl[n - 1 - k] = 0; }
    for (; i < end; ++i) {
        const int c = mid[i], s4 = up[i] + dn[i] + mid[i - C] + mid[i + C];
        const int cr = up[i - C] + up[i + C] + dn[i - C] + dn[i + C];
        sh[i] = clamp_px<uint16_t>(5 * c - s4, 65535);
        ed[i] = clamp_px<uint16_t>(8 * c - s4 - cr, 65535);
        bl[i] = clamp_px<uint16_t>((4 * c + 2 * s4 + cr) >> 4, 65535);
    }
}

inline void row_fast(const uint8_t* up, const uint8_t* mid, const uint8_t* dn,
                     uint8_t* sh, uint8_t* ed, uint8_t* bl, int W, int C, int bps) {
    if (bps == 2)
        row_u16_avx512((const uint16_t*)up, (const uint16_t*)mid, (const uint16_t*)dn,
                       (uint16_t*)sh, (uint16_t*)ed, (uint16_t*)bl, W, C);
    else
        row_u8_avx512(up, mid, dn, sh, ed, bl, W, C);
}
#else
// Fallback for machines without AVX-512BW: the scalar reference (auto-vectorised by -O3).
inline const char* row_isa() { return "scalar"; }
inline void row_fast(const uint8_t* up, const uint8_t* mid, const uint8_t* dn,
                     uint8_t* sh, uint8_t* ed, uint8_t* bl, int W, int C, int bps) {
    ref_row_bytes(up, mid, dn, sh, ed, bl, W, C, bps);
}
#endif

}  // namespace conv
