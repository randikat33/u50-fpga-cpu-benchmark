# RTX 4070 Ti SUPER prediction (addendum v0.9), registered before any timed run of a pricing kernel on the card

Generated 2026-10-27T10:34:55Z. 66 SMs at a locked 2340 MHz; eta = 0.928 (fp32 0.821, int 0.982, sfu 0.991); t0 (block) 31.9 us; blocks per SM {'heston_ft': 6, 'heston_qe': 4, 'basket_ft': 4, 'basket_qe': 4, 'asian_plain': 5, 'asian_cv': 3}.

Energy per thread instruction: fma 7.12 pJ, alu 9.66 pJ, xu 18.51 pJ. GPU idle 10.8 W, start-up (block) 35.0 W; CPU package idle 8.0 W, start-up (block) 31.1 W.

## Steady state per kernel and time step (primary boundary: GPU + CPU package)

| Kernel | M (steps) | Rate (G path-steps/s) | Binding | P_gpu (W) | P_gpu, v0.6 mix (W) | nJ per path-step |
|---|---|---|---|---|---|---|
| heston_ft | 1280 | 402.3 | issue | 173.7 | 165.0 | 0.452 |
| heston_ft | 5120 | 402.9 | issue | 173.8 | 165.1 | 0.451 |
| heston_ft | 20480 | 403.1 | issue | 173.8 | 165.1 | 0.451 |
| heston_qe | 40 | 172.7 | issue | 167.5 | 156.0 | 1.016 |
| heston_qe | 80 | 174.7 | xu | 167.4 | 155.9 | 1.004 |
| heston_qe | 320 | 175.9 | xu | 167.1 | 155.6 | 0.995 |
| heston_ft | 1920 | 402.6 | issue | 173.7 | 165.1 | 0.451 |
| heston_ft | 7680 | 403.0 | issue | 173.8 | 165.1 | 0.451 |
| heston_ft | 30720 | 403.1 | issue | 173.8 | 165.1 | 0.451 |
| heston_qe | 60 | 174.1 | xu | 167.5 | 156.1 | 1.008 |
| heston_qe | 240 | 175.8 | xu | 167.2 | 155.6 | 0.996 |
| heston_ft | 160 | 396.9 | issue | 173.5 | 164.8 | 0.457 |
| heston_ft | 640 | 401.5 | issue | 173.7 | 165.0 | 0.452 |
| heston_ft | 2560 | 402.7 | issue | 173.7 | 165.1 | 0.451 |
| heston_qe | 20 | 168.4 | issue | 167.2 | 155.9 | 1.040 |
| heston_ft | 8 | 138.9 | fp64 | 82.7 | 79.1 | 0.652 |
| heston_ft | 16 | 277.9 | fp64 | 138.9 | 132.2 | 0.528 |
| heston_ft | 64 | 387.9 | issue | 173.1 | 164.5 | 0.467 |
| heston_qe | 1 | 17.4 | fp64 | 41.0 | 39.3 | 2.821 |
| heston_qe | 4 | 69.5 | fp64 | 87.2 | 82.1 | 1.370 |
| heston_qe | 8 | 138.9 | fp64 | 148.8 | 139.1 | 1.128 |
| basket_ft | 8 | 45.4 | issue | 171.3 | 161.9 | 3.949 |
| basket_ft | 16 | 49.0 | issue | 172.2 | 162.7 | 3.674 |
| basket_ft | 64 | 52.2 | issue | 172.9 | 163.3 | 3.467 |
| basket_qe | 1 | 11.7 | fp64 | 135.4 | 126.9 | 12.257 |
| basket_qe | 2 | 17.5 | issue | 165.8 | 154.7 | 9.912 |
| basket_qe | 8 | 20.9 | issue | 167.1 | 155.5 | 8.381 |
| asian_plain | 250 | 265.8 | fp64 | 76.8 | 73.7 | 0.319 |
| asian_cv | 250 | 128.3 | fp64 | 43.1 | 41.3 | 0.398 |

## Every setting: time and energy at the primary boundary (block wait)

