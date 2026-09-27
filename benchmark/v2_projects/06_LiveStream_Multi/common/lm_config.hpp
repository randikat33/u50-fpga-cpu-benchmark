// lm_config.hpp - constants shared by kernel, host, CPU baseline and tests.
// v2 multi-output live stream: one 4K BGR frame -> 5-rung ABR ladder in one kernel call.
#pragma once

#define LM_MAX_IN_W   3840
#define LM_MAX_IN_H   2160
#define LM_NPPC       8
#define LM_BPP        3
#define LM_WORD_BITS  512
#define LM_WORD_BYTES 64
#define LM_PACK_BITS  (LM_NPPC * LM_BPP * 8)
#define LM_NOUT       5
#define LM_MAX_BATCH  8          // frames per kernel call (8 x 25 MB input < 1 GiB)

// Ladder rungs (kernel widths are multiples of 8; the sink centre-crops 432->426 and
// 856->854 to the standard ABR sizes, identically on both platforms, as in v1).
#define LM_W0 432
#define LM_H0 240
#define LM_W1 640
#define LM_H1 360
#define LM_W2 856
#define LM_H2 480
#define LM_W3 1280
#define LM_H3 720
#define LM_W4 1920
#define LM_H4 1080
// Per-rung MAX_DOWN_SCALE for Vitis Vision (ratio from 4K, rounded up, +1): sizes the
// line-buffer copies of each resizer to what that rung needs.
#define LM_MD0 10
#define LM_MD1 7
#define LM_MD2 6
#define LM_MD3 4
#define LM_MD4 3

#ifdef __cplusplus
#include <cstddef>
namespace lm {
struct Rung { int w, h, enc_w, md; const char* name; const char* bitrate; const char* maxrate; const char* bufsize; int x264_threads; };
// Bitrates and encoder threads are the v1 values.
static const Rung kRungs[LM_NOUT] = {
    {LM_W0, LM_H0, 426, LM_MD0, "240p", "500k", "750k", "1500k", 2},
    {LM_W1, LM_H1, 640, LM_MD1, "360p", "1000k", "1500k", "3000k", 2},
    {LM_W2, LM_H2, 854, LM_MD2, "480p", "2000k", "3000k", "6000k", 4},
    {LM_W3, LM_H3, 1280, LM_MD3, "720p", "4000k", "6000k", "12000k", 4},
    {LM_W4, LM_H4, 1920, LM_MD4, "1080p", "8000k", "12000k", "24000k", 6}};

inline size_t frame_bytes(int w, int h) { return (size_t)w * (size_t)h * LM_BPP; }
inline size_t words_for(size_t b) { return (b + LM_WORD_BYTES - 1) / LM_WORD_BYTES; }
inline size_t in_words(int w, int h) { return words_for(frame_bytes(w, h)); }
inline size_t rung_words(int k) { return words_for(frame_bytes(kRungs[k].w, kRungs[k].h)); }
// word offset of rung k inside one frame's output block
inline size_t rung_offset(int k) { size_t o = 0; for (int i = 0; i < k; i++) o += rung_words(i); return o; }
inline size_t out_words_per_frame() { return rung_offset(LM_NOUT); }
inline bool dims_ok(int in_w, int in_h) {
    if (in_w <= 0 || in_h < 2 || in_w > LM_MAX_IN_W || in_h > LM_MAX_IN_H || in_w % LM_NPPC) return false;
    for (int k = 0; k < LM_NOUT; k++) {
        const Rung& r = kRungs[k];
        if (r.w > in_w || r.h > in_h || in_w > r.w * (r.md - 1) || in_h > r.h * (r.md - 1)) return false;
    }
    return true;
}
}  // namespace lm
#endif
