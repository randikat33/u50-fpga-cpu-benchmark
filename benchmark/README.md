# Thesis_and_jounal_data: v2 benchmark campaign (Alveo U50 vs Xeon Gold 5318Y)

This one folder contains everything for the journal paper:

| Folder / file | What it is |
|---|---|
| `v2_projects/` | **New A-grade code for all 6 projects**: HLS kernel, testbench, XRT host, optimised CPU baseline(s), link config, Makefile, and a README per project |
| `campaign.sh` | **the one command**: builds, tunes, checks, runs the quick and full campaign, analyses; resumes after any interruption |
| `build_v2.sh` | builds all v2 programs and, on request, the xclbins |
| `tune_v2.sh` | builds every xclbin and automatically selects the fastest working design from the reports and card measurements |
| `v2/NN_*/run_benchmark.sh` | v2 benchmark sweeps (5 repetitions, cool-down, energy) |
| `v1/NN_*/run_benchmark.sh` | the thesis (v1) programs, key points only, for the before/after comparison |
| `run_all.sh` | runs everything, then builds tables, figures and **one zip to upload** |
| `PLAN.md` | research questions, hypotheses, design of experiments, paper outline |
| `v2_projects/VITIS_IDE_GUIDE.md` | the equivalent Vitis IDE settings, if you prefer the GUI |
| `config.sh` | every path and setting; put your changes in `config.local.sh` |

Your original thesis project folders are **not modified**.

---

## Step 1: copy, unpack, dependencies

```bash
cd ~/Desktop && unzip Thesis_and_jounal_data_v2.zip && cd Thesis_and_jounal_data
chmod +x campaign.sh tune_v2.sh run_all.sh build_v2.sh v1/*/*.sh v2/*/*.sh common/*.sh common/*.py analysis/*.py patches/*.sh v2_projects/*/test/*.sh
sudo apt install -y build-essential numactl zip ffmpeg libopencv-dev libssl-dev libpng-dev
pip3 install --user numpy pandas matplotlib
source /tools/Xilinx/Vitis/2023.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
./build_v2.sh --deps
```

Two projects (05 and 06) use the Vitis Vision library. If it is not at `/home/USER/Vitis_Libraries/vision`, create `config.local.sh` with:

```bash
VISION_ROOT=/path/to/Vitis_Libraries/vision
```

## Step 2: run the whole campaign (recommended)

```bash
./campaign.sh --setup-autoresume      # once, asks for sudo: RAPL + governor at every boot,
                                      # and restart the campaign automatically after a reboot
tmux new -s campaign
./campaign.sh                         # days of unattended work; safe to interrupt at any time
```

In another terminal, `./campaign.sh --status` shows the progress, and `tail -f campaign.log` shows the live log.

**The stages** run in order. Each one is recorded in `campaign_state/`:

| Stage | What happens | Time |
|---|---|---|
| deps | checks compilers, libraries, XRT, Vitis and the Vision library | seconds |
| programs | `build_v2.sh`: CPU baselines and FPGA hosts | minutes |
| tune | `tune_v2.sh --ablation`: all xclbins, automatic design selection, card self-check of every design, ablation xclbins | 1-3 days |
| check | `run_all.sh --check` | seconds |
| quick | `run_all.sh --quick` → `UPLOAD_ME_*.zip` | ~1.5 h |
| **pause** | stops so you can upload the quick zip for checking; continue with `./campaign.sh --continue` | |
| full | `run_all.sh`: the full v2 campaign and the v1 key points | ~14-18 h |
| analyze | final tables, figures, `REPORT_v2.md`, `UPLOAD_ME_*.zip` | minutes |

- **To run without the pause:** `./campaign.sh --no-pause`.
- **After fixing a failed stage:** `./campaign.sh` again, or `./campaign.sh --redo <stage>`.

### Interruptions and power failures

Nothing that has finished is ever repeated:

- **Builds.** make skips finished HLS and link steps. An xclbin only appears once v++ has completed (it is written to `partial_*.xclbin`, then renamed). An interrupted Vivado run starts that one build again.
- **Tuning.** Every variant has a `tune_state/<project>/L<n>.result` file. A card test that could not run (card not ready, crash) is retried at the next start, up to 3 times.
- **Measurements.** Every run has a `DONE` file. An interrupted run is repeated, and completed runs are skipped.
- **Automatic restart.** With `--setup-autoresume`, the machine restarts `campaign.sh` about 10 minutes after boot (skipped if the file ~/STOP_CAMPAIGN exists) and waits until the Alveo card is visible. Set the BIOS option "Restore on AC power loss" to **Power On**. The restart is removed automatically when the campaign finishes, or manually with `--disable-autoresume`.

### Automatic design selection (`tune_v2.sh`)

- **03 MC Heston and 04 Portfolio.** For these two, more lanes per CU can raise throughput until timing or routing becomes the limit. The script works like this:
  1. It builds the start value (MC 6, PF 8).
  2. For each variant it reads the HLS report (II, estimated Fmax), the routed timing (WNS), the utilisation and the **achieved clock** (`xclbinutil`).
  3. It runs the host with `--verify` on the card and takes the measured throughput.
  4. It keeps adding one lane while the variant builds, verifies and is more than `TUNE_MIN_GAIN_PCT` faster.
  5. If the start value fails, it steps down instead.
  6. The winner is copied to `build/` and written to `v2_projects/TUNED.conf`. Hosts and CPU programs are rebuilt with the same lane count, and every later `build_v2.sh` uses it.
