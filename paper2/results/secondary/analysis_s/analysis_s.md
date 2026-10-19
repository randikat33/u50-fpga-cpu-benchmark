# Paper 2, secondary cases: measured against predicted (phase5s_summary_s1.csv, phase5s_summary_s2.csv)

## Energy per pricing run: measured / predicted (variant same; informed in brackets)

| Case | Target | Boundary | card | card | matched | matched | native | native | Winner measured (predicted) |
|---|---|---|---|---|---|---|---|---|---|
| B | 1% | socket | ft 0.51 (0.65) | qe 0.38 (0.50) | ft 0.99 (0.99) | qe 1.00 (1.00) | ft 1.01 (1.01) | qe 1.00 (1.00) | cpu_native_qe (ok) |
| B | 1% | server | ft 0.49 (0.63) | qe 0.37 (0.48) | ft 0.99 (0.99) | qe 1.01 (1.01) | ft 1.00 (1.00) | qe 1.01 (1.01) | cpu_native_qe (ok) |
| B | 0.3% | socket | ft 1.09 (1.24) | qe 1.02 (1.23) | ft 0.99 (0.99) | qe 1.00 (1.00) | ft 1.01 (1.01) | qe 1.01 (1.01) | cpu_native_qe (ok) |
| B | 0.3% | server | ft 1.10 (1.23) | qe 1.01 (1.20) | ft 1.00 (1.00) | qe 1.00 (1.00) | ft 1.00 (1.00) | qe 1.00 (1.00) | cpu_native_qe (ok) |
| B | 0.1% | socket | ft 0.95 (1.04) | qe 1.01 (1.12) | ft 1.00 (1.00) | qe 1.00 (1.00) | ft 1.01 (1.01) | qe 1.01 (1.01) | card_qe (ok) |
| B | 0.1% | server | ft 0.97 (1.03) | qe 1.02 (1.09) | ft 1.00 (1.00) | qe 1.00 (1.00) | ft 1.00 (1.00) | qe 1.00 (1.00) | cpu_native_qe (ok) |
| A | 1% | socket | plain 0.89 (1.07) | cv 0.99 (1.23) | plain 0.98 (0.98) | cv 1.00 (1.00) | plain 1.00 (1.00) | cv 1.00 (1.00) | cpu_native_cv (ok) |
| A | 1% | server | plain 0.90 (1.07) | cv 0.98 (1.22) | plain 1.00 (1.00) | cv 0.99 (0.99) | plain 1.00 (1.00) | cv 1.00 (1.00) | cpu_native_cv (ok) |
| A | 0.3% | socket | plain 0.93 (1.05) | cv 0.96 (1.20) | plain 1.00 (1.00) | cv 0.99 (0.99) | plain 1.00 (1.00) | cv 1.00 (1.00) | cpu_native_cv (ok) |
| A | 0.3% | server | plain 0.98 (1.07) | cv 0.96 (1.19) | plain 1.00 (1.00) | cv 1.01 (1.01) | plain 1.00 (1.00) | cv 0.99 (0.99) | cpu_native_cv (ok) |
| A | 0.1% | socket | plain 0.87 (0.97) | cv 0.99 (1.24) | plain 1.00 (1.00) | cv 1.00 (1.00) | plain 1.00 (1.00) | cv 1.00 (1.00) | cpu_native_cv (ok) |
| A | 0.1% | server | plain 0.92 (0.99) | cv 0.98 (1.22) | plain 1.00 (1.00) | cv 1.00 (1.00) | plain 1.01 (1.01) | cv 1.00 (1.00) | cpu_native_cv (ok) |

## H5 (same)

- Energy within 20%: 68 of 72 (card 20 of 24); median |error| 0.6%, max 63%. Steady state within 20%: 72 of 72.
- Predicted winner right: 12 of 12; steady state 12 of 12.
- Pairwise verdicts right: 179 of 180.

## H5 (informed)

- Energy within 20%: 59 of 72 (card 11 of 24); median |error| 0.6%, max 52%. Steady state within 20%: 72 of 72.
- Predicted winner right: 12 of 12; steady state 11 of 12.
- Pairwise verdicts right: 179 of 180.

- RMSE within 1.1 × target (all strikes): 35 of 36 platform-settings.

  - B qe 0.3% cpu_native s0_c24: RMSE/eps [0.255, 1.293, 0.497] (10 runs)

## Hypotheses: measured (predicted, variant same)

- **HB2** holds (predicted hold): 1% 3.80, 0.3% 3.47, 0.1% 3.25
- **HA2** fails (predicted fail): 1% socket primary 20 vs 29.38; 1% socket steady 145 vs 1.26; 0.3% socket primary 178 vs 24.74; 0.3% socket steady 400 vs 1.26; 0.1% socket primary 303 vs 5.40; 0.1% socket steady 400 vs 1.26; failing: 1% socket primary, 1% server primary
- **HA3** holds (predicted hold): 1% best CPU / card 1.26, 0.3% best CPU / card 1.26, 0.1% best CPU / card 1.26

HB1 and HA1 are calibration results (registered: hold).
