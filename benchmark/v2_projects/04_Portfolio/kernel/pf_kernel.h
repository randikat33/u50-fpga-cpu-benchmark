// pf_kernel.h - v2 portfolio (two-stage Heston MC) HLS kernel interface.
#pragma once
#include <cstdint>
#include "pf_config.hpp"

// Argument indices (host and xrt_sim registration use the same order)
enum PfArg {
    PF_A_TRADES_F = 0,  // const float*     trade records viewed as float words   (gmem0)
    PF_A_TRADES_I,      // const uint32_t*  the SAME buffer viewed as uint32 words (gmem1, same bank)
    PF_A_IDS,           // const uint32_t*  global trade ids (stage 2)              (gmem2)
    PF_A_MARKET,        // const float*     Market blob + 4 parameter words         (gmem3)
    PF_A_OUT,           // uint64_t*        4 words per trade (Moments)             (gmem4)
    PF_A_N_TRADES,      // number of trade records in trades_*
    PF_A_N_IDS,         // number of ids to read (must be n_trades when use_ids=1, else 0)
    PF_A_ID_BASE,       // id of record 0 when use_ids=0
    PF_A_USE_IDS,
    PF_A_N_UNDER,       // underlying ids >= n_under map to 0 (v1)
    PF_A_N_PATHS,
    PF_A_N_STEPS,
    PF_A_CHUNK,         // paths per lane job (multiple of PF_IL; <= PF_LANES jobs per trade)
    PF_A_SEED,          // uint64
    PF_A_STAGE,
    PF_A_FLAGS,         // bit0: 1 = full truncation, 0 = reflection (v1)
    PF_NARGS
};

void pf_kernel(const float* trades_f, const uint32_t* trades_i, const uint32_t* ids,
               const float* market, uint64_t* out,
               uint32_t n_trades, uint32_t n_ids, uint32_t id_base, uint32_t use_ids,
               uint32_t n_under, uint32_t n_paths, uint32_t n_steps, uint32_t chunk_paths,
               uint64_t seed, uint32_t stage, uint32_t flags);
