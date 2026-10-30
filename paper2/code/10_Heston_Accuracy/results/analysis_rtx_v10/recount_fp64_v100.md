# Rate model with 64-bit conversions recounted (v100; fp64 limit 32.00, conversion limit 32.00 per SM per clock; energy per op: fp64 20.0 pJ, 64-bit conversion 20.0 pJ)

64-bit F2F per pair-step / per pair (/ per asset): asian_plain 2.00 / 4; asian_cv 4.00 / 4; basket_qe 0.00 / 15 / 3; basket_ft 0.00 / 15 / 3; heston_qe 0.00 / 18; heston_ft 0.00 / 18

| Case | Target | Method | rate registered (G/s) | rate shared | binds |
|---|---|---|---|---|---|
| A | 0.1% | cv | 347.8 | 415.4 | fma |
| A | 0.1% | plain | 434.9 | 434.9 | issue |
| A | 0.3% | cv | 347.8 | 415.4 | fma |
| A | 0.3% | plain | 434.9 | 434.9 | issue |
| A | 1% | cv | 347.8 | 415.4 | fma |
| A | 1% | plain | 434.9 | 434.9 | issue |
| B | 0.1% | ft | 29.8 | 29.8 | fma |
| B | 0.1% | qe | 11.9 | 11.9 | fma |
| B | 0.3% | ft | 27.8 | 27.8 | fma |
| B | 0.3% | qe | 9.8 | 9.8 | fma |
| B | 1% | ft | 25.6 | 25.6 | fma |
| B | 1% | qe | 7.9 | 7.9 | fma |
| I | 0.1% | ft | 241.6 | 241.6 | fma |
| I | 0.1% | qe | 101.2 | 101.2 | fma |
| I | 0.3% | ft | 241.6 | 241.6 | fma |
| I | 0.3% | qe | 100.4 | 100.4 | fma |
| I | 1% | ft | 241.2 | 241.2 | fma |
| I | 1% | qe | 99.3 | 99.3 | fma |
| II | 0.1% | ft | 241.7 | 241.7 | fma |
| II | 0.1% | qe | 101.1 | 101.1 | fma |
| II | 0.3% | ft | 241.6 | 241.6 | fma |
| II | 0.3% | qe | 100.0 | 100.0 | fma |
| II | 1% | ft | 241.4 | 241.4 | fma |
| II | 1% | qe | 100.0 | 100.0 | fma |
| III | 0.1% | ft | 241.4 | 241.4 | fma |
| III | 0.1% | qe | 99.3 | 99.3 | fma |
| III | 0.3% | ft | 240.8 | 240.8 | fma |
| III | 0.3% | qe | 97.2 | 97.2 | fma |
| III | 1% | ft | 238.2 | 238.2 | fma |
| III | 1% | qe | 97.2 | 97.2 | fma |
| P1 | 0.1% | ft | 233.1 | 233.1 | fma |
| P1 | 0.1% | qe | 91.5 | 91.5 | fma |
| P1 | 0.3% | ft | 210.8 | 210.8 | fma |
| P1 | 0.3% | qe | 83.3 | 83.3 | fma |
| P1 | 1% | ft | 186.9 | 186.9 | fma |
| P1 | 1% | qe | 54.2 | 54.2 | fma |
