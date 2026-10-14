# Paper 2: predictions made before any card run

Created (UTC): 2026-10-14T14:14:13Z. Protocol v0.4 (commit b790a13). Inputs: settings.csv sha256 52a4ff82eab005ac…, cpu_pred_summary.csv sha256 7a0b4a923d208c06….

## Card designs (routed)

| Kernel | Clock (MHz) | WNS (ns) | CUs × lanes | Peak (G path-steps/s) | Vivado power (W) |
|---|---|---|---|---|---|
| FT | 225 | +0.024 | 2 × 14 | 12.60 | 40.75 |
| QE | 225 | +0.022 | 2 × 7 | 6.30 | 46.50 |

Constants (paper 1, frozen): P0 = 17.0 W, host while a polling card runs H = 49.151 W (socket) / 92.94 W (server), launch overhead t0 = 0.7 ms per call.

## Energy to target per pricing run (J), primary outcome, and the predicted winner

Columns: card, CPU matched (AVX-512, bit-identical to the card), CPU native; FT and QE each. Steady state (secondary outcome: no start-up cost, many options per run) in the second table.

### Primary (one pricing run, start-up included)

| Case | Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|---|
| I | 1% | socket | 0.374 | 0.0641 | 1 | 0.142 | 0.269 | 0.0309 | cpu_native_qe |
| I | 1% | server | 0.564 | 0.103 | 2.42 | 0.352 | 0.268 | 0.0361 | cpu_native_qe |
| I | 0.3% | socket | 11.8 | 0.438 | 29.6 | 2.01 | 11.7 | 0.678 | card_qe |
| I | 0.3% | server | 17.6 | 0.649 | 24.2 | 1.71 | 10.8 | 0.651 | card_qe |
| I | 0.1% | socket | 419 | 14 | 1.02e+03 | 68.4 | 419 | 24.7 | card_qe |
| I | 0.1% | server | 623 | 20.4 | 739 | 50.3 | 379 | 23.5 | card_qe |
| II | 1% | socket | 1.32 | 0.132 | 3.25 | 0.419 | 1.27 | 0.138 | card_qe |
| II | 1% | server | 1.98 | 0.201 | 5.05 | 0.6 | 1.19 | 0.141 | cpu_native_qe |
| II | 0.3% | socket | 56.2 | 0.954 | 137 | 4.41 | 56.1 | 1.54 | card_qe |
| II | 0.3% | server | 83.6 | 1.4 | 111 | 3.71 | 51.2 | 1.47 | card_qe |
| II | 0.1% | socket | 2.01e+03 | 33.3 | 4.55e+03 | 159 | 1.96e+03 | 57.5 | card_qe |
| II | 0.1% | server | 2.99e+03 | 48.5 | 3.42e+03 | 115 | 1.81e+03 | 54.6 | card_qe |
| III | 1% | socket | 0.112 | 0.0619 | 0.223 | 0.0815 | 0.0607 | 0.0278 | cpu_native_qe |
| III | 1% | server | 0.175 | 0.0997 | 0.368 | 0.225 | 0.0646 | 0.0328 | cpu_native_qe |
| III | 0.3% | socket | 2.63 | 0.206 | 6.59 | 0.884 | 2.59 | 0.298 | card_qe |
| III | 0.3% | server | 3.92 | 0.309 | 4.9 | 0.722 | 2.4 | 0.287 | cpu_native_qe |
| III | 0.1% | socket | 92 | 2.97 | 225 | 15.2 | 92.1 | 5.39 | card_qe |
| III | 0.1% | server | 137 | 4.34 | 163 | 11.2 | 83.8 | 5.14 | card_qe |
| P1 | 1% | socket | 0.0479 | 0.0476 | 0.0139 | 0.011 | 0.0026 | 0.00178 | cpu_native_qe |
| P1 | 1% | server | 0.0794 | 0.0788 | 0.0549 | 0.0404 | 0.00875 | 0.00776 | cpu_native_qe |
| P1 | 0.3% | socket | 0.072 | 0.0592 | 0.105 | 0.108 | 0.0316 | 0.0222 | cpu_native_qe |
| P1 | 0.3% | server | 0.115 | 0.0958 | 0.124 | 0.121 | 0.036 | 0.0264 | cpu_native_qe |
| P1 | 0.1% | socket | 0.904 | 0.27 | 2.43 | 1.39 | 0.912 | 0.336 | card_qe |
| P1 | 0.1% | server | 1.35 | 0.402 | 1.85 | 1.07 | 0.852 | 0.319 | cpu_native_qe |

