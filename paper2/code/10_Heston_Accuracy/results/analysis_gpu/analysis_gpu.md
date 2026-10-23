# GPU measurements against the registered GPU prediction (protocol v0.6)

- Sessions: 2 x 650 jobs, all done; GPU sessions ran on the same day (deviation from Section 22b).
- Wait mode (session-1 steady state, primary boundary): **block**; block lower in 29 of 29 steady keys, geometric mean block/spin 0.887.
- Reproducibility (energy per run, session 1 vs 2): median |diff| 0.11%, max 8.6%, 2 of 130 groups over 5%.

## HG5 (GPU prediction): energy within 30% at the primary boundary (GPU + socket 1)

| Power rule | Wait | One run | Steady state | Time | median meas/pred, one run | steady | time |
|---|---|---|---|---|---|---|---|
| registered | spin | 22/36 | 8/36 | 32/36 | 1.23 | 1.58 | 1.03 |
| registered | block | 20/36 | 11/36 | 29/36 | 1.27 | 1.51 | 1.15 |
| mix | spin | 33/36 | 36/36 | 32/36 | 0.94 | 1.11 | 1.03 |
| mix | block | 34/36 | 36/36 | 29/36 | 0.99 | 1.04 | 1.15 |

- Lowest-energy combination right: steady state 36 of 36, one run 35 of 36.
- **HG5 fails** (registered power rule, block mode).

## Winners with three platforms (measured; GPU in its primary mode)

| Case | Target | Boundary | One run | Steady state | Predicted steady |
|---|---|---|---|---|---|
| I | 1% | socket | gpu_qe | gpu_qe | gpu_qe |
| I | 1% | server | gpu_qe | gpu_qe | gpu_qe |
| I | 0.3% | socket | gpu_qe | gpu_qe | gpu_qe |
| I | 0.3% | server | gpu_qe | gpu_qe | gpu_qe |
| I | 0.1% | socket | gpu_qe | gpu_qe | gpu_qe |
| I | 0.1% | server | gpu_qe | gpu_qe | gpu_qe |
| II | 1% | socket | gpu_qe | gpu_qe | gpu_qe |
| II | 1% | server | gpu_qe | gpu_qe | gpu_qe |
| II | 0.3% | socket | gpu_qe | gpu_qe | gpu_qe |
| II | 0.3% | server | gpu_qe | gpu_qe | gpu_qe |
| II | 0.1% | socket | gpu_qe | gpu_qe | gpu_qe |
| II | 0.1% | server | gpu_qe | gpu_qe | gpu_qe |
| III | 1% | socket | gpu_qe | gpu_qe | gpu_qe |
| III | 1% | server | gpu_qe | gpu_qe | gpu_qe |
| III | 0.3% | socket | gpu_qe | gpu_qe | gpu_qe |
| III | 0.3% | server | gpu_qe | gpu_qe | gpu_qe |
| III | 0.1% | socket | gpu_qe | gpu_qe | gpu_qe |
| III | 0.1% | server | gpu_qe | gpu_qe | gpu_qe |
| P1 | 1% | socket | cpu_native_qe | gpu_qe | gpu_qe |
| P1 | 1% | server | gpu_qe | gpu_qe | gpu_qe |
| P1 | 0.3% | socket | gpu_qe | gpu_qe | gpu_qe |
| P1 | 0.3% | server | gpu_qe | gpu_qe | gpu_qe |
| P1 | 0.1% | socket | gpu_qe | gpu_qe | gpu_qe |
| P1 | 0.1% | server | gpu_qe | gpu_qe | gpu_qe |
| B | 1% | socket | gpu_qe | gpu_qe | gpu_qe |
| B | 1% | server | gpu_qe | gpu_qe | gpu_qe |
| B | 0.3% | socket | gpu_qe | gpu_qe | gpu_qe |
| B | 0.3% | server | gpu_qe | gpu_qe | gpu_qe |
| B | 0.1% | socket | gpu_qe | gpu_qe | gpu_qe |
| B | 0.1% | server | gpu_qe | gpu_qe | gpu_qe |
| A | 1% | socket | cpu_native_cv | gpu_cv | gpu_cv |
| A | 1% | server | cpu_native_cv | gpu_cv | gpu_cv |
| A | 0.3% | socket | cpu_native_cv | gpu_cv | gpu_cv |
| A | 0.3% | server | cpu_native_cv | gpu_cv | gpu_cv |
| A | 0.1% | socket | gpu_cv | gpu_cv | gpu_cv |
| A | 0.1% | server | gpu_cv | gpu_cv | gpu_cv |

## HG1: method gain against platform gain (steady state, primary boundaries)

