# Addendum v0.9: predicted pass time and energy

Model inputs: C card+fcpu -19.0 W; stretch card|fcpu 0.990, fcpu|card 0.998; engaged card 17.7 W, fcpu 0.0 W, gpu 0.0 W, scpu 6.5 W; overhead gpu 10.1 mJ, scpu 14.2 mJ; coordinator gpu 10.5 mJ, scpu 15.8 mJ; gaps gpu 0.057 ms, scpu 0.227 ms.

Idle: frodo 98.0 W, Supermicro 64.7 W. R2 deadline 0.1587 s (1.02 x the midpoint of the shortest predicted pass and the lesser of model_E's pass and the V100 alone's measured pass); model_M plans to 0.1555 s (k = 1.020).

| Regime | Policy | T (s) | E (J) | E, additive v0.8 model (J) | from C (J) | engaged, net of coordinator (J) | options card, fcpu, gpu, scpu |
|---|---|---|---|---|---|---|---|
| R1 | alone_card | 29.8355 | 5633.20 | 5633.2 | +0.00 | +0.00 | 120, 0, 0, 0 |
| R1 | alone_fcpu | 2.5320 | 762.12 | 762.1 | +0.00 | +0.00 | 0, 120, 0, 0 |
| R1 | alone_gpu | 0.1818 | 47.13 | 46.6 | +0.00 | +0.00 | 0, 0, 120, 0 |
| R1 | alone_scpu | 9.9131 | 2051.99 | 2052.3 | +0.00 | +0.00 | 0, 0, 0, 120 |
| R1 | model_E | 0.1628 | 43.70 | 45.9 | -1.82 | -0.68 | 16, 40, 60, 4 |
| R1 | model_T | 0.1483 | 67.96 | 69.9 | -2.45 | -1.25 | 4, 1, 63, 52 |
| R1 | model_dev | 0.1844 | 46.14 | 45.6 | -0.20 | -1.00 | 19, 6, 95, 0 |
| R2 | model_M | 0.1555 | 46.12 | 48.3 | -2.52 | -0.95 | 1, 32, 48, 39 |
| R3 | alone_card_f | 29.8355 | 3702.90 | - | +0.00 | +0.00 | 120, 0, 0, 0 |
| R3 | alone_fcpu_f | 2.5320 | 598.30 | - | +0.00 | +0.00 | 0, 120, 0, 0 |
| R3 | model_E_f | 1.3837 | 292.09 | - | -25.97 | +0.00 | 4, 116, 0, 0 |

## Hypotheses: predicted values

- **HS5** in each regime, the corrected model policy's measured pass energy is at most 1.05 x the corrected oracle's (R1 model_E vs oracle, R2 model_M vs oracle_M, R3 model_E_f vs oracle_f; median over 5 repeats of the per-repeat ratio). Predicted: {'predicted_ratio': 1.0, 'threshold': 1.05}
- **HS6** R1: model_E's measured pass energy is at most 1.02 x alone_gpu's (medians of 5 repeats). Predicted: {'predicted_ratio': 0.9272, 'threshold': 1.02, 'model_E_is_alone_gpu': False}
- **HS7** R2: model_M meets the deadline (pass time <= D) in at least 4 of 5 repeats, and its measured pass energy is lower than model_T's if that comparison is called (predicted ratio >= 1.05). Predicted: {'deadline_s': 0.1587, 'deadline_rule': "1.02 x the midpoint of the shortest predicted pass and the lesser of model_E's pass and the V100 alone's measured pass", 'planning_deadline_s': 0.1555, 'margin_k': 1.02, 'predicted_T_s': 0.1555, 'predicted_ratio_T_over_M': 1.4735, 'call': 'called'}
- **HS8** R3: model_E_f's measured pass energy is at least 5% lower than each of alone_card_f's and alone_fcpu_f's. Predicted: {'predicted_saving': {'alone_card_f': 0.9211192986957476, 'alone_fcpu_f': 0.5118074792185725}, 'threshold': 0.05, 'predicted_holds': True}
- **HS9** every planned policy (R1-R3, 11 policies) has measured/predicted pass energy and pass time within 0.90-1.10 (medians of 5). Predicted: {'low': 0.9, 'high': 1.1, 'n': 11}
- **HS10** R1: model_E's measured pass energy is lower than model_T's if called (predicted ratio >= 1.05) (v0.8 HS4, repeated). Predicted: {'predicted_ratio': 1.555, 'call': 'called'}

## The v0.8 plans under the corrected model (post hoc; the v0.8 runs are not a test of v0.9)

| Policy | E v0.8 pred (J) | E corrected (J) | E measured (J) | meas / corrected | T v0.8 pred (s) | T corrected (s) | T measured (s) | meas / corrected |
|---|---|---|---|---|---|---|---|---|
| alone_card | 5633.2 | 5633.2 | 5841.3 | 1.037 | 29.8355 | 29.8355 | 31.7186 | 1.063 |
| alone_fcpu | 762.1 | 762.1 | 749.7 | 0.984 | 2.5320 | 2.5320 | 2.3685 | 0.935 |
| alone_gpu | 46.6 | 47.1 | 49.1 | 1.041 | 0.1940 | 0.1818 | 0.2047 | 1.126 |
| alone_scpu | 2052.3 | 2052.0 | 2007.6 | 0.978 | 9.9370 | 9.9131 | 9.8499 | 0.994 |
| model_E | 44.3 | 47.7 | 52.4 | 1.099 | 0.1721 | 0.1668 | 0.1809 | 1.085 |
| model_M | 45.1 | 47.1 | 53.3 | 1.130 | 0.1623 | 0.1585 | 0.1801 | 1.136 |
| model_T | 64.6 | 63.7 | 64.7 | 1.015 | 0.1542 | 0.1493 | 0.1980 | 1.326 |
| model_dev | 46.3 | 48.7 | 51.7 | 1.061 | 0.1917 | 0.1803 | 0.2030 | 1.126 |
| round_robin | 5046.5 | 5192.5 | 5307.9 | 1.022 | 26.1045 | 26.0987 | 28.5768 | 1.095 |

sha256 d4ad8c03b807be53e3f4b75763cb013c70dc75ea600dd97b66f0e233c66a778f
