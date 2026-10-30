# Rate model with 64-bit conversions recounted (rtx4070tis; fp64 limit 1.68, conversion limit 1.85 per SM per clock; energy per op: fp64 81.1 pJ, 64-bit conversion 56.8 pJ)

64-bit F2F per pair-step / per pair (/ per asset): asian_plain 2.00 / 4; asian_cv 4.00 / 4; basket_qe 0.00 / 15 / 3; basket_ft 0.00 / 15 / 3; heston_qe 0.00 / 18; heston_ft 0.00 / 18

- **fp64-bound settings (registered model):** registered: median 1.985, within 15% 0 of 12, within 30% 0 of 12; shared: median 0.901, within 15% 12 of 12, within 30% 12 of 12; separate: median 1.671, within 15% 0 of 12, within 30% 0 of 12
- **other settings:** registered: median 1.287, within 15% 1 of 24, within 30% 13 of 24; shared: median 1.286, within 15% 2 of 24, within 30% 14 of 24; separate: median 1.287, within 15% 1 of 24, within 30% 13 of 24
- **all 36:** registered: median 1.330, within 15% 1 of 36, within 30% 13 of 36; shared: median 1.281, within 15% 14 of 36, within 30% 26 of 36; separate: median 1.330, within 15% 1 of 36, within 30% 13 of 36

Energy per option, measured / predicted (pipe-energy rule at the recounted rate):
- **fp64-bound settings:** registered: median 1.821, within 15% 0 of 12, within 30% 3 of 12; shared: median 1.083, within 15% 9 of 12, within 30% 12 of 12; separate: median 1.268, within 15% 5 of 12, within 30% 9 of 12
- **all 36:** registered: median 1.049, within 15% 24 of 36, within 30% 27 of 36; shared: median 1.012, within 15% 33 of 36, within 30% 36 of 36; separate: median 1.015, within 15% 29 of 36, within 30% 33 of 36

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
