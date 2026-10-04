# Code added after the main campaign (rounds 5 to 7)

Unzip this archive into the same folder as `01_benchmark_code.zip`. It adds files and replaces three: `v2_projects/01_AES/host/aes_fpga.cpp` (new option, default behaviour unchanged), `v2_projects/01_AES/test/run_sim_tests.sh` (three more tests) and `v2_projects/03_MC_Heston/Makefile` (new `native` target).

The server's host name is replaced by "the server" in comments and guides. The sandbox path of the HLS header shims is replaced by `/home/USER/shim`. Nothing else differs from the code that ran.

## Round 5: replication, wall power, host overlap, MC schedules

- `round5_measurements.sh`: three replication sessions of the headline rows, wall-power runs (IPMI DCMI), AES with 1 to 4 streams per compute unit, convolution with 2 to 12 strips, MC with static and dynamic OpenMP schedules, video pipelines with `perf stat`.
- `v2_projects/01_AES/host/aes_fpga.cpp`: new option `--streams S`. The default (`--streams 1`) is the paper's host.
- `docs/ROUND5_RUN_GUIDE.md`.

## Round 6: out-of-sample test and CPU-native MC baseline

- `v2_projects/07_FIR/`: int16 FIR filter, 32 taps, 2 compute units, with an AVX-512 VNNI/BW CPU baseline.
- `v2_projects/08_MC_Asian/`: Monte Carlo arithmetic-average Asian option (GBM), with a bit-identical AVX-512 CPU baseline.
- `round6/predict_round6.py`, `design_facts.py`, `model_constants.json`: the prediction, made from the routed designs and CPU-only runs.
- `round6_new_kernels.sh`: build, design facts, CPU-only runs, prediction with SHA-256, then card runs. The measurement step refuses to run if the prediction file does not match its hash.
- `v2_projects/03_MC_Heston/cpu/mc_native.cpp`, `test/native_check.sh`: CPU-native MC Heston baseline (Box–Muller, explicit AVX-512, FMA). Not bit-identical to the card; prices agree within 3 standard errors.
- `docs/ROUND6_RUN_GUIDE.md`.

## Round 7: break-even sweep and CPU-native portfolio baseline

- `v2_projects/09_MC_Asian_Sweep/`: the MC Asian kernel with 16 lanes. The host option `--active-lanes A` gives work to the first A lanes only (kernel flag bits 8 to 14). The result is bit-identical for every A.
- `round7/predict_round7.py`, `design_facts7.py`, `model_constants.json`: predictions for all 16 lane counts and both boundaries, the predicted break-even points, the scoring rules and the three hypotheses.
- `round7_sweep.sh`: check, CPU-native portfolio runs, build, design facts, CPU-only runs, prediction with SHA-256, sweep on the card.
- `v2_projects/04_Portfolio/cpu/pf_native.cpp`, `scripts/pf_native_check.py`: CPU-native portfolio engine and its price check against the algorithm-matched engine.
- `docs/ROUND7_RUN_GUIDE.md`.

## Unattended runners

- `r56_queue.sh` with `r56_guard.py` (rounds 5 and 6) and `r7_queue.sh` with `r7_guard.py` (round 7).
- They run the steps in order on a shared server:
  - a measurement is stopped and repeated when other users become active;
  - the hardware build is only paused;
  - finished runs are never repeated;
  - the card runs of a predicted design start only after the operator releases them.
- `docs/SHARED_SERVER_AUTORUN.md`.

## Tests that need no FPGA

Each of `07_FIR`, `08_MC_Asian` and `09_MC_Asian_Sweep` has `make sim_test`: kernel C simulation against the scalar reference, CPU baselines, and the host program against a simulated XRT. They need the Vitis HLS headers; set `SHIM` in the Makefile to their location.
