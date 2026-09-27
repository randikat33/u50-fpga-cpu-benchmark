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
