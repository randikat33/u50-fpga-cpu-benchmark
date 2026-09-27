// ls_resize.h - v2 single-output resize kernel (Vitis Vision bilinear, 4K BGR -> <=1080p)
#pragma once
#include "ap_int.h"
#include "hls_stream.h"
#include "common/xf_common.hpp"
#include "common/xf_utility.hpp"
#include "imgproc/xf_resize.hpp"
#include "ls_config.hpp"

typedef ap_uint<LS_WORD_BITS> ls_word_t;   // one m_axi word (64 raw BGR bytes)
typedef ap_uint<LS_PACK_BITS> ls_pack_t;   // one xf::cv NPPC8 group (8 BGR pixels)

// Stream depths. The library's xf::cv::Mat FIFOs sit between the gearboxes and resize.
#define LS_DEPTH_IN   2048
#define LS_DEPTH_OUT  2048
#define LS_WORD_FIFO  512      // >= 2 x max burst length

// Vitis Vision changed the resize template list after 2023.1: newer releases (GitHub
// main / 2024.x) have an extra `bool USE_URAM` parameter before MAX_DOWN_SCALE. The
// Makefile detects which one is installed and defines LS_RESIZE_HAS_URAM_ARG=1/0.
#ifndef LS_RESIZE_HAS_URAM_ARG
#define LS_RESIZE_HAS_URAM_ARG 0
#endif

typedef xf::cv::Mat<XF_8UC3, LS_MAX_IN_H, LS_MAX_IN_W, XF_NPPC8, LS_DEPTH_IN> ls_in_mat_t;
typedef xf::cv::Mat<XF_8UC3, LS_MAX_OUT_H, LS_MAX_OUT_W, XF_NPPC8, LS_DEPTH_OUT> ls_out_mat_t;

extern "C" {
// src: ceil(in_h*in_w*3/64) words of raw BGR; dst: ceil(out_h*out_w*3/64) words.
// Invalid dimensions (see ls::dims_ok) make the kernel return without touching dst.
void ls_resize(const ls_word_t* src, ls_word_t* dst, int in_h, int in_w, int out_h, int out_w);
}
