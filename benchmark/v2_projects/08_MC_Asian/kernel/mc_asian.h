// mc_asian.h - top-level interface of the Monte Carlo Asian-option kernel (GBM, arithmetic average).
#pragma once
#include <cstdint>

#ifndef AS_LANES
#define AS_LANES 32         // interleaved lanes per CU (Makefile LANES=...)
#endif
#define AS_IL_LOG2 7
#define AS_IL (1 << AS_IL_LOG2)   // path-pair slots per lane (must exceed the step latency, ~50 cycles)
#define AS_MAX_LANES 64

// Arguments (XRT index) - same order and types as mc_heston_v2, so the host code is shared:
//  0 fpar      float[16]   m_axi gmem0 : asn::make_fpar() block
//  1 out       uint64[8]   m_axi gmem1 : exact moment sums (mc::OutIdx)
//  2 pair_base uint64      first global antithetic-pair index of this call
//  3 n_pairs   uint64      pairs in this call (pair_base + n_pairs <= 2^32)
//  4 steps     uint32      time steps M (1..65535)
//  5 flags     uint32      bit0: put
//  6 key_lo    uint64      RNG keys k0 | k1<<32  (mc::run_keys(seed))
//  7 key_hi    uint64      RNG keys k2 | k3<<32
void mc_asian(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs,
              uint32_t steps, uint32_t flags, uint64_t key_lo, uint64_t key_hi);
