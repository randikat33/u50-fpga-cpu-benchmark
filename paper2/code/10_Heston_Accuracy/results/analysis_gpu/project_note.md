# Paper 2, Path B: GPU measurements analysed (20 Oct 2026)

Script: scripts/analyse_gpu.py -> results/analysis_gpu/{analysis_gpu.md, analysis_gpu.json, gpu_vs_predicted.csv}.
Inputs: phase5g_summary_s1.csv, phase5g_summary_s2.csv (650/650 each, 0 disturbed), registered predictions_gpu.json (commit ccc1471, Zenodo 10.5281/zenodo.23495010), Part A measured CSVs.

## Results
- Wait mode: block (lower steady energy in 29/29 steady keys of session 1; block/spin 0.887). Primary GPU mode = block.
- Reproducibility s1 vs s2: median 0.11%, max 8.6% (P1 1% block, ~60 us runs); 2/130 groups > 5%.
- HG5 FAILS (registered power rule): block one run 20/36, steady 11/36 within 30% (median meas/pred 1.27 / 1.51); time 29/36 (1.15). Cause: measured GPU power 130-153 W vs registered binding-pipe rule ~100 W. Exploratory mix rule: 34/36 and 36/36. Winners: steady 36/36, one run 35/36 (B 1% socket tie gpu_qe vs cpu_native_qe, 0.0047 J each).
- HG1 FAILS as predicted: 11/18 hold; fails III 1%, P1 (all), B (all). Best platform is the GPU everywhere (steady). Platform gain ~8.9-11.9 (13.3 Asian).
- HG2 HOLDS: GPU QE/FT time per path-step median 2.16 (2.02-3.04).
- HG3 HOLDS: GPU fixed cost block 0.142 ms (spin 0.058); card 0.651 ms, so < half the card. Range 0.058-0.694 ms; the larger values are at settings where a run does not fill the GPU.
- HG4 FAILS: 207/216 pairwise single-run comparisons right; all 9 wrong are cpu_native vs gpu at the shortest settings (P1, B, A). Cause: the GPU's fixed-cost energy (9.5 mJ measured) is far below P_sys x t0 (23.2 mJ). Exploratory E0 variant: 211/216.
- Open question: V100 needs less energy than U50 in 18/18 (GPU/card 0.075-0.154, median 0.110).
- GPU accuracy (10 runs): RMSE <= 1.1 eps in 35/36; B 0.3% QE strike 2 = 1.18 (10 runs only, not significant).

## Deviations to report
1. Both GPU sessions on 20 Oct 2026 (Section 22b says different days). Optional: short steady-state session on another day.
2. GPU 12.5x-pairs bias check (Section 22) not done before the measurements; to be run now and reported as an order deviation.

## Next
- GPU bias-check script for the Supermicro.
- Then frodo additions (Section 23), addendum v0.7, commit GPU results (no co-author line).

## GPU bias check (Section 22), run 20 Oct 2026 23:2x after the measurements (order deviation)
- scripts/gpu_bias_check.py; 12.5x pairs, block, registered binary (sha checked), seeds 9001-9006. Results: results/gpu_bias_check.csv, results/gpu_bias/.
- 36/36 settings meet the target (predicted RMSE/eps 0.41-0.93); bias over eps/sqrt2 at 0 settings (largest |bias|/eps 0.67, P1 1% QE strike 0, which agrees with the matched and native codes).
- Agreement with matched code: |z| <= 3 everywhere except P1 0.3% QE strike 1 (z = -3.04; one of 108 comparisons, about 0.3 expected by chance). With CPU-native: max |z| 2.38.
- B 0.3% QE strike 1: GPU bias +0.16 eps, so the 1.18 from the 10 measurement runs was noise.