- **01, 02, 05 and 06.** These are built in their configured design and self-checked on the card. Their limit is PCIe or the host, so more lanes or CUs do not help. The 05 4-CU and 06 2-CU builds are measured as ablations to show this.
- **Output.** Every variant tried, with its numbers and the decision, is recorded in `tune_state/SUMMARY.md` and `tune_summary.csv`. The same data goes into the results as `fpga_tuning_v2.csv` and the "Automatic design selection" table in `REPORT_v2.md`, which is the design-space-exploration table for the paper.
- **Search ranges** are set in `config.sh`: `TUNE_MC_LANES`, `TUNE_PF_LANES` and `TUNE_MIN_GAIN_PCT`.

## Step 2 (alternative): the stages by hand

```bash
./build_v2.sh                         # CPU baselines + FPGA hosts (minutes)
./tune_v2.sh --ablation               # all xclbins + automatic selection (resumable; ./tune_v2.sh --status)
./run_all.sh --check                  # every line [ok]; fix paths in config.local.sh, never in the scripts
./run_all.sh --quick                  # ~1.5 h, 1 repetition, fewer sweep points -> results_quick/ + UPLOAD_ME_*.zip
./run_all.sh                          # full campaign (~14-18 h) -> results/ + UPLOAD_ME_*.zip
```

- `./build_v2.sh --xclbin` still builds the xclbins in their default form without tuning. A single project can be built with `./build_v2.sh --xclbin --only 01`.
- The report checklist is in `v2_projects/VITIS_IDE_GUIDE.md` §3 and in each project README §8. `tune_v2.sh` applies it automatically.

## Step 3: benchmark details

- **Phases.** Phase A runs the v2 sweeps, phase B runs the v1 key points (option B), and phase C runs the analysis and builds the zip.
- **sudo.** In an interactive run the password is asked once, for the RAPL counters and the governor. Unattended runs never wait for a password; they rely on `--setup-autoresume`.
- **Partial runs:**
  - `./run_all.sh --only 03,04`
  - `./run_all.sh --v2-only`
  - `./run_all.sh --v1-only`
  - `./run_all.sh --reps 7`
- **Tables and figures only:** `./run_all.sh --analyze`.
- **Keep the machine quiet** while the campaign runs: no other work and no desktop session load.

## Step 4: upload

Upload the `UPLOAD_ME_<host>_<date>.zip` that the script prints at the end. It contains:
- all raw logs, power traces and XRT traces;
- system facts and the build commands and reports;
- the tables, figures, `REPORT.md` (v1) and `REPORT_v2.md` (v2 and the comparison).

Send the `--quick` zip first if you want the parsing checked before the long run.

---

## What is measured

| Item | How |
|---|---|
| CPU | socket 0, 24 physical cores `0-23` (`numactl -N0 -m0 taskset -c 0-23`), `OMP_PROC_BIND=close`, governor `performance`; every CPU implementation is measured, and the paper uses the best one |
| FPGA hosts | pinned the same way (CPU-side work is part of the FPGA result) |
| Repetitions | 1 profiled FPGA run (XRT trace, not timed) + 5 timed repetitions; FPGA and all CPU implementations back to back in every repetition, then an adaptive cool-down (≥20 s, then FPGA ≤ idle +3 °C, CPU ≤ idle +5 °C) |
| Boundaries | **compute** (program's measured section, between `MARK start/end`), **warm E2E** (launch → one measured section), **cold E2E** (after loading a different bitstream); live stream: **FPS** of the processing window, plus a **resize-only** mode |
| Energy | RAPL socket-0 package + DRAM + Alveo rails at 10 Hz, integrated over the measured section, divided by work units (GB / Mpix / path-steps / frames); also a "CPU server without the card" boundary |
| CPU utilisation | of the pinned cores, over the measured section (`cpu_util_proc_pct`) |
| Correctness | `--verify` in repetition 1 (untimed); FPGA and CPU checksums compared in `accuracy_v2.csv` |
| Statistics | median, 95% CI, CV flag > 5%; speedup and energy ratios with bootstrap CIs; crossover points along each sweep |
| System facts | `lscpu`, NUMA, `xbutil`, `xclbinutil --info` (achieved clocks), exact compile commands, source checksums |
| FPGA build reports | for every v2 kernel: HLS synthesis report (II, latency and resources per loop, estimated Fmax), routed timing summary (WNS on the kernel and HBM clocks), utilisation per kernel and CU, power report, link summary, v++ log. Collected into `results/system/v2_builds/` and summarised in `fpga_hls_top_v2.csv`, `fpga_hls_v2.csv`, `fpga_impl_v2.csv` and figure `v2_fig10_resources` |

## Outputs (`results/`)

```
results/
  system/                   hardware, software, xclbin info, v2 build commands and reports
  idle/                     60 s idle baseline
  v2/raw/<project>/<group>/<config>/<platform>/<rep>/   stdout.log, power.csv, meta.json, xrt/
  v2/tables/                runs_v2, summary_v2, speedup_v2, crossover_v2, energy_v2,
                            cpu_impls_v2, stages_v2, accuracy_v2, v1_vs_v2, data_quality_v2,
                            fpga_hls_top_v2, fpga_hls_v2, fpga_impl_v2 (.csv)
  v2/figures/               v2_fig01 ... v2_fig10 (.pdf + .png), LAYOUT_QC.txt
  v2/REPORT_v2.md
  v1/...                    same structure for the v1 key points (REPORT.md)
```

Figures are checked automatically for clipped or overlapping text (`LAYOUT_QC.txt`).

## Do I need to recompile or re-synthesise?

| What | Needed? |
|---|---|
| v2 FPGA designs | **Yes**, new xclbins: `./campaign.sh` (or `./tune_v2.sh`), or the IDE guide |
| v2 CPU and host programs | **Yes**: `./build_v2.sh` (minutes) |
| v1 programs | **No**: the thesis binaries and xclbins are used as they are |
| Optional fair v1 live CPU | `bash patches/build_patched.sh` |
