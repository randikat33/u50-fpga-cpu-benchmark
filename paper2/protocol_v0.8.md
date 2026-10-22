# Paper 2 protocol, addendum v0.8: scheduling a book of options on two servers

**Authors:** Randika T. P. G. Pathiranage, Dasuni Hewawasam (University of Moratuwa)
**Date:** 22 October 2026
**Registers:** before any scheduling run, on GitHub (this file, the prediction file and the code, with SHA-256) and as a new version of the Zenodo record "Paper 2: registrations".

This addendum fixes the scheduling part of protocol v0.6, Section 25 (RQ5, a portfolio). Nothing registered earlier is changed.

## 1. Inputs measured before this addendum

Every number in the prediction (Section 5) comes from runs that were already finished. Their files and SHA-256 are in `predictions_sched.json`.

| Input | Source |
|---|---|
| U50 card: time and two-server-boundary energy of one run at each of the 36 settings (`--wait xrt`, registered xrt.ini) | frodo phase-6 sessions 1–2 (`phase6_summary_s1.csv`, `_s2.csv`), median |
| V100: the same (block wait, 1245 MHz) | GPU sessions 1–2 (`phase5g_summary_s1.csv`, `_s2.csv`), median |
| frodo CPU device: socket 1, 48 threads, CPU-native code, the same | `sched_inputs_frodo_summary.json`, 3 repeats, 22 October 2026 |
| Supermicro CPU device: socket 0, 20 threads, CPU-native code, the same | `sched_inputs_smc_summary.json`, 3 repeats, 22 October 2026 |
| Idle power of each server (all RAPL domains, card or GPU) | the same two files, 3 × 30 s each: frodo 98.0 W, Supermicro 64.7 W |
| Card class change: a new serve process, same design 0.60 s, other design (xclbin load) 5.35 s, and their energy above idle | `card_switch.py` inside the harness, 25 ordered design pairs × 3 rounds |
| CPU-device class change: frodo 30 ms (restart + first-run extra); Supermicro 9.2 ms and V100 0.15 ms through the agent | `card_switch.py`; the LAN check |
| Link: round trip 0.47 ms median, clock offset and drift | the LAN check (`sched_lan.json`) |
| Serve-mode part time per option (pipelined, Section 2), each device alone, at the 115 device–setting pairs whose single run takes under 50 ms, and the latency of a batch's first result (card 0.28 ms, fcpu 0.07 ms, V100 1.28 ms, Supermicro CPU 1.35 ms) | `sched_inputs_serve.json` (`scripts/sched_inputs_serve.py`, time only), second run, 22 October 2026, with the two-process agent |
| Gap between consecutive options of one class in the book's passes (beyond the part's own time): card 0.013 ms, fcpu 0.017 ms, V100 0.158 ms, Supermicro CPU 0.427 ms | the alone runs of the pre-registration checks 2–5 (`results/sched_checks/`), the median per check, then the median of the four |

The four device cost tables were measured by the single-run protocol of the earlier phases (one complete pricing run at the setting, repeated in a timed window). The input runs of 22 October 2026 were made for this addendum before it was written. The serve-mode runs and the gaps were added after the code checks of Section 7, item 5.

**Why the gaps come from the checks.** The serve-mode runs priced each class in a short burst of up to 50 options. They gave the V100's gap between options as 0.45 ms (first run) and 0.98 ms (second run). The book's passes in the checks, with the device always busy, gave 0.15–0.18 ms in all four checks. The model uses the book's value. The serve runs still give the part times, which the checks confirmed within 1% for the V100 and the card.

## 2. Devices, pinning and programs

| Device | Machine | Worker | Pinning |
|---|---|---|---|
| card | frodo | `build/h{q,s}_fpga --serve`, one process per option class, `--wait xrt`, registered xrt.ini | CPU 0, memory node 0 |
| fcpu | frodo | CPU-native serve program, 48 threads | socket 1, memory node 1 |
| gpu | Supermicro | `build/hs_coexec` (v0.7) with the class-change command `C case method steps seed` added (same process and CUDA context; the pricing code is unchanged) | CPU 10, memory node 1 |
| scpu | Supermicro | CPU-native serve program, 20 threads | socket 0, memory node 0 |

