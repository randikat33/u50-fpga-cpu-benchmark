// aes_x86.hpp - hand-written x86 AES-256-CTR kernels (single-threaded segment functions).
//   ctr_vaes  : AVX-512 VAES, 4 zmm (16 blocks) per iteration, counters built with AVX-512
//   ctr_aesni : AES-NI, 8 x 128-bit blocks per iteration (Intel's classic pipelined CTR)
// Both implement the NIST SP 800-38A full 128-bit big-endian counter: segment counter =
// IV + blk_off, +1 per block, carry across the 64-bit boundary.
#pragma once
#include <cstdint>
#include <cstring>
#include "aes_ref.hpp"
#if defined(__AES__) || defined(__VAES__)
#include <immintrin.h>
#endif

namespace aesx {

inline uint64_t bswap64(uint64_t v) { return __builtin_bswap64(v); }

// ======================================================================================
// AES-NI, 8 blocks per iteration
// ======================================================================================
#if defined(__AES__) && defined(__SSE4_1__)
constexpr bool HAVE_AESNI = true;
struct NiKey { __m128i k[15]; };
inline NiKey ni_key(const aes::RoundKeys& K) {
    NiKey r;
    for (int i = 0; i < 15; ++i) r.k[i] = _mm_loadu_si128(reinterpret_cast<const __m128i*>(K.rk[i]));
    return r;
}
static inline __m128i ni_block(const aes::Ctr& c) {
    // memory order: bytes 0..7 = big-endian hi, bytes 8..15 = big-endian lo
    return _mm_set_epi64x(static_cast<long long>(bswap64(c.lo)), static_cast<long long>(bswap64(c.hi)));
}
static inline __m128i ni_enc1(const NiKey& K, __m128i b) {
    b = _mm_xor_si128(b, K.k[0]);
    for (int r = 1; r < 14; ++r) b = _mm_aesenc_si128(b, K.k[r]);
    return _mm_aesenclast_si128(b, K.k[14]);
}
inline void ctr_aesni(const NiKey& K, const uint8_t iv[16], uint64_t blk_off, const uint8_t* in, uint8_t* out, size_t n) {
    aes::Ctr c = aes::ctr_add(aes::ctr_load(iv), blk_off);
    size_t p = 0;
    while (p + 128 <= n) {
        __m128i b0, b1, b2, b3, b4, b5, b6, b7;
        const __m128i k0 = K.k[0];
        if (c.lo <= ~uint64_t(0) - 7) {          // no carry inside this group: cheap build
            const long long h = static_cast<long long>(bswap64(c.hi));
            const uint64_t l = c.lo;
            b0 = _mm_set_epi64x(static_cast<long long>(bswap64(l)), h);
            b1 = _mm_set_epi64x(static_cast<long long>(bswap64(l + 1)), h);
            b2 = _mm_set_epi64x(static_cast<long long>(bswap64(l + 2)), h);
            b3 = _mm_set_epi64x(static_cast<long long>(bswap64(l + 3)), h);
            b4 = _mm_set_epi64x(static_cast<long long>(bswap64(l + 4)), h);
            b5 = _mm_set_epi64x(static_cast<long long>(bswap64(l + 5)), h);
            b6 = _mm_set_epi64x(static_cast<long long>(bswap64(l + 6)), h);
            b7 = _mm_set_epi64x(static_cast<long long>(bswap64(l + 7)), h);
        } else {
            b0 = ni_block(c); b1 = ni_block(aes::ctr_add(c, 1)); b2 = ni_block(aes::ctr_add(c, 2));
            b3 = ni_block(aes::ctr_add(c, 3)); b4 = ni_block(aes::ctr_add(c, 4)); b5 = ni_block(aes::ctr_add(c, 5));
            b6 = ni_block(aes::ctr_add(c, 6)); b7 = ni_block(aes::ctr_add(c, 7));
        }
        b0 = _mm_xor_si128(b0, k0); b1 = _mm_xor_si128(b1, k0); b2 = _mm_xor_si128(b2, k0); b3 = _mm_xor_si128(b3, k0);
        b4 = _mm_xor_si128(b4, k0); b5 = _mm_xor_si128(b5, k0); b6 = _mm_xor_si128(b6, k0); b7 = _mm_xor_si128(b7, k0);
        for (int r = 1; r < 14; ++r) {
            const __m128i k = K.k[r];
            b0 = _mm_aesenc_si128(b0, k); b1 = _mm_aesenc_si128(b1, k); b2 = _mm_aesenc_si128(b2, k); b3 = _mm_aesenc_si128(b3, k);
            b4 = _mm_aesenc_si128(b4, k); b5 = _mm_aesenc_si128(b5, k); b6 = _mm_aesenc_si128(b6, k); b7 = _mm_aesenc_si128(b7, k);
        }
        const __m128i kl = K.k[14];
        b0 = _mm_aesenclast_si128(b0, kl); b1 = _mm_aesenclast_si128(b1, kl); b2 = _mm_aesenclast_si128(b2, kl); b3 = _mm_aesenclast_si128(b3, kl);
        b4 = _mm_aesenclast_si128(b4, kl); b5 = _mm_aesenclast_si128(b5, kl); b6 = _mm_aesenclast_si128(b6, kl); b7 = _mm_aesenclast_si128(b7, kl);
#define NI_XS(i, b) _mm_storeu_si128(reinterpret_cast<__m128i*>(out + p + 16 * i), \
        _mm_xor_si128(b, _mm_loadu_si128(reinterpret_cast<const __m128i*>(in + p + 16 * i))))
        NI_XS(0, b0); NI_XS(1, b1); NI_XS(2, b2); NI_XS(3, b3); NI_XS(4, b4); NI_XS(5, b5); NI_XS(6, b6); NI_XS(7, b7);
#undef NI_XS
        p += 128;
        c = aes::ctr_add(c, 8);
    }
    for (; p < n; p += 16) {
        __m128i ks = ni_enc1(K, ni_block(c));
        if (p + 16 <= n) {
            _mm_storeu_si128(reinterpret_cast<__m128i*>(out + p),
                             _mm_xor_si128(ks, _mm_loadu_si128(reinterpret_cast<const __m128i*>(in + p))));
        } else {
            uint8_t t[16];
            _mm_storeu_si128(reinterpret_cast<__m128i*>(t), ks);
            for (size_t i = 0; p + i < n; ++i) out[p + i] = in[p + i] ^ t[i];
        }
        c = aes::ctr_add(c, 1);
    }
}
#else
constexpr bool HAVE_AESNI = false;
struct NiKey {};
inline NiKey ni_key(const aes::RoundKeys&) { return {}; }
inline void ctr_aesni(const NiKey&, const uint8_t*, uint64_t, const uint8_t*, uint8_t*, size_t) {}
#endif

