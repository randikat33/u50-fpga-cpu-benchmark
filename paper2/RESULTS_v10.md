# Paper 2, addendum v0.10: results (the RTX 4070 Ti SUPER's fp64 pipe)

Registered in addendum v0.10 (GitHub commit f50de656, 28 Oct 2026; Zenodo 10.5281/zenodo.23739777). The microbenchmark ran after the registration, on csetuf04 (SM locked at 2,340 MHz; the guard `scripts/guard.py --once` reported QUIET).

## Files

- **`results/rtx4070tis/v10/fp64_ubench_rtx.txt`:** the registered run (4 measured repeats per operation after a sizing run).
- **`fp64_ubench_rtx_BUSY_not_used.txt` and `fp64_ubench_rtx_BUSY_not_used_2.txt`:** two earlier runs, made while the guard reported BUSY (another user's container, without GPU access). They are not used, and they agree with the registered run within about 1–3%.
- **`hs_gpu_rtx.sass.xz`:** the SASS of the registered RTX binary (`build/hs_gpu`, SHA-256 d994d172…; the uncompressed `.sass` has SHA-256 9d2780e7…).
- **`results/analysis_rtx_v10/recount_fp64_rtx4070tis.{md,json}`:** HF2 and HF3. Made with `scripts/recount_fp64.py --sass hs_gpu_rtx.sass --fp64-rate 1.684 --cvt-rate 1.855 --e-fp64-pj 81.12 --e-cvt-pj 56.845`.
- **`recount_fp64_v100.{md,json}`:** the descriptive preview with the V100 binary's counts, disclosed in the protocol.

## Microbenchmark (medians of 4; idle with the context open: 38.9 W)

| Operation | Thread operations per SM per clock | Energy per operation above idle |
|---|---|---|
| DFMA | 1.684 | 81.1 pJ |
| DADD | 1.684 | 73.1 pJ |
| F2F.F64.F32 + F2F.F32.F64 | 1.855 | 60.4 pJ (56.8 pJ with the loop's FADD removed at 7.12 pJ per two conversions) |

## Hypotheses

- **HF1 holds.** The 64-bit conversion throughput is 1.855 per SM per clock, at most 1.25 × DFMA = 2.105: the conversions run at the fp64 rate, not at 16 per SM per clock. DFMA is 1.684, inside the registered 1.5–2.5.
- **HF2 holds.** With 64-bit conversions on the fp64 pipe at 1.684 (shared variant), the time per path-step of the 12 fp64-bound settings is within 15% at 12 of 12 (median measured/predicted 0.901; registered model 1.985). The separate-pipe variant fits poorly (median 1.671, 0 of 12), so the conversions share the fp64 unit.
- **HF3 holds.** With the recount and the measured energies per fp64-pipe operation, the steady-state energy per option is within 30% at 36 of 36 settings (registered criterion ≥ 30; HR1 had 27): median 1.012, 33 of 36 within 15%. The fp64-bound settings alone are 12 of 12 within 30% (median 1.083).

**64-bit F2F counts in the RTX binary:**

| Kernel | Per step | Per pair | Per asset |
|---|---|---|---|
| Asian plain | 2 | 4 | – |
| Asian CV | 4 | 4 | – |
| Heston | – | 18 | – |
| Basket | – | 15 | 3 |

These are the same counts as in the V100 binary.

## Not registered

- **Sensitivity of HF3.** Against the registered RTX idle of 10.75 W instead of the microbenchmark's 38.9 W, the per-operation energies become e_fp64 190 pJ and e_cvt 155 pJ. HF3 is still 36 of 36 within 30%, median 0.87.
- **Still unexplained.** Kernels that are not fp64-bound run 1.28× slower than the model on this card (issue efficiency on Ada). Lower power offsets this in energy.

## Every setting (from recount_fp64_rtx4070tis.md)

| Case | Target | Method | binds (reg.) | binds (shared) | t meas/pred: registered | shared | separate | E meas/pred: registered | shared |
|---|---|---|---|---|---|---|---|---|---|
| A | 0.1% | cv | fp64 | fp64 | 2.012 | 0.892 | 1.694 | 2.516 | 1.194 |
| A | 0.1% | plain | fp64 | fp64 | 2.056 | 0.895 | 1.731 | 1.821 | 1.083 |
| A | 0.3% | cv | fp64 | fp64 | 2.012 | 0.892 | 1.694 | 2.516 | 1.194 |
| A | 0.3% | plain | fp64 | fp64 | 2.056 | 0.895 | 1.731 | 1.821 | 1.083 |
| A | 1% | cv | fp64 | fp64 | 2.012 | 0.892 | 1.694 | 2.516 | 1.194 |
| A | 1% | plain | fp64 | fp64 | 2.056 | 0.895 | 1.731 | 1.821 | 1.083 |
| B | 0.1% | ft | issue | issue | 1.343 | 1.343 | 1.343 | 1.075 | 1.062 |
| B | 0.1% | qe | issue | issue | 1.064 | 1.064 | 1.064 | 0.917 | 0.881 |
| B | 0.3% | ft | issue | issue | 1.348 | 1.348 | 1.348 | 1.092 | 1.044 |
| B | 0.3% | qe | issue | fp64 | 1.539 | 0.963 | 1.539 | 1.113 | 0.926 |
| B | 1% | ft | issue | fp64 | 1.364 | 1.318 | 1.364 | 1.110 | 1.018 |
| B | 1% | qe | fp64 | fp64 | 1.958 | 0.918 | 1.648 | 1.290 | 0.941 |
| I | 0.1% | ft | issue | issue | 1.301 | 1.301 | 1.301 | 1.024 | 1.024 |
| I | 0.1% | qe | xu | xu | 1.323 | 1.326 | 1.326 | 0.967 | 0.962 |
| I | 0.3% | ft | issue | issue | 1.283 | 1.283 | 1.283 | 1.015 | 1.014 |
| I | 0.3% | qe | xu | issue | 1.310 | 1.312 | 1.312 | 0.954 | 0.935 |
| I | 1% | ft | issue | issue | 1.281 | 1.281 | 1.281 | 1.013 | 1.010 |
| I | 1% | qe | issue | issue | 1.284 | 1.284 | 1.284 | 0.958 | 0.919 |
| II | 0.1% | ft | issue | issue | 1.286 | 1.286 | 1.286 | 1.014 | 1.014 |
| II | 0.1% | qe | xu | xu | 1.305 | 1.309 | 1.309 | 0.943 | 0.937 |
| II | 0.3% | ft | issue | issue | 1.285 | 1.285 | 1.285 | 1.014 | 1.013 |
| II | 0.3% | qe | xu | issue | 1.276 | 1.277 | 1.277 | 0.935 | 0.909 |
| II | 1% | ft | issue | issue | 1.282 | 1.282 | 1.282 | 1.012 | 1.010 |
| II | 1% | qe | xu | issue | 1.276 | 1.277 | 1.277 | 0.935 | 0.909 |
| III | 0.1% | ft | issue | issue | 1.281 | 1.281 | 1.281 | 1.035 | 1.033 |
| III | 0.1% | qe | issue | issue | 1.335 | 1.335 | 1.335 | 1.031 | 0.990 |
| III | 0.3% | ft | issue | issue | 1.281 | 1.281 | 1.281 | 1.035 | 1.029 |
| III | 0.3% | qe | issue | issue | 1.330 | 1.330 | 1.330 | 1.049 | 0.970 |
| III | 1% | ft | issue | issue | 1.283 | 1.283 | 1.283 | 1.039 | 1.016 |
| III | 1% | qe | issue | issue | 1.330 | 1.330 | 1.330 | 1.049 | 0.970 |
| P1 | 0.1% | ft | issue | issue | 1.288 | 1.288 | 1.288 | 1.050 | 0.994 |
| P1 | 0.1% | qe | fp64 | fp64 | 1.709 | 0.931 | 1.439 | 1.158 | 0.900 |
| P1 | 0.3% | ft | fp64 | fp64 | 1.696 | 0.924 | 1.428 | 1.265 | 0.969 |
| P1 | 0.3% | qe | fp64 | fp64 | 1.666 | 0.908 | 1.403 | 1.411 | 0.959 |
| P1 | 1% | ft | fp64 | fp64 | 1.673 | 0.911 | 1.409 | 1.515 | 1.013 |
| P1 | 1% | qe | fp64 | fp64 | 1.669 | 0.909 | 1.405 | 2.117 | 1.105 |
