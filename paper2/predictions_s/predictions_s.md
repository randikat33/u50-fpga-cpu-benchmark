# Paper 2, secondary cases: predictions made before any card run of the new kernels

Created (UTC): 2026-10-18T07:19:36Z. Protocol v0.5 (commit d16fe83). Inputs (sha256): settings_s.csv d2604c78d0eaab76…, cpu_pred_summary_s.csv 7b6aa7c33bde8020…, reference_s.csv bf93424c89a10c5a…, beta_A.csv 2a8f4179c3196b87….

## Card designs (routed)

| Kernel | Clock (MHz) | WNS (ns) | CUs × lanes | Peak (G path-steps/s) | Vivado power (W) |
|---|---|---|---|---|---|
| hb_ft | 225 | +0.001 | 2 × 14 | 1.575 | 41.15 |
| hb_qe | 200 | +0.033 | 2 × 7 | 0.700 | 44.10 |
| ha_asian | 200 | +0.006 | 2 × 14 | 11.200 | 45.57 |

Basket rates are in basket path-steps (8 asset-steps each).

**Two variants.** *same*: t0 = 0.70 ms, card power P0 + d (P_viv − P0) (paper 1). *informed*: t0 = 0.534 ms (median of 16 main-case card runs with a predicted kernel time below 1 ms; range 0.15–0.87 ms), card power P0 + d c (P_viv − P0) with c = 0.680 (median of 38 main-case steady-state card runs; range 0.40–0.94). Host H = 49.151 W (socket) / 92.94 W (server) in both. H5 is tested on *same*. (Protocol v0.5, Section 8b, quoted the measured start-up cost as "about 0.2 ms": that is the value of the very smallest main-case runs. The rule above was fixed before any card run of the new kernels and gives the value shown.)

## Variant: same

### Case B: energy per pricing run (J), primary

| Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.0534 | 0.0486 | 0.067 | 0.0535 | 0.00908 | 0.00471 | cpu_native_qe |
| 1% | server | 0.0874 | 0.0804 | 0.185 | 0.127 | 0.015 | 0.0106 | cpu_native_qe |
| 0.3% | socket | 0.192 | 0.0883 | 0.493 | 0.315 | 0.161 | 0.0649 | cpu_native_qe |
| 0.3% | server | 0.294 | 0.139 | 0.418 | 0.287 | 0.159 | 0.0668 | cpu_native_qe |
| 0.1% | socket | 5.24 | 1.55 | 12.8 | 7.5 | 5.3 | 1.86 | card_qe |
| 0.1% | server | 7.8 | 2.29 | 10.6 | 6.02 | 4.96 | 1.81 | cpu_native_qe |

### Case B: energy, steady state (J), secondary

| Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.00658 | 0.00184 | 0.0206 | 0.0149 | 0.00789 | 0.00381 | card_qe |
| 1% | server | 0.00976 | 0.00271 | 0.0157 | 0.0103 | 0.00698 | 0.00331 | card_qe |
| 0.3% | socket | 0.145 | 0.0414 | 0.389 | 0.261 | 0.159 | 0.0635 | card_qe |
| 0.3% | server | 0.215 | 0.0609 | 0.295 | 0.186 | 0.142 | 0.0572 | cpu_native_qe |
| 0.1% | socket | 5.2 | 1.5 | 12.4 | 7.05 | 5.34 | 1.8 | card_qe |
| 0.1% | server | 7.72 | 2.21 | 9.42 | 5.19 | 4.76 | 1.76 | cpu_native_qe |

### Case B: time per pricing run (s)

| Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.000778 | 0.000725 | 0.000467 | 0.000383 | 5.05e-05 | 2.7e-05 | cpu_native_qe |
| 1% | server | 0.000778 | 0.000725 | 0.0007 | 0.000483 | 4.73e-05 | 3.64e-05 | cpu_native_qe |
| 0.3% | socket | 0.00232 | 0.00115 | 0.00281 | 0.0019 | 0.000895 | 0.000361 | cpu_native_qe |
| 0.3% | server | 0.00232 | 0.00115 | 0.00119 | 0.00085 | 0.000447 | 0.000195 | cpu_native_qe |
| 0.1% | socket | 0.0583 | 0.0168 | 0.0714 | 0.0427 | 0.0295 | 0.0103 | cpu_native_qe |
| 0.1% | server | 0.0583 | 0.0168 | 0.0298 | 0.0169 | 0.0139 | 0.00521 | cpu_native_qe |

### Case A: energy per pricing run (J), primary

| Target | Boundary | card_plain | card_cv | cpu_matched_plain | cpu_matched_cv | cpu_native_plain | cpu_native_cv | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.0923 | 0.0619 | 0.42 | 0.0244 | 0.0419 | 0.00207 | cpu_native_cv |
| 1% | server | 0.144 | 0.0998 | 0.978 | 0.0647 | 0.039 | 0.0105 | cpu_native_cv |
| 0.3% | socket | 0.486 | 0.0619 | 2.32 | 0.0938 | 0.426 | 0.00239 | cpu_native_cv |
| 0.3% | server | 0.72 | 0.0998 | 2.25 | 0.218 | 0.319 | 0.0109 | cpu_native_cv |
| 0.1% | socket | 3.96 | 0.062 | 18.2 | 0.381 | 3.76 | 0.0114 | cpu_native_cv |
| 0.1% | server | 5.8 | 0.0999 | 12.4 | 0.936 | 2.75 | 0.0172 | cpu_native_cv |

### Case A: energy, steady state (J), secondary

| Target | Boundary | card_plain | card_cv | cpu_matched_plain | cpu_matched_cv | cpu_native_plain | cpu_native_cv | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.0392 | 0.000271 | 0.182 | 0.00125 | 0.0376 | 0.000292 | card_cv |
| 1% | server | 0.0574 | 0.000396 | 0.118 | 0.000813 | 0.0273 | 0.000222 | cpu_native_cv |
| 0.3% | socket | 0.433 | 0.00108 | 2.01 | 0.00501 | 0.415 | 0.00117 | card_cv |
| 0.3% | server | 0.634 | 0.00158 | 1.3 | 0.00325 | 0.302 | 0.000886 | cpu_native_cv |
| 0.1% | socket | 3.9 | 0.00974 | 18 | 0.0451 | 3.74 | 0.0105 | card_cv |
| 0.1% | server | 5.7 | 0.0142 | 11.7 | 0.0293 | 2.72 | 0.00797 | cpu_native_cv |

### Case A: time per pricing run (s)

| Target | Boundary | card_plain | card_cv | cpu_matched_plain | cpu_matched_cv | cpu_native_plain | cpu_native_cv | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.00119 | 0.000865 | 0.00297 | 0.00019 | 0.000243 | 1.51e-05 | cpu_native_cv |
| 1% | server | 0.00119 | 0.000865 | 0.00367 | 0.000253 | 0.000115 | 3.97e-05 | cpu_native_cv |
| 0.3% | socket | 0.00535 | 0.000865 | 0.0145 | 0.000731 | 0.00243 | 1.56e-05 | cpu_native_cv |
| 0.3% | server | 0.00535 | 0.000865 | 0.00721 | 0.000854 | 0.0009 | 4.02e-05 | cpu_native_cv |
| 0.1% | socket | 0.042 | 0.000865 | 0.11 | 0.00292 | 0.0213 | 6.58e-05 | cpu_native_cv |
| 0.1% | server | 0.042 | 0.000865 | 0.035 | 0.00364 | 0.00771 | 5.55e-05 | cpu_native_cv |

### Hypotheses, variant same