// ======================================================================================
// AVX-512 VAES, 4 zmm = 16 blocks per iteration
// ======================================================================================
#if defined(__VAES__) && defined(__AVX512F__) && defined(__AVX512BW__) && defined(__AVX512DQ__)
constexpr bool HAVE_VAES = true;
struct VKey { __m512i k[15]; };
inline VKey v_key(const aes::RoundKeys& K) {
    VKey r;
    for (int i = 0; i < 15; ++i)
        r.k[i] = _mm512_broadcast_i64x2(_mm_loadu_si128(reinterpret_cast<const __m128i*>(K.rk[i])));
    return r;
}

// Encrypt 4 zmm of counter blocks and XOR with 256 bytes of input.
static inline void v_iter(const VKey& K, __m512i x0, __m512i x1, __m512i x2, __m512i x3,
                          const uint8_t* in, uint8_t* out) {
    const __m512i k0 = K.k[0];
    x0 = _mm512_xor_si512(x0, k0); x1 = _mm512_xor_si512(x1, k0);
    x2 = _mm512_xor_si512(x2, k0); x3 = _mm512_xor_si512(x3, k0);
    for (int r = 1; r < 14; ++r) {
        const __m512i k = K.k[r];
        x0 = _mm512_aesenc_epi128(x0, k); x1 = _mm512_aesenc_epi128(x1, k);
        x2 = _mm512_aesenc_epi128(x2, k); x3 = _mm512_aesenc_epi128(x3, k);
    }
    const __m512i kl = K.k[14];
    x0 = _mm512_aesenclast_epi128(x0, kl); x1 = _mm512_aesenclast_epi128(x1, kl);
    x2 = _mm512_aesenclast_epi128(x2, kl); x3 = _mm512_aesenclast_epi128(x3, kl);
    _mm512_storeu_si512(out,       _mm512_xor_si512(x0, _mm512_loadu_si512(in)));
    _mm512_storeu_si512(out + 64,  _mm512_xor_si512(x1, _mm512_loadu_si512(in + 64)));
    _mm512_storeu_si512(out + 128, _mm512_xor_si512(x2, _mm512_loadu_si512(in + 128)));
    _mm512_storeu_si512(out + 192, _mm512_xor_si512(x3, _mm512_loadu_si512(in + 192)));
}