- **Coordinator:** `scripts/sched_run.py` on frodo, pinned to CPU 0's sibling (each worker's CPU set is widened before `numactl` pins it). It drives the card and fcpu workers directly. It reaches the Supermicro's two workers through `scripts/sched_agent.py` (JSON lines over TCP), which runs as two processes pinned to socket 1 without CPU 10 and its sibling: the V100's worker and the power sampler on port 50555, and the CPU device's worker on port 50556.
- **Pipelined commands:** a local worker gets all of a class's options at once; the agent gets a device's whole list for the pass at once. Each worker runs its commands in order and changes class as needed.
- **Supermicro energy:** the agent samples RAPL of both sockets and NVML at 20 Hz. It returns the energy of the window, which the coordinator gives in its own clock, converted with the clock offset. The offset is measured by 30 round trips just before the window, taking the median of the fastest 20%.
- **frodo energy:** `common/run_measured.py`, which logs RAPL and the card rails at 10 Hz.

## 3. The book

- **120 options:** 20 per case (I, II, III, P1, basket B, Asian A), with 8 at 1%, 8 at 0.3% and 4 at 0.1%. The order is shuffled with seed 20261023.
- **An option** is one complete pricing run at its registered setting (`settings.csv`, `settings_s.csv`, rule empirical) with the method the policy gives it.
- **Classes:** options of one class (case, method, steps) share the class's seed (20262400 + the class's index) and use consecutive pair ranges [kN, (k+1)N). So every option has its own draws, and a worker serves a whole class.
- **A pass** prices the whole book.
  - Each device works through its options one at a time. Its classes are sorted by design (card designs FT, QE, basket FT, basket QE, Asian), then case, method and steps; options within a class are sorted by id.
  - The pass ends when the last device finishes. The next pass then starts.
  - The book is priced pass after pass, so a device with more than one class changes class at every class boundary, including from its last class back to its first. A device with one class keeps its worker.

## 4. Policies

| Policy | Placement |
|---|---|
| alone_card, alone_fcpu, alone_gpu, alone_scpu | every option on one device, with that device's lower-energy method for the option |
| round_robin | option k of the book order on device k mod 4 (card, fcpu, gpu, scpu), lower-energy method |
| model_E | the least predicted pass energy (Section 5) |
| model_T | the shortest predicted pass, then the least energy |
| model_M | the least predicted energy with the pass no longer than the midpoint of model_E's and model_T's predicted passes (0.1631 s) |
| model_dev | the least predicted energy above idle, ignoring the idle power over the pass (a device-only energy objective) |
| oracle | model_E's algorithm on costs corrected with the same repeat's four alone runs (Section 5) |

**Optimiser.**

1. **Local search:** repeat the best improving move until none improves the objective. A move is either one option to another (device, method), or all of one device's options of one class to another (device, method).
2. **Starting plans:** the search runs from each of these, and the best result is kept:
   - every option on its least-energy-above-idle (device, method);
   - each alone policy;
   - round_robin;
   - for model_E, model_T and model_M, also each other's current plans, repeated until model_E stops changing (at most 5 rounds).
3. **Ties:** the first in the fixed order (devices card, fcpu, gpu, scpu; options by id; the starts in the order above).
4. **Objectives:**
   - model_T uses the idle power set to 10^6 W;
   - model_dev uses it set to 0 W;
   - model_M adds 10^6 J per second over its deadline.

**One deadline, not three.** v0.6's outline and the plan of 22 October named model_E under three deadlines. The predicted model_E pass is only 11.6% longer than the shortest pass the optimiser finds (0.172 s against 0.154 s). Three deadlines in that gap would be about 3% apart, which is less than the model's expected error. One deadline is kept (model_M, at the midpoint), with model_T and model_dev at its two sides. The energy–makespan front is reported descriptively from the model (Section 6).

## 5. Prediction (`scripts/sched_model.py`, `scripts/predict_sched.py`)

**Cost of an option on device d with method m:**

- **Single run:** t_s is its measured time at the setting on d alone. ΔE_s = (the energy of all of that server's components during the run) − (that server's idle power) × t_s.
- **Part time b:**
  - **measured settings** (single run under 50 ms): the measured serve-mode part time;
  - **other settings:** t_s × d's median serve/single ratio over its measured settings of 20 ms or more.
- **Effective time:** t = b + g(d), where g(d) is d's gap between options (Section 1).
- **Energy above idle:** ΔE = (ΔE_s / t_s) × b.

