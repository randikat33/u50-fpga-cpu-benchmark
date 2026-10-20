# GPU prediction (protocol v0.6, Section 22a), registered before any timed GPU run

Generated 2026-10-20T09:00:38Z. V100 at a locked 1245 MHz; eta = 0.986 (fp32 0.977, int 0.978, sfu 1.003); t0 spin 0.044 ms, block 0.092 ms; latency L = 4 cycles; blocks per SM {'heston_ft': 5, 'heston_qe': 4, 'basket_ft': 4, 'basket_qe': 4, 'asian_plain': 5, 'asian_cv': 3}.

## Readings of Section 22a used here (fixed before any timed GPU run)

- Instruction counts are dynamic estimates from the SASS of the built binary (scripts/gpu_counts.py): cuRAND Philox refills at 1/4 per word drawn, the word-select switch at its average path, the every-second-step normal pair at 1/2, both branches of QE and of normcdfinvf at 1 (full divergence); work outside the step loop is counted per pair (and per asset in Case B).
- Pipes on sm_70: the SASS of the integer microbenchmark shows that its IMAD instructions run on the FMA pipe beside the ALU pipe (93.9 thread instructions per SM per clock, against 64 for one pipe); IMAD is therefore counted on the FMA pipe and the integer benchmark's peak is 96 (4 ALU + 2 IMAD per 6 instructions). Conversions share the XU (16 per clock) with MUFU.
- A warp issues a dependent instruction at most every L = 4 cycles; this limits short runs with few resident warps.
- Resident blocks per SM from the registers per thread (ptxas; 256 threads per block, 65,536 registers per SM, at most 8 blocks).
- Host power: socket 1 and socket 0 from the microbenchmarks in the same wait mode (busy: fp32 benchmark; waiting: start-up benchmark). Winners use, per setting, the wait mode with less predicted steady-state energy at the primary boundary.
- Power: the registered rule (busy power of the binding pipe's microbenchmark, weighted by duty) is the tested prediction; the 'mix' variant (each pipe's busy power in proportion to its utilisation) is reported for information only.
- Card and CPU energies used to name winners are the measured Part A values; GPU values are this prediction.

## Steady-state rate and energy per path-step (registered power rule, spin wait, GPU + socket 1)

| Kernel | M (steps) | Rate (G path-steps/s) | Binding pipe | P_gpu (W) | P_gpu mix (W) | nJ per path-step |
|---|---|---|---|---|---|---|
| heston_ft | 1280 | 241.2 | fma | 99.6 | 158.3 | 0.554 |
| heston_ft | 5120 | 241.6 | fma | 99.6 | 158.3 | 0.553 |
| heston_ft | 20480 | 241.6 | fma | 99.6 | 158.3 | 0.553 |
| heston_qe | 40 | 99.3 | fma | 99.6 | 145.4 | 1.346 |
| heston_qe | 80 | 100.4 | fma | 99.6 | 145.2 | 1.332 |
| heston_qe | 320 | 101.2 | fma | 99.6 | 145.0 | 1.321 |
| heston_ft | 1920 | 241.4 | fma | 99.6 | 158.3 | 0.554 |
| heston_ft | 7680 | 241.6 | fma | 99.6 | 158.3 | 0.553 |
| heston_ft | 30720 | 241.7 | fma | 99.6 | 158.3 | 0.553 |
| heston_qe | 60 | 100.0 | fma | 99.6 | 145.3 | 1.337 |
| heston_qe | 240 | 101.1 | fma | 99.6 | 145.0 | 1.322 |
| heston_ft | 160 | 238.2 | fma | 99.6 | 158.3 | 0.561 |
| heston_ft | 640 | 240.8 | fma | 99.6 | 158.3 | 0.555 |
| heston_ft | 2560 | 241.4 | fma | 99.6 | 158.3 | 0.554 |
| heston_qe | 20 | 97.2 | fma | 99.6 | 145.9 | 1.375 |
| heston_ft | 8 | 186.9 | fma | 99.6 | 158.7 | 0.715 |
| heston_ft | 16 | 210.8 | fma | 99.6 | 158.5 | 0.634 |
| heston_ft | 64 | 233.1 | fma | 99.6 | 158.3 | 0.573 |
| heston_qe | 1 | 54.2 | fma | 99.6 | 154.8 | 2.466 |
| heston_qe | 4 | 83.3 | fma | 99.6 | 148.8 | 1.605 |
| heston_qe | 8 | 91.5 | fma | 99.6 | 147.0 | 1.461 |
| basket_ft | 8 | 25.6 | fma | 99.6 | 151.7 | 5.221 |
| basket_ft | 16 | 27.8 | fma | 99.6 | 152.2 | 4.802 |
| basket_ft | 64 | 29.8 | fma | 99.6 | 152.6 | 4.487 |
| basket_qe | 1 | 7.9 | fma | 99.6 | 144.6 | 16.848 |
| basket_qe | 2 | 9.8 | fma | 99.6 | 144.8 | 13.625 |
| basket_qe | 8 | 11.9 | fma | 99.6 | 145.1 | 11.208 |
| asian_plain | 250 | 434.9 | issue | 99.6 | 166.9 | 0.307 |
| asian_cv | 250 | 347.8 | xu | 60.1 | 141.0 | 0.271 |