| Case | Target | Method gain | Platform gain | Best platform | Holds |
|---|---|---|---|---|---|
| I | 1% | 16.8 | 8.9 | gpu | yes |
| I | 0.3% | 33.3 | 8.9 | gpu | yes |
| I | 0.1% | 33.0 | 8.9 | gpu | yes |
| II | 1% | 17.4 | 8.9 | gpu | yes |
| II | 0.3% | 69.7 | 8.9 | gpu | yes |
| II | 0.1% | 67.4 | 8.9 | gpu | yes |
| III | 1% | 4.1 | 9.1 | gpu | no |
| III | 0.3% | 16.2 | 9.1 | gpu | yes |
| III | 0.1% | 32.2 | 9.3 | gpu | yes |
| P1 | 1% | 2.9 | 11.9 | gpu | no |
| P1 | 0.3% | 2.1 | 9.8 | gpu | no |
| P1 | 0.1% | 4.1 | 10.3 | gpu | no |
| B | 1% | 3.3 | 6.5 | gpu | no |
| B | 0.3% | 3.5 | 8.1 | gpu | no |
| B | 0.1% | 4.1 | 10.8 | gpu | no |
| A | 1% | 126.0 | 13.3 | gpu | yes |
| A | 0.3% | 347.7 | 13.3 | gpu | yes |
| A | 0.1% | 347.7 | 13.3 | gpu | yes |

**HG1 fails** (11 of 18 settings).

## HG2, HG3, HG4

- **HG2 holds**: GPU QE/FT time per path-step, median 2.16 (range 2.02-3.04); registered range 2.06-3.72.
- **HG3 holds**: GPU fixed cost per run, block 0.142 ms (range 0.058-0.694, 25 short settings); spin 0.058 ms, block 0.142 ms.
- **HG4 fails**: break-even rule right in 207 of 216 pairwise single-run comparisons; fixed costs (ms): card socket 0.651, card server 0.651, cpu_native socket 0.007, cpu_native server 0.034, gpu socket 0.142, gpu server 0.142

  - wrong: socket P1 0.3% qe cpu_native-gpu (measured ratio 2.641)
  - wrong: socket B 1% ft cpu_native-gpu (measured ratio 1.238)
  - wrong: socket B 1% qe cpu_native-gpu (measured ratio 1.011)
  - wrong: socket A 0.1% cv cpu_native-gpu (measured ratio 1.124)
  - wrong: server P1 1% ft cpu_native-gpu (measured ratio 1.499)
  - wrong: server P1 1% qe cpu_native-gpu (measured ratio 1.440)
  - wrong: server B 1% ft cpu_native-gpu (measured ratio 1.550)
  - wrong: server B 1% qe cpu_native-gpu (measured ratio 1.715)
  - wrong: server A 0.1% cv cpu_native-gpu (measured ratio 1.273)
- HG3 detail: card fixed cost 0.651 ms, so the 'less than half the card' part holds. GPU fixed-cost energy measured 9.49 mJ (socket), against P_sys x t0 = 23.20 mJ.
- Exploratory (not registered): the same rule with the measured fixed-cost energy E0 = E_run - e W in place of P_sys x t0 is right in 211 of 216; E0 (mJ): card socket 47.55, card server 76.29, cpu_native socket 1.09, cpu_native server 9.75, gpu socket 9.49, gpu server 12.83
  - wrong: socket B 1% ft cpu_native-gpu (measured ratio 1.238)
  - wrong: socket B 1% qe cpu_native-gpu (measured ratio 1.011)
  - wrong: server P1 1% ft cpu_native-gpu (measured ratio 1.499)
  - wrong: server P1 1% qe cpu_native-gpu (measured ratio 1.440)
  - wrong: server B 1% qe cpu_native-gpu (measured ratio 1.715)
- GPU fixed cost per short setting (ms): P1 1% qe 0.058, P1 1% ft 0.062, B 1% qe 0.069, P1 0.3% qe 0.105, B 1% ft 0.108, III 1% qe 0.109, I 1% qe 0.11, B 0.3% qe 0.12, P1 0.3% ft 0.137, A 1% plain 0.139, B 0.3% ft 0.139, III 1% ft 0.139, P1 0.1% qe 0.142, II 1% qe 0.144, III 0.3% qe 0.146, I 0.3% qe 0.148, A 1% cv 0.158, A 0.1% cv 0.16, A 0.3% cv 0.16, A 0.3% plain 0.192, I 1% ft 0.347, P1 0.1% ft 0.352, B 0.1% qe 0.379, II 0.3% qe 0.394, II 1% ft 0.694

## Open question: V100 against U50 (steady state, primary boundaries, better method each)

The V100 needs less energy in 18 of 18 case-target pairs; GPU/card energy 0.075-0.154 (median 0.110).

## Accuracy of the GPU code (10 runs per setting)

- RMSE within 1.1 x target at all strikes in 35 of 36 settings.
  - B 0.3% qe: RMSE/eps 0.27, 1.18, 0.51; mean error/eps -0.02, +0.47, +0.30