- **HB1** (FT/QE work 2–20; calibration): holds; 1% 8.3, 0.3% 8.1, 0.1% 8.0.
- **HB2** (basket algorithm factor 1–10, steady state, socket): predicted to hold; 1% 3.57, 0.3% 3.49, 0.1% 3.46.
- **HA1** (plain/CV work ≥ 100; calibration): holds; 1% 145, 0.3% 400, 0.1% 400.
- **HA2** (algorithm factor > platform gain; CPU CV < card plain): predicted to FAIL. 1% socket primary: 20 vs 29.89; 1% socket steady: 139 vs 1.08; 0.3% socket primary: 178 vs 25.92; 0.3% socket steady: 384 vs 1.08; 0.1% socket primary: 331 vs 5.46; 0.1% socket steady: 384 vs 1.08.
- **HA3** (steady state, socket: card CV < best CPU CV): predicted to hold; 1% best CPU / card 1.08, 0.3% best CPU / card 1.08, 0.1% best CPU / card 1.08.

## Variant: informed

### Case B: energy per pricing run (J), primary

| Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.0418 | 0.0374 | 0.067 | 0.0535 | 0.00908 | 0.00471 | cpu_native_qe |
| 1% | server | 0.0686 | 0.0619 | 0.185 | 0.127 | 0.015 | 0.0106 | cpu_native_qe |
| 0.3% | socket | 0.169 | 0.0734 | 0.493 | 0.315 | 0.161 | 0.0649 | cpu_native_qe |
| 0.3% | server | 0.263 | 0.116 | 0.418 | 0.287 | 0.159 | 0.0668 | cpu_native_qe |
| 0.1% | socket | 4.79 | 1.4 | 12.8 | 7.5 | 5.3 | 1.86 | card_qe |
| 0.1% | server | 7.33 | 2.13 | 10.6 | 6.02 | 4.96 | 1.81 | cpu_native_qe |

### Case B: energy, steady state (J), secondary

| Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.00601 | 0.00167 | 0.0206 | 0.0149 | 0.00789 | 0.00381 | card_qe |
| 1% | server | 0.0092 | 0.00254 | 0.0157 | 0.0103 | 0.00698 | 0.00331 | card_qe |
| 0.3% | socket | 0.132 | 0.0376 | 0.389 | 0.261 | 0.159 | 0.0635 | card_qe |
| 0.3% | server | 0.203 | 0.057 | 0.295 | 0.186 | 0.142 | 0.0572 | card_qe |
| 0.1% | socket | 4.75 | 1.36 | 12.4 | 7.05 | 5.34 | 1.8 | card_qe |
| 0.1% | server | 7.27 | 2.07 | 9.42 | 5.19 | 4.76 | 1.76 | cpu_native_qe |

### Case B: time per pricing run (s)

| Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.000612 | 0.000559 | 0.000467 | 0.000383 | 5.05e-05 | 2.7e-05 | cpu_native_qe |
| 1% | server | 0.000612 | 0.000559 | 0.0007 | 0.000483 | 4.73e-05 | 3.64e-05 | cpu_native_qe |
| 0.3% | socket | 0.00215 | 0.000984 | 0.00281 | 0.0019 | 0.000895 | 0.000361 | cpu_native_qe |
| 0.3% | server | 0.00215 | 0.000984 | 0.00119 | 0.00085 | 0.000447 | 0.000195 | cpu_native_qe |
| 0.1% | socket | 0.0581 | 0.0167 | 0.0714 | 0.0427 | 0.0295 | 0.0103 | cpu_native_qe |
| 0.1% | server | 0.0581 | 0.0167 | 0.0298 | 0.0169 | 0.0139 | 0.00521 | cpu_native_qe |

### Case A: energy per pricing run (J), primary

