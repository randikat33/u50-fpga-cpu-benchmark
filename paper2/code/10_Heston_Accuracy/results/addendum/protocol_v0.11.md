# Paper 2 protocol, addendum v0.11: the scheduler with a calibrated model

**Authors:** Randika T. P. G. Pathiranage, Dasuni Hewawasam (University of Moratuwa)
**Date:** 28 October 2026
**Registers:** before any run of the v0.11 scheduling jobs (`scripts/sched_queue_v11t.py`). It goes on GitHub (this file, the code, the prediction and the calibration data, with SHA-256) and as a new version (v8) of the Zenodo record "Paper 2: registered protocols and predictions", before the runs. Nothing registered earlier changes.

**Prediction:** `results/predictions_sched_v11/predictions_sched_v11.json`, SHA-256 `22292d48526f0ab4fed84b68900db18b904db582097e43e778237d3d45eee829`.

---

## 1. Why

v0.9's scheduling hypothesis HS6 failed: on the v0.8 book (M), the model's least-energy plan (model_E) used more energy than the V100 alone (measured ratio 1.046, predicted 0.927).

Addendum v0.10 planned a test of four books with the v0.9 model unchanged. Its run-code check showed that the model over-predicts the V100 on short options, so that test was not registered (v0.10 says so). This addendum re-measures the model's stream inputs first, rebuilds the model from them, and then tests it on books it has not seen.

## 2. What was measured before this registration (disclosed)

### 2.1 The v0.10 run-code check (frodo, 28 Oct 2026, 17:12–17:15 UTC)

8 jobs, 1 repeat, 3 s windows. Each book was run with the v0.9 model's plans. The check was not a result; its numbers were seen before the v0.11 model was fixed (§3.4).

| Book (1% / 0.3% / 0.1%) | Policy | T meas (ms) | E meas (J) | v0.9 model: E pred, meas/pred | v0.11 model: T pred, meas/pred | v0.11: E pred, meas/pred |
|---|---|---|---|---|---|---|
| S (20 / 0 / 0) | alone_gpu | 19.3 | 4.35 | 7.30, 0.597 | 18.9, 1.024 | 4.27, 1.019 |
| S | model_E of v0.9 | 12.0 | 3.66 | 4.44, 0.824 | 11.6, 1.038 | 3.66, 1.001 |
| S2 (16 / 4 / 0) | alone_gpu | 24.0 | 6.11 | 8.56, 0.714 | 23.9, 1.002 | 5.70, 1.072 |
| S2 | model_E of v0.9 | 16.9 | 5.23 | 6.00, 0.871 | 16.6, 1.020 | 5.12, 1.021 |
| M (8 / 8 / 4) | alone_gpu | 175.1 | 45.51 | 47.13, 0.966 | 175.0, 1.000 | 44.80, 1.016 |
| M | model_E of v0.9 | 162.0 | 46.87 | 43.70, 1.072 | 159.5, 1.016 | 47.12, 0.995 |
| L (0 / 0 / 20) | alone_gpu | 750.7 | 197.74 | 193.65, 1.021 | 748.5, 1.003 | 192.52, 1.027 |
| L | model_E of v0.9 | 748.0 | 195.69 | 193.24, 1.013 | 743.5, 1.006 | 192.52, 1.017 |

The two right-hand columns were computed after the model of §3 was fixed. They are in-sample for one choice in that model (§3.4).

### 2.2 The calibration runs (frodo and the Supermicro, 28 Oct 2026, 18:09–18:26 UTC)

16 jobs, input measurements (`scripts/sched_queue_v11.py`, SHA-256 `38b57c95…`). Each job is one device alone on a calibration book:
- **K1:** 10 / 0 / 0 per case (1% options only);
- **K2:** 0 / 10 / 0 (0.3% options only);
- **K:** 10 / 10 / 0, with only the card's Heston QE options or only its Asian CV options.

The plans used the v0.9 model's lower-energy method on that device. Each job had a 30 s window (at least 3 passes) and 2 repeats. No job was set aside. Per pass:

| Job | Device | T (ms), rep 1 / 2 | E (J), rep 1 / 2 | v0.9 model predicted |
|---|---|---|---|---|
| K1_alone_fcpu | frodo's CPU | 161.9 / 161.7 | 29.97 / 29.94 | 205.2 ms, 36.75 J |
| K1_alone_gpu | V100 | 10.0 / 10.2 | 2.26 / 2.30 | 14.4 ms, 3.83 J |
| K1_alone_scpu | Supermicro's CPU | 110.0 / 110.1 | 23.55 / 23.69 | 131.4 ms, 25.93 J |
| K2_alone_fcpu | frodo's CPU | 297.8 / 298.1 | 65.46 / 65.57 | 338.8 ms, 81.05 J |
| K2_alone_gpu | V100 | 22.2 / 22.2 | 5.75 / 5.74 | 26.3 ms, 6.79 J |
| K2_alone_scpu | Supermicro's CPU | 709.0 / 708.4 | 149.94 / 147.96 | 728.0 ms, 149.79 J |
| K_card_qe | card (QE design) | 3447.7 / 3482.9 | 625.17 / 631.65 | 3832.6 ms, 700.82 J |
| K_card_asian | card (Asian design) | 10.4 / 11.3 | 1.87 / 2.03 | 11.0 ms, 1.99 J |

