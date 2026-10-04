# 08_MC_Asian: Monte Carlo arithmetic-average Asian option (out-of-sample compute workload)

Written for review round 6. Its time and energy on the card were **predicted** with the paper's model before it ever ran on the card (`round6_new_kernels.sh`).

## Model

- One asset under GBM, exact log-step, arithmetic average over M monitoring dates, call or put, antithetic pairs.
- Reuses the building blocks of `03_MC_Heston/common` (included, not copied):
  - xoshiro128** streams per pair;
  - the segmented-quadratic ICDF;
  - the table exp(X/2);
  - exact 128-bit integer moments.
- Kernel, AVX-512 and scalar versions are bit-identical (`common/asian_model.hpp`).
- An independent double-precision Monte Carlo agrees within 3 SE. It is itself checked against the closed form of the geometric-average option.

## FPGA kernel (`kernel/mc_asian.cpp`)

- The Heston kernel's architecture: L interleaved lanes (default **32**), each advancing one (pair, step) per cycle with IL = 128 slots.
- Per lane and cycle: 1 xoshiro draw, 1 ICDF, 2 exp and 2 running sums.
- A merge stage passes one pair per cycle to the exact-moment settle stage, so full speed needs M ≥ L (the benchmark uses M = 128).
- Same argument list as `mc_heston_v2`.

## Host and CPU baseline

- `host/asian_fpga.cpp` is the MC Heston host (polling class).
- `cpu/asian_cpu.cpp`: AVX-512, 16 pairs per zmm, 1/2/4 interleaved blocks (`--interleave`, chosen on the server by `cputune`).
- The exp power of two uses `vscalefps`; the test checks that it equals the table for more than 10^6 inputs.

## Model inputs

- R_k = CUs × lanes × 2 × f (G path-steps/s).
- No per-unit PCIe traffic.
- Host class: polling.

## Build and test

```bash
make sim_test               # sandbox: C-sim (32, 1, 5 lanes), CPU, xrt_sim host, double reference (23 checks)
make cpu host LANES=32      # on the server
make xclbin fpga_reports LANES=32
```

The build must report the `RING` loop depth below 128 (IL). `round6/design_facts.py` checks this from the HLS log.
