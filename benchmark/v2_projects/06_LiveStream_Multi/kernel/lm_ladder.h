// lm_ladder.h - v2 multi-output resize kernel (one 4K input -> 5 outputs per frame)
#pragma once
#include "ap_int.h"
#include "hls_stream.h"
#include "common/xf_common.hpp"
#include "common/xf_utility.hpp"
#include "imgproc/xf_resize.hpp"
#include "lm_config.hpp"

typedef ap_uint<LM_WORD_BITS> lm_word_t;
typedef ap_uint<LM_PACK_BITS> lm_pack_t;

#define LM_DEPTH_IN  2048
#define LM_DEPTH_OUT 2048
#define LM_WORD_FIFO 512

#ifndef LS_RESIZE_HAS_URAM_ARG
#define LS_RESIZE_HAS_URAM_ARG 0
#endif

typedef xf::cv::Mat<XF_8UC3, LM_MAX_IN_H, LM_MAX_IN_W, XF_NPPC8, LM_DEPTH_IN> lm_in_mat_t;
typedef xf::cv::Mat<XF_8UC3, LM_H0, LM_W0, XF_NPPC8, LM_DEPTH_OUT> lm_out0_t;
typedef xf::cv::Mat<XF_8UC3, LM_H1, LM_W1, XF_NPPC8, LM_DEPTH_OUT> lm_out1_t;
typedef xf::cv::Mat<XF_8UC3, LM_H2, LM_W2, XF_NPPC8, LM_DEPTH_OUT> lm_out2_t;
typedef xf::cv::Mat<XF_8UC3, LM_H3, LM_W3, XF_NPPC8, LM_DEPTH_OUT> lm_out3_t;
typedef xf::cv::Mat<XF_8UC3, LM_H4, LM_W4, XF_NPPC8, LM_DEPTH_OUT> lm_out4_t;

extern "C" {
// src : nframes x ceil(in_h*in_w*3/64) words (frame f starts at word f*in_words)
// o0..o4 : the SAME output buffer passed 5 times (5 AXI masters on one HBM bank). Frame f,
//          rung k is written at word f*lm::out_words_per_frame() + lm::rung_offset(k).
// Invalid sizes or nframes outside 1..LM_MAX_BATCH: the kernel returns without writing.
void lm_ladder(const lm_word_t* src, lm_word_t* o0, lm_word_t* o1, lm_word_t* o2, lm_word_t* o3, lm_word_t* o4,
               int in_h, int in_w, int nframes);
}
