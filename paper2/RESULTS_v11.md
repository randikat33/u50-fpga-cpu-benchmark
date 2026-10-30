# Paper 2, addendum v0.11: results (the scheduler with a calibrated model)

Registered in addendum v0.11:
- **GitHub:** commit 603bd596 (29 Oct 2026, 08:41:09 UTC: protocol, code and the SHA-256 of every input) and commit 3870d8ae (08:45:05 UTC: the prediction and data files with those hashes).
- **Zenodo:** registrations record v8, 10.5281/zenodo.23759329.
- **Prediction:** SHA-256 22292d48….

The run folders are in `code/10_Heston_Accuracy/results/sched_runs_v11.tar.xz` (split into parts `sched_runs_v11.tar.xz.part-00`, `-01`, `-02`; join them with `cat sched_runs_v11.tar.xz.part-* > sched_runs_v11.tar.xz`).

## Runs (all on 29 October 2026, UTC; frodo and the Supermicro)

- **09:01–09:04:** run-code check (`--check`, 3 s windows, 9 of 9 jobs ran; it logs only that each job ran, not the results).
- **09:05–09:50:** repeats 1–3 (27 jobs).
- **09:58–10:17:** repeats 4–5. 11 jobs were done when the queue process stopped at 10:17, during the warm-up of N3 rep5 alone_gpu. There was no error output, so the process was stopped from outside. Its folder is kept as `sched_v11_interrupted_N3_rep5_alone_gpu_1017` and is not used.
- **About 10:56–11:24:** the restarted queue failed all 7 remaining jobs, 5 tries each, with rc 1. The Supermicro's CPU agent on port 50556 no longer accepted connections (`TimeoutError` in `sched_client`). It was most likely left hung by the stop at 10:17. These are failed starts, not rejected measurements.
- **About 14:45:** the Supermicro agent was restarted (SM clock still 1,245 MHz; RAPL readable).
- **14:50–15:01:** the 7 remaining jobs ran. 45 of 45 jobs, none set aside by the quietness or clock rules.

**Deviations from the protocol:**
1. Repeats 4–5 ran on the same date as repeats 1–3 (the protocol said a later date).
2. The interruption and the agent restart above.
3. The prediction and data files were committed 4 minutes after the protocol commit. The repository's `.gitignore` excluded `results/`; their SHA-256 values were already fixed in the first commit.

Per pass, two-server boundary; medians of 5 repeats [range].

| Book | Policy | T meas (ms) | T pred (ms) | T ratio | E meas (J) | E pred (J) | E ratio | error max / eps |
|---|---|---|---|---|---|---|---|---|
| N3 | alone_gpu | 31.3 [31.3-31.4] | 31.1 | 1.008 | 7.83 [7.58-7.85] | 7.77 | 1.009 | 1.78 |
| N3 | model_E | 31.6 [31.6-31.8] | 27.5 | 1.150 | 8.29 [8.28-8.31] | 7.15 | 1.159 | 1.78 |
| N3 | plan_v09 | 31.5 [31.4-32.2] | 24.7 | 1.279 | 8.71 [8.65-8.85] | 7.90 | 1.103 | 1.81 |
| N2 | alone_gpu | 107.1 [107.0-107.3] | 106.8 | 1.002 | 28.03 [27.94-28.10] | 27.36 | 1.025 | 1.56 |
| N2 | model_E | 104.6 [104.2-105.4] | 103.1 | 1.015 | 28.11 [26.89-28.34] | 26.75 | 1.051 | 1.61 |
| N2 | plan_v09 | 94.4 [94.1-94.5] | 93.9 | 1.006 | 29.27 [29.20-29.34] | 29.47 | 0.993 | 1.61 |
| S | alone_gpu | 19.5 [19.5-19.6] | 18.9 | 1.034 | 4.42 [4.40-4.45] | 4.27 | 1.035 | 0.79 |
| S | model_E | 15.4 [15.0-15.6] | 12.9 | 1.191 | 4.85 [4.71-4.89] | 3.28 | 1.478 | 0.79 |
| S | plan_v09 | 10.5 [10.4-10.9] | 11.6 | 0.907 | 3.80 [3.76-3.93] | 3.66 | 1.040 | 1.05 |

