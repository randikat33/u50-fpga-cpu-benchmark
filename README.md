# FPGA vs. CPU, whole-system: AMD Alveo U50 against a dual-socket Xeon Gold 5318Y

Code for the paper *"Slower can be greener: predicting when a PCIe FPGA card beats its host server in time and energy"* (submitted to the Journal of Systems Architecture).

- **Data (raw measurements, 3,011 runs):** Zenodo, DOI [10.5281/zenodo.23133623](https://doi.org/10.5281/zenodo.23133623)
- **Code DOI (this repository, archived by Zenodo):** created by the first GitHub release (`v1.0.0`); it will be added here
- **Licence:** MIT (`LICENSE`); the data on Zenodo are CC BY 4.0

## What is here

| Folder | Contents |
|---|---|
| `benchmark/` | measurement harness (`common/run_measured.py`, `common/power_monitor.py`), experiment scripts (`campaign.sh`, `energy_long.sh`, `cpu_scaling.sh`, `mc_steady.sh`, `revision_measurements.sh`, …), configuration (`config.sh`), build and tuning scripts (`build_v2.sh`, `tune_v2.sh`) |
| `benchmark/v2_projects/` | the six workloads (`01`–`06`): HLS kernels (Vitis 2023.1), XRT host programs and optimised CPU baselines (AVX-512, AES-NI/VAES); the kernels written to test the model: `07_FIR`, `08_MC_Asian`, `09_MC_Asian_Sweep`; CPU-native baselines `03_MC_Heston/cpu/mc_native.cpp` and `04_Portfolio/cpu/pf_native.cpp` |
| `benchmark/round6/`, `benchmark/round7/` | the model predictions made before the card runs (`predict_round6.py`, `predict_round7.py`, frozen `model_constants.json`) |
| `benchmark/round5_measurements.sh`, `round6_new_kernels.sh`, `round7_sweep.sh`, `r56_queue.sh`, `r7_queue.sh` | measurement scripts of the later rounds and the unattended queue for a shared server (see `benchmark/docs/`) |
| `benchmark/v1/`, `benchmark/v2/` | per-project campaign scripts |
| `paper/scripts/` | analysis: every number, table and figure of the paper (`paper_numbers.py`, `revision_analysis.py`, `time_energy_model.py`, `core_equivalents.py`, `revision_new_runs.py`, `make_paper_figures.py`, …) |
| `paper/litreview/` | methodology audit of 30 FPGA-vs-CPU papers: codes with verbatim quotes, exclusions, statistics (Supplementary File S1) |
| `docs/DATA_DICTIONARY.md` | fields of `meta.json`, `power.csv` and `stdout.log` |

## Runs in the dataset

| Group | Runs | Zenodo file |
|---|---:|---|
| v2 main campaign | 1,164 | 05 |
| two-CU ablation | 10 | 05 |
| v1 runs (original thesis implementations) | 153 | 05 |
| idle reference | 1 | 05 |
| CPU scaling | 100 | 03 |
| long-window energy | 75 | 03 |
| MC steady-clock sweep | 165 | 03 |
| MC warm-up protocol | 165 | 03 |
| later session | 355 | 04 |
| replication sessions, wall power, host overlap, MC schedules, video counters | 602 | 08 |
| out-of-sample test (FIR, MC Asian) and CPU-native MC | 96 | 09 |
| break-even sweep and CPU-native portfolio | 125 | 10 |
| **Total** | **3,011** | |

## Reproduce the paper's numbers

```bash
git clone https://github.com/randikat33/u50-fpga-cpu-benchmark && cd u50-fpga-cpu-benchmark
# download 03, 04, 08, 09 and 10 (all parts) from the Zenodo record, then:
for z in ~/Downloads/0[3489]_*.zip ~/Downloads/10_*.zip; do unzip -q -o "$z"; done     # creates paper/inputs/
cd paper/scripts
python3 paper_numbers.py && python3 revision_analysis.py && python3 time_energy_model.py \
  && python3 core_equivalents.py && python3 revision_new_runs.py \
  && python3 round5_analysis.py && python3 round6_analysis.py && python3 round7_analysis.py \
  && python3 make_paper_figures.py && python3 make_supp_tables.py
```
Python 3.10+ with numpy, pandas, matplotlib, openpyxl. Results appear in `paper/data/` and `paper/figures/`.

## Run the measurements on your own machine

Needs an Alveo U50 (or edit `config.sh`), Vitis/XRT 2023.1, Linux with RAPL access, `numactl`, and optionally `turbostat`. See `benchmark/README.md`; in short:

```bash
cd benchmark
./build_v2.sh            # build CPU baselines, hosts and bitstreams (hours)
./run_all.sh             # main campaign
taskset -c 95 ./revision_measurements.sh all      # supplementary measurements (core scaling, telemetry, ...)
```
Before measuring, stop background services such as the desktop file indexer
(`systemctl --user mask --now tracker-miner-fs-3 tracker-extract-3`).

## Citation

See `CITATION.cff`. Please cite the Zenodo dataset (and the paper once it is published).
