# Data dictionary

## `meta.json` (one per run)

| Field | Type | Meaning |
|---|---|---|
| `label` | string | experiment, workload, configuration, repetition |
| `cmd` | list | exact command line of the measured program |
| `cores`, `node`, `pinned` | string, string, bool | CPU list and NUMA node the program was pinned to (`numactl`) |
| `xclbin`, `bitstream_state` | string | FPGA bitstream loaded for the run; `n/a` for CPU runs |
| `t_launch`, `t_exit` | float (Unix s) | process start and end |
| `t_start_marker`, `t_end_marker` | float (Unix s) | the program's `MARK start` / `MARK end`: the measured window |
| `wall_s`, `proc_s`, `launch_to_end_s` | float (s) | durations |
| `exit_code`, `ok`, `timed_out`, `killed_after_end` | | run status; analyses use `ok=true` only |
| `idle_s` | float (s) | idle period recorded before launch (power floor) |
| `cpu_temp_pre/post`, `fpga_temp_pre/post` | float (°C) | temperatures before and after |
| `loadavg` | list | 1/5/15-min load average before launch |
| `cpu_util_run_pct` | float | CPU utilisation of the measured process |

## `power.csv` (10 Hz)

| Column | Unit | Source |
|---|---|---|
| `t_unix` | s | sample time; row *i* is the mean over (t<sub>i−1</sub>, t<sub>i</sub>] |
| `pkg0_w`, `pkg1_w` | W | RAPL package energy counters, differenced |
| `dram0_w`, `dram1_w` | W | RAPL DRAM counters, differenced |
| `card_w` | W | U50 board power = 12V_PEX + 12V_AUX + 3V3_PEX rails (voltage x current) |
| `card_src` | – | `xmc`: the card's XMC sensors read through the XRT driver |
| `fpga_temp_c`, `cpu0_temp_c` | °C | temperatures |

## `stdout.log` — `RESULT` keys (all programs)

| Key | Meaning |
|---|---|
| `t_compute_s` | compute time of one pass (median over passes when `runs`/`repeat` > 1) |
| `runs`, `repeat` | number of timed passes in the window |
| `bytes` (AES), `mpix` (conv), `paths`,`steps`,`path_steps` (MC), `fps`,`frames` (video) | work per pass |
| `out_fnv1a64`, `mom_hash`, `cks_*`, `verified`, `kat_ok` | output fingerprints and checks used for the FPGA = CPU comparison |
| `t_h2d_s`, `t_kernel_s`, `t_d2h_s` (FPGA) | accumulated phase times (host timers or XRT profile) |

## Energy boundaries used in the paper

- **Per socket:** `pkg0 + dram0` (+ `card` for FPGA runs).
- **Whole server:** `pkg0 + dram0 + pkg1 + dram1` (+ `card` for FPGA runs).

## Supplementary File S1 (`litreview/`)

`coding.csv` columns: `id, bibkey, short, year, venue, domain, fpga, cpu, c1_mt, c1_simd, c2_cores,
c3_transfers, c4_energy, c4b_boundary, c5_freq_warmup, c6_variability, c7_equivalence, c8_artifact,
source_url, evidence` (codes Y / partial / N / NS = not stated; `evidence` = verbatim quotes).

`published_power.csv`: published FPGA–CPU results on other cards that print a rate or run time and a power
on both sides (Supplementary File S2, S2.24). One row per comparison: `id, bibkey, study, card, cpu,
workload, metric, higher_is_better, cpu_value, fpga_value, P_C, P_F` (W, as printed), `E_C_J, E_F_J`
(printed energy, used when `power_derived = 1`: mean power = energy / run time), `printed_ratio`
(efficiency ratio printed in the paper, if any), `fpga_power_scope, cpu_power_scope, transfer, source`
(table in the cited paper), `source_url`. `scripts/make_supp_tables.py` computes `S`, `Sstar = P_F / P_C`
and `A = S / Sstar` into `data/lit_breakeven.csv` and `data/lit_breakeven_summary.json`. The values were
transcribed from the papers, not measured by us.

