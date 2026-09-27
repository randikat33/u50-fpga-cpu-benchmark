// vaes_probe.cpp - exits 0 if AVX-512 VAES instructions execute correctly on this machine.
#include <immintrin.h>
#include <cstring>
int main() {
    alignas(64) unsigned char a[64], k[64], o[64];
    std::memset(a, 0x11, 64); std::memset(k, 0x22, 64);
    __m512i r = _mm512_aesenc_epi128(_mm512_load_si512(a), _mm512_load_si512(k));
    _mm512_store_si512(o, r);
    __m128i r1 = _mm_aesenc_si128(_mm_loadu_si128((const __m128i*)a), _mm_loadu_si128((const __m128i*)k));
    unsigned char o1[16]; _mm_storeu_si128((__m128i*)o1, r1);
    return std::memcmp(o, o1, 16) == 0 && std::memcmp(o + 48, o1, 16) == 0 ? 0 : 1;
}
