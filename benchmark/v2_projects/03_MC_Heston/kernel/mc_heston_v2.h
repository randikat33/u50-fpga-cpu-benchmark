// mc_heston_v2.h - top-level interface of the v2 Monte Carlo Heston basket kernel.
#pragma once
#include <cstdint>

#ifndef MC_LANES
#define MC_LANES 6          // interleaved lanes per CU (Makefile LANES=...), see README resource table
#endif
#define MC_IL_LOG2 7
#define MC_IL (1 << MC_IL_LOG2)   // path-pair slots per lane (must exceed the update latency, ~60)
#define MC_MAX_LANES 16

// Arguments (XRT index):
//  0 fpar      float[96]   m_axi gmem0 : mc::make_fpar() block
//  1 out       uint64[8]   m_axi gmem1 : exact moment sums (mc::OutIdx)
//  2 pair_base uint64      first global antithetic-pair index of this call
//  3 n_pairs   uint64      pairs in this call (pair_base + n_pairs <= 2^32)
//  4 steps     uint32      time steps M (1..65535)
//  5 flags     uint32      bit0: put
//  6 key_lo    uint64      RNG keys k0 | k1<<32  (mc::run_keys(seed))
//  7 key_hi    uint64      RNG keys k2 | k3<<32
void mc_heston_v2(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs,
                  uint32_t steps, uint32_t flags, uint64_t key_lo, uint64_t key_hi);
