# Paper 2 protocol, addendum v0.9: a scheduler with measured engaged power and dispatch costs in three regimes, and a prospective test on an RTX 4070 Ti SUPER

**Authors:** Randika T. P. G. Pathiranage, Dasuni Hewawasam (University of Moratuwa)
**Date:** 27 October 2026
**Registers:** before any v0.9 scheduling run (Part A) and before any timed run of a pricing kernel on the RTX card (Part B). It goes on GitHub (this file, the two prediction files and the code, with SHA-256) and as a new version of the Zenodo record "Paper 2: registrations".

This addendum adds two registered tests. Nothing registered earlier changes. The v0.8 scheduling results (Section 7.11 of the manuscript) stand as reported. Their failure (HS2, HS3) is the motivation for Part A.

---

## Part A. The scheduler

### 1. Why

In v0.8 the model added each device's energy above idle. That held for single-device policies (within 10%) but not for mixed plans: model_E (GPU + Supermicro CPU + card) used 6.4% more energy than the V100 alone (HS2 failed), and three mixed policies fell outside the 15% band (HS3 failed).

The revision plan expected the missing term to be a cost of running devices together. The measurements of this addendum (Section 2) found three other causes, and one smaller concurrency effect:

1. **Engaged power.** A device in the plan keeps its worker loaded for the whole pass. After its work is done it does not return to idle power. The card keeps frodo's CPU sockets awake: +17.7 W while it waits, against +28.4 W while it computes.
2. **Dispatch costs.** Each option run through the agent costs energy beyond the single-run inputs: at the Supermicro (the agent and the host side of the device) and at frodo (the coordinator handling each reply).
3. **Two defects in the v0.8 run code.**
   - **Python 3.10 streams.** On Python 3.10 (both servers), writing to a socket file opened for reading and writing discards the lines already read ahead. Commands sent together could be lost. In v0.8 a lost command would have hung its pass. Every v0.8 job completed with all options returned, so the v0.8 results are unaffected.
   - **Nagle delays.** The agent's replies were held back by TCP's Nagle algorithm. In the v0.8 mixed passes, the Supermicro CPU's results reached frodo in bursts, and those passes ran 7–32% longer than predicted.
4. **Concurrency.** Two devices busy together on one server draw *less* than the sum of their separate increments, because the server's sockets wake once. On frodo this is −19.0 W.

v0.9 corrects the model with these measured terms, fixes the run code, and tests the corrected scheduler in three regimes.

### 2. Inputs measured before this addendum

All v0.8 inputs are unchanged (v0.8 Section 1), except the two Supermicro gaps (last row). The 26–27 October 2026 runs were made for this addendum, before it was written.

| Input | Script and file | Used in the model |
|---|---|---|
| frodo pair, card + fcpu, at two loads and half threads (3 repeats, 24 jobs) | `conc_frodo.py` → `conc_frodo_summary.csv` | C = −19.0 W; stretch card 0.990, fcpu 0.998 |
| Supermicro and cross-server pairs, run 1: one command per reply | `conc_smc.py --feed single` → `conc_smc_summary.csv`, `conc_cross_summary.csv` | no (descriptive, Section 8) |
| The same, run 2: batch feeding, with the Python 3.10 fix and without TCP_NODELAY | `conc_smc.py` → `conc_smc2_summary.csv`, `conc_cross2_summary.csv` | no (reported) |
| Engaged power, run 1: duty cycles, agent without TCP_NODELAY (51 jobs) | `conc_duty.py` → `conc_duty_summary.csv` | no (superseded) |
| Engaged power and dispatch costs, run 2: v0.9 agent (54 jobs) | `conc_duty.py` → `conc_duty2_summary.csv` → `conc_engaged.py` → `concurrency/engaged.json` | yes (Section 3) |
| Pass-time check with the v0.9 agent: six registered v0.8 plans, 3 repeats | `conc_timing.py` → `results/timing_v09/` | the V100's and Supermicro CPU's gaps (`concurrency/gaps_v09.json`) |

**Engaged power and dispatch costs** (`engaged.json`, from run 2). Each device's power on its own server, above idle, was fitted as P = f·a + (1 − f)·b, with f the measured busy fraction:

