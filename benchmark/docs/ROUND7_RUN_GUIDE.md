# Round 7: near-break-even test and CPU-native portfolio baseline

`r7_queue.sh` runs everything on its own. It uses the same rules as the round-5/6 queue:

- it pauses while other people use the server;
- it resumes after a power cut;
- it never repeats a finished run.

You only have to do 4 things: copy, setup, start, and e-mail the prediction when the log says so.

## What round 7 adds

**1. Near-break-even test of the model (the reviewers' main remaining point).**

- `09_MC_Asian_Sweep` is the round-6 MC Asian kernel, rebuilt with 16 lanes.
- The host decides at run time how many lanes get work: `--active-lanes 1..16`.
- So the card's speed grows in 16 steps while the CPU's stays fixed. Somewhere in the sweep the verdicts flip:
  - energy per socket first (about 4–5 lanes);
  - then energy per server (about 11);
  - then time per socket (about 9–15).
- The model predicts **where** they flip. As in round 6, the prediction is made and hashed **before** this design ever runs on the card.

**2. CPU-native portfolio baseline.**

- `04_Portfolio/cpu/pf_native.cpp` prices the same portfolio with Box–Muller, AVX-512 intrinsics and FMA. It is not bit-identical to the card, like `mc_native` in round 6.
- The step first checks that its prices agree with the paper's engine within 3 SE.
- Then it measures, 5 times each:
  - the paper's portfolio card;
  - the paper's CPU engine;
  - `pf_native`.

## 1. Copy (2 minutes)

```bash
cd ~/Desktop/Thesis_and_jounal_data
unzip -n ~/Downloads/round7_package.zip
chmod +x r7_queue.sh round7_sweep.sh
ls round7 v2_projects/09_MC_Asian_Sweep v2_projects/04_Portfolio/cpu/pf_native.cpp
```

- `-n` never overwrites anything; all files are new.
- If unzip says the file is not found, check where the browser saved it (`ls ~/Downloads`) and use that path.

## 2. Setup again (1 minute, asks for your sudo password once)

You removed the round-6 autostart and the sudo rule at the end of round 6, so this is needed again:

```bash
./r7_queue.sh setup
```

## 3. Start

```bash
./r7_queue.sh start
```

Watch it any time (Ctrl+C only stops the watching, not the queue):

```bash
watch -n 60 'tail -n 15 ~/Desktop/Thesis_and_jounal_data/r7_queue.log'
```

or `./r7_queue.sh status` for the full table.

Order and rough times:

| Step | Time | What |
|---|---|---|
| r7_check | 5 min | builds, CPU checks, kernel C-sim with active lanes |
| r7_pfnative | ~2 h | portfolio: card vs paper CPU vs CPU-native |
| r7_build | 8–20 h | sweep xclbin; tries 300 MHz, then 250 MHz if timing fails |
| r7_design | 1 min | clock, II, Vivado power from the reports |
| r7_cpu | ~20 min | CPU-only runs for the prediction |
| r7_predict | 1 min | **the prediction**, then the queue waits for you |
| r7_measure | ~2 h | the sweep (16 lane counts × 5 reps) interleaved with CPU runs |
| r7_pack | 1 min | `results_round7_<date>.tar.gz` |

## 4. When the log says `WAITING FOR YOU`

```bash
cd ~/Desktop/Thesis_and_jounal_data
cat results_round7/predictions/predictions.md
cat results_round7/predictions/*.sha256
```

1. E-mail Dr. Hettiarachchi the `predictions.md` file (attached or pasted) and the two SHA-256 lines. Subject, for example: "Round 7 prediction, before measurement". Keep the sent e-mail: the paper cites its time.
2. Then run:

```bash
./r7_queue.sh published
```

The measurement starts by itself.

## 5. At the end

When the log says `ALL DONE`:

- upload the newest `results_round7_*.tar.gz` here;
- then run:

```bash
./r7_queue.sh disable
sudo rm /etc/sudoers.d/thesis-benchmark /etc/tmpfiles.d/thesis-benchmark.conf
```

## If something goes wrong

| Problem | What to do |
|---|---|
| `r7_check` FAILED | Send `.r7_queue/r7_check.out` and `results_round7/logs/check_verify.log` |
| build failed at both clocks | Send `results_round7/logs/09_xclbin*.log` (last 50 lines of each) |
| `design` lists a problem | Send `.r7_queue/r7_design.out`. Do not run `published` |
| `measure` says the card differs from the CPU | Send `results_round7/logs/sweep_card_verify_l*.log`. Nothing else is measured |
| you need the server for yourself | `./r7_queue.sh pause`, later `./r7_queue.sh resume` |
| a step failed 3 times | `./r7_queue.sh status` shows it; send `.r7_queue/<step>.out` |

## Safety

- Nothing here changes the round 1–6 results or the existing xclbins.
- `results_round7/` and `v2_projects/09_MC_Asian_Sweep/build/` are the only new output folders.
