# RTX 4070 Ti SUPER: the registered tests (addendum v0.9, Part B)

Sessions 1, 2; block wait; 29 steady-state runs x 5 repeats per session; SM locked at 2,340 MHz. Measured value per run key: the median over all repeats. II FT 0.1% (M 30,720, over 1e11 path-steps, so no steady-state run of its own) uses the M 7,680 run, as marked. Prediction SHA-256 4bee4fe5.

## Verdicts

- **HR1 (energy):** 27 of 36 within 30% (needs 30): **fails**. Median measured / predicted 1.049, range 0.917-2.516.
- **HR2 (Proposition 2):** the measured verdict agrees at 15 of 15 called pairs: **holds**. Inside the band (reported only): 3 of 3 agree.
- **HR3 (rate):** 13 of 36 within 30% (needs 30): **fails**. Median measured / predicted time per path-step 1.330, range 1.064-2.056.
- **Open question:** the RTX needs less energy than the V100 at 13 of 18 pairs at their own hosts (predicted 18); at a matched host (frodo's socket) at 12 of 18 (predicted 14).

## Every setting (steady state, GPU + CPU package)

| Case | Target | Method | M | Bound (pred.) | E meas (J) | E pred (J) | E meas/pred | t meas/pred | P_gpu meas (W) | P_gpu pred (W) |
|---|---|---|---|---|---|---|---|---|---|---|
| I | 1% | ft | 1280 | issue | 0.01694 | 0.01672 | 1.013 | 1.281 | 135.5 | 173.7 |
| I | 0.3% | ft | 5120 | issue | 0.7475 | 0.7366 | 1.015 | 1.283 | 135.6 | 173.8 |
| I | 0.1% | ft | 20480 | issue | 27.1 | 26.47 | 1.024 | 1.301 | 134.8 | 173.8 |
| I | 1% | qe | 40 | issue | 0.001116 | 0.001165 | 0.958 | 1.284 | 122.6 | 167.5 |
| I | 0.3% | qe | 80 | xu | 0.02437 | 0.02554 | 0.954 | 1.310 | 119.5 | 167.4 |
| I | 0.1% | qe | 320 | xu | 0.8829 | 0.913 | 0.967 | 1.323 | 119.7 | 167.1 |
| II | 1% | ft | 1920 | issue | 0.08073 | 0.07975 | 1.012 | 1.282 | 135.4 | 173.7 |
| II | 0.3% | ft | 7680 | issue | 3.579 | 3.53 | 1.014 | 1.285 | 135.2 | 173.8 |
| II | 0.1% | ft | 30720 (run 7680) | issue | 128.8 | 127 | 1.014 | 1.286 | 135.2 | 173.8 |
| II | 1% | qe | 60 | xu | 0.00505 | 0.005403 | 0.935 | 1.276 | 120.3 | 167.5 |
| II | 0.3% | qe | 60 | xu | 0.05605 | 0.05997 | 0.935 | 1.276 | 120.3 | 167.5 |
| II | 0.1% | qe | 240 | xu | 2.055 | 2.179 | 0.943 | 1.305 | 118.2 | 167.2 |
| III | 1% | ft | 160 | issue | 0.003844 | 0.003699 | 1.039 | 1.283 | 138.9 | 173.5 |
| III | 0.3% | ft | 640 | issue | 0.1677 | 0.162 | 1.035 | 1.281 | 138.7 | 173.7 |
| III | 0.1% | ft | 2560 | issue | 6.013 | 5.81 | 1.035 | 1.281 | 138.7 | 173.7 |
| III | 1% | qe | 20 | issue | 0.001028 | 0.0009799 | 1.049 | 1.330 | 129.8 | 167.2 |
| III | 0.3% | qe | 20 | issue | 0.0114 | 0.01087 | 1.049 | 1.330 | 129.8 | 167.2 |
| III | 0.1% | qe | 40 | issue | 0.2018 | 0.1958 | 1.031 | 1.335 | 127.2 | 167.5 |
| P1 | 1% | ft | 8 | fp64 | 0.0001629 | 0.0001076 | 1.515 | 1.673 | 73.9 | 82.7 |
| P1 | 0.3% | ft | 16 | fp64 | 0.002311 | 0.001827 | 1.265 | 1.696 | 101.5 | 138.9 |
| P1 | 0.1% | ft | 64 | issue | 0.0589 | 0.05608 | 1.050 | 1.288 | 139.5 | 173.1 |
| P1 | 1% | qe | 1 | fp64 | 0.0001131 | 5.344e-05 | 2.117 | 1.669 | 54.1 | 41.0 |
| P1 | 0.3% | qe | 4 | fp64 | 0.001559 | 0.001105 | 1.411 | 1.666 | 72.5 | 87.2 |
| P1 | 0.1% | qe | 8 | fp64 | 0.01911 | 0.01651 | 1.158 | 1.709 | 98.1 | 148.8 |
| B | 1% | ft | 8 | issue | 0.0005025 | 0.0004529 | 1.110 | 1.364 | 137.8 | 171.3 |
| B | 0.3% | ft | 16 | issue | 0.01013 | 0.009277 | 1.092 | 1.348 | 137.8 | 172.2 |
| B | 0.1% | ft | 64 | issue | 0.3379 | 0.3143 | 1.075 | 1.343 | 136.7 | 172.9 |
| B | 1% | qe | 1 | fp64 | 0.0002185 | 0.0001694 | 1.290 | 1.958 | 86.4 | 135.4 |
| B | 0.3% | qe | 2 | issue | 0.003431 | 0.003083 | 1.113 | 1.539 | 117.6 | 165.8 |
| B | 0.1% | qe | 8 | issue | 0.0867 | 0.0945 | 0.917 | 1.064 | 142.7 | 167.1 |
| A | 1% | plain | 250 | fp64 | 0.002694 | 0.001479 | 1.821 | 2.056 | 67.1 | 76.8 |
| A | 0.3% | plain | 250 | fp64 | 0.02975 | 0.01633 | 1.821 | 2.056 | 67.1 | 76.8 |
| A | 0.1% | plain | 250 | fp64 | 0.2677 | 0.147 | 1.821 | 2.056 | 67.1 | 76.8 |
| A | 1% | cv | 250 | fp64 | 3.207e-05 | 1.275e-05 | 2.516 | 2.012 | 55.8 | 43.1 |
| A | 0.3% | cv | 250 | fp64 | 0.0001283 | 5.098e-05 | 2.516 | 2.012 | 55.8 | 43.1 |
| A | 0.1% | cv | 250 | fp64 | 0.001155 | 0.0004588 | 2.516 | 2.012 | 55.8 | 43.1 |

## HR2: Proposition 2 with the measured RTX energies (socket boundary)

| Case | Target | best / runner-up | kappa | G | margin meas | margin pred | verdict meas | verdict pred | called | agree |
|---|---|---|---|---|---|---|---|---|---|---|
| I | 1% | rtx / card | 2.08 | 14.26 | 1.09 | 1.06 | method | method | no | yes |
| I | 0.3% | rtx / card | 2.08 | 14.26 | 2.16 | 2.11 | method | method | yes | yes |
| I | 0.1% | rtx / card | 2.08 | 14.26 | 2.15 | 2.10 | method | method | yes | yes |
| II | 1% | rtx / card | 2.06 | 14.24 | 1.13 | 1.11 | method | method | no | yes |
| II | 0.3% | rtx / card | 2.06 | 14.24 | 4.50 | 4.43 | method | method | yes | yes |
| II | 0.1% | rtx / card | 2.06 | 14.24 | 4.40 | 4.34 | method | method | yes | yes |
| III | 1% | rtx / card | 2.24 | 14.65 | 0.26 | 0.25 | platform | platform | yes | yes |
| III | 0.3% | rtx / card | 2.24 | 14.65 | 1.04 | 1.01 | method | method | no | yes |
| III | 0.1% | rtx / card | 2.24 | 14.65 | 2.03 | 1.97 | method | method | yes | yes |
| P1 | 1% | rtx / card | 2.66 | 13.12 | 0.25 | 0.24 | platform | platform | yes | yes |
| P1 | 0.3% | rtx / card | 2.66 | 13.12 | 0.12 | 0.12 | platform | platform | yes | yes |
| P1 | 0.1% | rtx / card | 2.66 | 13.12 | 0.23 | 0.22 | platform | platform | yes | yes |
| B | 1% | rtx / card | 2.06 | 17.37 | 0.23 | 0.22 | platform | platform | yes | yes |
| B | 0.3% | rtx / card | 2.06 | 17.37 | 0.23 | 0.21 | platform | platform | yes | yes |
| B | 0.1% | rtx / card | 2.06 | 17.37 | 0.22 | 0.21 | platform | platform | yes | yes |
| A | 1% | rtx / card | 1.73 | 7.27 | 11.56 | 6.35 | method | method | yes | yes |
| A | 0.3% | rtx / card | 1.73 | 7.27 | 31.90 | 17.52 | method | method | yes | yes |
| A | 0.1% | rtx / card | 1.73 | 7.27 | 31.90 | 17.52 | method | method | yes | yes |

## Open question: RTX against the V100 (both measured; steady state, better method each)

| Case | Target | V100 (J) | RTX (J) | RTX / V100 | predicted | matched host | predicted |
|---|---|---|---|---|---|---|---|
| I | 1% | 0.001745 | 0.001116 | 0.640 | 0.668 | 0.677 | 0.665 |
| I | 0.3% | 0.03869 | 0.02437 | 0.630 | 0.660 | 0.670 | 0.657 |
| I | 0.1% | 1.407 | 0.8829 | 0.627 | 0.649 | 0.667 | 0.646 |
| II | 1% | 0.007968 | 0.00505 | 0.634 | 0.678 | 0.673 | 0.674 |
| II | 0.3% | 0.08844 | 0.05605 | 0.634 | 0.678 | 0.673 | 0.674 |
| II | 0.1% | 3.277 | 2.055 | 0.627 | 0.665 | 0.668 | 0.661 |
| III | 1% | 0.001593 | 0.001028 | 0.645 | 0.615 | 0.679 | 0.617 |
| III | 0.3% | 0.01767 | 0.0114 | 0.645 | 0.615 | 0.679 | 0.617 |
| III | 0.1% | 0.3196 | 0.2018 | 0.631 | 0.613 | 0.668 | 0.615 |
| P1 | 1% | 5.934e-05 | 0.0001131 | 1.907 | 0.901 | 2.594 | 1.357 |
| P1 | 0.3% | 0.001534 | 0.001559 | 1.016 | 0.720 | 1.262 | 0.849 |
| P1 | 0.1% | 0.02425 | 0.01911 | 0.788 | 0.681 | 0.899 | 0.707 |
| B | 1% | 0.000253 | 0.0002185 | 0.864 | 0.670 | 1.016 | 0.707 |
| B | 0.3% | 0.004849 | 0.003431 | 0.708 | 0.636 | 0.772 | 0.647 |
| B | 0.1% | 0.1393 | 0.0867 | 0.623 | 0.679 | 0.652 | 0.691 |
| A | 1% | 1.754e-05 | 3.207e-05 | 1.828 | 0.726 | 2.432 | 1.063 |
| A | 0.3% | 7.018e-05 | 0.0001283 | 1.828 | 0.726 | 2.432 | 1.063 |
| A | 0.1% | 0.0006316 | 0.001155 | 1.828 | 0.726 | 2.432 | 1.063 |

## Lowest steady-state energy with the measured RTX (socket boundary)

| Case | Target | All five devices | Without the V100 |
|---|---|---|---|
| I | 1% | rtx_qe | rtx_qe |
| I | 0.3% | rtx_qe | rtx_qe |
| I | 0.1% | rtx_qe | rtx_qe |
| II | 1% | rtx_qe | rtx_qe |
| II | 0.3% | rtx_qe | rtx_qe |
| II | 0.1% | rtx_qe | rtx_qe |
| III | 1% | rtx_qe | rtx_qe |
| III | 0.3% | rtx_qe | rtx_qe |
| III | 0.1% | rtx_qe | rtx_qe |
| P1 | 1% | gpu_qe | rtx_qe |
| P1 | 0.3% | gpu_qe | rtx_qe |
| P1 | 0.1% | rtx_qe | rtx_qe |
| B | 1% | rtx_qe | rtx_qe |
| B | 0.3% | rtx_qe | rtx_qe |
| B | 0.1% | rtx_qe | rtx_qe |
| A | 1% | gpu_cv | rtx_cv |
| A | 0.3% | gpu_cv | rtx_cv |
| A | 0.1% | gpu_cv | rtx_cv |

Session 2 / session 1 energy per path-step over the 29 run keys: median 1.0014, range 0.9893-1.0204.
