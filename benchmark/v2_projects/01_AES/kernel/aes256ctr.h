// aes256ctr.h - AES-256-CTR HLS kernel (one kernel for encrypt and decrypt).
#pragma once
#include <ap_int.h>
#include <hls_stream.h>

typedef ap_uint<512> aes_word_t;   // 4 AES blocks; block b = bytes 16b..16b+15, byte j = bits 8j+7:8j
typedef ap_uint<32>  aes_cnt_t;    // word count (<= 2^24 for a 1 GiB BO)

// Number of AES cores per word. Fixed by the 512-bit word (4 x 128); documented, not tunable.
#define AES_LANES 4
// Pipeline depth of the keystream generator in cycles (counter pair + ARK0 + 14 x 2):
// 1 + 1 + 28 = 30. The C testbench checks that exactly n_words words come out.
#define AES_KS_DEPTH 30
// Parameter buffer: 4 words (15 round keys + IV), see common/aes_ref.hpp::pack_params
#define AES_PARAM_WORDS 4
// FIFO depths between dataflow processes (>= AES_KS_DEPTH + 2 so the reader never
// stalls in steady state)
#define AES_DATA_FIFO_DEPTH 64
#define AES_KS_FIFO_DEPTH   64

void aes256ctr(const aes_word_t* src, aes_word_t* dst, const aes_word_t* prm,
               ap_uint<64> blk_off, aes_cnt_t n_words);

// Exposed for the unit test only: the bit-sliced S-box used by the datapath.
unsigned char aes_sbox_hw_test(unsigned char x);
