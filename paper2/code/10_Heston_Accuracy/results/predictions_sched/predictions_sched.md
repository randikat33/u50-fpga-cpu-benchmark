# Addendum v0.8: predicted pass time and energy per policy

Idle power: frodo 98.0 W, Supermicro 64.7 W (two-server 162.7 W).

| Policy | T (s) | E (J) | E / model_E | options card, fcpu, gpu, scpu |
|---|---|---|---|---|
| alone_card | 29.836 | 5633.2 | 127.12 | 120, 0, 0, 0 |
| alone_fcpu | 2.532 | 762.1 | 17.20 | 0, 120, 0, 0 |
| alone_gpu | 0.194 | 46.6 | 1.05 | 0, 0, 120, 0 |
| alone_scpu | 9.937 | 2052.3 | 46.31 | 0, 0, 0, 120 |
| round_robin | 26.105 | 5046.5 | 113.88 | 30, 30, 30, 30 |
| model_E | 0.172 | 44.3 | 1.00 | 8, 0, 52, 60 |
| model_T | 0.154 | 64.6 | 1.46 | 16, 4, 48, 52 |
| model_M | 0.162 | 45.1 | 1.02 | 15, 0, 37, 68 |
| model_dev | 0.192 | 46.3 | 1.04 | 8, 0, 112, 0 |

model_E's T is 11.6% above the shortest pass found (0.154 s); model_M's deadline 0.1631 s.

sha256 6d3ac807b7658c9bba6eea4502e4f7284608267575cb07596d7dd1ef24f08ed3
