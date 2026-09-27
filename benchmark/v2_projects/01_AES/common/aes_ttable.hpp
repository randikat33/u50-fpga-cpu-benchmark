// aes_ttable.hpp - portable 32-bit T-table AES-256-CTR (the classic rijndael-alg-fst
// structure: 4 x 256-entry 32-bit tables, 16 lookups + 4 XOR per round). Uses no crypto or
// SIMD instructions; this is the "no crypto extensions" architectural baseline. Also used
// (multi-threaded) as the fast independent checker for --verify.
#pragma once
#include "aes_ref.hpp"

namespace aes {

struct TTables {
    uint32_t Te0[256], Te1[256], Te2[256], Te3[256];
    TTables() {
        for (int x = 0; x < 256; ++x) {
            uint8_t s = SBOX[x], s2 = xtime(s), s3 = static_cast<uint8_t>(s2 ^ s);
            // column (2s, s, s, 3s) as a big-endian 32-bit word
            Te0[x] = (uint32_t(s2) << 24) | (uint32_t(s) << 16) | (uint32_t(s) << 8) | s3;
            Te1[x] = (uint32_t(s3) << 24) | (uint32_t(s2) << 16) | (uint32_t(s) << 8) | s;
            Te2[x] = (uint32_t(s) << 24) | (uint32_t(s3) << 16) | (uint32_t(s2) << 8) | s;
            Te3[x] = (uint32_t(s) << 24) | (uint32_t(s) << 16) | (uint32_t(s3) << 8) | s2;
        }
    }
    static const TTables& get() { static const TTables t; return t; }
};

struct TTKey { uint32_t rk[15][4]; };
inline TTKey tt_key(const RoundKeys& K) {
    TTKey k;
    for (int r = 0; r < 15; ++r)
        for (int c = 0; c < 4; ++c) {
            const uint8_t* p = K.rk[r] + 4 * c;
            k.rk[r][c] = (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3];
        }
    return k;
}

inline void tt_encrypt_words(const TTables& T, const TTKey& k, uint32_t s0, uint32_t s1, uint32_t s2, uint32_t s3,
                             uint8_t out[16]) {
    s0 ^= k.rk[0][0]; s1 ^= k.rk[0][1]; s2 ^= k.rk[0][2]; s3 ^= k.rk[0][3];
    for (int r = 1; r < NR; ++r) {
        uint32_t t0 = T.Te0[s0 >> 24] ^ T.Te1[(s1 >> 16) & 0xff] ^ T.Te2[(s2 >> 8) & 0xff] ^ T.Te3[s3 & 0xff] ^ k.rk[r][0];
        uint32_t t1 = T.Te0[s1 >> 24] ^ T.Te1[(s2 >> 16) & 0xff] ^ T.Te2[(s3 >> 8) & 0xff] ^ T.Te3[s0 & 0xff] ^ k.rk[r][1];
        uint32_t t2 = T.Te0[s2 >> 24] ^ T.Te1[(s3 >> 16) & 0xff] ^ T.Te2[(s0 >> 8) & 0xff] ^ T.Te3[s1 & 0xff] ^ k.rk[r][2];
        uint32_t t3 = T.Te0[s3 >> 24] ^ T.Te1[(s0 >> 16) & 0xff] ^ T.Te2[(s1 >> 8) & 0xff] ^ T.Te3[s2 & 0xff] ^ k.rk[r][3];
        s0 = t0; s1 = t1; s2 = t2; s3 = t3;
    }
    const uint32_t* f = k.rk[NR];
    uint32_t o0 = (uint32_t(SBOX[s0 >> 24]) << 24 | uint32_t(SBOX[(s1 >> 16) & 0xff]) << 16 |
                   uint32_t(SBOX[(s2 >> 8) & 0xff]) << 8 | SBOX[s3 & 0xff]) ^ f[0];
    uint32_t o1 = (uint32_t(SBOX[s1 >> 24]) << 24 | uint32_t(SBOX[(s2 >> 16) & 0xff]) << 16 |
                   uint32_t(SBOX[(s3 >> 8) & 0xff]) << 8 | SBOX[s0 & 0xff]) ^ f[1];
    uint32_t o2 = (uint32_t(SBOX[s2 >> 24]) << 24 | uint32_t(SBOX[(s3 >> 16) & 0xff]) << 16 |
                   uint32_t(SBOX[(s0 >> 8) & 0xff]) << 8 | SBOX[s1 & 0xff]) ^ f[2];
    uint32_t o3 = (uint32_t(SBOX[s3 >> 24]) << 24 | uint32_t(SBOX[(s0 >> 16) & 0xff]) << 16 |
                   uint32_t(SBOX[(s1 >> 8) & 0xff]) << 8 | SBOX[s2 & 0xff]) ^ f[3];
    for (int i = 0; i < 4; ++i) {
        out[i] = uint8_t(o0 >> (24 - 8 * i)); out[4 + i] = uint8_t(o1 >> (24 - 8 * i));
        out[8 + i] = uint8_t(o2 >> (24 - 8 * i)); out[12 + i] = uint8_t(o3 >> (24 - 8 * i));
    }
}

// CTR over n bytes (single thread). First block counter = IV + blk_off.
inline void ctr_ttable(const TTKey& k, const uint8_t iv[16], uint64_t blk_off, const uint8_t* in, uint8_t* out, size_t n) {
    const TTables& T = TTables::get();
    Ctr c = ctr_add(ctr_load(iv), blk_off);
    uint8_t ks[16];
    size_t p = 0;
    for (; p + 16 <= n; p += 16) {
        tt_encrypt_words(T, k, uint32_t(c.hi >> 32), uint32_t(c.hi), uint32_t(c.lo >> 32), uint32_t(c.lo), ks);
        for (int i = 0; i < 16; ++i) out[p + i] = in[p + i] ^ ks[i];
        if (++c.lo == 0) ++c.hi;
    }
    if (p < n) {
        tt_encrypt_words(T, k, uint32_t(c.hi >> 32), uint32_t(c.hi), uint32_t(c.lo >> 32), uint32_t(c.lo), ks);
        for (size_t i = 0; p + i < n; ++i) out[p + i] = in[p + i] ^ ks[i];
    }
}

}  // namespace aes