Every option's price was within 1.54 eps of its reference.

---

## 3. The v0.11 model

**Code:** `scripts/calib_model_v11.py`. It starts from the registered v0.9 model, rebuilt from `predictions_sched_v09.json` (SHA-256 d4ad8c03…, checked). Unchanged from v0.9:
- frodo's idle power (97.97 W);
- the stretches s;
- the engaged powers;
- the card's other-design change (5.35 s, 146.7 J);
- the 0.1% classes;
- the card's ft, bft and bqe designs;
- the policies and the optimiser.

### 3.1 Times

- **Per class.** Each 1% and 0.3% class takes the median interval between consecutive results of that class in the calibration (46 classes, at least 162 intervals each).
- **Per device overheads, two terms:**
  - h(d) per class: the median over class batches of (batch span − sum of the class times);
  - g(d) per pass: the median over jobs of (pass time − n_classes × h − sum of times).

| | h per class (ms) | g per pass (ms) |
|---|---|---|
| fcpu | 18.96 | 4.58 |
| gpu | 0.073 | 1.11 |
| scpu | 6.36 | 0.76 |
| card, QE design | 544.8 | −18.9 |
| card, Asian design | 0.47 | 0.39 |

- **Remote devices (V100, Supermicro CPU)** pay h for every class, including a single class.
- **Local devices (card, fcpu)** keep their worker when they have a single class, and then pay v0.9's latency per pass. This is v0.9's rule, and the check's single-class runs on the card and on fcpu agree with it.

### 3.2 Energy