### Steady state (secondary)

| Case | Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Winner |
|---|---|---|---|---|---|---|---|---|---|
| I | 1% | socket | 0.264 | 0.0174 | 0.647 | 0.0859 | 0.266 | 0.0299 | card_qe |
| I | 1% | server | 0.393 | 0.0254 | 0.471 | 0.062 | 0.242 | 0.0277 | card_qe |
| I | 0.3% | socket | 11.7 | 0.386 | 28.4 | 1.89 | 11.7 | 0.673 | card_qe |
| I | 0.3% | server | 17.3 | 0.563 | 20.7 | 1.36 | 10.7 | 0.629 | card_qe |
| I | 0.1% | socket | 419 | 13.9 | 1.02e+03 | 68 | 419 | 24.7 | card_qe |
| I | 0.1% | server | 623 | 20.3 | 739 | 49 | 379 | 23.4 | card_qe |
| II | 1% | socket | 1.26 | 0.0814 | 3.08 | 0.385 | 1.27 | 0.138 | card_qe |
| II | 1% | server | 1.87 | 0.119 | 2.23 | 0.279 | 1.15 | 0.128 | card_qe |
| II | 0.3% | socket | 55.9 | 0.903 | 137 | 4.27 | 56.2 | 1.53 | card_qe |
| II | 0.3% | server | 83.1 | 1.32 | 102 | 3.09 | 51.1 | 1.43 | card_qe |
| II | 0.1% | socket | 2.01e+03 | 33.2 | 4.55e+03 | 158 | 1.96e+03 | 57.6 | card_qe |
| II | 0.1% | server | 2.99e+03 | 48.4 | 3.42e+03 | 115 | 1.81e+03 | 54.4 | card_qe |
| III | 1% | socket | 0.0577 | 0.0143 | 0.149 | 0.0776 | 0.0593 | 0.0267 | card_qe |
| III | 1% | server | 0.0858 | 0.0209 | 0.106 | 0.0554 | 0.0538 | 0.0249 | card_qe |
| III | 0.3% | socket | 2.56 | 0.159 | 6.3 | 0.861 | 2.58 | 0.296 | card_qe |
| III | 0.3% | server | 3.8 | 0.231 | 4.53 | 0.615 | 2.34 | 0.276 | card_qe |
| III | 0.1% | socket | 91.9 | 2.93 | 225 | 15.1 | 92.1 | 5.38 | card_qe |
| III | 0.1% | server | 137 | 4.26 | 163 | 10.9 | 83.8 | 5.05 | card_qe |
| P1 | 1% | socket | 0.00118 | 0.000863 | 0.00609 | 0.00432 | 0.00172 | 0.000971 | card_qe |
| P1 | 1% | server | 0.00175 | 0.00126 | 0.00436 | 0.00304 | 0.00148 | 0.000792 | cpu_native_qe |
| P1 | 0.3% | socket | 0.0247 | 0.0123 | 0.0943 | 0.0914 | 0.0305 | 0.0214 | card_qe |
| P1 | 0.3% | server | 0.0367 | 0.0179 | 0.0672 | 0.064 | 0.0269 | 0.0187 | card_qe |
| P1 | 0.1% | socket | 0.857 | 0.222 | 2.4 | 1.37 | 0.909 | 0.334 | card_qe |
| P1 | 0.1% | server | 1.27 | 0.324 | 1.7 | 0.973 | 0.82 | 0.305 | cpu_native_qe |

### Time per pricing run (s), primary