// Generic (carry-safe) counter build for 16 blocks starting at c.
static inline void v_ctr_slow(const aes::Ctr& c, __m512i x[4]) {
    uint8_t buf[256];
    for (int i = 0; i < 16; ++i) aes::ctr_store(aes::ctr_add(c, uint64_t(i)), buf + 16 * i);
    for (int z = 0; z < 4; ++z) x[z] = _mm512_loadu_si512(buf + 64 * z);
}

inline void ctr_vaes(const VKey& K, const uint8_t iv[16], uint64_t blk_off, const uint8_t* in, uint8_t* out, size_t n) {
    aes::Ctr c = aes::ctr_add(aes::ctr_load(iv), blk_off);
    // Byte-reverse each qword (pshufb indices are per 128-bit lane).
    const __m512i BSW = _mm512_set_epi8(
        8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7,
        8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7);
    // Low-counter layout for unpacklo/unpackhi: even qwords -> first zmm, odd -> second zmm.
    //   A: blocks 0..7  = {0,4,1,5,2,6,3,7};  B: blocks 8..15 = {8,12,9,13,10,14,11,15}
    const __m512i OFF_A = _mm512_setr_epi64(0, 4, 1, 5, 2, 6, 3, 7);
    const __m512i OFF_B = _mm512_setr_epi64(8, 12, 9, 13, 10, 14, 11, 15);
    const __m512i STEP = _mm512_set1_epi64(16);
    __m512i loA = _mm512_add_epi64(_mm512_set1_epi64(static_cast<long long>(c.lo)), OFF_A);
    __m512i loB = _mm512_add_epi64(_mm512_set1_epi64(static_cast<long long>(c.lo)), OFF_B);
    __m512i hiS = _mm512_set1_epi64(static_cast<long long>(bswap64(c.hi)));
    size_t p = 0;
    while (p + 256 <= n) {
        if (c.lo <= ~uint64_t(0) - 15) {   // fast path: no carry within these 16 blocks
            const __m512i sA = _mm512_shuffle_epi8(loA, BSW);
            const __m512i sB = _mm512_shuffle_epi8(loB, BSW);
            v_iter(K, _mm512_unpacklo_epi64(hiS, sA), _mm512_unpackhi_epi64(hiS, sA),
                   _mm512_unpacklo_epi64(hiS, sB), _mm512_unpackhi_epi64(hiS, sB), in + p, out + p);
            const uint64_t prev = c.lo;
            c.lo += 16;
            if (c.lo < prev) {              // the NEXT group starts past the 64-bit boundary
                ++c.hi;
                hiS = _mm512_set1_epi64(static_cast<long long>(bswap64(c.hi)));
            }
            loA = _mm512_add_epi64(loA, STEP);
            loB = _mm512_add_epi64(loB, STEP);
        } else {                            // carry crosses the 64-bit boundary (rare)
            __m512i x[4];
            v_ctr_slow(c, x);
            v_iter(K, x[0], x[1], x[2], x[3], in + p, out + p);
            c = aes::ctr_add(c, 16);
            loA = _mm512_add_epi64(_mm512_set1_epi64(static_cast<long long>(c.lo)), OFF_A);
            loB = _mm512_add_epi64(_mm512_set1_epi64(static_cast<long long>(c.lo)), OFF_B);
            hiS = _mm512_set1_epi64(static_cast<long long>(bswap64(c.hi)));
        }
        p += 256;
    }
    if (p < n) {   // tail (< 16 blocks): pad into a scratch buffer, discard extra keystream
        uint8_t ti[256] = {0}, to[256];
        std::memcpy(ti, in + p, n - p);
        __m512i x[4];
        v_ctr_slow(c, x);
        v_iter(K, x[0], x[1], x[2], x[3], ti, to);
        std::memcpy(out + p, to, n - p);
    }
}
#else
constexpr bool HAVE_VAES = false;
struct VKey {};
inline VKey v_key(const aes::RoundKeys&) { return {}; }
inline void ctr_vaes(const VKey&, const uint8_t*, uint64_t, const uint8_t*, uint8_t*, size_t) {}
#endif

}  // namespace aesx
