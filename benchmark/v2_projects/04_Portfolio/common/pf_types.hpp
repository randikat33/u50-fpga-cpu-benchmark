// pf_types.hpp - v1-compatible file formats (portfolio.bin, market.bin, results*.bin).
//   portfolio.bin : uint32 n_trades, then n_trades x Trade (44 bytes each, 11 x 4-byte words)
//   market.bin    : Market (28672 bytes = 7168 float32)
//   results*.bin  : uint32 n, then n x Result {float price, float std_err}
#pragma once
#include <cstdint>
#include "pf_config.hpp"

#ifndef MAX_UNDERLYINGS
#define MAX_UNDERLYINGS PF_MAX_UNDER
#endif
#ifndef MAX_FACTORS
#define MAX_FACTORS PF_NF
#endif

// option_type: 0=call, 1=put ; option_kind: 0=European, 1=Asian arithmetic average
struct Trade {
    uint32_t underlying_id;   // word 0
    float K;                  // word 1
    float T;                  // word 2
    uint8_t option_type;      // word 3 (byte 0)
    uint8_t option_kind;      // word 3 (byte 1)
    uint16_t _pad0;           // word 3 (bytes 2-3)
    float notional;           // word 4
    float position;           // word 5
    float v0;                 // word 6
    float kappa;              // word 7
    float theta;              // word 8
    float sigma;              // word 9
    float rho;                // word 10
};

struct Market {
    float spot[MAX_UNDERLYINGS];
    float rate[MAX_UNDERLYINGS];
    float divq[MAX_UNDERLYINGS];
    float factor_w[MAX_UNDERLYINGS][MAX_FACTORS];
};

struct Result {
    float price;
    float std_err;
};

// Per-trade raw Monte Carlo moments (kernel output, 4 x uint64 = 32 bytes).
//   sum  = sum_p q_p        with q_p = payoff_p quantised to 2^-16
//   sum2 = sum_p q_p^2      (96-bit, split in lo/hi words)
//   tag  = trade id (low 32 bits) | 1<<63 (valid marker)
struct Moments {
    uint64_t sum;
    uint64_t s2lo;
    uint64_t s2hi;
    uint64_t tag;
};

static_assert(sizeof(Trade) == 44, "Trade layout must match v1 (44 bytes)");
static_assert(sizeof(Market) == 28672, "Market layout must match v1");
static_assert(sizeof(Result) == 8, "Result layout");
static_assert(sizeof(Moments) == 32, "Moments layout");
