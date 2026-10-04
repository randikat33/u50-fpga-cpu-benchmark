# Running rounds 5 and 6 unattended on a shared server

`r56_queue.sh` runs every remaining step of rounds 5 and 6 for you. Other students can keep using the server, and the measurements survive power cuts. You no longer start the steps one by one.

## What it does

| Situation | What happens |
|---|---|
| Nobody else is active | The next step runs. The order, and the minimum gaps between sessions, are built in (see "The order" below) |
| Another student starts working (their processes use more than 0.1 core, or system services more than 1 core) | Checked every ~15 s. **A measurement is stopped at once.** The unfinished run, and any run that ended in the last 2 minutes, are discarded and repeated later. |
| Another student starts working during the **xclbin build** (12–20 h) | The build is **frozen**. It uses no CPU, but it keeps its memory. It continues where it stopped. |
| The other student stops | The queue waits until the server has been quiet for **10 minutes**, then continues |
| Power cut or reboot | The server switches itself back on (BMC setting). The queue starts again 10 minutes after boot, and a watchdog checks every 30 minutes. Finished runs are never repeated. |
| You need the machine yourself | `./r56_queue.sh pause`, then later `./r56_queue.sh resume` |

A student who is merely logged in but idle does not stop the measurements. If you prefer to pause whenever anyone else is logged in, start with `STRICT_LOGIN=1 ./r56_queue.sh start`.

Every 2 seconds, the activity of other users is logged to `results_round6/activity.csv`. The analysis can then show that no measured run overlapped anyone else's work.

## One-time setup (5 minutes, needs your sudo password once)

Copy `r56_queue.sh` and `r56_guard.py` into the suite folder (next to `round5_measurements.sh` and `round6_new_kernels.sh`). Then:

```bash
cd ~/Desktop/Thesis_and_jounal_data
chmod +x r56_queue.sh r56_guard.py
./r56_queue.sh setup
./r56_queue.sh start
```

`setup` does four things:

1. makes the RAPL energy counters readable and sets the CPU governor to performance at every boot (the same as the paper's `campaign.sh --setup-autoresume`);
2. allows **only** `ipmitool`, `perf`, `turbostat`, `dmidecode`, `dmesg`, `lspci` and the two monitor-stop commands to run with sudo without a password, so measurements after a reboot work without you;
3. adds the autostart to your crontab (at boot, plus a 30-minute watchdog);
4. sets the BMC **power-restore policy to "always on"**. If that fails, set "Power Restore Policy = Always On" in XClarity or the BIOS by hand.

## Daily use

```bash
./r56_queue.sh status        # what is done, what runs, who else is active, interruptions
tail -f r56_queue.log        # live log
```

**The one thing you must do yourself.** When the log says

`WAITING FOR YOU: publish results_round6/predictions`:

1. push `results_round6/predictions/` to GitHub, or e-mail `predictions.md` to Dr. Hettiarachchi;
2. then run `./r56_queue.sh published`.

The queue keeps working on the other steps meanwhile. The card runs of the new kernels start only after this.

When the log says **ALL DONE**, upload the newest `results_round5_*.tar.gz` and `results_round6_*.tar.gz`, and run `./r56_queue.sh disable`.

## The order

1. Round 5:
   - check
   - hygiene
   - session s1
   - wall
   - overlap
   - mcperf
   - videoperf
2. Round 6:
   - check
   - **build**
   - design
3. Round 5 session s2 (at least 20 h after s1)
4. Round 6:
   - cputune
   - cpu
   - predict
   - mcnative
5. Round 5 session s3 (at least 20 h after s2)
6. Round 6 measure (after `published`)
7. Pack both

Total: about 3–4 days if the server is mostly free.

## Other commands

| Command | Effect |
|---|---|
| `./r56_queue.sh stop` | Stop everything now. The interrupted run is repeated next time. `start` continues. |
| `./r56_queue.sh redo r5_wall` | Run one step again |
| `QUIET_MIN=20 ./r56_queue.sh start` | Wait 20 quiet minutes instead of 10 |
| `./r56_queue.sh disable` | Remove the autostart at the end. Also: `sudo rm /etc/sudoers.d/thesis-benchmark /etc/tmpfiles.d/thesis-benchmark.conf` |
