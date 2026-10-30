# Scheduling, addendum v0.9: results against the registered prediction

Per pass; medians of 5 repeats [range]. R1, R2: two-server boundary; R3: frodo (card + both sockets + DRAM).

| Regime | Policy | T meas (s) | T pred | T ratio | E meas (J) | E pred | E ratio | error max / eps |
|---|---|---|---|---|---|---|---|---|
| R1 | alone_card | 31.8301 [31.1692-34.5618] | 29.8355 | 1.067 | 5956.89 [5776.10-6523.03] | 5633.20 | 1.057 | 1.33 |
| R1 | alone_fcpu | 2.7479 [2.3639-2.7604] | 2.5320 | 1.085 | 826.14 [749.48-831.81] | 762.12 | 1.084 | 1.61 |
| R1 | alone_gpu | 0.1753 [0.1745-0.1756] | 0.1818 | 0.964 | 45.39 [45.25-45.63] | 47.13 | 0.963 | 1.33 |
| R1 | alone_scpu | 9.7618 [9.7596-9.7641] | 9.9131 | 0.985 | 2037.98 [2025.22-2060.40] | 2051.99 | 0.993 | 1.61 |
| R1 | model_E | 0.1622 [0.1607-0.1630] | 0.1628 | 0.996 | 47.49 [47.16-47.53] | 43.70 | 1.087 | 1.61 |
| R1 | model_T | 0.1462 [0.1459-0.1467] | 0.1483 | 0.985 | 69.73 [69.63-69.81] | 67.96 | 1.026 | 1.61 |
| R1 | model_dev | 0.1776 [0.1773-0.1783] | 0.1844 | 0.963 | 49.18 [49.11-49.34] | 46.14 | 1.066 | 1.33 |
| R2 | model_M | 0.1556 [0.1554-0.1561] | 0.1555 | 1.001 | 50.15 [49.91-50.22] | 46.12 | 1.087 | 1.61 |
| R3 | alone_card_f | 31.8301 [31.1692-34.5618] | 29.8355 | 1.067 | 3949.88 [3805.63-4376.59] | 3702.90 | 1.067 | 1.33 |
| R3 | alone_fcpu_f | 2.7479 [2.3639-2.7604] | 2.5320 | 1.085 | 651.99 [599.82-657.86] | 598.30 | 1.090 | 1.61 |
| R3 | model_E_f | 1.4015 [1.4010-1.4017] | 1.3837 | 1.013 | 251.55 [251.37-253.20] | 292.09 | 0.861 | 1.61 |
| R1 | oracle | 0.1634 [0.1612-0.1747] | - | - | 48.86 [48.47-49.40] | - | - | 1.61 |
| R2 | oracle_M | 0.1596 [0.1576-0.1598] | - | - | 48.48 [48.22-48.78] | - | - | 1.61 |
| R3 | oracle_f | 1.4011 [1.4010-1.4015] | - | - | 251.98 [251.18-252.44] | - | - | 1.61 |

## Hypotheses

- **HS5 holds**: R1 model_E / oracle median 0.972 (0.972, 0.968, 0.978, 0.959, 0.973); R2 model_M / oracle_M median 1.032 (1.032, 1.041, 1.028, 1.031, 1.036); R3 model_E_f / oracle_f median 1.001 (0.998, 1.001, 1.001, 0.997, 1.003) (registered <= 1.05).
- **HS6 fails**: R1 model_E / alone_gpu 1.046 (registered <= 1.02; predicted 0.927).
- **HS7 holds**: model_M met D = 0.1587 s in 5 of 5 repeats; model_T / model_M energy 1.390 (called).
- **HS8 holds**: R3 model_E_f saves 93.6% against alone_card_f, 61.4% against alone_fcpu_f (registered >= 5% each).
- **HS9 fails** (10 of 11 within 0.90-1.10 in both): alone_card E 1.057 T 1.067; alone_fcpu E 1.084 T 1.085; alone_gpu E 0.963 T 0.964; alone_scpu E 0.993 T 0.985; model_E E 1.087 T 0.996; model_T E 1.026 T 0.985; model_dev E 1.066 T 0.963; model_M E 1.087 T 1.001; alone_card_f E 1.067 T 1.067; alone_fcpu_f E 1.090 T 1.085; model_E_f E 0.861 T 1.013 OUT.
- **HS10 holds**: model_T / model_E energy 1.468 (called).
