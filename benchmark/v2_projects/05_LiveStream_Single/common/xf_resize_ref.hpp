// xf_resize_ref.hpp - scalar reference model of Vitis Vision 2023.1 xf::cv::resize
// (XF_INTERPOLATION_BILINEAR, 8-bit, one channel), bit-exact for a given NPPC.
//
// Derived from L1/include/imgproc/xf_resize_nn_bilinear.hpp:
//   S      = (in << 32) / out                     (Q32.32, truncating division)
//   ind(c) = floor(((2c+1)*S - 2^32) / 2^11)      (Q.22; scaleCompute: (c+0.5)*S - 0.5, AP_TRN)
//   x(c)   = ind(npc*j) + i*floor(S/2^10)         (c = npc*j + i; scaleXParallel uses the Q.22 scale)
//   y(r)   = ind(r)
//   clamp to [0, (len-1) << 22]; integer part = index, weight = frac >> 12 (ap_ufixed<12,2>, AP_TRN)
//   wxy    = (wx*wy) >> 10
//   sum    = A0*1024 + (B0-A0)*wy + (A1-A0)*wx + (A0+B1-B0-A1)*wxy   (always >= 0)
//   out    = sum >> 10
// A0=I[y][x], A1=I[y][x+1], B0=I[y+1][x], B1=I[y+1][x+1] (neighbours clamped; their weight is 0 there).
// OpenCV INTER_LINEAR uses the same half-pixel geometry but 11-bit rounded weights, so the
// two differ by a small bias (typically |d| <= 2, mean ~0.5 LSB); see README section 6.
#pragma once
#include <cstdint>
#include <vector>
#include <algorithm>

namespace lsref {

inline uint64_t scale_q32(int in, int out) { return (static_cast<uint64_t>(in) << 32) / static_cast<uint64_t>(out); }
inline int64_t ind22(int64_t c, uint64_t S) {
    return static_cast<int64_t>((2 * c + 1) * static_cast<int64_t>(S) - (int64_t(1) << 32)) >> 11;
}

struct Axis {                       // per output coordinate
    std::vector<int32_t> i0, i1, w; // index, clamped next index, 10-bit weight
};

// npc = pixels per cycle of the kernel instance (x axis only); use 1 for the y axis.
inline Axis build_axis(int in, int out, int npc) {
    Axis a; a.i0.resize(out); a.i1.resize(out); a.w.resize(out);
    uint64_t S = scale_q32(in, out);
    int64_t s22 = static_cast<int64_t>(S >> 10);
    int64_t maxq = static_cast<int64_t>(in - 1) << 22;
    for (int c = 0; c < out; ++c) {
        int j = c / npc, i = c - j * npc;
        int64_t q = ind22(static_cast<int64_t>(j) * npc, S) + static_cast<int64_t>(i) * s22;
        if (q < 0) q = 0; else if (q > maxq) q = maxq;
        int32_t x = static_cast<int32_t>(q >> 22);
        a.i0[c] = x;
        a.i1[c] = std::min(x + 1, in - 1);
        a.w[c] = static_cast<int32_t>((q & ((int64_t(1) << 22) - 1)) >> 12);
    }
    return a;
}

inline uint8_t interp(int a0, int a1, int b0, int b1, int wx, int wy) {
    int wxy = (wx * wy) >> 10;
    int sum = (a0 << 10) + (b0 - a0) * wy + (a1 - a0) * wx + (a0 + b1 - b0 - a1) * wxy;
    return static_cast<uint8_t>(sum >> 10);   // sum in [0, 255*1024]
}

// Resize one 8-bit plane. src rows have 'sstride' bytes, dst rows 'dstride' bytes.
inline void resize_plane(const uint8_t* src, int sw, int sh, int sstride,
                         uint8_t* dst, int dw, int dh, int dstride, int npc) {
    Axis ax = build_axis(sw, dw, npc), ay = build_axis(sh, dh, 1);
    for (int r = 0; r < dh; ++r) {
        const uint8_t* r0 = src + static_cast<size_t>(ay.i0[r]) * sstride;
        const uint8_t* r1 = src + static_cast<size_t>(ay.i1[r]) * sstride;
        uint8_t* o = dst + static_cast<size_t>(r) * dstride;
        int wy = ay.w[r];
        for (int c = 0; c < dw; ++c) {
            int x0 = ax.i0[c], x1 = ax.i1[c];
            o[c] = interp(r0[x0], r0[x1], r1[x0], r1[x1], ax.w[c], wy);
        }
    }
}
}  // namespace lsref
