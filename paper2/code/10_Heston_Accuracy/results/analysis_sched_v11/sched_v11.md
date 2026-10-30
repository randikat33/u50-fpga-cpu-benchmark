# Scheduling, addendum v0.11: the calibrated model on three books

Per pass, two-server boundary; medians of 5 repeats [range].

| Book | Policy | T meas (ms) | T pred (ms) | T ratio | E meas (J) | E pred (J) | E ratio | error max / eps |
|---|---|---|---|---|---|---|---|---|
| N3 | alone_gpu | 31.3 [31.3-31.4] | 31.1 | 1.008 | 7.83 [7.58-7.85] | 7.77 | 1.009 | 1.78 |
| N3 | model_E | 31.6 [31.6-31.8] | 27.5 | 1.150 | 8.29 [8.28-8.31] | 7.15 | 1.159 | 1.78 |
| N3 | plan_v09 | 31.5 [31.4-32.2] | 24.7 | 1.279 | 8.71 [8.65-8.85] | 7.90 | 1.103 | 1.81 |
| N2 | alone_gpu | 107.1 [107.0-107.3] | 106.8 | 1.002 | 28.03 [27.94-28.10] | 27.36 | 1.025 | 1.56 |
| N2 | model_E | 104.6 [104.2-105.4] | 103.1 | 1.015 | 28.11 [26.89-28.34] | 26.75 | 1.051 | 1.61 |
| N2 | plan_v09 | 94.4 [94.1-94.5] | 93.9 | 1.006 | 29.27 [29.20-29.34] | 29.47 | 0.993 | 1.61 |
| S | alone_gpu | 19.5 [19.5-19.6] | 18.9 | 1.034 | 4.42 [4.40-4.45] | 4.27 | 1.035 | 0.79 |
| S | model_E | 15.4 [15.0-15.6] | 12.9 | 1.191 | 4.85 [4.71-4.89] | 3.28 | 1.478 | 0.79 |
| S | plan_v09 | 10.5 [10.4-10.9] | 11.6 | 0.907 | 3.80 [3.76-3.93] | 3.66 | 1.040 | 1.05 |

| Book | model_E / alone_gpu: measured | predicted | model_E / plan_v09: measured | predicted |
|---|---|---|---|---|
| N3 | 1.058 | 0.920 | 0.951 | 0.905 |
| N2 | 1.003 | 0.978 | 0.960 | 0.908 |
| S | 1.097 | 0.768 | 1.274 | 0.896 |

## Hypotheses

- **HC1 fails** (primary): book N3 ratio 1.058 (registered <= 0.96; predicted 0.920).
- **HC2 fails** (primary): model_E / plan_v09 N3 0.951, N2 0.960 (registered <= 0.95 in both).
- **HC3 fails** (primary, 4 of 6 within 0.90-1.10 in both): N2/alone_gpu E 1.025 T 1.002; N2/model_E E 1.051 T 1.015; N2/plan_v09 E 0.993 T 1.006; N3/alone_gpu E 1.009 T 1.008; N3/model_E E 1.159 T 1.150 OUT; N3/plan_v09 E 1.103 T 1.279 OUT.
- **HC4 holds** (primary): book N2 ratio 1.003 (registered >= 0.95; predicted 0.978).
- **HC5 fails** (primary): measured order N2 < N3 < S (predicted S < N3 < N2).
- **HC6 fails** (secondary): book S ratio 1.097 (registered <= 0.85; predicted 0.768); S/alone_gpu E 1.035 T 1.034; S/model_E E 1.478 T 1.191 OUT; S/plan_v09 E 1.040 T 0.907.
