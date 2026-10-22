# Paper 2 protocol, addendum v0.7: co-execution

**Authors:** Randika T. P. G. Pathiranage, Dasuni Hewawasam (University of Moratuwa)
**Date:** 22 October 2026
**Registers:** before any co-execution run, on GitHub (this file, the prediction file and the code, with SHA-256) and as a new version of the Zenodo record "Paper 2: registrations".

This addendum fixes the co-execution part of protocol v0.6, Section 25 (RQ5, one option). It does not cover scheduling: the book of options, the two-machine scheduler and its hypotheses follow in addendum v0.8, after frodo's remaining input runs. Nothing registered earlier is changed.

## 1. Inputs measured before this addendum

Every number in the prediction (Section 5) comes from runs that were already finished. Their files and SHA-256 are in `predictions_coexec.json`.

| Input | Source |
|---|---|
| V100: steady-state rate, GPU power, socket-1 power (block wait) | GPU sessions 1–2 (`phase5g_summary_s1.csv`, `phase5g_summary_s2.csv`), median of the two |
| Supermicro CPU-native, 18 threads (socket 1 without core 10) | `smc_cpu_summary.csv` (input runs of 22 October 2026, set `s1_t18`) |
| U50 card: rate, card power, host-socket power (`--wait xrt`, registered xrt.ini) | frodo phase-6 session 1 (`phase6_summary_s1.csv`) |
| frodo CPU-native, 48 threads of socket 0 | frodo phase-6 session 1, set `s0_t48` |
| Idle powers: V100 35.8 W (locked 1245 MHz); card 21.5 W (median during the CPU jobs of phase-6 session 1); frodo socket 0 39.5 W | sweep log, phase-6 log, F1 diagnostic |
| Fixed costs per call: V100 0.142 ms (block, HG3); card 0.651 ms (Part A) | GPU analysis; Part A |

The Supermicro CPU runs were made for this purpose before registration, as the GPU microbenchmarks were before the GPU prediction. Their first attempt recorded wrong energies (Section 8, item 1); it was discarded and rerun in full with the fixed harness.

## 2. Devices and pinning

| Machine | Accelerator worker | CPU worker | Coordinator |
|---|---|---|---|
| frodo | U50 card: host program on CPU 0 (its sibling left idle), memory node 0, `--wait xrt`, registered xrt.ini | CPU-native code on socket 0's other 23 cores, 46 threads | CPU 48 (CPU 0's sibling) |
| Supermicro | V100: host program on CPU 10, memory node 1, block wait, clock locked at 1245 MHz | CPU-native code on socket 1's other 9 cores, 18 threads | CPU 30 (CPU 10's sibling) |

**Programs (serve mode).** Each worker loads its device once. It then prices one pair range per command (`R id a0 a1`) and replies with its three prices:

- **Card:** `host/hq_fpga.cpp` and `host/hs_fpga.cpp` gain `--serve`. Their other modes are unchanged.
- **V100:** `gpu/hs_coexec.cu` includes the registered `gpu/hs_gpu.cu` unchanged, with its `main` renamed, and adds the command loop. `build/hs_gpu` is not rebuilt.
- **CPU:** `cpu/native_serve.cpp` includes `hq_native.cpp` and `hs_native.cpp` unchanged and calls their `main` once per command.

**Check before the runs.** `scripts/coexec_check.py` compares the serve programs with the normal programs on four cases: the prices for the same range must be equal.

## 3. Workloads and policies

**Workloads.** The 12 kernels (six cases × two methods), each at the step of its 0.1% setting:

- **setting:** the registered 0.1% setting, with its pairs;
- **large:** pairs for about 4 s on the accelerator alone, capped at 2^31 pairs (2^29 for the basket).

**Policies** for one pricing run of N pairs:

| Policy | Rule |
|---|---|
| acc | all pairs on the accelerator |
| cpu | all pairs on the CPU; run only where the prediction gives ≤ 30 s |
| static_E | the CPU fraction f with the least predicted energy at the primary boundary, no deadline |
| static_T | the f with the least predicted makespan |
| static_M | the least predicted energy under a deadline halfway between static_E's and static_T's makespan |
| dynamic | K = min(64, N/B) equal blocks in pair order, with B ≥ 20 accelerator fixed costs of work; each worker takes the next block when it is free (self-scheduling) |

- **Static split:** pairs [0, N − round(fN)) go to the accelerator and the rest to the CPU, and both parts start together.
- **Repeated fractions:** a policy whose fraction equals another policy's is run once, under the first name in the order of the table.
- **Price of a run:** the pair-weighted mean of its parts' prices.

## 4. Measurement

- **Steady-clock protocol:** each job follows it, as before. The workers are started outside the window; then come ≥ 3 s of untimed pricing runs and a timed window of ≥ 10 s.
- **Per run:** the window's time and energy divided by the number of runs. The makespan of each run is also recorded.
- **Harness:**
  - Supermicro: `scripts/gpu_run.py` (RAPL of both sockets, NVML), as amended in Section 8.
  - frodo: `common/run_measured.py` (RAPL and card rails at 10 Hz).
- **Schedule:** one session per machine, 3 repeats, interleaved, seeds 20262100 + repeat.
- **Repeated jobs:** quietness rules and disturbance as before. On the Supermicro, a run whose median SM clock is more than 1% off 1245 MHz is also repeated.
- **Boundaries:**
  - **primary:** frodo card + socket 0; Supermicro GPU + socket 1;
  - **server:** both machines, adding the other socket.
