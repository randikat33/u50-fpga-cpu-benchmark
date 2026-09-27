// conv_ref.hpp - scalar reference model (the definition of correct output).
// Semantics are those of the thesis (v1) kernels and CPU hosts:
//   sharpen = clamp( 5*C - (N+S+W+E) )                 kernel [0,-1,0; -1,5,-1; 0,-1,0]
//   edge    = clamp( 8*C - (sum of the 8 neighbours) )  kernel [-1 x8, 8 centre]
//   blur    = clamp( (NW+2N+NE+2W+4C+2E+SW+2S+SE) >> 4 ) (floor division by 16)
// clamp to [0, 2^bits-1], computed independently per channel, and a zero 1-pixel border
// (rows 0 and H-1, columns 0 and W-1). Images smaller than 3x3 give all-zero outputs.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace conv {

template <typename T>
inline T clamp_px(int v, int maxv) { return static_cast<T>(v < 0 ? 0 : (v > maxv ? maxv : v)); }

// One interior output row. up/mid/dn are the rows y-1, y, y+1 (W*C samples each).
template <typename T>
inline void ref_row(const T* up, const T* mid, const T* dn, T* sh, T* ed, T* bl, int W, int C) {
    const int n = W * C;
    const int maxv = (1 << (8 * sizeof(T))) - 1;
    if (W < 3) {
        std::memset(sh, 0, n * sizeof(T)); std::memset(ed, 0, n * sizeof(T)); std::memset(bl, 0, n * sizeof(T));
        return;
    }
    for (int i = 0; i < C; ++i) { sh[i] = ed[i] = bl[i] = 0; sh[n - 1 - i] = ed[n - 1 - i] = bl[n - 1 - i] = 0; }
    for (int i = C; i < n - C; ++i) {
        const int c = mid[i], N = up[i], S = dn[i], Wv = mid[i - C], E = mid[i + C];
        const int NW = up[i - C], NE = up[i + C], SW = dn[i - C], SE = dn[i + C];
        const int s4 = N + S + Wv + E;
        const int cr = NW + NE + SW + SE;
        sh[i] = clamp_px<T>(5 * c - s4, maxv);
        ed[i] = clamp_px<T>(8 * c - s4 - cr, maxv);
        bl[i] = clamp_px<T>((4 * c + 2 * s4 + cr) >> 4, maxv);
    }
}

// Byte-level dispatcher (bps = bytes per sample, 1 or 2).
inline void ref_row_bytes(const uint8_t* up, const uint8_t* mid, const uint8_t* dn,
                          uint8_t* sh, uint8_t* ed, uint8_t* bl, int W, int C, int bps) {
    if (bps == 2)
        ref_row<uint16_t>((const uint16_t*)up, (const uint16_t*)mid, (const uint16_t*)dn,
                          (uint16_t*)sh, (uint16_t*)ed, (uint16_t*)bl, W, C);
    else
        ref_row<uint8_t>(up, mid, dn, sh, ed, bl, W, C);
}

// Whole image on row-pointer arrays (out rows must be writable, distinct buffers).
inline void ref_image(const uint8_t* const* in, uint8_t* const* sh, uint8_t* const* ed, uint8_t* const* bl,
                      int W, int H, int C, int bps) {
    const size_t rb = (size_t)W * C * bps;
    for (int y = 0; y < H; ++y) {
        if (y == 0 || y == H - 1 || H < 3) {
            std::memset(sh[y], 0, rb); std::memset(ed[y], 0, rb); std::memset(bl[y], 0, rb);
            continue;
        }
        ref_row_bytes(in[y - 1], in[y], in[y + 1], sh[y], ed[y], bl[y], W, C, bps);
    }
}

}  // namespace conv
