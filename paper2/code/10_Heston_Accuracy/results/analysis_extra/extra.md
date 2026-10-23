# Extra analyses of the existing data (exploratory, not registered)

## 1. Prediction skill without the CPU

- Card energy within 20%, primary: 60 of 72; steady state: 70 of 72.

| Rule | Winner right, primary | Winner right, steady |
|---|---|---|
| registered model | 36 / 36 | 35 / 36 |
| better method on the card | 14 / 36 | 28 / 36 |
| better method on CPU-native | 22 / 36 | 8 / 36 |

## 2. Break-even rule (card wins one run iff W > W*)

- t0_0.53ms: 36 of 36 single-run comparisons (best method, card vs CPU-native) right.
- t0_0.70ms: 36 of 36 single-run comparisons (best method, card vs CPU-native) right.

| Case | Target | Boundary | W | W* (t0 0.53 ms) | W/W* | predicted | measured |
|---|---|---|---|---|---|---|---|
| A | 0.1% | server | 1.15e+06 | inf | 0.00 | cpu | cpu |
| A | 0.1% | socket | 1.15e+06 | 2.31e+07 | 0.05 | cpu | cpu |
| A | 0.3% | server | 1.28e+05 | inf | 0.00 | cpu | cpu |
| A | 0.3% | socket | 1.28e+05 | 2.31e+07 | 0.01 | cpu | cpu |
| A | 1% | server | 3.2e+04 | inf | 0.00 | cpu | cpu |
| A | 1% | socket | 3.2e+04 | 2.31e+07 | 0.00 | cpu | cpu |
| B | 0.1% | server | 1.13e+07 | inf | 0.00 | cpu | cpu |
| B | 0.1% | socket | 1.13e+07 | 1.77e+06 | 6.38 | card | card |
| B | 0.3% | server | 3.11e+05 | inf | 0.00 | cpu | cpu |
| B | 0.3% | socket | 3.11e+05 | 6.08e+05 | 0.51 | cpu | cpu |
| B | 1% | server | 1.38e+04 | 1.24e+06 | 0.01 | cpu | cpu |
| B | 1% | socket | 1.38e+04 | 3.02e+05 | 0.05 | cpu | cpu |
| I | 0.1% | server | 9.17e+08 | 1.45e+07 | 63.42 | card | card |
| I | 0.1% | socket | 9.17e+08 | 3.36e+06 | 272.70 | card | card |
| I | 0.3% | server | 2.54e+07 | 1.66e+07 | 1.53 | card | card |
| I | 0.3% | socket | 2.54e+07 | 3.46e+06 | 7.35 | card | card |
| I | 1% | server | 1.15e+06 | 2.01e+07 | 0.06 | cpu | cpu |
| I | 1% | socket | 1.15e+06 | 3.51e+06 | 0.33 | cpu | cpu |
| II | 0.1% | server | 2.19e+09 | 1.52e+07 | 144.28 | card | card |
| II | 0.1% | socket | 2.19e+09 | 3.34e+06 | 654.00 | card | card |
| II | 0.3% | server | 5.95e+07 | 1.91e+07 | 3.11 | card | card |
| II | 0.3% | socket | 5.95e+07 | 3.53e+06 | 16.85 | card | card |
| II | 1% | server | 5.36e+06 | 1.91e+07 | 0.28 | cpu | cpu |
| II | 1% | socket | 5.36e+06 | 3.53e+06 | 1.52 | card | card |
| III | 0.1% | server | 1.93e+08 | 2e+07 | 9.63 | card | card |
| III | 0.1% | socket | 1.93e+08 | 3.91e+06 | 49.34 | card | card |
| III | 0.3% | server | 1.04e+07 | 2.07e+07 | 0.51 | cpu | cpu |
| III | 0.3% | socket | 1.04e+07 | 3.76e+06 | 2.78 | card | card |
| III | 1% | server | 9.42e+05 | 2.07e+07 | 0.05 | cpu | cpu |
| III | 1% | socket | 9.42e+05 | 3.76e+06 | 0.25 | cpu | cpu |
| P1 | 0.1% | server | 1.46e+07 | inf | 0.00 | cpu | cpu |
| P1 | 0.1% | socket | 1.46e+07 | 8.34e+06 | 1.76 | card | card |
| P1 | 0.3% | server | 8.07e+05 | inf | 0.00 | cpu | cpu |
| P1 | 0.3% | socket | 8.07e+05 | 6.05e+06 | 0.13 | cpu | cpu |
| P1 | 1% | server | 1.89e+04 | inf | 0.00 | cpu | cpu |
| P1 | 1% | socket | 1.89e+04 | 3.39e+06 | 0.01 | cpu | cpu |