| Case | Target | Method | Run time (ms) | Bound | P_gpu (W) | E one run (J) | E steady (J) | E steady, frodo's host (J) |
|---|---|---|---|---|---|---|---|---|
| I | 1% | ft | 0.247 | latency | 75.2 | 0.02127 | 0.01672 | 0.02051 |
| I | 0.3% | ft | 5.185 | issue | 172.9 | 0.9385 | 0.7366 | 0.9035 |
| I | 0.1% | ft | 154.564 | issue | 173.7 | 28.08 | 26.47 | 32.46 |
| I | 1% | qe | 0.048 | latency | 48.9 | 0.003443 | 0.001165 | 0.001439 |
| I | 0.3% | qe | 0.218 | xu | 148.0 | 0.03468 | 0.02554 | 0.03154 |
| I | 0.1% | qe | 5.441 | xu | 166.4 | 0.9491 | 0.913 | 1.128 |
| II | 1% | ft | 0.475 | issue | 163.2 | 0.08204 | 0.07975 | 0.09783 |
| II | 0.3% | ft | 23.216 | issue | 173.6 | 4.215 | 3.53 | 4.33 |
| II | 0.1% | ft | 710.851 | issue | 173.8 | 129.2 | 127 | 155.8 |
| II | 1% | qe | 0.064 | xu | 98.6 | 0.007549 | 0.005403 | 0.006671 |
| II | 0.3% | qe | 0.405 | xu | 157.1 | 0.0675 | 0.05997 | 0.07404 |
| II | 0.1% | qe | 12.580 | xu | 166.8 | 2.2 | 2.179 | 2.691 |
| III | 1% | ft | 0.059 | latency | 80.0 | 0.005945 | 0.003699 | 0.004538 |
| III | 0.3% | ft | 1.001 | issue | 169.3 | 0.1782 | 0.162 | 0.1988 |
| III | 0.1% | ft | 32.254 | issue | 173.6 | 5.857 | 5.81 | 7.127 |
| III | 1% | qe | 0.040 | latency | 52.0 | 0.003135 | 0.0009799 | 0.00121 |
| III | 0.3% | qe | 0.096 | issue | 123.3 | 0.01336 | 0.01087 | 0.01343 |
| III | 0.1% | qe | 1.159 | issue | 163.8 | 0.1999 | 0.1958 | 0.2417 |
| P1 | 1% | ft | 0.034 | fp64 | 36.2 | 0.002233 | 0.0001076 | 0.0001564 |
| P1 | 0.3% | ft | 0.055 | fp64 | 78.9 | 0.005538 | 0.001827 | 0.002339 |
| P1 | 0.1% | ft | 0.366 | issue | 161.1 | 0.06268 | 0.05608 | 0.06883 |
| P1 | 1% | qe | 0.034 | fp64 | 34.6 | 0.002179 | 5.344e-05 | 9.836e-05 |
| P1 | 0.3% | qe | 0.047 | fp64 | 52.1 | 0.003591 | 0.001105 | 0.001584 |
| P1 | 0.1% | qe | 0.141 | fp64 | 123.0 | 0.01919 | 0.01651 | 0.02085 |
| B | 1% | ft | 0.044 | latency | 37.7 | 0.002738 | 0.0004529 | 0.000557 |
| B | 0.3% | ft | 0.120 | issue | 135.7 | 0.018 | 0.009277 | 0.0114 |
| B | 0.1% | ft | 1.856 | issue | 170.6 | 0.332 | 0.3143 | 0.3859 |
| B | 1% | qe | 0.037 | latency | 35.9 | 0.002344 | 0.0001694 | 0.0002181 |
| B | 0.3% | qe | 0.063 | issue | 99.3 | 0.007469 | 0.003083 | 0.003814 |
| B | 0.1% | qe | 0.601 | issue | 160.1 | 0.1018 | 0.0945 | 0.1167 |
| A | 1% | plain | 0.064 | fp64 | 41.4 | 0.003881 | 0.001479 | 0.002198 |
| A | 0.3% | plain | 0.350 | fp64 | 73.0 | 0.02904 | 0.01633 | 0.02427 |
| A | 0.1% | plain | 1.780 | fp64 | 76.0 | 0.1502 | 0.147 | 0.2184 |
| A | 1% | cv | 0.098 | fp64 | 19.0 | 0.003375 | 1.275e-05 | 2.302e-05 |
| A | 0.3% | cv | 0.098 | fp64 | 19.0 | 0.003375 | 5.098e-05 | 9.208e-05 |
| A | 0.1% | cv | 0.098 | fp64 | 21.6 | 0.003633 | 0.0004588 | 0.0008287 |

