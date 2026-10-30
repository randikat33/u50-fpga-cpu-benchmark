# v0.11 scheduling: exploratory diagnosis (not registered)

Written after the registered analysis (analysis_sched_v11/sched_v11.md). Nothing here changes a verdict.

## 1. Energy per server (medians of the 5 registered repeats; J per pass)

| Book | Policy | frodo above idle: meas / model | Supermicro above idle: meas / model | idle x T: meas / model |
|---|---|---|---|---|
| N3 | alone_gpu | 0.52 / 0.51 | 2.26 / 2.24 | 5.05 / 5.01 |
| N3 | model_E | 0.93 / 0.61 | 2.26 / 2.10 | 5.10 / 4.44 |
| N3 | plan_v09 | 0.88 / 0.91 | 2.74 / 3.01 | 5.09 / 3.98 |
| N2 | alone_gpu | 0.67 / 0.65 | 10.13 / 9.48 | 17.26 / 17.22 |
| N2 | model_E | 1.29 / 0.81 | 10.00 / 9.33 | 16.87 / 16.61 |
| N2 | plan_v09 | 2.93 / 4.02 | 11.13 / 10.32 | 15.22 / 15.13 |
| S | alone_gpu | 0.34 / 0.31 | 0.93 / 0.92 | 3.15 / 3.05 |
| S | model_E | 1.60 / 0.53 | 0.75 / 0.66 | 2.49 / 2.09 |
| S | plan_v09 | 1.39 / 0.71 | 0.72 / 1.08 | 1.69 / 1.87 |

## 2. Device finish times and the V100's stream (repeat 1, passes after the first; ms)

| Book | Policy | dev_T measured | busy predicted | V100: options, classes, first result, same-class sum, boundaries, kernel busy |
|---|---|---|---|---|
| N3 | alone_gpu | gpu 30.84 | gpu 31.10 | 120, 9, 0.81, 28.02, 1.83, 22.70 |
| N3 | model_E | fcpu 7.25, gpu 31.24 | fcpu 7.26, gpu 27.52 | 100, 8, 1.16, 27.64, 1.61, 20.28 |
| N3 | plan_v09 | card 15.68, fcpu 3.75, gpu 26.58, scpu 30.75 | card 10.25, fcpu 2.53, gpu 24.67, scpu 22.10 | 75, 6, 1.25, 23.30, 1.26, 18.29 |
| N2 | alone_gpu | gpu 106.78 | gpu 106.83 | 120, 14, 0.86, 66.24, 39.42, 96.74 |
| N2 | model_E | fcpu 9.95, gpu 104.07 | fcpu 6.83, gpu 103.07 | 100, 13, 1.21, 63.20, 39.20, 94.47 |
| N2 | plan_v09 | card 58.59, fcpu 73.42, gpu 93.95, scpu 43.57 | card 59.59, fcpu 67.80, gpu 93.87, scpu 35.32 | 58, 8, 11.09, 54.18, 28.00, 87.39 |
| S | alone_gpu | gpu 19.00 | gpu 18.89 | 120, 6, 0.68, 17.22, 0.97, 11.76 |
| S | model_E | card 8.49, fcpu 4.26, gpu 14.62 | card 3.07, fcpu 8.17, gpu 12.95 | 80, 4, 0.60, 12.95, 0.59, 7.79 |
| S | plan_v09 | card 6.17, fcpu 3.56, gpu 10.21, scpu 9.66 | card 11.16, fcpu 8.17, gpu 10.21, scpu 11.60 | 60, 3, 0.63, 8.99, 0.31, 6.28 |

## 2b. The V100's time per class per pass (ms; mean over the passes of repeat 1; sum of same-class intervals)