## 3. RQ2 under the work-optimal error split (same calibration grid)

| Case | Target | steps/yr FT, QE (registered → optimal) | W optimal / registered (FT, QE) | Method gain reg → opt | Platform gain | Margin reg → opt |
|---|---|---|---|---|---|---|
| I | 1% | 128, 4 → 128, 4 | 0.56, 0.50 | 15.6 → 17.2 | 1.93 | 8.07 → 8.92 |
| I | 0.3% | 512, 8 → 256, 8 | 0.52, 0.53 | 31.2 → 30.6 | 1.95 | 16.00 → 15.70 |
| I | 0.1% | 2048, 32 → 2048, 8 | 0.67, 0.26 | 30.9 → 80.8 | 1.96 | 15.74 → 41.15 |
| II | 1% | 128, 4 → 64, 2 | 0.52, 0.35 | 16.2 → 24.3 | 1.94 | 8.36 → 12.58 |
| II | 0.3% | 512, 4 → 256, 4 | 0.36, 0.63 | 64.7 → 36.9 | 1.94 | 33.43 → 19.09 |
| II | 0.1% | 2048, 16 → 1024, 16 | 0.74, 0.61 | 63.3 → 77.2 | 1.98 | 31.97 → 39.01 |
| III | 1% | 32, 4 → 32, 4 | 0.67, 0.52 | 3.8 → 4.8 | 1.85 | 2.04 → 2.61 |
| III | 0.3% | 128, 4 → 128, 4 | 0.65, 0.81 | 15.0 → 12.0 | 1.85 | 8.11 → 6.50 |
| III | 0.1% | 512, 8 → 512, 8 | 0.62, 0.73 | 29.3 → 24.8 | 1.82 | 16.12 → 13.63 |
| P1 | 1% | 8, 1 → 8, 1 | 0.68, 0.51 | 1.8 → 2.4 | 1.38 | 1.30 → 1.71 |
| P1 | 0.3% | 16, 4 → 16, 4 | 0.84, 0.66 | 1.6 → 2.1 | 1.42 | 1.16 → 1.46 |
| P1 | 0.1% | 64, 8 → 64, 8 | 0.96, 0.59 | 3.3 → 5.3 | 1.33 | 2.45 → 4.00 |
| B | 1% | 8, 1 → 8, 1 | 0.59, 0.55 | 3.8 → 4.1 | 2.31 | 1.65 → 1.78 |
| B | 0.3% | 16, 2 → 16, 2 | 0.65, 0.76 | 3.5 → 3.0 | 1.61 | 2.15 → 1.86 |
| B | 0.1% | 64, 8 → 64, 8 | 0.59, 0.74 | 3.2 → 2.6 | 1.20 | 2.71 → 2.17 |

## 4. Resource use of the routed designs (share of the user region of the U50)

| Design | Clock (MHz) | LUT | Registers | BRAM | DSP |
|---|---|---|---|---|---|
| Heston FT | 225 | 362473 [ 48.83%] | 464232 [ 29.61%] | 332 [ 28.55%] | 4018 [ 67.55%] |
| Heston QE | 225 | 405656 [ 54.65%] | 471852 [ 30.09%] | 214 [ 18.40%] | 3830 [ 64.39%] |
| Basket FT | 225 | 415976 [ 56.03%] | 545396 [ 34.78%] | 332 [ 28.55%] | 4354 [ 73.20%] |
| Basket QE | 200 | 428239 [ 57.69%] | 511345 [ 32.61%] | 214 [ 18.40%] | 3998 [ 67.22%] |
| Asian | 200 | 462069 [ 62.32%] | 530428 [ 33.83%] | 436 [ 37.49%] | 4460 [ 74.98%] |
