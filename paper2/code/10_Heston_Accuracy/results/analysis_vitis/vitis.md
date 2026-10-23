# Vitis Libraries 2023.1 engines on the U50 (protocol v0.6 Section 23 item 5; descriptive)

Library test kernels unchanged (MCEuropeanHestonEngine: QE, double, 1 MC module; MCAsianAPEngine: arithmetic Asian, Black-Scholes, double, 2 MC modules), at our settings with samples = 2 x our pairs, 3 repeats each (45 jobs, 0 set aside), run_measured, socket-0 host, registered xrt.ini. Ratios are against our card's phase-6 run at the same setting (card + socket boundary).

| Case | Target | steps | library t/run (s) | ours (s) | time ratio | energy ratio | error / eps (3 strikes) |
|---|---|---|---|---|---|---|---|
| I | 1% | 40 | 0.0141 | 0.00082 | 17.2 | 16.1 | +0.30, -1.10, -0.11 |
| I | 0.3% | 80 | 0.2598 | 0.00513 | 50.6 | 43.8 | +0.96, -6.92, -0.11 |
| I | 0.1% | 320 | 9.1991 | 0.14724 | 62.5 | 52.0 | -1.99, -18.32, +0.70 |
| II | 1% | 60 | 0.0572 | 0.00178 | 32.1 | 29.1 | +0.77, +0.63, +1.47 |
| II | 0.3% | 60 | 0.6051 | 0.01068 | 56.6 | 49.1 | +3.99, +0.54, +2.35 |
| II | 0.1% | 240 | 21.9400 | 0.35020 | 62.7 | 53.1 | +5.12, +10.55, +12.17 |
| III | 1% | 20 | 0.0122 | 0.00048 | 25.5 | 22.6 | +0.48, +0.17, +0.37 |
| III | 0.3% | 20 | 0.1108 | 0.00270 | 41.0 | 34.7 | +1.78, -0.28, +0.71 |
| III | 0.1% | 40 | 1.9649 | 0.03258 | 60.3 | 46.8 | +3.51, +5.95, +9.91 |
| P1 | 1% | 1 | 0.0022 | 0.00024 | 9.2 | 8.6 | -1.73, -3.65, -0.04 |
| P1 | 0.3% | 4 | 0.0120 | 0.00083 | 14.5 | 13.5 | -2.12, -4.13, +0.16 |
| P1 | 0.1% | 8 | 0.1624 | 0.00364 | 44.6 | 36.5 | -3.11, -10.09, +0.09 |
| A | 1% | 250 | 0.0281 | 0.00113 | 25.0 | 23.0 | -0.36, -0.45, -0.38 |
| A | 0.3% | 250 | 0.2615 | 0.00566 | 46.2 | 41.0 | -1.02, -1.32, -1.02 |
| A | 0.1% | 250 | 2.3131 | 0.04247 | 54.5 | 47.7 | -3.04, -3.95, -3.03 |

- Time ratio 9.2-62.7 (median 44.6); energy ratio 8.6-53.1 (median 36.5). The library prices one strike per call, so it simulates 3x our paths for the same three prices; per simulated path-step it is still 15x slower in median.
- Settings meeting |error| <= 1 eps at all strikes (one fixed-seed run each): 2 of 15.
- Heston time-step check (P1, 1,829,376 samples, K=100 error / eps): 8 steps -10.1, 16 steps -4.8, 32 steps -2.3, 64 steps -0.7, 128 steps -0.8: the error roughly halves per doubling of steps (first-order time-step bias), larger than our QE's at the registered steps.
- Asian: 249 steps (the library test's fixings - 1) gives the same prices as 250 within 3e-4: 1% 5.7556, 0.3% 5.7590, 0.1% 5.7592; the library's Asian prices are about 0.018 below the reference at every strike and target (a bias independent of the number of paths).
