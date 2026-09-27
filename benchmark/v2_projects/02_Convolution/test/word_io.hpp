// word_io.hpp - byte buffer <-> ap_uint<512> conversion for C-simulation (layout-independent:
// byte b of word w is bit range [8b+7:8b], i.e. little-endian as on the AXI bus).
#pragma once
#include <cstdint>
#include <cstring>
#include "conv3_core.hpp"

inline void bytes_to_words(const uint8_t* src, conv3::word_t* dst, size_t nwords) {
    for (size_t i = 0; i < nwords; ++i) {
        conv3::word_t w = 0;
        for (int q = 0; q < 8; ++q) {
            uint64_t v; std::memcpy(&v, src + i * 64 + q * 8, 8);
            w.range(64 * q + 63, 64 * q) = v;
        }
        dst[i] = w;
    }
}
inline void words_to_bytes(const conv3::word_t* src, uint8_t* dst, size_t nwords) {
    for (size_t i = 0; i < nwords; ++i)
        for (int q = 0; q < 8; ++q) {
            uint64_t v = src[i].range(64 * q + 63, 64 * q).to_uint64();
            std::memcpy(dst + i * 64 + q * 8, &v, 8);
        }
}
