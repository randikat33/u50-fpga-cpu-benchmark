// ha_kernel.h - top-level interface of the Case A Asian kernel (paper 2, protocol v0.5).
#pragma once
#include <cstdint>

#ifndef HA_LANES
#define HA_LANES 16          // interleaved lanes per CU (Makefile LANES)
#endif
#ifndef HA_IL_LOG2
#define HA_IL_LOG2 7         // 128 slots per lane (datapath latency < 128 cycles)
#endif
#define HA_IL (1 << HA_IL_LOG2)
#define HA_MAX_LANES 64
#define HA_TOP ha_asian

// Arguments (XRT index):
//  0 fpar      float[16]   m_axi gmem0 : ha::make_fpar() block
//  1 out       uint64[32]  m_axi gmem1 : per strike sums of ys, ys^2, gs, gs^2, ys gs (ha::mom_to_words), pairs, info
//  2 pair_base uint64      first global antithetic-pair index of this call
//  3 n_pairs   uint64      pairs in this call (pair_base + n_pairs <= 2^32)
//  4 steps     uint32      monitoring dates M (1..65535)
//  5 key_lo    uint64      RNG keys k0 | k1<<32
//  6 key_hi    uint64      RNG keys k2 | k3<<32
void HA_TOP(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs, uint32_t steps,
            uint64_t key_lo, uint64_t key_hi);