## Every setting (spin wait, registered power): time and energy at the GPU's primary boundary (GPU + socket 1)

| Case | Target | Method | Run time (ms) | Bound | E one run (J) | E steady (J) | E steady, mix power (J) |
|---|---|---|---|---|---|---|---|
| I | 1% | ft | 0.428 | latency | 0.05451 | 0.02052 | 0.02951 |
| I | 0.3% | ft | 8.725 | fma | 1.164 | 0.9037 | 1.3 |
| I | 0.1% | ft | 243.050 | fma | 32.48 | 32.47 | 46.71 |
| I | 1% | qe | 0.072 | latency | 0.006877 | 0.001544 | 0.002073 |
| I | 0.3% | qe | 0.305 | fma | 0.03802 | 0.03388 | 0.04542 |
| I | 0.1% | qe | 9.372 | fma | 1.25 | 1.212 | 1.623 |
| II | 1% | ft | 0.777 | fma | 0.1011 | 0.09785 | 0.1408 |
| II | 0.3% | ft | 32.595 | fma | 4.354 | 4.331 | 6.23 |
| II | 0.1% | ft | 1171.636 | fma | 156.6 | 155.8 | 224.1 |
| II | 1% | qe | 0.099 | fma | 0.0105 | 0.007165 | 0.009611 |
| II | 0.3% | qe | 0.732 | fma | 0.09509 | 0.07953 | 0.1067 |
| II | 0.1% | qe | 21.828 | fma | 2.915 | 2.892 | 3.874 |
| III | 1% | ft | 0.092 | latency | 0.009625 | 0.00454 | 0.006531 |
| III | 0.3% | ft | 1.677 | fma | 0.2214 | 0.1988 | 0.286 |
| III | 0.1% | ft | 54.330 | fma | 7.259 | 7.128 | 10.25 |
| III | 1% | qe | 0.058 | latency | 0.005041 | 0.001295 | 0.001743 |
| III | 0.3% | qe | 0.178 | fma | 0.02113 | 0.01437 | 0.01933 |
| III | 0.1% | qe | 2.024 | fma | 0.2678 | 0.2594 | 0.3482 |
| P1 | 1% | ft | 0.047 | latency | 0.003532 | 0.0001179 | 0.00017 |
| P1 | 0.3% | ft | 0.075 | fma | 0.007265 | 0.002192 | 0.003158 |
| P1 | 0.1% | ft | 0.606 | fma | 0.07826 | 0.06888 | 0.09912 |
| P1 | 1% | qe | 0.045 | latency | 0.003296 | 4.672e-05 | 6.601e-05 |
| P1 | 0.3% | qe | 0.059 | fma | 0.005212 | 0.001295 | 0.001771 |
| P1 | 0.1% | qe | 0.216 | fma | 0.02609 | 0.02138 | 0.02897 |
| B | 1% | ft | 0.065 | latency | 0.005953 | 0.0005988 | 0.0008319 |
| B | 0.3% | ft | 0.135 | fma | 0.0153 | 0.01212 | 0.01689 |
| B | 0.1% | ft | 3.212 | fma | 0.4265 | 0.4068 | 0.5679 |
| B | 1% | qe | 0.052 | latency | 0.004227 | 0.0002329 | 0.0003112 |
| B | 0.3% | qe | 0.076 | fma | 0.007434 | 0.004238 | 0.00567 |
| B | 0.1% | qe | 1.033 | fma | 0.1353 | 0.1264 | 0.1694 |
| A | 1% | plain | 0.091 | latency | 0.009403 | 0.001426 | 0.002143 |
| A | 0.3% | plain | 0.279 | issue | 0.03458 | 0.01575 | 0.02367 |
| A | 0.1% | plain | 1.221 | issue | 0.1605 | 0.1417 | 0.213 |
| A | 1% | cv | 0.092 | latency | 0.007689 | 8.661e-06 | 1.61e-05 |
| A | 0.3% | cv | 0.092 | latency | 0.007689 | 3.465e-05 | 6.439e-05 |
| A | 0.1% | cv | 0.092 | latency | 0.007689 | 0.0003118 | 0.0005795 |

