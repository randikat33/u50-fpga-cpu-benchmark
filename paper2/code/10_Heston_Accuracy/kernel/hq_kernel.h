// hq_kernel.h - top-level interface of the paper-2 Heston kernels (single asset, three strikes).
// One source, two kernels: HQ_SCHEME=0 -> hq_ft (Euler, full truncation), HQ_SCHEME=1 -> hq_qe (QE).
#pragma once
#include <cstdint>

#ifndef HQ_SCHEME
#define HQ_SCHEME 1
#endif
#ifndef HQ_LANES
#define HQ_LANES 16          // interleaved lanes per CU (Makefile LANES_FT / LANES_QE)
#endif
#ifndef HQ_IL_LOG2
#if HQ_SCHEME == 0
#define HQ_IL_LOG2 7         // FT: 128 slots per lane (datapath latency < 128 cycles)
#else
#define HQ_IL_LOG2 8         // QE: 256 slots per lane (four divides and three square roots in series)
#endif
#endif
#define HQ_IL (1 << HQ_IL_LOG2)
#define HQ_MAX_LANES 64

#if HQ_SCHEME == 0
#define HQ_TOP hq_ft
#else
#define HQ_TOP hq_qe
#endif

// Arguments (XRT index):
//  0 fpar      float[32]   m_axi gmem0 : hq::make_fpar() block
//  1 out       uint64[14]  m_axi gmem1 : exact moment sums per strike (hq::mom_to_words) + pairs + info
//  2 pair_base uint64      first global antithetic-pair index of this call
//  3 n_pairs   uint64      pairs in this call (pair_base + n_pairs <= 2^32)
//  4 steps     uint32      time steps M (1..65535)
//  5 key_lo    uint64      RNG keys k0 | k1<<32  (mc::run_keys(seed))
//  6 key_hi    uint64      RNG keys k2 | k3<<32
void HQ_TOP(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs, uint32_t steps,
            uint64_t key_lo, uint64_t key_hi);
