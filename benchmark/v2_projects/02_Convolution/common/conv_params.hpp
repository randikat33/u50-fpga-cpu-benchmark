// conv_params.hpp - geometry constants shared by kernel, host, CPU baselines and tests.
// Kernel-safe: plain macros only (no std containers, no dynamic memory).
//
// Data layout ("raw row stream", identical on host and kernel):
//   * one image row = W*C samples, interleaved exactly as decoded (B,G,R for colour,
//     OpenCV order), samples are 1 byte (8-bit) or 2 bytes little-endian (16-bit);
//   * each row is padded with don't-care bytes to a multiple of 64 bytes, so a row is
//     STRIDE = ceil(W*C*bps/64) 512-bit words; word k of a row holds samples k*L..k*L+L-1
//     where L = 64/bps samples per word (64 for 8-bit, 32 for 16-bit);
//   * outputs use the same layout; padding samples of outputs are 0.
// The host therefore never packs pixels: the PNG decoder writes rows straight into the
// input buffer-object memory.
#pragma once

#define CONV_WORD_BITS 512
#define CONV_WORD_BYTES 64
#define CONV_MAX_WIDTH 8192                 // max pixels per row supported by the bitstream
#define CONV_MAX_ROWS (1 << 24)             // sanity limit on rows per kernel call
// line-buffer depth (words per row at CONV_MAX_WIDTH)
#define CONV_MAX_STRIDE_RGB16 768           // ceil(8192*3*2/64)
#define CONV_MAX_STRIDE_RGB8 384            // ceil(8192*3*1/64)
#define CONV_MAX_STRIDE_GRAY8 128           // ceil(8192*1*1/64)
// Minimum loop iterations per row (before the +1 flush iteration). Guarantees that two
// accesses to the same line-buffer address are >= CONV_MIN_ITERS iterations apart, which
// makes "DEPENDENCE inter false" safe for RAM read->write stage offsets < CONV_MIN_ITERS.
#define CONV_MIN_ITERS 4
