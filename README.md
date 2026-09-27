# FPGA vs. CPU, whole-system: AMD Alveo U50 against a dual-socket Xeon Gold 5318Y

Code for the paper *"Faster is not greener: predicting when a PCIe FPGA card beats its host server in time
and energy"* (Journal of Systems Architecture, under review).

- **Data (raw measurements, 2,188 runs):** Zenodo, DOI 10.5281/zenodo.XXXXXXX ← *fill in*
- **Code DOI (this repository, archived by Zenodo):** 10.5281/zenodo.YYYYYYY ← *fill in after the first release*
- **Licence:** MIT (`LICENSE`); the data on Zenodo are CC BY 4.0

## What is here

| Folder | Contents |
|---|---|
| `benchmark/` | measurement harness (`common/run_measured.py`, `common/power_monitor.py`), experiment scripts (`campaign.sh`, `energy_long.sh`, `cpu_scaling.sh`, `mc_steady.sh`, `revision_measurements.sh`, …), configuration (`config.sh`), build and tuning scripts (`build_v2.sh`, `tune_v2.sh`) |
| `benchmark/v2_projects/` | the six workloads: HLS kernels (Vitis 2023.1), XRT host programs and optimised CPU baselines (AVX-512, AES-NI/VAES) |
| `benchmark/v1/`, `benchmark/v2/` | per-project campaign scripts |
| `paper/scripts/` | analysis: every number, table and figure of the paper (`paper_numbers.py`, `revision_analysis.py`, `time_energy_model.py`, `core_equivalents.py`, `revision_new_runs.py`, `make_paper_figures.py`, …) |
| `paper/litreview/` | methodology audit of 30 FPGA-vs-CPU papers: codes with verbatim quotes, exclusions, statistics |
| `docs/DATA_DICTIONARY.md` | fields of `meta.json`, `power.csv` and `stdout.log` |

## Reproduce the paper's numbers

```bash
git clone <this repository> && cd <repository>
# download 03_analysis_inputs.zip and 04_results_revision_part*.zip from the Zenodo record, then:
for z in ~/Downloads/0[34]_*.zip; do unzip -q -o "$z"; done     # creates paper/inputs/
cd paper/scripts
python3 paper_numbers.py && python3 revision_analysis.py && python3 time_energy_model.py \
  && python3 core_equivalents.py && python3 revision_new_runs.py && python3 make_paper_figures.py
```
Python 3.10+ with numpy, pandas, matplotlib, openpyxl. Results appear in `paper/data/` and `paper/figures/`.

## Run the measurements on your own machine

Needs an Alveo U50 (or edit `config.sh`), Vitis/XRT 2023.1, Linux with RAPL access, `numactl`, and
optionally `turbostat`. See `benchmark/README.md` for the full description; in short:

```bash
cd benchmark
./build_v2.sh            # build CPU baselines, hosts and bitstreams (hours)
./run_all.sh             # main campaign
taskset -c 95 ./revision_measurements.sh all      # supplementary measurements (core scaling, telemetry, ...)
```

## Citation

See `CITATION.cff`, or cite the paper and the Zenodo dataset.
