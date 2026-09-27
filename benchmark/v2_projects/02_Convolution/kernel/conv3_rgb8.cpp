// conv3_rgb8.cpp - kernel top conv3_rgb8 (B=8 bits, L=64 samples/word, C=3 channels).
// All logic lives in conv3_core.hpp / conv3_top.inc (one templated source for all variants).
#include <cstdio>
#include <cstdlib>
#include "conv3_core.hpp"
#define CONV3_TOP conv3_rgb8
#define CONV3_B 8
#define CONV3_L 64
#define CONV3_C 3
#define CONV3_SH 0
#define CONV3_MAXS CONV_MAX_STRIDE_RGB8
#include "conv3_top.inc"
