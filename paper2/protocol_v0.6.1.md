# Paper 2 protocol, addendum v0.6.1: the frodo runs of Part B

**Authors:** Randika T. P. G. Pathiranage, Dasuni Hewawasam (University of Moratuwa)
**Date:** 21 October 2026
**Registers:** before any session run of Section 23 (items 1–4) and before any run of items 6–7, on GitHub (this file with its SHA-256, and the code it names) and as a new version of the Zenodo record "Paper 2: registrations" (Section 28).
**Changes nothing registered earlier.** Part A's settings, hypotheses, predictions and results are unchanged. So are v0.6's hypotheses, including HF1 and HF2, and the GPU prediction (commit ccc1471, Zenodo 10.5281/zenodo.23495010).

This addendum fixes what v0.6 Section 23 left open:

- the exact host settings of the non-polling card wait (HF1);
- the session queue and its analysis rules;
- the accuracy-only runs (HF2, CPU-native bias, Section 24).

It also records a diagnostic run and the deviations so far.

## 1. The card's host wait: what the diagnostic showed (20 October 2026)

The card hosts (`host/hq_fpga.cpp`, `host/hs_fpga.cpp`) were rebuilt with a wait option, and `scripts/card_wait_diag.py` was run on frodo. This was a diagnostic: no timed result of it is an outcome.

**Software.**

- XRT 2.16.204 (branch 2023.2).
- Driver KDS state (sysfs): `ert_disable 1`, `kds_interrupt ert`, CU-to-host interrupt capability 0, `kds_interval 0`.

**Correctness.** In both wait modes, the card's moments equal the matched CPU program's (mom_hash) for Cases I FT, P1 QE, B FT and A CV.

**Where the host's time and power go** (20 s windows; host pinned to socket 0, cores 0–23; the registered xrt.ini):

| Window | Run time | Host process (cores) | Driver CU threads (cores) | Socket 0 (W) | Socket 1 (W) | Busy cores, socket 0 / 1 |
|---|---|---|---|---|---|---|
| Idle (no program) | – | – | – | 39.5 | 32.0 | 0.05 / 0.05 |
| I FT 1280 steps, `--wait xrt` | 795 ms | 0.00 | < 0.01 | 50.2 | 43.2 | 0.11 / 0.06 |
| I FT 1280 steps, `--wait callback` | 795 ms | 0.00 | < 0.01 | 49.9 | 43.3 | 0.13 / 0.03 |
| P1 QE 1 step, `--wait xrt` | 0.52 ms | 0.24 | 0.29 + 0.30 | 51.3 | 43.7 | 0.89 / 0.25 |
| P1 QE 1 step, `--wait callback` | 0.70 ms | 0.33 | 0.21 + 0.22 | 51.2 | 43.4 | 0.91 / 0.03 |

In every card window, two kernel mailbox workers used about 0.06 cores each. They serve the 20 ms card-rail telemetry.

**What this means.**

1. **No busy loop in the host.** In the registered host, `xrt::run::wait()` sleeps in the kernel (`poll()` on the device file). The host process uses no measurable CPU while a long kernel runs. Only the xocl driver's per-CU threads poll the CU after each start, and that matters only for runs of about 1 ms or less.
2. **The basis stated for HF1 was wrong.** v0.6 said that the "busy-polling host draws about 49 W". Socket 0 does draw about 50 W during card runs, but the idle socket 1 rises by the same 11 W (32 → 43 W) with no busy cores. So the rise is not host computation. Its likely causes are package idle-state residency and the card's telemetry and PCIe traffic; this is not tested here.
3. **HF1 is expected to fail.** The callback wait does not change socket power in the diagnostic. HF1 is kept exactly as registered (v0.6, Section 26) and is tested and reported whatever the outcome. The paper states the wrong basis.
4. **Exploratory telemetry check.** Because the telemetry could explain part of the host power, an exploratory check (Section 3) measures it.

## 2. HF1: the registered non-polling mode

**`--wait callback`** (`host/wait_mode.hpp`):

- A completion callback is registered once per run object with `xrt::run::add_callback(ERT_CMD_STATE_COMPLETED, …)`.
- The host thread does not call into XRT to wait. It sleeps on a condition variable (a futex) until the callbacks of all CUs of the pricing run have arrived.

**Everything else is as in Part A:**

- the same xrt.ini (`common/xrt_timing.ini`: card-rail telemetry every 20 ms);
- the same pinning (socket 0 cores, memory node 0);
- the same xclbins;
- the same harness (`common/run_measured.py`).

The reference is **`--wait xrt`**, the unchanged Part A host. It is measured in the same sessions, interleaved.

**HF1 test.**

- For each of the 36 settings (six cases × three targets × two methods), take the card's steady-state energy per option at the socket boundary: the setting's work times the energy per path-step of its steady-state run (the setting itself where it is not short), as in Part A. Each value is the median of the two session medians.
- **HF1 holds if** the ratio of `callback` to `xrt` is at most 0.90 at every setting.
- Also reported, as descriptions: the ratio against Part A's registered card results, and the host CPU fraction (`host_cpu_frac`).

## 3. The frodo sessions (`scripts/phase6.py`)

**Schedule.**