- **The Supermicro's idle power** was re-measured: 63.22 W (v0.9: 64.70 W). This is the median power of that server during the 8 calibration jobs that did not use it.
- **fcpu, gpu, scpu.** Energy above idle on the device's own server = a × (sum of v0.9's single-run energies) + b × busy time. a and b are solved from K1 and K2 (medians of the two repeats). On the other server, P_x × busy time (frodo's coordinator for the remote devices):

| | a | b (W) | P_x (W) |
|---|---|---|---|
| fcpu | 0.519 | 18.09 | 0.00 |
| gpu | 0.885 | 27.70 | 16.40 |
| scpu | 0.081 | 46.55 | −0.08 |

- **Card.** Energy above idle = r × (sum of single-run energies + 19.77 W × overhead time). 19.77 W is v0.9's same-design change power. r = 0.985 for the QE design and 0.605 for the Asian design; 1 for designs not calibrated.
- **0.1% classes** keep v0.9's time and energy, including v0.9's dispatch terms. The calibration has no 0.1% option, and v0.9 predicted book L within 3% in the check.

### 3.3 Combining

The powers of devices that are busy at the same time add:
- v0.9's pair term C (card + fcpu, −19.0 W, measured on long runs) is dropped;
- the coordinator's power is paid even while frodo is awake for other reasons.

### 3.4 How §3.3 was chosen (disclosed)

The rule in §3.3 was selected after the v0.10 check. Six variants were compared on the check's 8 jobs: with or without C, with or without v0.9's coordinator rule, and with or without the engaged powers. §3.3 had the smallest largest error: measured/predicted pass energy 0.995–1.072, against 1.016–1.097 with v0.9's rules.

The check books are therefore in-sample for this model. Book S is run again here as a secondary test only.

### 3.5 In-sample fit (the calibration itself)

Measured/predicted pass time is 0.984–1.009 and pass energy 0.984–1.025 for every calibration job, except K_card_asian (1.056 and 1.055).

---

## 4. Books, policies, predictions

Books: options per case at 1% / 0.3% / 0.1%; six cases, 120 options, book seed 20261023 as in v0.8–v0.10.
- **N3:** 10 / 10 / 0. New: no run of this book exists. **Primary.**
- **N2:** 6 / 12 / 2. New. **Primary.**
- **S:** 20 / 0 / 0. Seen in the v0.10 check. **Secondary.**

Policies, for every book:
- **alone_gpu:** every option on the V100.
- **model_E:** the v0.11 model's least-energy plan over the four devices.
- **plan_v09:** the plan the registered v0.9 model chooses for the book (its model_E). This policy shows what the calibration changes.

Predicted, per pass:

| Book | Policy | T (ms) | E (J) | Options: card, fcpu, gpu, scpu | E / alone_gpu |
|---|---|---|---|---|---|
| N3 | alone_gpu | 31.1 | 7.77 | 0, 0, 120, 0 | 1.000 |
| N3 | model_E | 27.5 | 7.15 | 0, 20, 100, 0 | **0.920** |
| N3 | plan_v09 | 24.7 | 7.90 | 20, 10, 75, 15 | 1.017 |
| N2 | alone_gpu | 106.8 | 27.36 | 0, 0, 120, 0 | 1.000 |
| N2 | model_E | 103.1 | 26.75 | 0, 20, 100, 0 | **0.978** |
| N2 | plan_v09 | 93.9 | 29.47 | 12, 21, 58, 29 | 1.077 |
| S | alone_gpu | 18.9 | 4.27 | 0, 0, 120, 0 | 1.000 |
| S | model_E | 12.9 | 3.28 | 20, 20, 80, 0 | **0.768** |
| S | plan_v09 | 11.6 | 3.66 | 20, 20, 60, 20 | 0.856 |

Predicted model_E / plan_v09: N3 0.905, N2 0.908, S 0.896.

**The model's mechanism.** The two servers idle at 161.2 W together, so a shorter pass saves energy. On books of short options, model_E moves the Asian options (and on book S, the basket options) from the V100 to frodo's CPU or the card, which shortens the V100's pass. The 0.1% options keep the V100 busy for most of the pass, so on N2 there is little to gain.

The prediction also lists books S2, M and L, for the record; they are not run here.

## 5. Hypotheses

All values are measured per pass, as medians of 5 repeats.

- **HC1 (primary).** Book N3: model_E's pass energy is at most 0.96 × alone_gpu's (predicted 0.920).
- **HC2 (primary).** Books N3 and N2: the v0.11 plan uses less energy than the v0.9 model's plan for the same book. E(model_E) / E(plan_v09) is at most 0.95 in both (predicted 0.905 and 0.908).
- **HC3 (primary).** Books N3 and N2: all 6 jobs have measured/predicted pass energy and pass time within 0.90–1.10.
- **HC4 (primary).** Book N2: model_E saves less than 5% against alone_gpu (ratio at least 0.95; predicted 0.978). The model predicts no worthwhile saving when the book has 0.1% options.
- **HC5 (primary).** The ratio model_E / alone_gpu increases in the predicted order S < N3 < N2.
- **HC6 (secondary; book S seen).** model_E's pass energy is at most 0.85 × alone_gpu's (predicted 0.768), and the three S jobs are within 0.90–1.10 of the predicted pass energy and time.

## 6. Procedure

- **Code:** `scripts/sched_queue_v11t.py` on frodo; the v0.9 agent runs on the Supermicro, with the V100 locked at 1,245 MHz.
- **Jobs:** 5 repeats. In each repeat the books run in the order N3, N2, S. In each book the three policies are rotated by the repeat: repeat k starts with policy (k − 1) mod 3, in the order alone_gpu, model_E, plan_v09. 45 jobs, about 1.2 h.
- **Each job:** the quietness wait, 20 s idle, 1 warm-up pass, then a window of at least 60 s and 3 passes.
- **A job is repeated** (up to 5 tries) if:
  - frodo's or the Supermicro's other users used more than 1 core on average, or more than 4 in any second;
  - the GPU's median SM clock was not 1,245 ± 12.5 MHz;
  - any option's run was not ok.

  These are v0.9's rules.
- **Two days.** Repeats 1–3 run on day 1 and repeats 4–5 on a later date.
- **Run-code check.** `--check` runs after this registration: every job once for 3 s, before day 1. It logs only whether each job ran, with no times or energies, and it is not a result. If a job fails, the fix is reported as a deviation.

## 7. Analysis

`scripts/analyse_sched_v11.py` reads `results/sched_v11_summary.csv` (written by `sched_queue_v11t.py --summary`). It computes the medians of 5 repeats per job, the ratios, and HC1–HC6 exactly as stated in §5.

Also reported, without a test:
- the per-server energies;
- each device's busy time against the model's.

v0.11 runs no oracle policy. The bug in v0.9's `corrected()` (it scales only dE and adds the overhead and coordinator terms unscaled; found during the v0.10 check) therefore does not affect v0.11. It is reported with the v0.9 results.

## 8. Code and inputs registered

**Code:** `calib_model_v11.py`, `predict_sched_v11.py`, `sched_queue_v11t.py`, `analyse_sched_v11.py`. Used unchanged:
- `sched_model.py` (fe7984a3…);
- `sched_run.py` (ed5254bf…);
- `sched_queue_v11.py` (38b57c95…, the calibration);
- `sched_agent.py`;
- `sched_client.py`.

Hashes are in `protocol_v0.11_code.sha256`.

**Inputs:**
- the v0.9 prediction (d4ad8c03…);
- the calibration data: `results/sched_calib_v11` (as `sched_calib_v11.tar.xz`), `sched_calib_v11_summary.csv` and `sched_calib_v11_events.log`;
- the v0.10 check data: `sched_v10_check.tar.xz` and `sched_v10_check_events.log`.

The prediction is deterministic: `python3 scripts/predict_sched_v11.py` rebuilds it from these files with the same SHA-256.