**Per pass:**

- busy(d) = Σ t + L(d) × (its number of classes for card and fcpu, or 1 for gpu and scpu) + Σ the class changes' times, where L(d) is d's batch latency
- ΔE(d) = Σ ΔE + Σ the class changes' energy above idle
- T = max over d of busy(d)
- E = Σ_d ΔE(d) + (P_idle,frodo + P_idle,Supermicro) × T

The boundary is the **two servers**: frodo's card, both sockets and their DRAM, and the Supermicro's GPU, both sockets and their DRAM.

**Stated assumptions:**

1. Devices running together add their energies above idle (HC1 of v0.7 found this within 15% on both machines).
2. **Serve-mode energy:** an option in serve mode draws the same power above idle as a single run at its setting, for its part time. The gap is at idle.
3. **Class changes:** CPU and GPU class changes add time but no energy above idle.

**Oracle costs.** For each device, in the same repeat:

- **times:** for each setting it ran in alone_d, the median interval between consecutive results of one class (its effective time). For its other method: the model's time × the device's median measured/model ratio;
- **energy above idle:** the model's × the ratio (measured alone pass energy − idle × pass time − model class-change energy) / (model energy of its options).

**Predicted** (two-server boundary, per pass):

| Policy | T (s) | E (J) | E / model_E | options card, fcpu, gpu, scpu |
|---|---|---|---|---|
| alone_card | 29.836 | 5633.2 | 127.1 | 120, 0, 0, 0 |
| alone_fcpu | 2.532 | 762.1 | 17.2 | 0, 120, 0, 0 |
| alone_gpu | 0.194 | 46.6 | 1.052 | 0, 0, 120, 0 |
| alone_scpu | 9.937 | 2052.3 | 46.3 | 0, 0, 0, 120 |
| round_robin | 26.105 | 5046.5 | 113.9 | 30, 30, 30, 30 |
| model_E | 0.172 | 44.3 | 1.000 | 8, 0, 52, 60 |
| model_T | 0.154 | 64.6 | 1.458 | 16, 4, 48, 52 |
| model_M | 0.162 | 45.1 | 1.018 | 15, 0, 37, 68 |
| model_dev | 0.192 | 46.3 | 1.044 | 8, 0, 112, 0 |

