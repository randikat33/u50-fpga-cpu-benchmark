# V100 with the v0.9 rules (pipe energy, host idle during the kernel): fit to its own measurements (post hoc)

eta 0.986; pipe energy fma 10.26, alu 10.83, xu 15.23 pJ.

| Case | Target | Method | steady meas/pred | one run meas/pred |
|---|---|---|---|---|
| I | 1% | ft | 1.113 | 1.187 |
| I | 0.3% | ft | 1.114 | 0.884 |
| I | 0.1% | ft | 1.117 | 1.108 |
| I | 1% | qe | 0.951 | 1.047 |
| I | 0.3% | qe | 0.963 | 1.066 |
| I | 0.1% | qe | 0.980 | 0.972 |
| II | 1% | ft | 1.108 | 1.381 |
| II | 0.3% | ft | 1.111 | 1.103 |
| II | 0.1% | ft | 1.106 | 1.100 |
| II | 1% | qe | 0.937 | 1.222 |
| II | 0.3% | qe | 0.937 | 1.023 |
| II | 0.1% | qe | 0.956 | 0.955 |
| III | 1% | ft | 1.123 | 1.246 |
| III | 0.3% | ft | 1.124 | 1.110 |
| III | 0.1% | ft | 1.125 | 1.101 |
| III | 1% | qe | 1.033 | 1.130 |
| III | 0.3% | qe | 1.033 | 1.024 |
| III | 0.1% | qe | 1.037 | 1.084 |
| P1 | 1% | ft | 1.154 | 0.715 |
| P1 | 0.3% | ft | 1.140 | 1.103 |
| P1 | 0.1% | ft | 1.127 | 1.229 |
| P1 | 1% | qe | 1.011 | 0.685 |
| P1 | 0.3% | qe | 0.978 | 1.015 |
| P1 | 0.1% | qe | 0.945 | 1.022 |
| B | 1% | ft | 1.124 | 1.010 |
| B | 0.3% | ft | 1.123 | 1.260 |
| B | 0.1% | ft | 1.127 | 1.114 |
| B | 1% | qe | 0.919 | 0.736 |
| B | 0.3% | qe | 0.967 | 1.182 |
| B | 0.1% | qe | 0.930 | 1.018 |
| A | 1% | plain | 1.153 | 1.133 |
| A | 0.3% | plain | 1.153 | 0.788 |
| A | 0.1% | plain | 1.153 | 1.163 |
| A | 1% | cv | 1.238 | 1.164 |
| A | 0.3% | cv | 1.238 | 1.172 |
| A | 0.1% | cv | 1.238 | 1.191 |

Steady state within 30%: 36 of 36 (median 1.112, range 0.919-1.238); one run: 34 of 36 (median 1.102, range 0.685-1.381); time per path-step at steady state: 36 of 36 within 30% (median 1.144, range 0.891-1.293).