| Device | Busy a (W) | Engaged b (W) | Dispatch cost per option |
|---|---|---|---|
| card | 28.4 | **17.7** | none |
| fcpu | 145.0 | −26.6 → **0** (non-linear at low duty) | none |
| gpu | 111.4 | −0.4 → **0** | Supermicro **10.1 mJ**; frodo coordinator **10.5 mJ** |
| scpu | 63.2 | **6.5** | Supermicro **14.2 mJ**; frodo coordinator **15.8 mJ** |

The dispatch costs are the measured energy per option minus the model's v0.8 single-run input: gpu 43–44 mJ against 34.0 mJ, scpu 52–54 mJ against 37.5 mJ.

**Gaps between options**, re-measured with the v0.9 agent (alone runs of the pass-time check):

| Device | v0.9 | v0.8 |
|---|---|---|
| V100 | 0.057 ms | 0.158 ms |
| Supermicro CPU | 0.227 ms | 0.427 ms |

The card's and fcpu's gaps are unchanged (local workers).

**Pairs at full load, run 2 (C, reported, not used):**

| Pair | C |
|---|---|
| gpu + scpu, with the v0.9 agent | −42.8 W: frodo −15.8 W, Supermicro −27.0 W |
| card + gpu | −2.6 W |
| card + scpu | −1.4 W |
| fcpu + gpu | −2.8 W |
| fcpu + scpu | −1.9 W |

The cross-server pairs were measured with the agent before TCP_NODELAY.

### 3. The corrected model (`scripts/sched_model.py`)

The per-option costs, class changes and latencies are v0.8's (v0.8 Section 5), with the re-measured gaps. A pass is swept from each device's finish to the next, with every device starting at the start of the pass. While a set A of devices is busy, the servers draw:

1. **Busy devices.** Each device in A draws its alone power above idle: its options' energy (v0.8 inputs, plus the dispatch costs) over its busy time.
2. **The frodo pair.** C(card, fcpu) = −19.0 W while both are busy. The card is stretched by 0.990 and fcpu by 0.998 while the other is busy.
3. **Engaged power.** A device with options in the plan that has finished draws its engaged power, until the pass ends. This is not paid while another device of its server is busy: that device already keeps the server awake. The measured card_f10 + fcpu interaction was −17.2 W against a card engaged power of 17.7 W.
4. **The frodo coordinator.** Each remote option costs its frodo part, except while frodo is awake anyway (the card has options in the plan, or fcpu is busy).
5. **Totals.** E = the integral of these powers + (P_idle,frodo + P_idle,Supermicro) × T. T = the last finish.

**Not used:** C for the Supermicro and cross-server pairs. Those were measured with both devices at full rate (about 1,800 and 950 options/s); the scheduler drives them at about 300 options/s. Applied to the pass-time check's plans, they made the model 14–21% low in energy.

With no concurrency input, the model reproduces the registered v0.8 prediction exactly (every plan and value; checked 26–27 October 2026).

**How the model form was chosen.** The terms were chosen on the pre-registration runs, mainly the pass-time check, which ran six registered v0.8 plans with the v0.9 agent. With the final inputs the model gives, as measured / predicted:

| Plan | Energy | Pass time |
|---|---|---|
| alone_gpu | 0.955 | 0.966 |
| alone_scpu | 0.982 | 0.985 |
| model_E | 1.034 | 0.994 |
| model_T | 1.036 | 0.985 |
| model_M | 1.049 | 0.996 |
| model_dev | 0.983 | 0.970 |

These are not tests. The registered test is the v0.9 plans, which the model makes itself (Sections 4–5).

### 4. Regimes and policies

The book is v0.8's: 120 options, seed 20261023, the same classes and seeds.

- **R1, least energy, four devices, two-server boundary.**
  - Planned policies: alone_card, alone_fcpu, alone_gpu, alone_scpu, model_E, model_T and model_dev (as in v0.8).
- **R2, a deadline.**
  - **Rule:** D = 1.02 × the midpoint of the shortest predicted pass and the lesser of model_E's predicted pass and the V100 alone's measured pass with the v0.9 agent (0.1756 s). Here **D = 0.1587 s**.
  - **model_M:** the least predicted energy with T ≤ D/1.02 = 0.1555 s. The 2% margin covers the pass-time check's spread (measured / predicted 0.966–0.996).
  - **Boundary:** two servers.
