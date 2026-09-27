// ls_resize_cpu.hpp - CPU resize implementations for one I420 frame.
//   opencv : cv::resize(INTER_LINEAR) per plane (industry baseline; IPP/HAL if the build has it)
//   avx512 : hand-written AVX-512 version of the Vitis Vision fixed-point bilinear
//            (bit-exact with the FPGA kernel; scalar fallback without AVX-512)
//   ref    : scalar model (common/xf_resize_ref.hpp)
#pragma once
#include <cstdint>
#include <string>
#include "xf_resize_ref.hpp"

namespace ls {
enum class CpuImpl { OpenCV, Avx512, Ref };
CpuImpl parse_impl(const std::string& s);   // throws on unknown
const char* impl_name(CpuImpl i);
const char* isa_name();                      // compiled ISA of the avx512 path

struct XfPlan {                              // tables for one plane geometry (exact FPGA numerics)
    int sw = 0, sh = 0, dw = 0, dh = 0, npc = 1;
    lsref::Axis ax, ay;
    void build(int sw_, int sh_, int dw_, int dh_, int npc_);
};

// One plane. dst rows have 'dstride' bytes. 'threads' > 1 -> OpenMP rows (avx512/ref only).
void resize_plane_opencv(const uint8_t* src, int sw, int sh, int sstride, uint8_t* dst, int dw, int dh, int dstride);
void resize_plane_xf(const XfPlan& p, const uint8_t* src, int sstride, uint8_t* dst, int dstride, bool simd, int threads);
}  // namespace ls