- Two sessions on different days, 5 repeats each, interleaved.
- Seeds 20261600 + 100·session + repeat.
- Every setting ("set") and the steady state of each short setting ("ss": < 10^10 path-steps; pairs as in phase 5).
- Main cases (I, II, III, P1) and secondary cases (B, A).
- Harness, steady-clock protocol (≥ 3 s warm-up, ≥ 10 s window) and quietness guard as in phase 5.

**Jobs of one repeat (316).**

| Platform | Configuration | Jobs |
|---|---|---|
| Card, `--wait xrt` | host on socket 0 | 36 set + 27 ss |
| Card, `--wait callback` | host on socket 0 | 36 set + 27 ss |
| Matched AVX-512 and CPU-native | **s0_t48**: 48 threads, both hardware threads of socket 0's 24 cores | 2 × (36 set + 27 ss) |
| Matched AVX-512 and CPU-native | s0_c24: 24 cores, steady state of every case, method and step | 2 × 30 ss |
| Card, `--wait xrt`, exploratory | xrt.ini variants: telemetry every 200 ms; telemetry off | 2 × 2 ss (I FT 1280 steps; P1 QE 1 step) |

- The AVX-512 interleave is the one tuned in Part A on 24 cores.
- The card's moments must equal the matched code's at 48 threads (mom_hash) for the same seed and pairs.

**Beside each session (as root):**

- `scripts/wall_logger.py`: IPMI DCMI wall power at 1 Hz.
- `scripts/clock_logger.py`: measured clocks of both sockets every 10 s, from the APERF, MPERF and TSC registers. This is turbostat's method, read directly so that no extra package is needed.

**SMT rule (v0.6, Section 23 item 1).**

- For each case, method and step, the 48-thread socket result replaces the 24-thread one if its steady-state energy per path-step at the socket boundary is lower in session 1 of this queue (s0_t48 against s0_c24, same session).
- The 24-thread values reported stay Part A's.
- HG1 and HG4 are re-evaluated with this better-of CPU socket result. That evaluation is the confirmatory one, because Section 23 item 1 makes it the paper's CPU result. The evaluation with Part A's 24-thread result is reported beside it.

**Descriptive (no test):**

- the 48-thread results;
- wall power;
- clocks;
- the telemetry check: host socket power and card power at 20 ms, 200 ms and off.

## 4. Accuracy-only runs (no time or energy outcomes)

These run on frodo outside the sessions, at `nice 19`.

**High-replicate runs (v0.6, Section 23 item 6).**

- Each of the 36 settings is run 200 times at its own steps and pairs:
  - matched code (`hq_cpu`/`hs_cpu --impl avx512`, bit-identical to the card): seeds 20000–20199;
  - CPU-native code: seeds 30000–30199.
- The RMSE/ε at each strike uses the reference price.
- The 95% interval comes from 10,000 bootstrap resamples of the 200 replicates (percentile).

**HF2 holds if** the matched code's RMSE/ε is at most 1.1 and its upper 95% bound at most 1.25, at all three strikes of all 36 settings.

**CPU-native bias (item 7).** It comes from the mean of the 200 native replicates (200 × N pairs per setting), with the 12.5× check of Part A beside it.

**Section 24, exploratory:**

- **Finer grid.** Steps per year 3·2^k between the registered grid points:
  - QE: 3, 6, 12, 24, 48;
  - FT: 3, 6, …, 3072;
  - basket B: the same values within its registered grid.

  Cases I–III, P1 and B, with the float model and Part A's precision rule (seeds 401–404 and 801).
- **QE-M.** Andersen's martingale-corrected QE in double precision. It is calibrated on the QE grid and the finer QE grid for Cases I–III, P1 and B. It uses the seeds of Part A's QE calibration (301–304 and 601), so QE and QE-M share draws, with the same precision rule.

For each alternative rule, the work to target and the method gain are recomputed with the measured steady-state energy per path-step.

## 5. Vitis engines (v0.6, Section 23 item 5)

These are unchanged and built after the sessions:

- library release 2023.1 of the Vitis Libraries, matching Vitis 2023.1;
- frodo's clone is at v2024.2_update3 and is checked out at the 2023.1 release for the build.

If the 2023.1 release cannot be built for the U50, this is reported, as v0.6 requires.

## 6. Deviations from v0.6 so far

1. **Same-day GPU sessions.** Both GPU sessions ran on 20 October 2026 (15:17–18:31 and about 19:45–23:04). Section 22b asks for different days. Their energies per run agree within 0.11% (median) and 8.6% (largest, P1 1% runs of about 60 µs). A short steady-state GPU session on another day may be added and reported.
2. **GPU bias check after the measurements.** The check of Section 22 (12.5× pairs, every setting) ran after the GPU measurements, on 20 October 2026, not before. It is accuracy-only and did not change any setting:
   - all 36 settings meet the target;
   - no bias exceeds ε/√2.
3. **HF1's basis.** HF1's stated basis was wrong (Section 1). The hypothesis itself is unchanged.

## 7. Code registered with this addendum

| Code | Files |
|---|---|
| Card hosts with the wait option | `host/wait_mode.hpp`, `host/hq_fpga.cpp`, `host/hs_fpga.cpp` |
| Diagnostic | `scripts/card_wait_diag.py`, with its output (`frodo_f1_diag/`) |
| Session queue and loggers | `scripts/phase6.py`, `scripts/xrt_ini/xrt_profile_200ms.ini`, `scripts/xrt_ini/xrt_no_profile.ini`, `scripts/clock_logger.py`, `scripts/wall_logger.py` |