| Case | Target | Boundary | card_ft | card_qe | cpu_matched_ft | cpu_matched_qe | cpu_native_ft | cpu_native_qe | Fastest |
|---|---|---|---|---|---|---|---|---|---|
| I | 1% | socket | 0.00435 | 0.000886 | 0.00631 | 0.000915 | 0.00149 | 0.000176 | cpu_native_qe |
| I | 1% | server | 0.00435 | 0.000886 | 0.0093 | 0.00129 | 0.000751 | 0.000112 | cpu_native_qe |
| I | 0.3% | socket | 0.132 | 0.0048 | 0.168 | 0.0117 | 0.0652 | 0.00382 | cpu_native_qe |
| I | 0.3% | server | 0.132 | 0.0048 | 0.0721 | 0.00481 | 0.0303 | 0.00191 | cpu_native_qe |
| I | 0.1% | socket | 4.66 | 0.146 | 5.71 | 0.394 | 2.33 | 0.139 | cpu_native_qe |
| I | 0.1% | server | 4.66 | 0.146 | 2.08 | 0.141 | 1.07 | 0.0685 | cpu_native_qe |
| II | 1% | socket | 0.0149 | 0.00159 | 0.0183 | 0.00245 | 0.00708 | 0.000782 | cpu_native_qe |
| II | 1% | server | 0.0149 | 0.00159 | 0.026 | 0.00191 | 0.00335 | 0.00042 | cpu_native_qe |
| II | 0.3% | socket | 0.625 | 0.0102 | 0.764 | 0.0255 | 0.312 | 0.00864 | cpu_native_qe |
| II | 0.3% | server | 0.625 | 0.0102 | 0.351 | 0.0104 | 0.143 | 0.00428 | cpu_native_qe |
| II | 0.1% | socket | 22.4 | 0.348 | 27.2 | 0.909 | 11.3 | 0.323 | cpu_native_qe |
| II | 0.1% | server | 22.4 | 0.348 | 9.91 | 0.32 | 5.07 | 0.159 | cpu_native_qe |
| III | 1% | socket | 0.00143 | 0.000863 | 0.00136 | 0.000476 | 0.000338 | 0.000158 | cpu_native_qe |
| III | 1% | server | 0.00143 | 0.000863 | 0.00125 | 0.000787 | 0.000182 | 0.000102 | cpu_native_qe |
| III | 0.3% | socket | 0.0295 | 0.00237 | 0.0369 | 0.00515 | 0.0144 | 0.00168 | cpu_native_qe |
| III | 0.3% | server | 0.0295 | 0.00237 | 0.0137 | 0.00203 | 0.00672 | 0.000843 | cpu_native_qe |
| III | 0.1% | socket | 1.02 | 0.0313 | 1.25 | 0.0874 | 0.512 | 0.0305 | cpu_native_qe |
| III | 0.1% | server | 1.02 | 0.0313 | 0.456 | 0.0313 | 0.235 | 0.0151 | cpu_native_qe |
| P1 | 1% | socket | 0.000718 | 0.000713 | 9.46e-05 | 7.64e-05 | 1.52e-05 | 1.13e-05 | cpu_native_qe |
| P1 | 1% | server | 0.000718 | 0.000713 | 0.000207 | 0.000152 | 3.12e-05 | 2.88e-05 | cpu_native_qe |
| P1 | 0.3% | socket | 0.000986 | 0.000835 | 0.00062 | 0.000661 | 0.000175 | 0.000126 | cpu_native_qe |
| P1 | 0.3% | server | 0.000986 | 0.000835 | 0.000379 | 0.000378 | 0.000103 | 8.17e-05 | cpu_native_qe |
| P1 | 0.1% | socket | 0.0102 | 0.00303 | 0.0136 | 0.00817 | 0.00507 | 0.00188 | cpu_native_qe |
| P1 | 0.1% | server | 0.0102 | 0.00303 | 0.00519 | 0.00301 | 0.00239 | 0.00092 | cpu_native_qe |

## Hypotheses (protocol v0.4, Section 8): predicted verdicts

- **H1** (work ratio; calibration, not a card prediction): holds; I 1% 32.3, I 0.3% 64.2, I 0.1% 64.0, II 1% 33.0, II 0.3% 131.6, II 0.1% 128.8, III 1% 8.6, III 0.3% 34.3, III 0.1% 66.8.
- **H2** (QE/FT cost per path-step within 1.2–3 on every platform): predicted to FAIL. Median (range) by platform: card socket 2.00 (2.00–6.00); card server 2.00 (2.00–6.00); cpu_matched socket 4.41 (4.20–6.41); cpu_matched server 4.22 (3.93–6.08); cpu_native socket 3.72 (3.03–5.02); cpu_native server 3.96 (3.00–4.67).
- **H3** (cases I–III: CPU socket running QE needs less energy than the card running FT, every target): predicted to hold (steady state: fails). Card-FT energy / CPU-QE energy: I 1% 2.63, I 0.3% 5.90, I 0.1% 6.13, II 1% 3.16, II 0.3% 12.75, II 0.1% 12.67, III 1% 1.38, III 0.3% 2.98, III 0.1% 6.06.
- **H4** (P1: the card running FT beats the CPU running QE at 0.3% only): predicted to FAIL (steady state: fails). 1%: card FT 0.0479 J vs CPU QE 0.011 J, 0.3%: card FT 0.072 J vs CPU QE 0.108 J, 0.1%: card FT 0.904 J vs CPU QE 1.39 J.
- **H5** is tested against these predictions after the card runs; **H6** is not measured on the card.

