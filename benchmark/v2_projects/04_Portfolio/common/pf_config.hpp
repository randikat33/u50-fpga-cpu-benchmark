// pf_config.hpp - build-time configuration shared by kernel, host, CPU and tests.
// All values can be overridden with -D on the command line (Makefile variables).
#pragma once

// Lanes (independent Heston engines) per compute unit.  Resource per lane: see README 3.4.
#ifndef PF_LANES
#define PF_LANES 8
#endif

// Interleave depth (slots per lane) = paths per lane block. Power of two.
// MUST be larger than the pipeline depth of the lane loop between the state-RAM
// read and write (csynth report of pf_lane_loop, see README 8). 128 >> ~70 expected.
#ifndef PF_LOG_IL
#define PF_LOG_IL 7
#endif
#define PF_IL (1u << PF_LOG_IL)
// literal copy for HLS pragmas (pragma arguments must be literals)
#ifndef PF_IL_PRAGMA
#define PF_IL_PRAGMA 128
#endif
#ifdef __cplusplus
static_assert(PF_IL == PF_IL_PRAGMA, "PF_IL_PRAGMA must equal 1 << PF_LOG_IL");
#endif

// Number of systematic factors of the v1 market model (Market::factor_w columns).
#define PF_NF 4

#define PF_MAX_UNDER 1024          // v1 MAX_UNDERLYINGS (Market layout)
#define PF_MAX_STEPS 1024          // time steps per path (kernel counter widths)
#define PF_MARKET_WORDS 7168       // 1024 * (3 + 4) floats = sizeof(Market)/4
#define PF_PARAM_WORDS 4           // dspot, drate, volscale, reserved (appended after market)
#define PF_TRADE_WORDS 11          // sizeof(Trade) / 4 (v1 layout, 44 bytes)
#define PF_MOM_WORDS 4             // per-trade output record (uint64 words)

// Payoff quantisation: payoffs are accumulated exactly as integers with 2^-16 resolution
// (rounded from a 2^-17 truncation); payoffs are saturated at PF_PO_CAP.
#define PF_PO_CAP 16383.998046875f // 2^14 - 2^-9 (exactly representable)

// Keys for the hash of (seed, stage, trade id)
#define PF_GOLDEN64 0x9E3779B97F4A7C15ull