- **R3, frodo only.**
  - Devices card and fcpu; boundary frodo (card, both sockets and DRAM).
  - Planned policies: alone_card_f, alone_fcpu_f and model_E_f.
  - The two alone policies are R1's alone_card and alone_fcpu runs, taken at frodo's boundary.
- **Oracles**, made at run time from the same repeat's alone runs (`sched_model.corrected`, with this model's terms):
  - **oracle (R1):** model_E's algorithm;
  - **oracle_M (R2):** model_M's algorithm at D, with no margin;
  - **oracle_f (R3):** model_E_f's algorithm on the card's and fcpu's alone runs, at frodo's boundary.
- **Dropped from v0.8:** round_robin.

### 5. Prediction (`scripts/predict_sched_v09.py`)

`results/predictions_sched_v09/predictions_sched_v09.json`, SHA-256 `d4ad8c03b807be53e3f4b75763cb013c70dc75ea600dd97b66f0e233c66a778f` (with its `.md`):

| Regime | Policy | T (s) | E (J) | Options: card, fcpu, gpu, scpu |
|---|---|---|---|---|
| R1 | alone_card | 29.836 | 5633.2 | 120, 0, 0, 0 |
| R1 | alone_fcpu | 2.532 | 762.1 | 0, 120, 0, 0 |
| R1 | alone_gpu | 0.1818 | 47.13 | 0, 0, 120, 0 |
| R1 | alone_scpu | 9.913 | 2052.0 | 0, 0, 0, 120 |
| R1 | model_E | 0.1628 | 43.70 | 16, 40, 60, 4 |
| R1 | model_T | 0.1483 | 67.96 | 4, 1, 63, 52 |
| R1 | model_dev | 0.1844 | 46.14 | 19, 6, 95, 0 |
| R2 | model_M (D 0.1587 s) | 0.1555 | 46.12 | 1, 32, 48, 39 |
| R3 | alone_card_f | 29.836 | 3702.9 | 120, 0 |
| R3 | alone_fcpu_f | 2.532 | 598.3 | 0, 120 |
| R3 | model_E_f | 1.3837 | 292.09 | 4, 116 |

The file also gives every plan, the additive v0.8 model's value for each plan, and the registered v0.8 plans re-evaluated with this model (descriptive).

### 6. Measurement

- **Run code:** the v0.9 agent on the Supermicro (`sched_agent.py`, with the Python 3.10 fix and TCP_NODELAY) and `sched_client.py` on frodo (the same fix). `sched_run.py` is v0.8's, unchanged.
- **Queue:** `scripts/sched_queue_v09.py` on frodo. In each repeat, 12 jobs in this order:
  - alone_fcpu, alone_card, alone_scpu, alone_gpu;
  - model_E, model_T, model_M, model_dev, model_E_f;
  - oracle, oracle_M, oracle_f.
- **Each job as v0.8:**
  - the quietness wait, 20 s idle, the workers started, 1 warm-up pass;
  - then a timed window of whole passes, at least 3 passes and at least 60 s;
  - T = window / passes; E = the boundary's energy in the window / passes (R3: frodo's only).
