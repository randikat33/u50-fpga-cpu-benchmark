// tb_aes.cpp - C-simulation testbench: kernel (aes256ctr) + all CPU implementations vs the
// textbook reference and NIST SP 800-38A F.5.5/F.5.6.
#include <openssl/evp.h>
#include <cstdio>
#include <random>
#include <string>
#include <vector>

#include "aes256ctr.h"
#include "aes_ref.hpp"
#include "aes_ttable.hpp"
#include "aes_x86.hpp"

static int g_fail = 0, g_pass = 0;
static void check(bool c, const std::string& what) {
    if (c) ++g_pass; else { ++g_fail; std::printf("FAIL: %s\n", what.c_str()); }
}

// Runs the HLS top exactly as the host does (pad to whole words, params buffer).
static void kernel_ctr(const aes::RoundKeys& K, const uint8_t iv[16], uint64_t blk_off,
                       const uint8_t* in, uint8_t* out, size_t n, uint64_t* words_out = nullptr) {
    const size_t words = (n + 63) / 64;
    std::vector<aes_word_t> src(words), dst(words);
    std::vector<uint8_t> pad(words * 64, 0);
    std::memcpy(pad.data(), in, n);
    std::memcpy(static_cast<void*>(src.data()), pad.data(), words * 64);
    for (auto& w : dst) w = 0;
    aes_word_t prm[AES_PARAM_WORDS];
    uint8_t pb[aes::PARAM_BYTES];
    aes::pack_params(K, iv, pb);
    std::memcpy(static_cast<void*>(prm), pb, sizeof pb);
    aes256ctr(src.data(), dst.data(), prm, ap_uint<64>(blk_off), aes_cnt_t(static_cast<uint32_t>(words)));
    std::memcpy(pad.data(), static_cast<const void*>(dst.data()), words * 64);
    std::memcpy(out, pad.data(), n);
    if (words_out) *words_out = words;
}

static void openssl_ctr(const uint8_t key[32], const uint8_t iv[16], uint64_t blk_off,
                        const uint8_t* in, uint8_t* out, size_t n) {
    uint8_t ivs[16];
    aes::ctr_add_iv(iv, blk_off, ivs);
    EVP_CIPHER_CTX* c = EVP_CIPHER_CTX_new();
    int l = 0;
    EVP_EncryptInit_ex(c, EVP_aes_256_ctr(), nullptr, key, ivs);
    EVP_EncryptUpdate(c, out, &l, in, static_cast<int>(n));
    EVP_CIPHER_CTX_free(c);
}

struct Case { size_t n; uint64_t blk_off; std::string iv_hex; std::string tag; };

