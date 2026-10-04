// fir_v2.h - int16 FIR filter HLS kernel (Alveo U50), FIR_L = 16 samples per clock cycle.
#pragma once
#include <ap_int.h>
#include <hls_stream.h>
#include "fir_params.h"     // FIR_TAPS, FIR_L, FIR_HW

typedef ap_uint<256> fir_word_t;   // 16 int16 samples; sample j = bits 16j+15:16j (little endian)
typedef ap_uint<32> fir_cnt_t;

#define FIR_CWORDS ((FIR_TAPS + FIR_L - 1) / FIR_L)   // coefficient words
#define FIR_FIFO_DEPTH 64

// src : FIR_HW history words followed by n_words data words (history = the FIR_HW*16 samples
//       before the chunk; zeros at the start of a stream)
// dst : n_words output words (output sample i belongs to input sample i of the data part)
// coef: FIR_CWORDS words, c[k] = sample k (k < FIR_TAPS)
void fir_v2(const fir_word_t* src, fir_word_t* dst, const fir_word_t* coef, fir_cnt_t n_words);
