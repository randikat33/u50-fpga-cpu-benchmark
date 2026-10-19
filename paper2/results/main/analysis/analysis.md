# Paper 2 (main cases): measured against predicted (phase5_summary_s1.csv, phase5_summary_s2.csv)

## Energy per pricing run: measured / predicted

| Case | Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner measured (predicted) |
|---|---|---|---|---|---|---|---|---|---|
| I | 1% | socket | 1.02 | 0.61 | 0.99 | 0.99 | 1.02 | 1.01 | cpu_native_qe (ok) |
| I | 1% | server | 1.07 | 0.60 | 0.98 | 1.00 | 1.01 | 1.00 | cpu_native_qe (ok) |
| I | 0.3% | socket | 0.93 | 0.96 | 1.00 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| I | 0.3% | server | 0.96 | 1.00 | 1.04 | 1.00 | 0.99 | 1.00 | card_qe (ok) |
| I | 0.1% | socket | 0.93 | 0.91 | 1.00 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| I | 0.1% | server | 0.96 | 0.94 | 1.00 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| II | 1% | socket | 0.94 | 1.02 | 1.00 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| II | 1% | server | 0.97 | 1.05 | 1.01 | 0.97 | 1.00 | 0.99 | cpu_native_qe (ok) |
| II | 0.3% | socket | 0.91 | 0.91 | 1.00 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| II | 0.3% | server | 0.95 | 0.96 | 0.95 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| II | 0.1% | socket | 0.92 | 0.88 | 1.00 | 1.00 | 1.00 | 1.01 | card_qe (ok) |
| II | 0.1% | server | 0.95 | 0.92 | 1.00 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| III | 1% | socket | 1.11 | 0.71 | 1.00 | 1.00 | 1.00 | 1.00 | cpu_native_qe (ok) |
| III | 1% | server | 1.11 | 0.69 | 1.01 | 1.00 | 1.00 | 1.00 | cpu_native_qe (ok) |
| III | 0.3% | socket | 0.95 | 1.12 | 1.00 | 1.00 | 1.00 | 1.00 | card_qe (ok) |
| III | 0.3% | server | 0.98 | 1.13 | 1.00 | 1.00 | 1.01 | 1.01 | cpu_native_qe (ok) |
| III | 0.1% | socket | 0.94 | 1.02 | 1.00 | 1.00 | 1.01 | 1.01 | card_qe (ok) |
| III | 0.1% | server | 0.96 | 1.03 | 1.00 | 0.99 | 1.00 | 0.99 | card_qe (ok) |
| P1 | 1% | socket | 0.30 | 0.43 | 1.00 | 1.00 | 1.00 | 1.00 | cpu_native_qe (ok) |
| P1 | 1% | server | 0.29 | 0.42 | 0.98 | 1.02 | 0.99 | 1.01 | cpu_native_qe (ok) |
| P1 | 0.3% | socket | 1.00 | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | cpu_native_qe (ok) |
| P1 | 0.3% | server | 0.99 | 0.96 | 1.00 | 1.01 | 1.00 | 1.00 | cpu_native_qe (ok) |
| P1 | 0.1% | socket | 0.98 | 1.18 | 1.00 | 1.00 | 1.01 | 1.01 | card_qe (ok) |
| P1 | 0.1% | server | 1.00 | 1.19 | 1.00 | 1.01 | 0.99 | 1.00 | cpu_native_qe (ok) |

## Hypotheses on the measurements

- **H2** (QE/FT cost per path-step 1.2–3 everywhere): fails; card socket 2.06; card server 2.06; cpu_matched socket 4.41; cpu_matched server 4.22; cpu_native socket 3.72; cpu_native server 3.96
- **H3** (I–III: CPU socket QE < card FT in energy): holds (steady state: fails); card-FT / CPU-QE: I 1% 2.71, I 0.3% 5.50, I 0.1% 5.70, II 1% 2.97, II 0.3% 11.66, II 0.1% 11.67, III 1% 1.52, III 0.3% 2.83, III 0.1% 5.70
- **H4** (P1: card FT wins at 0.3% only): fails (steady state: fails); 1%: card 0.0142 J vs CPU 0.0109 J, 0.3%: card 0.0723 J vs CPU 0.108 J, 0.1%: card 0.885 J vs CPU 1.39 J
- **H5** (model): energy within 20% in 136 of 144 (card 40 of 48; median |error| 1%, max 71%; steady state 142 of 144); predicted winner right in 24 of 24 (steady state 23); pairwise verdicts right in 348 of 360; RMSE within 1.1 × target in 69 of 72.

  - RMSE miss: III qe 0.3% card: [0.445, 0.753, 1.196] (10 runs)
  - RMSE miss: III qe 0.3% cpu_matched: [0.445, 0.753, 1.196] (10 runs)
  - RMSE miss: III qe 0.3% cpu_native: [0.352, 0.973, 1.257] (10 runs)

## RQ2 (steady state, socket): algorithm factor against platform factor

| Case | Target | Algorithm factor (FT/QE, best platform each) | Platform factor QE (best CPU / card) | Platform factor FT |
|---|---|---|---|---|
| I | 1% | 15.6 | 1.93 | 1.09 |
| I | 0.3% | 31.2 | 1.95 | 1.09 |
| I | 0.1% | 30.9 | 1.96 | 1.08 |
| II | 1% | 16.2 | 1.94 | 1.11 |
| II | 0.3% | 64.7 | 1.94 | 1.10 |
| II | 0.1% | 63.3 | 1.98 | 1.06 |
| III | 1% | 3.8 | 1.85 | 1.09 |
| III | 0.3% | 15.0 | 1.85 | 1.07 |
| III | 0.1% | 29.3 | 1.82 | 1.07 |
| P1 | 1% | 1.8 | 1.38 | 1.37 |
| P1 | 0.3% | 1.6 | 1.42 | 1.23 |
| P1 | 0.1% | 3.3 | 1.33 | 1.11 |
