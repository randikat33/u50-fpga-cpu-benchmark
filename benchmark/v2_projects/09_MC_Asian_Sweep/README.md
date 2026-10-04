# 09_MC_Asian_Sweep: MC Asian kernel with a run-time number of active lanes (review round 7)

The `08_MC_Asian` kernel, rebuilt with **16 lanes**. The host picks how many of them get work (`--active-lanes A`, kernel `flags` bits 8..14). The card's rate then grows in steps of one lane while the CPU's rate stays fixed, so the time and energy verdicts cross over inside the sweep.

This is the near-break-even test of the time-energy model:

- The prediction (`round7/predict_round7.py`) is made from the routed design and CPU-only runs.
- It is hashed and e-mailed **before** this design ever runs on the card.

## What changed from 08_MC_Asian

- `kernel/mc_asian.cpp`, `split()`:
  - Only the first A lanes receive pairs. A = 0 or A > L means all lanes.
  - The other lanes get n = 0 and finish at once.
  - Results do not depend on A: every pair is seeded from its global index and the moment sums are exact integers.
- `kernel/mc_asian.h`: AS_LANES default 16.
- `host/asian_fpga.cpp`:
  - New option `--active-lanes A`.
  - Reports `active_lanes` and `impl=fpga_l16_a<A>_cu1`.
  - The verify call uses the same flags.
- `Makefile` (LANES ?= 16) and the tests:
  - `tb_kernel` checks A = 1, 2, 3, 7, L, 0 and L+5 against the reference.
  - The `xrt_sim` host checks A = 1, 3, 7 and L against the CPU hash.

Everything else (numerics, CPU baseline, xclbin name `build/mc_asian.xclbin`) is unchanged.

## Model inputs

- R_k(A) = CUs × A × 2 × f (G path-steps/s).
- Duty = A / L.
- P_card = P0 + (A/L)(P_viv − P0).
- Host class: polling.

## Build and test

```bash
make sim_test                                   # sandbox: C-sim (16, 1, 5 lanes + active lanes), CPU, xrt_sim host
make cpu host                                   # on the server
make xclbin LANES=16 FREQ_HZ=300000000 STRATEGY=Congestion_SpreadLogic_high LINK_CFG=$PWD/cfg/link_slr1.cfg
```

`round7_sweep.sh build` runs the last line and retries at 250 MHz if 300 MHz misses timing.