## Predicted lowest-energy combination (card and CPUs measured, Part A; RTX predicted)

| Case | Target | Boundary | One run | Steady state |
|---|---|---|---|---|
| I | 1% | socket | rtx_qe | rtx_qe |
| I | 1% | server | rtx_qe | rtx_qe |
| I | 0.3% | socket | rtx_qe | rtx_qe |
| I | 0.3% | server | rtx_qe | rtx_qe |
| I | 0.1% | socket | rtx_qe | rtx_qe |
| I | 0.1% | server | rtx_qe | rtx_qe |
| II | 1% | socket | rtx_qe | rtx_qe |
| II | 1% | server | rtx_qe | rtx_qe |
| II | 0.3% | socket | rtx_qe | rtx_qe |
| II | 0.3% | server | rtx_qe | rtx_qe |
| II | 0.1% | socket | rtx_qe | rtx_qe |
| II | 0.1% | server | rtx_qe | rtx_qe |
| III | 1% | socket | rtx_qe | rtx_qe |
| III | 1% | server | rtx_qe | rtx_qe |
| III | 0.3% | socket | rtx_qe | rtx_qe |
| III | 0.3% | server | rtx_qe | rtx_qe |
| III | 0.1% | socket | rtx_qe | rtx_qe |
| III | 0.1% | server | rtx_qe | rtx_qe |
| P1 | 1% | socket | cpu_native_qe | rtx_qe |
| P1 | 1% | server | rtx_qe | rtx_qe |
| P1 | 0.3% | socket | rtx_qe | rtx_qe |
| P1 | 0.3% | server | rtx_qe | rtx_qe |
| P1 | 0.1% | socket | rtx_qe | rtx_qe |
| P1 | 0.1% | server | rtx_qe | rtx_qe |
| B | 1% | socket | rtx_qe | rtx_qe |
| B | 1% | server | rtx_qe | rtx_qe |
| B | 0.3% | socket | rtx_qe | rtx_qe |
| B | 0.3% | server | rtx_qe | rtx_qe |
| B | 0.1% | socket | rtx_qe | rtx_qe |
| B | 0.1% | server | rtx_qe | rtx_qe |
| A | 1% | socket | cpu_native_cv | rtx_cv |
| A | 1% | server | rtx_cv | rtx_cv |
| A | 0.3% | socket | cpu_native_cv | rtx_cv |
| A | 0.3% | server | rtx_cv | rtx_cv |
| A | 0.1% | socket | rtx_cv | rtx_cv |
| A | 0.1% | server | rtx_cv | rtx_cv |

## HR2: Proposition 2 (platforms card, CPU matched, CPU native, RTX; socket boundary)