| Book | model_E / alone_gpu: measured | predicted | model_E / plan_v09: measured | predicted |
|---|---|---|---|---|
| N3 | 1.058 | 0.920 | 0.951 | 0.905 |
| N2 | 1.003 | 0.978 | 0.960 | 0.908 |
| S | 1.097 | 0.768 | 1.274 | 0.896 |

## Hypotheses

- **HC1 fails** (primary): book N3 ratio 1.058 (registered <= 0.96; predicted 0.920).
- **HC2 fails** (primary): model_E / plan_v09 N3 0.951, N2 0.960 (registered <= 0.95 in both).
- **HC3 fails** (primary, 4 of 6 within 0.90-1.10 in both): N2/alone_gpu E 1.025 T 1.002; N2/model_E E 1.051 T 1.015; N2/plan_v09 E 0.993 T 1.006; N3/alone_gpu E 1.009 T 1.008; N3/model_E E 1.159 T 1.150 OUT; N3/plan_v09 E 1.103 T 1.279 OUT.
- **HC4 holds** (primary): book N2 ratio 1.003 (registered >= 0.95; predicted 0.978).
- **HC5 fails** (primary): measured order N2 < N3 < S (predicted S < N3 < N2).
- **HC6 fails** (secondary): book S ratio 1.097 (registered <= 0.85; predicted 0.768); S/alone_gpu E 1.035 T 1.034; S/model_E E 1.478 T 1.191 OUT; S/plan_v09 E 1.040 T 0.907.

## Robustness repeat (not registered)

Repeat 6 ran with the same queue (`--reps 6`) on 29 October 2026, 21:08–21:25 UTC, about 6 h after the registered runs, on the same date. It is outside the registered summary and analysis. Energies differ from the registered medians by at most 2.9% (S model_E), and every verdict is the same; see `analysis_sched_v11/explore_v11.md`, section 4.

A second queue command (`--reps 4-5`, nothing left to run) was started at 21:08:04. It rewrote the same 45-row summary and logged "SCHED FINISHED: 18 of 18 jobs" at 21:09:10, during the first rep-6 job's idle and warm-up time.

## Exploratory diagnosis (not registered; `scripts/explore_sched_v11.py` → `analysis_sched_v11/explore_v11.md`)

1. **Plans that were not chosen by the model are predicted well.** alone_gpu is within 1.0–3.5% on all three books; the v0.9 model's plan on N2 is within 0.7%. The large errors are in the plans the v0.11 optimiser selected.
2. **Moving options off the V100 did not shorten its pass as modelled.** On N3, model_E moved the 20 Asian options (3.4 ms of the V100's stream) to frodo's CPU. The V100's first classes then took longer while the local device ran at the start of the pass:
   - P1 FT 0.97 → 1.68 ms;
   - I QE (40 steps) 1.29 → 3.52 ms.

   The V100's finish time stayed at 31.2 ms (predicted 27.5). Book S shows the same pattern. The per-class stream times measured with the device alone are therefore not additive across plans: running a local device couples into the remote stream through the shared coordinator.
3. **frodo's energy above idle is under-predicted when local devices run beside the V100:**
   - N3 model_E: 0.93 against 0.61 J;
   - N2 model_E: 1.29 against 0.81 J;
   - S model_E: 1.60 against 0.53 J. Here the card ran one class of basket QE options, a design the calibration did not measure: card finish 8.5 ms against a predicted 3.1.
4. **A heterogeneous plan can still save energy on short books.** The v0.9 model's plan used 14% less than the V100 alone on S (3.80 against 4.42 J), but the v0.11 model ranked its own plan above it.