## Rounds 5–7 (files 08–11)

### Folder layout

`paper/inputs/results_round<N>/raw/<group>/<workload>/<configuration>/<rep>/`, with the run files described above.

| Round | `<group>` values |
|---|---|
| 5 | `session_s1`, `session_s2`, `session_s3` (replication sessions), `wall`, `overlap`, `mcperf`, `mcperf_without_counters`, `videoperf` |
| 6 | `tune`, `pred` (CPU-only runs used by the prediction), `meas` (card and CPU runs, interleaved), `membw`, `mcnative` |
| 7 | `pred`, `meas` (`asian_l<A>/fpga`: the card with A active lanes; `asian/<cpu config>`: CPU runs), `pfnative` |

CPU configurations: `s0_c24` (one socket, 24 cores), `s0_t48` (one socket with SMT), `s01_t96` (whole server).

### `predictions/predictions.json` (rounds 6 and 7)

| Field | Meaning |
|---|---|
| `created_utc` | time at which the prediction was generated |
| `model_constants` | frozen model inputs: link rate `B_GBps`, idle card power `P0_W`, host power per class and boundary `H_W`, tie band for time, in-sample error band |
| `design` | facts read from the routed design: kernel clock, compute units, lanes, initiation interval, Vivado power `P_viv_W`, routed slack |
| `predictions[]` | one entry per boundary (round 7: and per number of active lanes): predicted rate `R_F_pred`, card power, host power, speedup `S_pred`, break-even speedup `Sstar_pred`, energy advantage `A_pred`, the naïve estimate, both verdicts, and (round 7) the two `near_break_even_*` flags |
| `crossovers` (round 7) | predicted number of active lanes at which S = 1/1.2, 1, 1.2 and A = 1 |
| `scoring`, `hypotheses` (round 7) | the rules fixed with the prediction |

`predictions.json.sha256` and `predictions.md.sha256` hold the SHA-256 of the two prediction files; `predictions.md` is the same prediction as a table.

### `activity.csv` (rounds 6 and 7)

One line every 2 s while the unattended queue ran: `t_unix`, `other_users_cores` (CPU used by other human users, in cores), `system_cores` (CPU used by system accounts), `others_logged_in`, `top` (the busiest such process as `account:process`). A measurement was stopped and repeated when `other_users_cores` exceeded 0.1 or `system_cores` exceeded 1.0.

### Other files

| File | Meaning |
|---|---|
| `round<N>.log` | complete output of the run script, with wall-clock times |
| `design.json`, `design_reports/` | design facts and the Vivado/HLS reports they were read from |
| `measure_started.txt`, `measure_finished.txt` | start and end of the card measurement |
| `cpu_tuning.txt` | CPU settings chosen by CPU-only tuning runs (instruction-set variant, loop interleave) |
| `logs/` | build logs, correctness checks (card against CPU), CPU-native price checks |
| `results_round5/telemetry/` | per-core `turbostat` and `perf stat` output of the runs that recorded them |
| `results_round5/wall/*.csv` | the server's input power (IPMI DCMI, about 1 Hz) during the wall-power runs and replication sessions 2 and 3 |

Additional `RESULT` keys: `active_lanes` (MC Asian sweep: lanes that received work), `impl` (implementation name, for example `fpga_l16_a7_cu1`, `pf_native_boxmuller_fma`), `isa` (instruction set of a CPU-native build).

### File 11: audit trail

| File | Meaning |
|---|---|
| `r7_queue.log`, `r56_queue.log` | log of the unattended queue: start and end of every step, pauses caused by other users, the release of the card measurement |
| `queue_r7/`, `queue_r56/` | the queue's state folder: output of every step, completion markers with their times, the release marker |
| `file_times_round<N>.txt` | `stat` output (modification, change and birth times) of the prediction files, the bitstream, the design facts and the first card runs |
| `sha256_round<N>.txt` | SHA-256 of the prediction files and the bitstream, computed when the audit trail was collected |
| `clock.txt` | time, time zone and clock-synchronisation status of the server at collection time |