The plans (every option's device and method) are in the file `predictions_sched.json`, SHA-256 `6d3ac807b7658c9bba6eea4502e4f7284608267575cb07596d7dd1ef24f08ed3`.

## 6. Measurement and hypotheses

**Schedule.**

- 5 repeats, run in this order in each: alone_fcpu, alone_card, alone_scpu, round_robin, alone_gpu, model_E, model_T, model_M, model_dev, then oracle. In checks 2–5, the job after alone_fcpu drew 30–65 W more on frodo, so alone_fcpu is followed by the long card job.
- Each job: the quietness wait, 20 s idle, the workers started, 1 warm-up pass, then a timed window of whole passes (at least 3 passes and at least 60 s).
- Per pass: T = window / passes, and E = (frodo's + the Supermicro's energy in the window) / passes.
- **Repeated jobs** (up to 5 tries): when frodo's other users or the Supermicro's (seen by the agent) used more than 1 core on average or more than 4 in any second; when the V100's median SM clock was not 1245 ± 12.5 MHz (GPU in the plan); or when any option's run did not return all its pairs and finite prices.
- **Queue:** `scripts/sched_queue.py` on frodo, with the agent running on the Supermicro (50 jobs, about 1.5 h).

**Hypotheses** (confirmatory; values are medians over the 5 repeats):

- **HS1 (model against oracle).** model_E's measured pass energy is at most 1.05 × the oracle's: the median of the per-repeat ratio. Predicted ratio: 1.00.
- **Calls.** As in v0.7 (HC3), a comparison is called "lower" only when its predicted ratio is at least 1.05. Comparisons below that are "no call" and are reported only.
- **HS2 (scheduling pays).** model_E's measured pass energy is lower than that of each of the four alone policies and round_robin.
  - All five are called. Predicted ratios: alone_gpu ×1.052 (the closest), the others ×17 or more.
- **HS3 (the model).** For each of the 9 planned policies, the measured pass energy and pass time are within 0.85–1.15 of the prediction.
- **HS4 (the idle term).** model_E's measured pass energy is lower than model_T's.
  - Predicted ×1.46.
  - model_dev (×1.044) is a no call.

**Known risks, from the checks** (one short repeat each; not results):

- **Background energy:** frodo's background varied by up to 20% of a 0.2 s pass's energy between jobs.
- **Concurrency:** with the V100 and the Supermicro CPU both busy, the measured energy was about 20 W above the sum of the devices' increments. This is the additivity assumption (Section 5).
- **Effect on HS2:** HS2's closest comparison (×1.052) is therefore uncertain.

**Descriptive:**

- the energy–makespan front of the model (model_T, model_M, model_E, model_dev), and the measured policies on it, including whether model_M met its deadline;
- the communication-to-computation ratio per device (link and agent time / busy time);
- the card's break-even batch: the number of options of one class per pass above which a design load pays off, from the measured change cost;
- each server's share of the energy;
- the accuracy of the options (error / ε over all passes, per policy);
- the oracle's own predicted energy.

**Analysis.** Each test is done once, as registered, and reported whatever the outcome.

## 7. Changes and deviations since v0.7

1. **`gpu/hs_coexec.cu` gained the command `C`** (class change in the same process). It was rebuilt on the Supermicro on 22 October 2026 for the input runs. The v0.7 co-execution runs had finished before that, with the version registered in v0.7; the pricing code is the same.
2. **Three deadline policies replaced** by one (model_M), with model_T and model_dev (Section 4).
3. **Supermicro CPU device on socket 0** (20 threads): socket 1 holds the GPU's host thread.
4. **Expected length.** The plan of 22 October estimated 4–6 h. With the measured costs, a pass takes 0.2–25 s, so 5 repeats of 10 policies take about 1.2–1.5 h.
5. **Code check before registration** (22 October 2026, `sched_queue.py --check`, 1 short repeat; not a result). It found three things, all fixed before this addendum was registered:
   - **Worker start:** the workers did not start under the harness, because `numactl` refuses CPUs outside its parent's set. The coordinator is pinned to one CPU, so each worker's CPU set is now widened first.
   - **Small options:** the fast policies' passes were 17–41% longer than the model of the time predicted. The slow ones (card, both CPUs alone, round_robin) were within 6%. The coordinator then sent one option at a time, and the model had no serve-mode cost per call. The coordinator is now pipelined (Section 2), and serve-mode costs were measured (Section 1).
   - **Optimiser:** after the serve-mode costs, the first optimiser (one start) found a model_M plan with less energy than its model_E plan. The optimiser now starts from several plans (Section 4).
   - **One agent process for both workers** (checks 2 and 3): with both Supermicro workers in one agent process, the time between the V100's options rose from 0.26 ms (V100 alone) to 1.3 ms. Pinning the agent off socket 0 did not change this. The agent now runs as two processes, one per worker (Section 2). The input runs (`sched_inputs_serve.py`, the LAN check) used the single process.
   - **Serve-mode inputs measured again** with the two-process agent (22 October 2026). The first serve run used the single process; it is kept as `sched_inputs_serve_run1.json`, and the prediction uses the second.
   - **20 s idle before each job:** in check 4, frodo's power stayed up to 48 W above idle in the job after a heavy CPU job.
   - **Gap between options from the checks** (Section 1): the second serve run gave the V100 a gap of 0.98 ms against 0.15–0.18 ms in the book's passes, so the model takes the gap from the checks' alone runs.
   - **Job order** changed (Section 6), and HS2 and HS4 use the 5% calling rule of v0.7.
   - **The last check (check 5)** ran the run code registered here: `sched_run.py`, the agent and the queue. After it, only the queue's job order and the model's gap input changed. Checks 1–5 are kept in `results/sched_checks/`.

## 8. Code registered with this addendum

`scripts/sched_model.py`, `scripts/predict_sched.py`, `scripts/sched_run.py`, `scripts/sched_queue.py`, `scripts/sched_client.py`, `scripts/sched_agent.py`, `scripts/sched_inputs_frodo.py`, `scripts/sched_inputs_smc.py`, `scripts/sched_inputs_serve.py`, `scripts/card_switch.py`, `gpu/hs_coexec.cu`, and the prediction file. The inputs added before registration (`sched_inputs_serve.json`, `sched_inputs_serve_run1.json`, `results/sched_checks/`) are registered with it. Their hashes are in `protocol_v0.8_code.sha256`.