## Predicted lowest-energy combination (card and CPU: measured, Part A; GPU: this prediction)

| Case | Target | Boundary | One run | Steady state | GPU / card, steady (best method each) |
|---|---|---|---|---|---|
| I | 1% | socket | gpu_qe | gpu_qe | 0.093 |
| I | 1% | server | gpu_qe | gpu_qe | 0.071 |
| I | 0.3% | socket | gpu_qe | gpu_qe | 0.092 |
| I | 0.3% | server | gpu_qe | gpu_qe | 0.070 |
| I | 0.1% | socket | gpu_qe | gpu_qe | 0.090 |
| I | 0.1% | server | gpu_qe | gpu_qe | 0.069 |
| II | 1% | socket | gpu_qe | gpu_qe | 0.094 |
| II | 1% | server | gpu_qe | gpu_qe | 0.071 |
| II | 0.3% | socket | gpu_qe | gpu_qe | 0.094 |
| II | 0.3% | server | gpu_qe | gpu_qe | 0.071 |
| II | 0.1% | socket | gpu_qe | gpu_qe | 0.092 |
| II | 0.1% | server | gpu_qe | gpu_qe | 0.071 |
| III | 1% | socket | gpu_qe | gpu_qe | 0.083 |
| III | 1% | server | gpu_qe | gpu_qe | 0.066 |
| III | 0.3% | socket | gpu_qe | gpu_qe | 0.083 |
| III | 0.3% | server | gpu_qe | gpu_qe | 0.066 |
| III | 0.1% | socket | gpu_qe | gpu_qe | 0.082 |
| III | 0.1% | server | gpu_qe | gpu_qe | 0.065 |
| P1 | 1% | socket | cpu_native_qe | gpu_qe | 0.062 |
| P1 | 1% | server | gpu_qe | gpu_qe | 0.046 |
| P1 | 0.3% | socket | gpu_qe | gpu_qe | 0.080 |
| P1 | 0.3% | server | gpu_qe | gpu_qe | 0.063 |
| P1 | 0.1% | socket | gpu_qe | gpu_qe | 0.080 |
| P1 | 0.1% | server | gpu_qe | gpu_qe | 0.063 |
| B | 1% | socket | cpu_native_qe | gpu_qe | 0.132 |
| B | 1% | server | gpu_qe | gpu_qe | 0.101 |
| B | 0.3% | socket | gpu_qe | gpu_qe | 0.100 |
| B | 0.3% | server | gpu_qe | gpu_qe | 0.078 |
| B | 0.1% | socket | gpu_qe | gpu_qe | 0.078 |
| B | 0.1% | server | gpu_qe | gpu_qe | 0.062 |
| A | 1% | socket | cpu_native_cv | gpu_cv | 0.034 |
| A | 1% | server | cpu_native_cv | gpu_cv | 0.027 |
| A | 0.3% | socket | cpu_native_cv | gpu_cv | 0.034 |
| A | 0.3% | server | cpu_native_cv | gpu_cv | 0.027 |
| A | 0.1% | socket | gpu_cv | gpu_cv | 0.034 |
| A | 0.1% | server | gpu_cv | gpu_cv | 0.027 |

## Hypotheses (Section 26): predicted verdicts

- **HG2** GPU QE/FT time per path-step between 2.06 and 3.72: predicted holds (predicted ratio 2.39)
- **HG3** GPU fixed cost per pricing run below 0.2 ms: predicted holds (predicted t0 0.044 ms spin, 0.092 ms block)
- **HG1** method gain > platform gain (three platforms, steady state, primary boundaries), every case and target: predicted fails (smallest margin 0.14)
- **HG4** break-even rule picks every single-run winner: predicted holds
- **HG5** GPU energy within 30% (one run, steady state) and steady-state winner right in >= 30 of 36: predicted holds
- **HF1** non-polling card host wait cuts the card's steady-state energy per option at the socket boundary by >= 10% in every case: predicted holds
- **HF2** matched code RMSE/eps <= 1.1 at all strikes, upper 95% bound <= 1.25, 200 replicates: predicted holds

**Open question (V100 against U50, steady state, primary boundaries):** the V100 is predicted to need less energy in 18 of 18 case-target pairs.