int main() {
    // 1. bit-sliced hardware S-box == FIPS table, all 256 inputs
    bool sb = true;
    for (int x = 0; x < 256; ++x) sb = sb && aes_sbox_hw_test(static_cast<unsigned char>(x)) == aes::SBOX[x];
    check(sb, "bit-sliced S-box == table (256 inputs)");

    // 2. NIST F.5.5 (encrypt) and F.5.6 (decrypt) on every implementation
    const aes::RoundKeys KN = aes::expand_key(aes::NIST_KEY);
    const aes::TTKey tkN = aes::tt_key(KN);
    uint8_t o[64];
    auto nist = [&](const char* name, auto fn) {
        fn(aes::NIST_PT, o, 64); check(std::memcmp(o, aes::NIST_CT, 64) == 0, std::string("F.5.5 ") + name);
        fn(aes::NIST_CT, o, 64); check(std::memcmp(o, aes::NIST_PT, 64) == 0, std::string("F.5.6 ") + name);
    };
    nist("ref", [&](const uint8_t* i, uint8_t* q, size_t n) { aes::ctr_ref(KN, aes::NIST_IV, 0, i, q, n); });
    nist("ttable", [&](const uint8_t* i, uint8_t* q, size_t n) { aes::ctr_ttable(tkN, aes::NIST_IV, 0, i, q, n); });
    nist("kernel", [&](const uint8_t* i, uint8_t* q, size_t n) { kernel_ctr(KN, aes::NIST_IV, 0, i, q, n); });
    nist("openssl", [&](const uint8_t* i, uint8_t* q, size_t n) { openssl_ctr(aes::NIST_KEY, aes::NIST_IV, 0, i, q, n); });
    if (aesx::HAVE_AESNI) {
        auto nk = aesx::ni_key(KN);
        nist("aesni", [&](const uint8_t* i, uint8_t* q, size_t n) { aesx::ctr_aesni(nk, aes::NIST_IV, 0, i, q, n); });
    } else std::printf("NOTE: aesni not compiled in\n");
    if (aesx::HAVE_VAES) {
        auto vk = aesx::v_key(KN);
        nist("vaes", [&](const uint8_t* i, uint8_t* q, size_t n) { aesx::ctr_vaes(vk, aes::NIST_IV, 0, i, q, n); });
    } else std::printf("NOTE: vaes not compiled in\n");
    // single-block F.5.5 blocks 2..4 with block offsets
    for (int b = 1; b < 4; ++b) {
        kernel_ctr(KN, aes::NIST_IV, b, aes::NIST_PT + 16 * b, o, 16);
        check(std::memcmp(o, aes::NIST_CT + 16 * b, 16) == 0, "kernel F.5.5 block " + std::to_string(b + 1) + " via blk_off");
    }

    // 3. random data, edge sizes, offsets, counter carries
    std::mt19937_64 rng(12345);
    uint8_t key[32];
    for (auto& k : key) k = static_cast<uint8_t>(rng());
    const aes::RoundKeys K = aes::expand_key(key);
    const aes::TTKey tk = aes::tt_key(K);
    auto nk = aesx::ni_key(K);
    auto vk = aesx::v_key(K);
    const std::string IVR = "0123456789abcdef0011223344556677";
    std::vector<Case> cases = {
        {1, 0, IVR, "1 byte"}, {15, 0, IVR, "15"}, {16, 0, IVR, "16"}, {17, 0, IVR, "17"},
        {63, 0, IVR, "63"}, {64, 0, IVR, "64"}, {65, 0, IVR, "65"}, {127, 0, IVR, "127"},
        {255, 0, IVR, "255"}, {256, 0, IVR, "256"}, {257, 0, IVR, "257"}, {1000, 7, IVR, "1000 off7"},
        {4099, 123456789, IVR, "4099 off"},
        {3 * 1048576 + 5, 0, IVR, "3MiB+5"},
        {1048576 + 13, 1ull << 40, IVR, "1MiB+13 big offset"},
        // counter carry across the 64-bit boundary: IV low = all ones
        {4096 + 9, 0, "0001020304050607ffffffffffffffff", "carry lo=FF..FF"},
        {4096 + 9, 0, "00010203040506fffffffffffffffff0", "carry mid-word lo=FF..F0"},
        {1000, 0, "0001020304050607fffffffffffffff9", "carry inside a VAES group"},
        {1000, 0, "0001020304050607ffffffffffffffe0", "carry at a VAES group boundary"},
        {2000, 3, "000102030405060700000000fffffffd", "32-bit carry (OpenSSL ctr32 path)"},
        // carry produced by the block offset, and 128-bit wrap-around
        {777, 0x10, "0001020304050607fffffffffffffff8", "carry via blk_off"},
        {1024, 0, "ffffffffffffffffffffffffffffffff", "128-bit wrap"},
    };
    for (const auto& c : cases) {
        uint8_t iv[16];
        aes::parse_hex(c.iv_hex, iv, 16, "iv");
        std::vector<uint8_t> in(c.n), ref(c.n), got(c.n), back(c.n);
        for (auto& b : in) b = static_cast<uint8_t>(rng());
        aes::ctr_ref(K, iv, c.blk_off, in.data(), ref.data(), c.n);
        auto cmp = [&](const char* name) { check(ref == got, std::string(name) + " == ref [" + c.tag + "]"); };
        uint64_t words = 0;
        kernel_ctr(K, iv, c.blk_off, in.data(), got.data(), c.n, &words); cmp("kernel");
        kernel_ctr(K, iv, c.blk_off, got.data(), back.data(), c.n);
        check(back == in, "kernel dec(enc(x)) == x [" + c.tag + "]");
        aes::ctr_ttable(tk, iv, c.blk_off, in.data(), got.data(), c.n); cmp("ttable");
        openssl_ctr(key, iv, c.blk_off, in.data(), got.data(), c.n); cmp("openssl");
        if (aesx::HAVE_AESNI) { aesx::ctr_aesni(nk, iv, c.blk_off, in.data(), got.data(), c.n); cmp("aesni"); }
        if (aesx::HAVE_VAES) { aesx::ctr_vaes(vk, iv, c.blk_off, in.data(), got.data(), c.n); cmp("vaes"); }
        aes::ctr_ref(K, iv, c.blk_off, ref.data(), back.data(), c.n);
        check(back == in, "ref dec(enc(x)) == x [" + c.tag + "]");
    }

    // 4. chunked processing == one-shot (the host/CPU split rule: counter = IV + off/16)
    {
        uint8_t iv[16];
        aes::parse_hex("0001020304050607fffffffffffffff0", iv, 16, "iv");
        const size_t n = 5 * 4096 + 3, ch = 4096;
        std::vector<uint8_t> in(n), ref(n), got(n);
        for (auto& b : in) b = static_cast<uint8_t>(rng());
        aes::ctr_ref(K, iv, 0, in.data(), ref.data(), n);
        for (size_t off = 0; off < n; off += ch)
            kernel_ctr(K, iv, off / 16, in.data() + off, got.data() + off, std::min(ch, n - off));
        check(ref == got, "kernel chunked (5 chunks + tail, carry) == one-shot");
    }

    // 5. zero-word call must terminate (dataflow drains, no deadlock)
    {
        aes_word_t prm[AES_PARAM_WORDS], d0[1], d1[1];
        for (auto& w : prm) w = 0;
        d0[0] = 0; d1[0] = 0;
        aes256ctr(d0, d1, prm, 0, 0);
        check(d1[0] == 0, "n_words = 0 writes nothing");
    }

    std::printf("tb_aes: %d passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
