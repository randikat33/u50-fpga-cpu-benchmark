# Round 5: new measurements on the server (Tier 1)

This file explains how to run the new measurements on the server and send the results back.

- **What you run:** one script, `round5_measurements.sh`.
- **Where:** copy it into the suite folder on the server. That is the folder with `config.sh`, `common/`, `energy_long.sh` and `revision_measurements.sh` (`~/Desktop/Thesis_and_jounal_data`).
- **Restarting:** every step can be stopped and started again. Finished runs are skipped.

## 0. One-time preparation (5 minutes)

```bash
cd ~/Desktop/Thesis_and_jounal_data
cp /path/to/round5_measurements.sh . && chmod +x round5_measurements.sh
sudo apt install -y ipmitool linux-tools-$(uname -r) linux-tools-common   # wall power + perf (skip if installed)
sudo -v
taskset -c 95 ./round5_measurements.sh check
```

`check` prints what works and saves it to `results_round5/capabilities.txt`.

- **Wall power must show 1.** If it shows 0, run `sudo ipmitool dcmi power reading` and send me the output.
- **perf must show 1**, and it should list at least `cycles,instructions,LLC-load-misses`.

## 1. Day 1 (about 7 hours; you can leave it running)

```bash
sudo -v
taskset -c 95 ./round5_measurements.sh all
```

`all` runs these steps in order:

1. `check`
2. `hygiene`: stops the file indexer.
3. `session s1`: headline rows plus wall power.
4. `wall`: long windows for wall power.
5. `overlap`: better FPGA hosts. AES uses 1–4 buffer sets per CU, built as a patched copy of your AES host (your original is not changed). Convolution uses smaller strips.
6. `mcperf`: slow core, static vs dynamic schedule.
7. `videoperf`: 240p/1080p decode-thread counters.
8. `pack`

Watch the progress with `tail -f results_round5/round5.log`.

## 2. Day 2 and day 3 (about 1.5 hours each)

Run these on *different days*, and preferably after a reboot on one of them:

```bash
sudo -v
taskset -c 95 ./round5_measurements.sh session s2      # day 2
taskset -c 95 ./round5_measurements.sh session s3      # day 3
taskset -c 95 ./round5_measurements.sh pack
```

## 3. Upload

Upload the newest `results_round5_<date>.tar.gz` to the chat.

Claude will then:

1. unpack it into `paper/inputs/results_round5/`;
2. run `paper/scripts/round5_analysis.py`;
3. update the paper: the wall-power boundary, between-session intervals, the PCIe drift, 240p cache misses and the slow core.

## If something goes wrong

| Problem | What to do |
|---|---|
| `wall power: 0` | `sudo apt install ipmitool`. If `sudo ipmitool dcmi power reading` still fails, enable IPMI-over-KCS in XClarity (BMC settings) or send the error |
| `perf: 0` | `sudo apt install linux-tools-$(uname -r)`. A missing single event is fine; the script keeps the supported ones |
| a run fails | Start the same command again. Failed runs are repeated, finished runs are skipped |
| you need the machine back | Ctrl+C, then start the same command later |
| undo the indexer change | `systemctl --user unmask tracker-miner-fs-3 tracker-extract-3` |

## What each step answers (review points)

| Step | Review point |
|---|---|
| `session s1..s3` | S3: intervals between sessions (days), not only within one; the PCIe link state and DMA bandwidth per session; the AES stream rate drift (6.5 vs 4.6 GB/s) |
| `wall` | S4: energy at the wall (third boundary), and platform power that RAPL does not see (fans, PSU losses, BMC) |
| `videoperf` | S2: whether the faster 240p decoding with the card comes from fewer last-level-cache misses |
| `mcperf` | M7: what makes one core slow (cache, TLB, remote memory?), and whether a dynamic OpenMP schedule removes the slow processes. Outputs are checked to be identical |
| `overlap` | M5: does an overlapped FPGA host reach the model's overlapped bound (AES 12.2 GB/s, convolution 1.36 Gpixel/s) and change any verdict? Outputs are verified |
| `hygiene` | M7: no desktop file indexer running during measurements |