- **Repeat rules:** as v0.8 (other users on either server, the V100's clock 1245 ± 12.5 MHz when the GPU is in the plan, every option ok), up to 5 tries.
- **Schedule:** 5 repeats on two days, repeats 1–3 and then 4–5 (`--reps 1-3`, `--reps 4-5`). About 25 minutes per repeat.

### 7. Hypotheses (confirmatory; medians over 5 repeats)

A comparison of two policies is called only when its predicted ratio is at least 1.05 (v0.7 HC3).

- **HS5 (model against oracle, each regime).** The model policy's measured pass energy is at most 1.05 × its oracle's (median of the per-repeat ratio):
  - R1 model_E / oracle;
  - R2 model_M / oracle_M;
  - R3 model_E_f / oracle_f.
- **HS6 (R1: one device or several).** model_E's measured pass energy is at most 1.02 × alone_gpu's. Predicted 0.927.
- **HS7 (R2: the deadline).**
  - model_M meets D = 0.1587 s in at least 4 of 5 repeats.
  - Its energy is lower than model_T's. Called, predicted ×1.47.
- **HS8 (R3).** model_E_f's measured pass energy is at least 5% lower than each of alone_card_f's and alone_fcpu_f's. Predicted savings: 92% and 51%.
- **HS9 (the model).** Each of the 11 planned policies has measured / predicted pass energy and pass time within 0.90–1.10.
- **HS10 (the idle term, v0.8 HS4).** model_E's energy is lower than model_T's. Called, predicted ×1.56.

**Descriptive:**
- the energy–makespan front per regime;
- each server's share and the per-domain power of the mixed passes;
- the accuracy of the options (error / ε);
- the oracles' own predictions;
- the registered v0.8 plans under this model.

**Analysis:** `scripts/analyse_sched_v09.py`. Each test is done once, as registered, and reported whatever the outcome.

**Known risks, from the run-code check of 27 October 2026.** One short repeat, 3 s windows (1–20 passes), so these are not results. Measured (predicted):

| Policy | E (J) | T (s) |
|---|---|---|
| alone_fcpu | 748.6 (762.1) | 2.366 (2.532) |
| alone_card | 5877.5 (5633.2) | 32.08 (29.84) |
| alone_scpu | 2016.1 (2052.0) | 9.764 (9.913) |
| alone_gpu | 43.7 (47.1) | 0.175 (0.182) |
| model_E | 47.6 (43.7) | 0.162 (0.163) |
| model_T | 65.1 (68.0) | 0.156 (0.148) |
| model_M | 49.2 (46.1) | 0.155 (0.156) |
| model_dev | 48.1 (46.1) | 0.177 (0.184) |
| model_E_f | 251.2 (292.1) | 1.402 (1.384) |
| oracle | 47.6 | — |
| oracle_M | 47.7 | — |
| oracle_f | 250.2 | — |

What the check suggests:
- **HS6 may fail:** model_E 47.6 J against alone_gpu 43.7 J. The plan's 40 fcpu options, and fcpu's non-linear power at low duty (Section 2), are the likely cause.
- **HS9 may fail for model_E_f:** 0.86. fcpu's energy per small option is below the model's single-run input.
- **The oracles chose plans close to the model's own.**

These findings came after the prediction was made. The prediction was not changed.

---

## Part B. The RTX 4070 Ti SUPER

### 8. Platform (csetuf04)

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER (Ada Lovelace, AD103, compute capability 8.9), 66 SMs, 16 GB GDDR6X, **no ECC**, 285 W default limit |
| SM clock | locked at **2,340 MHz** (base) with `nvidia-smi -lgc`, by `scripts/pick_clock.py`'s rule (Section 13) |
| Software | driver 580.196.07 (held), CUDA 12.8 (nvcc V12.8.93), Ubuntu 24.04.5, kernel 7.0.0-38 (packages held), text mode during runs |
| Host | Intel Core i7-13700, ASUS TUF GAMING Z790-PLUS WIFI, 2 × 16 GB DDR5 at 4,000 MT/s, one socket |
| Boundary | GPU (NVML energy counter) + CPU package (RAPL package-0; no RAPL DRAM domain). No wall meter (a stated limitation) |
| Build | `gpu/Makefile.4060`: the V100's source with `-arch=sm_89`. Registers: heston FT 40, QE 60, basket FT 57, QE 64, Asian plain 44, CV 80. Basket QE spills 24 bytes, as on the V100. Functional test ALL PASS. Binary SHA-256 in `build/hs_gpu.sha256` (step 4A) |
| Heat test | 10 min at full load: max 74 °C; power-limited near 284 W at default clocks. At the locked clock the microbenchmarks drew at most 139 W, with no power-cap throttle |
| Accuracy check | 12.5× pairs, before any timed run: 36 of 36 settings meet the target (max predicted RMSE/ε 0.928). Bias over ε/√2 at none. \|z\| ≤ 3.04 against the matched calibration, ≤ 2.38 against CPU-native |

### 9. The prediction model (`scripts/predict_gpu_4070tis.py`)

This is the V100 model of protocol v0.6 with the card's own constants. Its two power rules were fixed from V100 data only, and the card is the first platform the model has not seen.

- **Rate:**
  - limits per SM per clock: fp32 128, int 64, special functions 16, fp64 2, issue 128 (cc 8.9);
  - a dependent issue every 4 cycles;
  - resident blocks from the registers (1,536 threads and 65,536 registers per SM);
  - η = 0.928, the geometric mean of the microbenchmarks' achieved / peak (fp32 0.821, int 0.982, sfu 0.991).
- **GPU power ("pipe energy"):**
  - P_gpu = P_idle + Σ_p e_p × (thread instructions per second on pipe p);
  - e_fma = 7.12 pJ, e_alu = 9.66 pJ, e_xu = 18.51 pJ, from the microbenchmarks at 2,340 MHz;
  - P_idle = 10.8 W.
- **Host, blocking wait:**
  - during the kernel, the idle package power, 8.0 W;
  - during the fixed part t0 = 31.9 µs, the block start-up benchmark's power: GPU 35.0 W, package 31.1 W.
- **The same rules on the V100** (training fit, post hoc, `v100_fit.md`):
  - steady-state energy within 30% at 36 of 36 settings (median measured / predicted 1.112);
  - one-run energy at 34 of 36 (median 1.102);
  - time per path-step at 36 of 36 (median 1.144).

  The RTX measurements may therefore come out 10–15% above the prediction.

### 10. Predictions

`results/predictions_gpu_4070tis/predictions_gpu_4070tis.json`, SHA-256 `4bee4fe52c25385cd05cfad1c4c7c1c86a0ed91a555e9dfa76a84f8da3ba3eb5`, with its `.md`.

**Steady state** (29 keys behind the 36 settings; energy at GPU + package):

| Kernel | Rate (G path-steps/s) | Energy (nJ per path-step) |
|---|---|---|
| Heston FT | 397–403 | 0.45–0.46 |
| Heston QE | 168–176 | 1.00–1.04 |
| Basket FT | 45–52 | 3.5–3.9 |
| Basket QE | 12–21 | 8.4–12.3 |
| Asian plain | 266 | 0.32 |
| Asian CV | 128 | 0.40 |

**Comparisons:**

- **Lowest energy:** the RTX wins all 36 rows at steady state. In one run it wins 33; CPU-native wins P1 1% and Asian 1% and 0.3% at the socket boundary.
- **Against the V100** (measured): RTX / V100 = 0.61–0.90 at steady state, lower at 18 of 18 pairs, all called.
- **Matched host** (frodo's 49.15 W socket): 0.62–1.36. The RTX is higher at P1 1% and at the three Asian targets.
- **Proposition 2:**
  - 15 of 18 pairs called (I 1%, II 1% and III 0.3% are inside the band [1/1.3, 1.3]);
  - method above platform at 11 pairs, platform above method at 7 (III 1%, P1, B);
  - κ = 1.25–2.42; G = 13.4–18.3, with the U50 card as runner-up throughout.

### 11. Measurement (`scripts/phase5_gpu_4060.py`)

- **What it is:** the V100's queue with blocking wait only, under `gpu_run_4060.py`.
- **Jobs:** the 29 steady-state runs × 5 repeats per session (145 jobs, about 50 min). `--with-set` adds the 36 settings' single runs, which are descriptive (Proposition 1).
- **Seeds:** as the V100's sessions, so the draws are the same.
- **Sessions:** two, on different days.
- **Binary:** checked against step 4A's SHA-256.
- **Clock:** the locked clock is read from step 4B's `locked_clock.txt`.
- **Quiet and repeat rules:**
  - before each job, `guard.wait_quiet`: 60 s quiet after any pause, with no other login, GPU process, container, or CPU use above 0.5 cores;
  - during each job the guard checks every ~5 s;
  - a job is repeated (up to 6 tries) if the guard saw anything, if gpu_run_4060 marks it disturbed, or if its median SM clock is more than 1% from 2,340 MHz.
- **Accuracy:** every run returns all pairs and finite prices; the error against the reference is recorded (no ECC).
- **The third party's container** stays paused until both sessions are done.

### 12. Hypotheses

- **HR1 (power and energy).** Steady-state energy per option, at GPU + package, within 30% of the prediction at ≥ 30 of the 36 settings. This is the first registered test of the pipe-energy rule.
- **HR2 (Proposition 2).** At every called pair, the measured RTX energies give the predicted verdict. Pairs inside the band are reported only.
- **HR3 (rate).** Steady-state time per path-step within 30% at ≥ 30 of the 36 settings.
- **Open question,** reported with its predicted answer: does the RTX need less energy per option than the V100? Predicted: yes at 18 of 18 at their own hosts; no at 4 at a matched host.

**Analysis:** `scripts/analyse_gpu.py` on the new results, extended for HR1–HR3.

---

## 13. Changes and deviations

**Part A:**

1. **`sched_model.py`.**
   - Added: the concurrency term, engaged power, dispatch costs (`engaged_W`, `overhead_J`, `coord_J`), device subsets and frodo-only boundary, and re-measured gaps.
   - Loads now carry the option count.
   - `measured_pass` and `corrected` take the boundary.
   - With no concurrency input, v0.8 is reproduced exactly.
2. **`sched_agent.py` and `sched_client.py`:** separate read and write streams (the Python 3.10 fix). The agent also sets TCP_NODELAY and accepts a thread count for the CPU device. `sched_run.py` is unchanged.
3. **The revision plan's concurrency matrix was measured but is used only for the frodo pair** (Section 3).
   - Two first runs were superseded: the Supermicro run with one command per reply, and the duty-cycle run before TCP_NODELAY.
   - Their records are kept.
4. **The model's terms were chosen on pre-registration runs** (Section 3), not on any v0.9 run.
5. **R2's rule and margin:** set as in Section 4, replacing the draft's 0.170 s with k = 1.059.
6. **Dropped:** round_robin. R3's alone policies come from R1's runs.
7. **HS9's band** is 0.90–1.10 (v0.8: 0.85–1.15).
8. **The run-code check's findings** are listed in Section 7. No change was made after the check.

**Part B:**

1. **The card** is an RTX 4070 Ti SUPER, not the RTX 4060 named in the revision plan; the scripts keep "4060" names.
2. **Build:** sm_89, `gpu/Makefile.4060`.
3. **`gpu_run_4060.py`:** RAPL by name, package only; no numactl; NVML energy counter, with integrated power as fallback.
4. **Clock:** locked at 2,340 MHz by `pick_clock.py`'s rule:
   - 2,340 MHz if the three benchmarks are undisturbed, show no power-cap throttle, hold the clock within 1% and draw ≤ 95% of the limit;
   - otherwise 2,100 MHz.
5. **No wall meter.**
6. **Shared desktop:**
   - the guard decides when jobs run;
   - a third party's container (no GPU access) was paused on 26 October 2026 and is restored after the runs;
   - driver and kernel are held since an unplanned upgrade and reboot on 26 October 2026, before any measurement on the card.
7. **Host power during the kernel** is the idle package power under the blocking wait, not v0.6's rule. This comes from V100 data.
8. **HR3 is new.**

## 14. Code and inputs registered with this addendum

**Part A:**

- **Code:**
  - `scripts/sched_model.py`, `predict_sched_v09.py`, `sched_queue_v09.py`, `analyse_sched_v09.py`;
  - `sched_agent.py`, `sched_client.py`, `sched_run.py`;
  - `conc_frodo.py`, `conc_smc.py`, `conc_duty.py`, `conc_matrix.py`, `conc_engaged.py`, `conc_timing.py`.
- **Inputs:**
  - `results/concurrency/` (`C_matrix.json`, `engaged.json`, `gaps_v09.json`);
  - the summaries `conc_frodo_summary.csv`, `conc_smc_summary.csv`, `conc_cross_summary.csv`, `conc_smc2_summary.csv`, `conc_cross2_summary.csv`, `conc_duty_summary.csv`, `conc_duty2_summary.csv`;
  - `predictions_sched_v09.*`.

**Part B:**

- **Code:** `scripts/predict_gpu_4070tis.py`, `phase5_gpu_4060.py`, `gpu_run_4060.py`, `gpu_bias_check_4060.py`, `guard.py`, `pick_clock.py`, `sass_count.py`, `check_4060.py`, `gpu/Makefile.4060`.
- **Inputs:** the step 4A and 4B results (`results/rtx4070tis/`), `predictions_gpu_4070tis.*`, `v100_fit.*`.

Hashes: `protocol_v0.9_code.sha256`.
