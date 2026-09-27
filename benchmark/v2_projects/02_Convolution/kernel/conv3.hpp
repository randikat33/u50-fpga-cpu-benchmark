// conv3.hpp - prototypes of the three kernel tops (for testbenches / xrt_sim registration).
#pragma once
#include "conv3_core.hpp"
void conv3_rgb16(const conv3::word_t*, conv3::word_t*, conv3::word_t*, conv3::word_t*, unsigned, unsigned, unsigned, unsigned);
void conv3_rgb8(const conv3::word_t*, conv3::word_t*, conv3::word_t*, conv3::word_t*, unsigned, unsigned, unsigned, unsigned);
void conv3_gray8(const conv3::word_t*, conv3::word_t*, conv3::word_t*, conv3::word_t*, unsigned, unsigned, unsigned, unsigned);
