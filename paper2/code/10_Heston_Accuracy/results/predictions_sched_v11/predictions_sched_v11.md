# Addendum v0.11: predicted pass time and energy (the calibrated model)

Model: the registered v0.9 model (SHA-256 d4ad8c03...) with the v0.11 calibration. Two-server boundary.

| Book (1% / 0.3% / 0.1%) | Policy | T (ms) | E (J) | Options card, fcpu, gpu, scpu | / alone_gpu | model_E / plan_v09 |
|---|---|---|---|---|---|---|
| N3 (10 / 10 / 0) | alone_gpu | 31.1 | 7.77 | 0, 0, 120, 0 | 1.000 | |
| N3 (10 / 10 / 0) | model_E | 27.5 | 7.15 | 0, 20, 100, 0 | 0.920 | 0.905 |
| N3 (10 / 10 / 0) | plan_v09 | 24.7 | 7.90 | 20, 10, 75, 15 | 1.017 | |
| N2 (6 / 12 / 2) | alone_gpu | 106.8 | 27.36 | 0, 0, 120, 0 | 1.000 | |
| N2 (6 / 12 / 2) | model_E | 103.1 | 26.75 | 0, 20, 100, 0 | 0.978 | 0.908 |
| N2 (6 / 12 / 2) | plan_v09 | 93.9 | 29.47 | 12, 21, 58, 29 | 1.077 | |
| S (20 / 0 / 0) | alone_gpu | 18.9 | 4.27 | 0, 0, 120, 0 | 1.000 | |
| S (20 / 0 / 0) | model_E | 12.9 | 3.28 | 20, 20, 80, 0 | 0.768 | 0.896 |
| S (20 / 0 / 0) | plan_v09 | 11.6 | 3.66 | 20, 20, 60, 20 | 0.856 | |
| S2 (16 / 4 / 0) (not run) | alone_gpu | 23.9 | 5.70 | 0, 0, 120, 0 | 1.000 | |
| S2 (16 / 4 / 0) (not run) | model_E | 18.4 | 4.89 | 16, 20, 84, 0 | 0.858 | 0.955 |
| S2 (16 / 4 / 0) (not run) | plan_v09 | 16.6 | 5.12 | 20, 16, 68, 16 | 0.899 | |
| M (8 / 8 / 4) (not run) | alone_gpu | 175.0 | 44.80 | 0, 0, 120, 0 | 1.000 | |
| M (8 / 8 / 4) (not run) | model_E | 171.1 | 44.23 | 0, 20, 100, 0 | 0.987 | 0.939 |
| M (8 / 8 / 4) (not run) | plan_v09 | 159.5 | 47.12 | 16, 40, 60, 4 | 1.052 | |
| L (0 / 0 / 20) (not run) | alone_gpu | 748.5 | 192.52 | 0, 0, 120, 0 | 1.000 | |
| L (0 / 0 / 20) (not run) | model_E | 743.5 | 192.16 | 0, 20, 100, 0 | 0.998 | 0.998 |
| L (0 / 0 / 20) (not run) | plan_v09 | 743.5 | 192.52 | 0, 16, 100, 4 | 1.000 | |

## Hypotheses

- **HC1** (primary) book N3: model_E's measured pass energy is at most 0.96 x alone_gpu's (medians of 5 repeats).
- **HC2** (primary) books N3 and N2: the v0.11 plan uses less energy than the v0.9 model's plan for the same book: measured E(model_E) / E(plan_v09) at most 0.95 in both.
- **HC3** (primary) books N3 and N2: every policy (6 jobs) has measured / predicted pass energy and pass time within 0.90-1.10.
- **HC4** (primary) book N2 (with 0.1% options): model_E saves less than 5% against alone_gpu (measured ratio at least 0.95): the model predicts no worthwhile saving.
- **HC5** (primary) the measured ratio model_E / alone_gpu increases across the books in the predicted order.
- **HC6** (secondary) book S (seen in the v0.10 check): model_E's measured pass energy is at most 0.85 x alone_gpu's, and its three jobs are within 0.90-1.10 of the predicted pass energy and time.

sha256 22292d48526f0ab4fed84b68900db18b904db582097e43e778237d3d45eee829
