// ls_config.hpp - workload constants shared by kernel, host, CPU baseline and tests.
// v2 single-output live-stream resize (4K BGR -> one of 5 output qualities).
#pragma once

// ---- frame geometry --------------------------------------------------------------
#define LS_MAX_IN_W   3840          // maximum (and paper) input width
#define LS_MAX_IN_H   2160
#define LS_MAX_OUT_W  1920          // largest output (quality 4 = 1080p)
#define LS_MAX_OUT_H  1080
#define LS_NPPC       8             // pixels per clock inside the kernel (XF_NPPC8)
#define LS_BPP        3             // BGR, 8 bits per channel (XF_8UC3)

// ---- AXI packing ------------------------------------------------------------------
// The host buffer is the raw interleaved BGR frame (exactly what the decoder returns),
// padded with zeros to a multiple of LS_WORD_BYTES. One m_axi word = 64 bytes.
#define LS_WORD_BITS  512
#define LS_WORD_BYTES 64
#define LS_PACK_BITS  (LS_NPPC * LS_BPP * 8)   // 192: one xf::cv NPPC8 pixel group

// Maximum down-scale used by the library line buffers: 3840/432 = 8.9 -> 9 (+margin).
// v1 used 16, which sized 9 line-buffer copies instead of 6.
#define LS_MAX_DOWN   10

#ifdef __cplusplus
#include <cstddef>
namespace ls {
struct Quality { int w, h; const char* name; };
// All widths are divisible by 8 (NPPC8). 240p is 432 wide and 480p is 856 wide, as in
// v1's FPGA path. Both platforms use these exact sizes; the v1 CPU path cropped 432->426
// and resized 480p to 856 wide first. v2 does not crop on either side.
static const Quality kQualities[5] = {
    {432, 240, "240p"}, {640, 360, "360p"}, {856, 480, "480p"}, {1280, 720, "720p"}, {1920, 1080, "1080p"}};

inline size_t frame_bytes(int w, int h) { return (size_t)w * (size_t)h * LS_BPP; }
inline size_t words_for(size_t bytes) { return (bytes + LS_WORD_BYTES - 1) / LS_WORD_BYTES; }
inline size_t padded_bytes(int w, int h) { return words_for(frame_bytes(w, h)) * LS_WORD_BYTES; }
inline bool dims_ok(int in_w, int in_h, int out_w, int out_h) {
    return in_w > 0 && in_h > 1 && out_w > 0 && out_h > 1 &&   // the library needs >= 2 rows
           in_w <= LS_MAX_IN_W && in_h <= LS_MAX_IN_H &&
           out_w <= LS_MAX_OUT_W && out_h <= LS_MAX_OUT_H && (in_w % LS_NPPC) == 0 && (out_w % LS_NPPC) == 0 &&
           out_w <= in_w && out_h <= in_h && (in_w + out_w - 1) / out_w <= LS_MAX_DOWN - 1 &&
           (in_h + out_h - 1) / out_h <= LS_MAX_DOWN - 1;
}
}  // namespace ls
#endif
