# Scheduling (addendum v0.8): results against the registered prediction

Two-server boundary, per pass; medians of 5 repeats (range in brackets).

| Policy | T meas (s) | T pred | T ratio | E meas (J) | E pred | E ratio | E / model_E | frodo / Supermicro share |
|---|---|---|---|---|---|---|---|---|
| alone_card | 31.7186 [31.0949-32.1321] | 29.8355 | 1.063 | 5841.3 [5688.5-5909.2] | 5633.2 | 1.037 | 111.446 | 0.66 / 0.34 |
| alone_fcpu | 2.3685 [2.3618-2.3759] | 2.5320 | 0.935 | 749.7 [748.0-753.6] | 762.1 | 0.984 | 14.304 | 0.80 / 0.20 |
| alone_gpu | 0.2047 [0.2032-0.2052] | 0.1940 | 1.055 | 49.1 [48.9-49.3] | 46.6 | 1.052 | 0.936 | 0.42 / 0.58 |
| alone_scpu | 9.8499 [9.8489-9.8540] | 9.9370 | 0.991 | 2007.6 [2002.7-2013.0] | 2052.3 | 0.978 | 38.303 | 0.46 / 0.54 |
| round_robin | 28.5768 [27.6162-28.8775] | 26.1045 | 1.095 | 5307.9 [5191.0-5471.5] | 5046.5 | 1.052 | 101.269 | 0.65 / 0.35 |
| model_E | 0.1809 [0.1806-0.1816] | 0.1721 | 1.052 | 52.4 [52.4-53.2] | 44.3 | 1.183 | 1.000 | 0.40 / 0.60 |
| model_T | 0.1980 [0.1979-0.2000] | 0.1542 | 1.284 | 64.7 [64.3-64.9] | 64.6 | 1.001 | 1.234 | 0.52 / 0.48 |
| model_M | 0.1801 [0.1801-0.1812] | 0.1623 | 1.110 | 53.3 [53.1-53.7] | 45.1 | 1.181 | 1.016 | 0.40 / 0.60 |
| model_dev | 0.2030 [0.2020-0.2032] | 0.1917 | 1.059 | 51.7 [51.5-52.0] | 46.3 | 1.117 | 0.986 | 0.45 / 0.55 |
| oracle | 0.1722 [0.1696-0.1785] | - | - | 50.8 [50.6-52.7] | - | - | 0.970 | 0.41 / 0.59 |

## Hypotheses

- **HS1 holds**: model_E / oracle per repeat 1.050, 1.030, 1.035, 0.994, 1.029; median 1.030 (registered <= 1.05).
- **HS2 fails**: alone_card x111.446 (lower, ok); alone_fcpu x14.304 (lower, ok); alone_gpu x0.936 (lower, WRONG); alone_scpu x38.303 (lower, ok); round_robin x101.269 (lower, ok).
- **HS3 fails** (6 of 9 policies within 0.85-1.15 in both): alone_card E 1.037 T 1.063; alone_fcpu E 0.984 T 0.935; alone_gpu E 1.052 T 1.055; alone_scpu E 0.978 T 0.991; round_robin E 1.052 T 1.095; model_E E 1.183 T 1.052 OUT; model_T E 1.001 T 1.284 OUT; model_M E 1.181 T 1.110 OUT; model_dev E 1.117 T 1.059.
- **HS4 holds**: model_T x1.234 (lower, ok); model_dev x0.986 (no call).

## Descriptive

- model_M's deadline 0.1631 s: met in 0 of 5 repeats (median pass 0.1801 s).
- Energy-makespan points (measured medians): model_T (0.198 s, 64.7 J), model_M (0.180 s, 53.3 J), model_E (0.181 s, 52.4 J), model_dev (0.203 s, 51.7 J), alone_gpu (0.205 s, 49.1 J), oracle (0.172 s, 50.8 J).
- Communication + coordination / computation (device timeline minus part times, over part times), median: gpu in alone_gpu 0.18, scpu in alone_scpu 0.02, gpu in model_E 0.11, scpu in model_E 1.82.
- Accuracy, RMS of the options' worst-strike error / eps over all passes: alone_fcpu 0.89, alone_card 0.60, alone_scpu 0.89, round_robin 0.76, alone_gpu 0.64, model_E 0.82, model_T 0.80, model_M 0.88, model_dev 0.64, oracle 0.81.
- Oracle's own predicted energy (corrected costs) per repeat: 35.9 J [16, 0, 52, 52], 37.1 J [16, 0, 44, 60], 36.0 J [16, 0, 52, 52], 34.7 J [3, 0, 65, 52], 36.1 J [16, 0, 44, 60].
- Card design load: 5.35 s and 147 J above idle, i.e. 1018 J at the two-server boundary per load (about 19 passes of model_E).
