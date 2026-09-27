// conv3_rgb16.cpp - kernel top conv3_rgb16 (B=16 bits, L=32 samples/word, C=3 channels).
// All logic lives in conv3_core.hpp / conv3_top.inc (one templated source for all variants).
#include <cstdio>
#include <cstdlib>
#include "conv3_core.hpp"
#define CONV3_TOP conv3_rgb16
#define CONV3_B 16
#define CONV3_L 32
#define CONV3_C 3
#define CONV3_SH 1
#define CONV3_MAXS CONV_MAX_STRIDE_RGB16
#include "conv3_top.inc"
