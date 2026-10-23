# Paper 2, Part B: results (measured 20–23 October 2026)

These are the results of the runs registered in protocol v0.6, addendum v0.6.1, addendum v0.7 and addendum v0.8. The registration chain is the Zenodo record "Paper 2: registrations", concept DOI 10.5281/zenodo.23494044, with versions v1–v5 and the GitHub commits listed in `REGISTRATION_COMMITS.txt` there.

All paths are under `code/10_Heston_Accuracy/results/`. The analysis scripts are in `code/10_Heston_Accuracy/scripts/`.

| Part | Data | Analysis |
|---|---|---|
| V100 GPU sessions 1–3, bias check, clock and launch-shape sweep, microbenchmarks | `phase5g_summary_s1.csv`, `_s2.csv`, `_s3.csv`, `phase5g_logs/`, `gpu_bias/`, `gpu_bias_check.csv`, `gpu_sweep_summary.csv`, `gpu_ubench/`, `gpu_sass/` | `analysis_gpu/`, `analysis_gpu_cpu48/` (`analyse_gpu.py`) |
| Supermicro CPU-native runs | `smc_cpu_summary.csv` | — |
| frodo phase 6, sessions 1–2 (HF1, SMT, telemetry, clocks, wall power) | `phase6/`, `phase6_summary_s1.csv`, `_s2.csv`, logs, `frodo_f1/`, `host_timing_check.csv` | `analysis_phase6/` (`analyse_phase6.py`) |
| Co-execution (v0.7), both machines | `coexec_smc_summary.csv`, `coexec_frodo_summary.csv` | `analysis_coexec/` (`analyse_coexec.py`) |
| 200-replicate accuracy runs (HF2, CPU-native bias) | `replicates/`, `replicates_summary.csv` | `analysis_replicates/` (`analyse_replicates.py`) |
| Section 24: finer step grid and QE-M | `fine/`, `qem/`, logs | `analysis_s24/` (`analyse_s24.py`) |
| Scheduling (v0.8): inputs | `sched_inputs_smc_summary.json`, `sched_inputs_frodo/`, `sched_inputs_frodo_summary.json`, `sched_inputs_serve*.json`, `sched_lan.json`; code checks 1–5 in `sched_checks_v08.tar.xz` | — |
| Scheduling (v0.8): runs, 5 repeats × 10 policies | `sched_runs_v08.tar.xz` (every job's raw files), `sched_summary.csv`, logs | `analysis_sched/` (`analyse_sched.py`) |
| AMD Vitis Libraries 2023.1 engines on the U50 | `vitis/`, `vitis_summary.csv`, logs | `analysis_vitis/` |

## Outcomes of the registered hypotheses

| Hypothesis | Outcome |
|---|---|
| HG1 | fails (11 of 18) |
| HG2 | holds |
| HG3 | holds |
| HG4 | fails (207 of 216) |
| HG5 | fails under the registered power rule |
| HF1 | fails: the host does not busy-poll, so its basis was wrong |
| HF2 | holds (108 of 108) |
| HC1–HC4 | hold on both machines |
| HS1 | holds: model_E is 1.03 × the oracle |
| HS2 | fails: the V100 alone uses 6.4% less energy than model_E |
| HS3 | fails: 6 of 9 policies within 15% |
| HS4 | holds |

The details and every deviation are in the paper and its supplement.