| Case | Target | M | kappa | G | kappa G | margin | predicted | called |
|---|---|---|---|---|---|---|---|---|
| I | 1% | 32.3 | 2.21 | 13.79 | 30.4 | 1.06 | method | no |
| I | 0.3% | 64.2 | 2.21 | 13.79 | 30.4 | 2.11 | method | yes |
| I | 0.1% | 64.0 | 2.21 | 13.79 | 30.4 | 2.10 | method | yes |
| II | 1% | 33.0 | 2.21 | 13.43 | 29.7 | 1.11 | method | no |
| II | 0.3% | 131.6 | 2.21 | 13.43 | 29.7 | 4.43 | method | yes |
| II | 0.1% | 128.8 | 2.21 | 13.43 | 29.7 | 4.34 | method | yes |
| III | 1% | 8.6 | 2.25 | 15.10 | 34.0 | 0.25 | platform | yes |
| III | 0.3% | 34.3 | 2.25 | 15.10 | 34.0 | 1.01 | method | no |
| III | 0.1% | 66.8 | 2.25 | 15.10 | 34.0 | 1.97 | method | yes |
| P1 | 1% | 8.7 | 2.42 | 15.19 | 36.7 | 0.24 | platform | yes |
| P1 | 0.3% | 4.3 | 2.42 | 15.19 | 36.7 | 0.12 | platform | yes |
| P1 | 0.1% | 8.2 | 2.42 | 15.19 | 36.7 | 0.22 | platform | yes |
| B | 1% | 8.3 | 2.42 | 15.94 | 38.5 | 0.22 | platform | yes |
| B | 0.3% | 8.1 | 2.42 | 15.94 | 38.5 | 0.21 | platform | yes |
| B | 0.1% | 8.0 | 2.42 | 15.94 | 38.5 | 0.21 | platform | yes |
| A | 1% | 145.0 | 1.25 | 18.29 | 22.9 | 6.35 | method | yes |
| A | 0.3% | 400.2 | 1.25 | 18.29 | 22.9 | 17.52 | method | yes |
| A | 0.1% | 400.2 | 1.25 | 18.29 | 22.9 | 17.52 | method | yes |

## Open question: RTX 4070 Ti SUPER (predicted) against the V100 (measured), steady state, better method each

| Case | Target | V100 (J) | RTX (J) | RTX / V100 | RTX / V100, both with frodo's host |
|---|---|---|---|---|---|
| I | 1% | 0.001745 | 0.001165 | 0.668 | 0.665 |
| I | 0.3% | 0.03869 | 0.02554 | 0.660 | 0.657 |
| I | 0.1% | 1.407 | 0.913 | 0.649 | 0.646 |
| II | 1% | 0.007968 | 0.005403 | 0.678 | 0.674 |
| II | 0.3% | 0.08844 | 0.05997 | 0.678 | 0.674 |
| II | 0.1% | 3.277 | 2.179 | 0.665 | 0.661 |
| III | 1% | 0.001593 | 0.0009799 | 0.615 | 0.617 |
| III | 0.3% | 0.01767 | 0.01087 | 0.615 | 0.617 |
| III | 0.1% | 0.3196 | 0.1958 | 0.613 | 0.615 |
| P1 | 1% | 5.934e-05 | 5.344e-05 | 0.901 | 1.357 |
| P1 | 0.3% | 0.001534 | 0.001105 | 0.720 | 0.849 |
| P1 | 0.1% | 0.02425 | 0.01651 | 0.681 | 0.707 |
| B | 1% | 0.000253 | 0.0001694 | 0.670 | 0.707 |
| B | 0.3% | 0.004849 | 0.003083 | 0.636 | 0.647 |
| B | 0.1% | 0.1393 | 0.0945 | 0.679 | 0.691 |
| A | 1% | 1.754e-05 | 1.275e-05 | 0.726 | 1.063 |
| A | 0.3% | 7.018e-05 | 5.098e-05 | 0.726 | 1.063 |
| A | 0.1% | 0.0006316 | 0.0004588 | 0.726 | 1.063 |

Predicted: the RTX card needs less energy than the V100 at 18 of 18 case-target pairs (primary boundaries).

## Hypotheses: predicted verdicts

- **HR1** steady-state energy per option at the primary boundary (GPU + CPU package), block wait, within 30% of the prediction (pipe-energy rule) at >= 30 of the 36 settings
- **HR2** Proposition 2 with the predicted RTX energies gives the measured verdict (method gain above platform gain) at every called pair (predicted margin outside [1/1.3, 1.3]); pairs inside the band are reported, not tested (15 of 18 pairs called; method predicted to win at 11 of 18)
- **HR3** time per path-step at steady state within 30% of the prediction at >= 30 of the 36 settings
