# Round 6: out-of-sample test of the model with two new kernels

> **Shared server?** You do not have to run the steps below by hand. `r56_queue.sh` runs all remaining round-5 and round-6 steps, pauses while other students work, and resumes after power cuts. See `SHARED_SERVER_AUTORUN.md`. The steps below explain what it runs.

## What this tests

The paper's model predicts the card's speedup S and energy advantage A. It needs only three kinds of input:

- the routed design;
- one PCIe number;
- CPU-only runs.

Until now it was checked only on the workloads it was built from. Round 6 applies it to **two kernels written after the model**:

| Project | What | Host class | Expected by the design |
|---|---|---|---|
| `07_FIR` | int16 FIR filter, 32 taps, 2 CUs | streaming (like AES) | PCIe-bound card; CPU probably faster |
| `08_MC_Asian` | Monte Carlo Asian option (GBM), 32 lanes | polling (like MC Heston) | compute-bound; card probably faster |

The prediction is written, hashed and committed **before** either kernel runs on the card. After that, the card is measured.

Numbers are compared with the paper's estimators: bootstrap CIs, min–max ranges, and the tie band 0.8–1.2 for time verdicts.

## 0. Copy the files (5 minutes)

Unzip `round6_package.zip` into the suite folder (`~/Desktop/Thesis_and_jounal_data`). It adds new files only; nothing of yours is overwritten.

```bash
cd ~/Desktop/Thesis_and_jounal_data
unzip -n /path/to/round6_package.zip       # -n: never overwrite; adds round6_new_kernels.sh, round6/, 07_FIR, 08_MC_Asian, 2 files in 03_MC_Heston
sudo -v
chmod +x round6_new_kernels.sh
taskset -c 95 ./round6_new_kernels.sh check
```

`check` does four things:

1. builds the two CPU baselines and FPGA hosts;
2. checks that scalar, AVX-512 VNNI and AVX-512 BW give identical outputs on this CPU;
3. checks that the thread count does not change the outputs;
4. runs the kernel C-simulations with the Vitis headers.

It must end with `check passed`.

## 1. Build the two xclbins (12–20 hours, can run overnight)

```bash
tmux new -s r6
taskset -c 95 ./round6_new_kernels.sh build
```

- The two designs are built one after the other.
- If it stops, run the same command again. A finished xclbin is not rebuilt.
- Logs are in `results_round6/logs/`.

## 2. Design facts (1 minute)

```bash
taskset -c 95 ./round6_new_kernels.sh design
```

This prints the following, read from the build reports:

- kernel clock;
- CU count;
- II;
- the MC pipeline depth;
- Vivado power.

`"problems": []` means everything is fine. If it reports the RING depth ≥ 128, stop and send me the output.

## 3. CPU-only runs (about 45 minutes; the card is not used)

```bash
sudo -v
taskset -c 95 ./round6_new_kernels.sh cputune     # picks VNNI vs BW (FIR) and 1/2/4 blocks (MC)
taskset -c 95 ./round6_new_kernels.sh cpu         # 5 reps, one socket and the whole server
```

## 4. Predict, then publish the prediction (5 minutes)

```bash
taskset -c 95 ./round6_new_kernels.sh predict
```

This writes three things to `results_round6/predictions/`:

- `predictions.md` (a table with S, A and both verdicts per boundary);
- `predictions.json`;
- their SHA-256.

If the suite folder is a git repository, the step also commits them.

**Before step 5, make the prediction public or witnessed.** Use one of these:

- **Preferred:** copy `results_round6/predictions/` into the GitHub repository and push it. The commit time is then public.
- **Or:** e-mail `predictions.md` and the SHA-256 line to Dr. Hettiarachchi before measuring.

The paper will say how the prediction was fixed, so keep that e-mail or commit link.

## 5. Measure on the card (about 1.5 hours)

```bash
sudo -v
taskset -c 95 ./round6_new_kernels.sh measure
taskset -c 95 ./round6_new_kernels.sh pack
```

`measure` runs in this order:

1. Checks that the prediction file is unchanged since it was made.
2. Verifies both kernels on the card. The FIR output must equal the CPU output; the MC Asian result must be bit-identical to the CPU.
3. Runs the card and fresh CPU runs interleaved, 5 repetitions.

Upload `results_round6_<date>.tar.gz`.

## 6. Optional but recommended: MC Heston against a CPU-native CPU baseline (30 minutes)

```bash
taskset -c 95 ./round6_new_kernels.sh mcnative
taskset -c 95 ./round6_new_kernels.sh pack
```

The paper's MC CPU baseline is bit-identical to the card. It therefore uses the card's table-based normal generator, which costs gathers on the CPU.

This step builds `03_MC_Heston/build/mc_native` and compares three things on the headline MC row (67M × 128):

- the paper's MC card;
- the paper's CPU baseline;
- `mc_native`, which uses Box–Muller with vectorised `logf`/`sincosf`, fast-math and double sums.

`mc_native` is not bit-identical to the card. Its prices must agree with the paper's engine within 3 standard errors, and the step checks this first.

In the sandbox it is 1.6× faster per core. This step shows how much that changes S and A on the server.

Your `03_MC_Heston/Makefile` is not replaced. The step only adds `cpu/mc_native.cpp` and `test/native_check.sh`.

## Memory configuration

`check` saves the DIMM list to `results_round6/memory_dimms.txt`. This needs `sudo -v` first. The paper should state how many memory channels are populated, because the streaming CPU baselines (AES, convolution, FIR) may be limited by memory bandwidth.

If you can install DIMMs in the empty channels, tell me before you do. I will send a short script that measures the memory-bound rows again with the new configuration.

## Shortcut

`./round6_new_kernels.sh all` runs `check → build → design → cputune → cpu → predict` and stops there. Then do step 4 (publish), and step 5.

## If something goes wrong

| Problem | What to do |
|---|---|
| `check` says outputs differ | Send `results_round6/logs/check_verify.log`. Do not continue |
| build fails | Send the last 50 lines of `results_round6/logs/<project>_xclbin.log` |
| MC Asian does not fit or route | `ASIAN_LANES=24 ./round6_new_kernels.sh build` (then use `ASIAN_LANES=24` for every later step) |
| `design` reports a negative WNS | Fine: the tool lowered the clock, and the prediction uses the clock stored in the xclbin |
| a run fails | Run the same step again. Finished runs are skipped |
| `predict` refuses | Card runs of the new kernels already exist. Tell me; the prediction would no longer be out-of-sample |

## Workload sizes

| Workload | Card | CPU |
|---|---|---|
| FIR | 64 GiB stream (no-io, as AES), ~15 s | 2 GiB signal in DRAM × 64 (socket) / 128 (server) passes |
| MC Asian | 2^28 paths × 128 dates, 4 runs per window | same, 1 run (socket) / 2 runs (server) per window |
