// aes_ref.hpp - AES-256-CTR scalar reference model shared by kernel testbench, FPGA host,
// CPU baseline and tests. Textbook byte-wise FIPS-197 AES (deliberately simple; it is the
// oracle, not a baseline) plus everything that defines the CTR contract:
//
//   * key schedule (15 round keys, AES byte order)
//   * NIST SP 800-38A counter: the FULL 128-bit big-endian counter block is incremented
//     once per block (carry propagates across the 64-bit boundary, same as OpenSSL)
//   * block offset: the chunk that starts at byte B uses counter = IV + B/16  (B % 16 == 0)
//   * the kernel parameter buffer layout (4 x 512-bit words = 256 bytes)
//   * hex parsing, NIST F.5.5 test vector
//
// Header-only, C++17, no HLS or x86-specific includes.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>

namespace aes {

constexpr size_t BLOCK = 16;
constexpr size_t WORD_BYTES = 64;           // one 512-bit kernel word = 4 AES blocks
constexpr size_t PARAM_WORDS = 4;           // kernel parameter buffer: 4 words
constexpr size_t PARAM_BYTES = PARAM_WORDS * WORD_BYTES;
constexpr int NR = 14;                      // AES-256 rounds

constexpr uint8_t SBOX[256] = {
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16};

inline uint8_t xtime(uint8_t x) { return static_cast<uint8_t>((x << 1) ^ ((x & 0x80) ? 0x1b : 0x00)); }

// ---- key schedule: rk[r][i], r = 0..14, AES byte order ------------------------------
struct RoundKeys { uint8_t rk[15][16]; };

inline RoundKeys expand_key(const uint8_t key[32]) {
    uint8_t w[60][4];
    for (int i = 0; i < 8; ++i) for (int j = 0; j < 4; ++j) w[i][j] = key[4 * i + j];
    uint8_t rcon = 0x01;
    for (int i = 8; i < 60; ++i) {
        uint8_t t[4] = {w[i - 1][0], w[i - 1][1], w[i - 1][2], w[i - 1][3]};
        if (i % 8 == 0) {
            uint8_t t0 = t[0];
            t[0] = static_cast<uint8_t>(SBOX[t[1]] ^ rcon); t[1] = SBOX[t[2]]; t[2] = SBOX[t[3]]; t[3] = SBOX[t0];
            rcon = xtime(rcon);
        } else if (i % 8 == 4) {
            for (int j = 0; j < 4; ++j) t[j] = SBOX[t[j]];
        }
        for (int j = 0; j < 4; ++j) w[i][j] = w[i - 8][j] ^ t[j];
    }
    RoundKeys r;
    for (int k = 0; k < 15; ++k) for (int c = 0; c < 4; ++c) for (int j = 0; j < 4; ++j) r.rk[k][4 * c + j] = w[4 * k + c][j];
    return r;
}

// ---- textbook block encryption --------------------------------------------------------
inline void encrypt_block(const RoundKeys& K, const uint8_t in[16], uint8_t out[16]) {
    uint8_t s[16];
    for (int i = 0; i < 16; ++i) s[i] = in[i] ^ K.rk[0][i];
    for (int r = 1; r <= NR; ++r) {
        uint8_t t[16];
        for (int i = 0; i < 16; ++i) t[i] = SBOX[s[i]];
        // ShiftRows (column-major state: byte 4c+row)
        uint8_t u[16] = {t[0], t[5], t[10], t[15], t[4], t[9], t[14], t[3],
                         t[8], t[13], t[2], t[7], t[12], t[1], t[6], t[11]};
        if (r != NR) {
            for (int c = 0; c < 4; ++c) {
                uint8_t a0 = u[4 * c], a1 = u[4 * c + 1], a2 = u[4 * c + 2], a3 = u[4 * c + 3];
                uint8_t x = a0 ^ a1 ^ a2 ^ a3;
                u[4 * c]     = a0 ^ x ^ xtime(a0 ^ a1);
                u[4 * c + 1] = a1 ^ x ^ xtime(a1 ^ a2);
                u[4 * c + 2] = a2 ^ x ^ xtime(a2 ^ a3);
                u[4 * c + 3] = a3 ^ x ^ xtime(a3 ^ a0);
            }
        }
        for (int i = 0; i < 16; ++i) s[i] = u[i] ^ K.rk[r][i];
    }
    std::memcpy(out, s, 16);
}

// ---- 128-bit big-endian counter --------------------------------------------------------
struct Ctr { uint64_t hi, lo; };
inline uint64_t load_be64(const uint8_t* p) { uint64_t v = 0; for (int i = 0; i < 8; ++i) v = (v << 8) | p[i]; return v; }
inline void store_be64(uint8_t* p, uint64_t v) { for (int i = 7; i >= 0; --i) { p[i] = static_cast<uint8_t>(v); v >>= 8; } }
inline Ctr ctr_load(const uint8_t iv[16]) { return Ctr{load_be64(iv), load_be64(iv + 8)}; }
inline void ctr_store(const Ctr& c, uint8_t out[16]) { store_be64(out, c.hi); store_be64(out + 8, c.lo); }
// value + n  (mod 2^128)
inline Ctr ctr_add(const Ctr& c, uint64_t n) { Ctr r{c.hi, c.lo + n}; if (r.lo < c.lo) ++r.hi; return r; }
inline void ctr_add_iv(const uint8_t iv[16], uint64_t n, uint8_t out[16]) { ctr_store(ctr_add(ctr_load(iv), n), out); }

// CTR over n bytes; first block uses counter IV + blk_off.
inline void ctr_ref(const RoundKeys& K, const uint8_t iv[16], uint64_t blk_off,
                    const uint8_t* in, uint8_t* out, size_t n) {
    Ctr c = ctr_add(ctr_load(iv), blk_off);
    uint8_t cb[16], ks[16];
    for (size_t p = 0; p < n; p += 16) {
        ctr_store(c, cb);
        encrypt_block(K, cb, ks);
        size_t m = n - p < 16 ? n - p : 16;
        for (size_t i = 0; i < m; ++i) out[p + i] = in[p + i] ^ ks[i];
        c = ctr_add(c, 1);
    }
}

// ---- kernel parameter buffer (256 bytes = 4 x 512-bit words, byte j = word bits 8j+7:8j)
//   bytes   0..239 : rk[0..14], 16 bytes each, AES byte order
//   bytes 240..255 : IV (initial counter block, big-endian)
inline void pack_params(const RoundKeys& K, const uint8_t iv[16], uint8_t out[PARAM_BYTES]) {
    for (int r = 0; r < 15; ++r) std::memcpy(out + 16 * r, K.rk[r], 16);
    std::memcpy(out + 240, iv, 16);
}

// ---- hex parsing -----------------------------------------------------------------------
inline void parse_hex(const std::string& s, uint8_t* out, size_t n, const char* what) {
    std::string h = s;
    if (h.rfind("0x", 0) == 0 || h.rfind("0X", 0) == 0) h = h.substr(2);
    if (h.size() != 2 * n) throw std::runtime_error(std::string(what) + " must be " + std::to_string(2 * n) + " hex digits");
    auto nib = [&](char c) -> uint8_t {
        if (c >= '0' && c <= '9') return static_cast<uint8_t>(c - '0');
        if (c >= 'a' && c <= 'f') return static_cast<uint8_t>(c - 'a' + 10);
        if (c >= 'A' && c <= 'F') return static_cast<uint8_t>(c - 'A' + 10);
        throw std::runtime_error(std::string("bad hex digit in ") + what);
    };
    for (size_t i = 0; i < n; ++i) out[i] = static_cast<uint8_t>((nib(h[2 * i]) << 4) | nib(h[2 * i + 1]));
}

// Defaults used by the suite (same values on every platform)
constexpr const char* DEFAULT_KEY = "603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4";
constexpr const char* DEFAULT_IV  = "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff";

// ---- NIST SP 800-38A F.5.5 (CTR-AES256.Encrypt); F.5.6 is the same data reversed ----------
constexpr uint8_t NIST_KEY[32] = {
    0x60,0x3d,0xeb,0x10,0x15,0xca,0x71,0xbe,0x2b,0x73,0xae,0xf0,0x85,0x7d,0x77,0x81,
    0x1f,0x35,0x2c,0x07,0x3b,0x61,0x08,0xd7,0x2d,0x98,0x10,0xa3,0x09,0x14,0xdf,0xf4};
constexpr uint8_t NIST_IV[16] = {
    0xf0,0xf1,0xf2,0xf3,0xf4,0xf5,0xf6,0xf7,0xf8,0xf9,0xfa,0xfb,0xfc,0xfd,0xfe,0xff};
constexpr uint8_t NIST_PT[64] = {
    0x6b,0xc1,0xbe,0xe2,0x2e,0x40,0x9f,0x96,0xe9,0x3d,0x7e,0x11,0x73,0x93,0x17,0x2a,
    0xae,0x2d,0x8a,0x57,0x1e,0x03,0xac,0x9c,0x9e,0xb7,0x6f,0xac,0x45,0xaf,0x8e,0x51,
    0x30,0xc8,0x1c,0x46,0xa3,0x5c,0xe4,0x11,0xe5,0xfb,0xc1,0x19,0x1a,0x0a,0x52,0xef,
    0xf6,0x9f,0x24,0x45,0xdf,0x4f,0x9b,0x17,0xad,0x2b,0x41,0x7b,0xe6,0x6c,0x37,0x10};
constexpr uint8_t NIST_CT[64] = {
    0x60,0x1e,0xc3,0x13,0x77,0x57,0x89,0xa5,0xb7,0xa7,0xf5,0x04,0xbb,0xf3,0xd2,0x28,
    0xf4,0x43,0xe3,0xca,0x4d,0x62,0xb5,0x9a,0xca,0x84,0xe9,0x90,0xca,0xca,0xf5,0xc5,
    0x2b,0x09,0x30,0xda,0xa2,0x3d,0xe9,0x4c,0xe8,0x70,0x17,0xba,0x2d,0x84,0x98,0x8d,
    0xdf,0xc9,0xc5,0x8d,0xb6,0x7a,0xad,0xa6,0x13,0xc2,0xdd,0x08,0x45,0x79,0x41,0xa6};

}  // namespace aes