- **Queue:** `scripts/coexec_queue.py --machine smc|frodo` (Supermicro 321 jobs, frodo 318 jobs, each about 2 h).

## 5. Prediction (`scripts/predict_coexec.py`)

For one pricing run with CPU fraction f and work W path-steps:

- t_a = (1 − f) W / r_a + t0_a + t_ov
- t_c = f W / r_c + t_ov (a part with no pairs takes no time)
- T = max(t_a, t_c)
- Device energy: P_a t_a + P_a,idle (T − t_a)
- Host-socket energy:
  - P_c,busy while the CPU part runs;
  - P_h while only the accelerator runs.

**Stated assumptions:**

1. **Coordinator overhead:** t_ov = 1 ms per part.
2. **frodo's CPU rate:** 46/48 of the 48-thread rate.
3. **frodo host overhead:** the card program's host-socket overhead (P_h − 39.5 W, mostly the card telemetry) adds to the CPU's busy socket power.
4. **Idle accelerator:** an idle accelerator draws its idle power.
5. **Dynamic:** simulated by self-scheduling with the same rates and per-block costs.

**Prediction summary:** large workloads, primary boundary, ratios against acc. The full table, the setting workloads and the server boundary are in the file.

| | static_E | static_T makespan | static_T energy | dynamic / static_T makespan |
|---|---|---|---|---|
| Supermicro, all 12 kernels | f = 0 (GPU alone) | 0.979–0.986 | 1.165–1.199 | 1.14–1.96 |
| frodo, FT kernels, basket FT, Asian | f = 0.68–0.73 | 0.27–0.32 | 0.74–0.86 | 1.03–1.07 |
| frodo, QE of Cases I–III | f = 0 (card alone) | 0.48–0.49 | 1.20–1.27 | 1.03 |
| frodo, QE of P1 and the basket | f = 0.60–0.61 | 0.39–0.40 | 0.96–1.00 | 1.03–1.05 |

The file is `predictions_coexec.json`, SHA-256 `8badae207be465a1e9200ba8119da3f87b94b216c77400795435f97a8f6cdc1e`.

## 6. Hypotheses (confirmatory, large workloads, primary boundary)

- **HC1 (the model).** For the static policies with 0 < f < 1, on both machines and for every kernel, the measured makespan and energy per run are within 15% of the prediction. Predicted: holds.
- **HC2 (V100 + CPU).**
  - The energy-optimal split leaves the Supermicro's CPU idle (f_E = 0) for every kernel.
  - The time-optimal split raises the GPU+socket energy per run: measured static_T > measured acc, for every kernel.
  - Predicted: holds; time saved 1.4–2.1%, energy +17–20%.
- **HC3 (U50 + CPU).** On frodo:
  - **Makespan:** static_T shortens it against the card alone for every kernel.
  - **Energy per run:** lower for I, II, III and P1 FT, basket FT, Asian plain and Asian CV (predicted ratio ≤ 0.95), and higher for QE of Cases I–III (≥ 1.05).
  - **No call:** P1 QE and basket QE are predicted within ±5% and are reported only.
  - Predicted: holds.
- **HC4 (dynamic self-scheduling).**
  - The measured dynamic makespan is within 10% of the measured static_T on frodo for every kernel.
  - It is more than 10% above it on the Supermicro for every kernel. There, a CPU block takes longer than the GPU's whole share, so the run waits for the CPU's last block.
  - Predicted: holds.

**Descriptive:** the setting workloads; the server boundary; static_E and static_M where they are not tested; the accuracy of the combined estimator (mean error against the reference over the repeats); the CPU busy fraction.

## 7. Analysis

- **Values:** medians over the 3 repeats. A deduplicated policy (Section 3) is compared under each name it stands for.
- **Ties:** a ratio within the repeats' range of 1 is reported as a tie.
- **Each test once:** HC1–HC4 are tested once, as registered, and reported whatever the outcome.

## 8. Changes and deviations since v0.6.1

1. **`scripts/gpu_run.py` timed the CPU programs wrongly.**
   - **The bug:** the CPU programs print their MARK times from a monotonic clock (seconds since boot). The harness interpolated the epoch-time samples at those times, so it extrapolated from the first sample interval. The energy of a CPU program run was therefore the power in the first 50 ms times the window, and the other-users check was wrong too.
   - **What it affected:** only the first attempt of the Supermicro CPU input runs, which was discarded.
   - **What it did not affect:** GPU runs, whose program prints wall-clock time, and all frodo runs, whose harness times the marks when they arrive.
   - **The fix:** the harness now keeps the arrival time of each MARK line and uses it when the printed time is more than 2 s away from it (`mark_clock` in each `meta.json`). It also now excludes all descendants of the measured process from "other users", which co-execution needs because its workers are grandchildren.
2. **Serve modes added to the card hosts (Section 2).** Their normal modes are unchanged.
3. **Scheduling moved to addendum v0.8.**
4. **Steady-state GPU repeat on 22 October 2026, a third day.** All 29 steady-state keys are within −0.6% and +1.4% of the median of the two sessions. This covers deviation 1 of v0.6.1.

## 9. Code registered with this addendum

`scripts/predict_coexec.py`, `scripts/coexec_run.py`, `scripts/coexec_queue.py`, `scripts/coexec_check.py`, `scripts/gpu_run.py`, `gpu/hs_coexec.cu`, `gpu/Makefile`, `cpu/native_serve.cpp`, `host/hq_fpga.cpp`, `host/hs_fpga.cpp`, `Makefile`, and the prediction file. Their hashes are in `protocol_v0.7_code.sha256`.
