# Paper 2: measured results

Energy to a target accuracy for Heston Monte Carlo on the AMD Alveo U50 card and on the CPU (frodo). Everything here was measured after the predictions were registered:

- main cases: predictions in `../predictions/`, protocol v0.4;
- secondary cases: predictions in `../predictions_s/`, protocol v0.5.

| Folder | Contents |
|---|---|
| `main/` | Cases I, II, III and P1: the two session summaries (`phase5_summary_s1.csv`, `_s2.csv`), run logs, event logs (waits, repeats), card check, `analysis/` (analysis.md, analysis.json, measured_vs_predicted.csv), and `phase5_runs.tar.gz` (every run's metadata, stdout and power trace). |
| `secondary/` | Case B (8-asset basket) and Case A (Asian option, control variate): the same items, with `analysis_s/` and `phase5s_runs.tar.gz`. |
| `figures/` | fig1 energy to target, fig2 method gain against platform gain, fig3 measured against predicted (PDF and PNG), and `fig2_points.json`. |
| `../code/10_Heston_Accuracy/` | Source snapshot: kernels, host, CPU programs, tests and the scripts that produced every file here. |

To reproduce the analyses from the summaries, run these from `code/10_Heston_Accuracy` after copying the summaries and prediction files into its `results/` folder:

```bash
python3 scripts/analyse.py
python3 scripts/analyse_s.py
python3 scripts/make_figures.py
```

The secondary analysis compares only against the registered prediction variant `same`. The variant `informed` is reported alongside it for information.
