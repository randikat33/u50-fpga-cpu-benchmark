// ls_kernel_if.hpp - constants shared by the FPGA kernel, the host, the CPU baseline and the tests.
// Live-stream single-output resize (project "live_single"), v2.
#pragma once

namespace lsk {
// Maximum geometry synthesized into the kernel (template parameters of xf::cv::Mat / resize).
constexpr int MAX_IN_W = 3840;   // luma
constexpr int MAX_IN_H = 2160;
constexpr int MAX_OUT_W = 1920;
constexpr int MAX_OUT_H = 1080;
constexpr int MAX_CIN_W = MAX_IN_W / 2;   // chroma (4:2:0)
constexpr int MAX_CIN_H = MAX_IN_H / 2;
constexpr int MAX_COUT_W = MAX_OUT_W / 2;
constexpr int MAX_COUT_H = MAX_OUT_H / 2;

// Pixels per clock of the Vitis Vision resize instances.
constexpr int NPPC_Y = 8;   // luma: 8 px/cycle
constexpr int NPPC_C = 4;   // chroma planes are 4x smaller, 4 px/cycle keeps them ahead of luma

// xf::cv::resize MAX_DOWN_SCALE template parameter (line-buffer words per output word = MAX_DOWN_SCALE+1).
// 9 gives BUFFER_WORDS=10: legal scale factor up to ~10.2 (NPPC8) / ~12 (NPPC4). The host limits it to 10.
constexpr int MAX_DOWN_SCALE = 9;
constexpr int MAX_SCALE_ALLOWED = 10;

// m_axi word width (bytes per word = 64)
constexpr int AXI_W = 512;
constexpr int AXI_BYTES = AXI_W / 8;

// Kernel top name and argument order (host, xrt_sim registration and tests use these).
constexpr const char* TOP = "ls_resize";
enum Arg { A_Y_IN = 0, A_U_IN, A_V_IN, A_Y_OUT, A_U_OUT, A_V_OUT, A_IN_W, A_IN_H, A_OUT_W, A_OUT_H, N_ARGS };

// Kernel only down-scales (or keeps size) and needs >= 2 input rows/cols.
inline bool geometry_ok(int in_w, int in_h, int out_w, int out_h) {
    if (in_w < 2 || in_h < 2 || out_w < 1 || out_h < 1) return false;
    if (in_w > MAX_IN_W || in_h > MAX_IN_H || out_w > MAX_OUT_W || out_h > MAX_OUT_H) return false;
    if (out_w > in_w || out_h > in_h) return false;
    if ((long)out_w * MAX_SCALE_ALLOWED < in_w || (long)out_h * MAX_SCALE_ALLOWED < in_h) return false;
    int ciw = (in_w + 1) / 2, cih = (in_h + 1) / 2, cow = (out_w + 1) / 2, coh = (out_h + 1) / 2;
    if (ciw < 2 || cih < 2) return false;
    if ((long)cow * MAX_SCALE_ALLOWED < ciw || (long)coh * MAX_SCALE_ALLOWED < cih) return false;
    return true;
}
// Bytes of a tightly packed plane rounded up to whole 64-byte AXI words.
inline long axi_bytes(long w, long h) { return (w * h + AXI_BYTES - 1) / AXI_BYTES * AXI_BYTES; }
}  // namespace lsk