| Target | Boundary | card_plain | card_cv | cpu_matched_plain | cpu_matched_cv | cpu_native_plain | cpu_native_cv | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.0769 | 0.0494 | 0.42 | 0.0244 | 0.0419 | 0.00207 | cpu_native_cv |
| 1% | server | 0.121 | 0.08 | 0.978 | 0.0647 | 0.039 | 0.0105 | cpu_native_cv |
| 0.3% | socket | 0.433 | 0.0494 | 2.32 | 0.0938 | 0.426 | 0.00239 | cpu_native_cv |
| 0.3% | server | 0.66 | 0.08 | 2.25 | 0.218 | 0.319 | 0.0109 | cpu_native_cv |
| 0.1% | socket | 3.57 | 0.0495 | 18.2 | 0.381 | 3.76 | 0.0114 | cpu_native_cv |
| 0.1% | server | 5.4 | 0.0801 | 12.4 | 0.936 | 2.75 | 0.0172 | cpu_native_cv |

### Case A: energy, steady state (J), secondary

| Target | Boundary | card_plain | card_cv | cpu_matched_plain | cpu_matched_cv | cpu_native_plain | cpu_native_cv | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.0355 | 0.000245 | 0.182 | 0.00125 | 0.0376 | 0.000292 | card_cv |
| 1% | server | 0.0536 | 0.00037 | 0.118 | 0.000813 | 0.0273 | 0.000222 | cpu_native_cv |
| 0.3% | socket | 0.392 | 0.000978 | 2.01 | 0.00501 | 0.415 | 0.00117 | card_cv |
| 0.3% | server | 0.592 | 0.00148 | 1.3 | 0.00325 | 0.302 | 0.000886 | cpu_native_cv |
| 0.1% | socket | 3.52 | 0.0088 | 18 | 0.0451 | 3.74 | 0.0105 | card_cv |
| 0.1% | server | 5.33 | 0.0133 | 11.7 | 0.0293 | 2.72 | 0.00797 | cpu_native_cv |

### Case A: time per pricing run (s)

| Target | Boundary | card_plain | card_cv | cpu_matched_plain | cpu_matched_cv | cpu_native_plain | cpu_native_cv | Winner |
|---|---|---|---|---|---|---|---|---|
| 1% | socket | 0.00102 | 0.000699 | 0.00297 | 0.00019 | 0.000243 | 1.51e-05 | cpu_native_cv |
| 1% | server | 0.00102 | 0.000699 | 0.00367 | 0.000253 | 0.000115 | 3.97e-05 | cpu_native_cv |
| 0.3% | socket | 0.00518 | 0.000699 | 0.0145 | 0.000731 | 0.00243 | 1.56e-05 | cpu_native_cv |
| 0.3% | server | 0.00518 | 0.000699 | 0.00721 | 0.000854 | 0.0009 | 4.02e-05 | cpu_native_cv |
| 0.1% | socket | 0.0418 | 0.000699 | 0.11 | 0.00292 | 0.0213 | 6.58e-05 | cpu_native_cv |
| 0.1% | server | 0.0418 | 0.000699 | 0.035 | 0.00364 | 0.00771 | 5.55e-05 | cpu_native_cv |

### Hypotheses, variant informed

- **HB1** (FT/QE work 2–20; calibration): holds; 1% 8.3, 0.3% 8.1, 0.1% 8.0.
- **HB2** (basket algorithm factor 1–10, steady state, socket): predicted to hold; 1% 3.60, 0.3% 3.52, 0.1% 3.49.
- **HA1** (plain/CV work ≥ 100; calibration): holds; 1% 145, 0.3% 400, 0.1% 400.
- **HA2** (algorithm factor > platform gain; CPU CV < card plain): predicted to FAIL. 1% socket primary: 20 vs 23.86; 1% socket steady: 145 vs 1.19; 0.3% socket primary: 178 vs 20.69; 0.3% socket steady: 400 vs 1.19; 0.1% socket primary: 314 vs 4.36; 0.1% socket steady: 400 vs 1.19.
- **HA3** (steady state, socket: card CV < best CPU CV): predicted to hold; 1% best CPU / card 1.19, 0.3% best CPU / card 1.19, 0.1% best CPU / card 1.19.

H5 (model: energy within 20%, every winner and pairwise verdict, RMSE within 1.1 × target) is tested on variant *same* after the card runs, and reported separately from the main cases.