- **N3 alone_gpu:** P1 ft 8 steps 0.97; I qe 40 steps 1.29; I qe 80 steps 4.12; II qe 60 steps 10.88; III qe 20 steps 3.93; P1 qe 4 steps 1.19; B ft 8 steps 1.09; B qe 2 steps 1.30; A cv 250 steps 3.42; total 28.20
- **N3 model_E:** P1 ft 8 steps 1.68; I qe 40 steps 3.52; I qe 80 steps 4.24; II qe 60 steps 10.95; III qe 20 steps 3.81; P1 qe 4 steps 1.15; B ft 8 steps 1.13; B qe 2 steps 1.39; total 27.86
- **N3 plan_v09:** I qe 40 steps 3.10; I qe 80 steps 4.18; II qe 60 steps 10.42; III qe 20 steps 3.81; P1 qe 4 steps 0.52; B qe 2 steps 1.37; total 23.41
- **N2 alone_gpu:** P1 ft 8 steps 0.56; I qe 40 steps 0.72; I qe 80 steps 5.14; I qe 320 steps 10.14; II qe 60 steps 12.00; II qe 240 steps 22.98; III qe 20 steps 3.82; III qe 40 steps 2.58; P1 qe 4 steps 1.37; P1 qe 8 steps 0.22; B ft 8 steps 0.61; B qe 2 steps 1.65; B qe 8 steps 1.15; A cv 250 steps 3.31; total 66.24
- **N2 model_E:** P1 ft 8 steps 0.53; I qe 40 steps 0.71; I qe 80 steps 5.25; I qe 320 steps 10.16; II qe 60 steps 11.98; II qe 240 steps 23.04; III qe 20 steps 3.87; III qe 40 steps 2.61; P1 qe 4 steps 1.42; P1 qe 8 steps 0.23; B ft 8 steps 0.61; B qe 2 steps 1.74; B qe 8 steps 1.18; total 63.32
- **N2 plan_v09:** I qe 320 steps 10.09; II qe 60 steps 11.35; II qe 240 steps 23.12; III qe 20 steps 3.78; III qe 40 steps 2.65; P1 qe 8 steps 0.40; B qe 2 steps 1.66; B qe 8 steps 1.19; total 54.24
- **S alone_gpu:** P1 ft 8 steps 2.17; I qe 40 steps 2.87; II qe 60 steps 4.28; III qe 20 steps 2.45; B ft 8 steps 2.33; A cv 250 steps 3.41; total 17.51
- **S model_E:** P1 ft 8 steps 3.22; I qe 40 steps 3.24; II qe 60 steps 4.23; III qe 20 steps 2.40; total 13.09
- **S plan_v09:** II qe 60 steps 4.31; III qe 20 steps 2.54; B ft 8 steps 2.45; total 9.30

## 3. The V100's interval between results of one class (ms; repeat 1): measured in the plan / calibrated

- **N3 alone_gpu:** A 0.3% cv 0.185 / 0.181; A 1% cv 0.175 / 0.169; B 0.3% qe 0.117 / 0.180; B 1% ft 0.072 / 0.121; I 0.3% qe 0.445 / 0.469; I 1% qe 0.180 / 0.144; II 0.3% qe 0.898 / 0.908; II 1% qe 0.206 / 0.199; III 0.3% qe 0.267 / 0.248; III 1% qe 0.066 / 0.124; P1 0.3% qe 0.077 / 0.079; P1 1% ft 0.057 / 0.110
- **N3 model_E:** B 0.3% qe 0.153 / 0.180; B 1% ft 0.120 / 0.121; I 0.3% qe 0.461 / 0.469; I 1% qe 0.122 / 0.144; II 0.3% qe 0.896 / 0.908; II 1% qe 0.206 / 0.199; III 0.3% qe 0.258 / 0.248; III 1% qe 0.067 / 0.124; P1 0.3% qe 0.128 / 0.079; P1 1% ft 0.033 / 0.110
- **N3 plan_v09:** B 0.3% qe 0.149 / 0.180; I 0.3% qe 0.446 / 0.469; I 1% qe 0.108 / 0.144; II 0.3% qe 0.834 / 0.908; II 1% qe 0.207 / 0.199; III 0.3% qe 0.255 / 0.248; III 1% qe 0.128 / 0.124; P1 0.3% qe 0.129 / 0.079
- **N2 alone_gpu:** A 0.1% cv 0.175 / 0.247; A 0.3% cv 0.168 / 0.181; A 1% cv 0.153 / 0.169; B 0.1% qe 1.144 / 1.265; B 0.3% qe 0.124 / 0.180; B 1% ft 0.097 / 0.121; I 0.1% qe 10.134 / 10.047; I 0.3% qe 0.464 / 0.469; I 1% qe 0.178 / 0.144; II 0.1% qe 22.966 / 22.913; II 0.3% qe 0.940 / 0.908; II 1% qe 0.105 / 0.199; III 0.1% qe 2.575 / 2.514; III 0.3% qe 0.262 / 0.248; III 1% qe 0.066 / 0.124; P1 0.1% qe 0.185 / 0.363; P1 0.3% qe 0.099 / 0.079; P1 1% ft 0.059 / 0.110
- **N2 model_E:** B 0.1% qe 1.175 / 1.265; B 0.3% qe 0.127 / 0.180; B 1% ft 0.098 / 0.121; I 0.1% qe 10.154 / 10.047; I 0.3% qe 0.453 / 0.469; I 1% qe 0.042 / 0.144; II 0.1% qe 23.030 / 22.913; II 0.3% qe 0.937 / 0.908; II 1% qe 0.122 / 0.199; III 0.1% qe 2.594 / 2.514; III 0.3% qe 0.270 / 0.248; III 1% qe 0.065 / 0.124; P1 0.1% qe 0.189 / 0.363; P1 0.3% qe 0.099 / 0.079; P1 1% ft 0.044 / 0.110
- **N2 plan_v09:** B 0.1% qe 1.202 / 1.265; B 0.3% qe 0.162 / 0.180; I 0.1% qe 10.087 / 10.047; II 0.1% qe 23.162 / 22.913; II 0.3% qe 0.513 / 0.908; II 1% qe 0.202 / 0.199; III 0.1% qe 2.663 / 2.514; III 0.3% qe 0.257 / 0.248; III 1% qe 0.071 / 0.124; P1 0.1% qe 0.403 / 0.363
- **S alone_gpu:** A 1% cv 0.182 / 0.169; B 1% ft 0.120 / 0.121; I 1% qe 0.164 / 0.144; II 1% qe 0.207 / 0.199; III 1% qe 0.125 / 0.124; P1 1% ft 0.081 / 0.110
- **S model_E:** I 1% qe 0.143 / 0.144; II 1% qe 0.199 / 0.199; III 1% qe 0.125 / 0.124; P1 1% ft 0.030 / 0.110
- **S plan_v09:** B 1% ft 0.124 / 0.121; II 1% qe 0.213 / 0.199; III 1% qe 0.130 / 0.124

## 4. Robustness repeat (rep 6; 29 Oct 2026 21:08-21:25 UTC, about 6 h after the registered runs; not registered)

| Book | Policy | rep 6 E (J) | registered median (J) | difference |
|---|---|---|---|---|
| N3 | alone_gpu | 7.90 | 7.83 | +0.8% |
| N3 | model_E | 8.26 | 8.29 | -0.3% |
| N3 | plan_v09 | 8.66 | 8.71 | -0.7% |
| N2 | alone_gpu | 28.00 | 28.03 | -0.1% |
| N2 | model_E | 28.02 | 28.11 | -0.3% |
| N2 | plan_v09 | 29.29 | 29.27 | +0.1% |
| S | alone_gpu | 4.40 | 4.42 | -0.3% |
| S | model_E | 4.70 | 4.85 | -2.9% |
| S | plan_v09 | 3.78 | 3.80 | -0.6% |

Ratios in rep 6: N3 model_E/alone_gpu 1.046, model_E/plan_v09 0.954; N2 model_E/alone_gpu 1.000, model_E/plan_v09 0.957; S model_E/alone_gpu 1.068, model_E/plan_v09 1.244.
