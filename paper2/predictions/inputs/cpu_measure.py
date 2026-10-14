#!/usr/bin/env python3
"""cpu_measure.py - phase 4 inputs of paper 2: time and energy of the CPU at every chosen setting (CPU only).

For both CPU baselines (matched AVX-512 code, CPU-native code) and both boundaries of paper 1
(s0_c24: one socket, 24 cores; s01_t96: whole server, 96 threads), every setting of results/settings.csv
runs under paper 1's measurement harness (common/run_measured.py: RAPL power at 10 Hz, timing markers,
pinning) and its steady-clock protocol (>= 3 s of untimed warm-up passes, then timed passes in a window
of >= 10 s; protocol v0.4, Section 10):
  set : the setting itself (its pairs and steps); time and energy per pricing run = window / runs
  ss  : the same scheme and time step with many more pairs (steady state, protocol v0.4 secondary outcome);
        only where the setting itself is shorter than STEADY_PATH_STEPS
  tune: once, the AVX-512 interleave (1, 2 or 4 zmm blocks) per scheme, as paper 1's tuning step
Each job is repeated REPS times (rep r uses seed 20261014 + r), interleaved so slow drifts spread evenly.

The server is shared. Before each job the script waits until other processes use less than
QUIET_CORES cores (this includes a card build still running); during each job it records the CPU used
by every process outside the measurement, and a job during which others used more than BUSY_MEAN cores
on average (or BUSY_PEAK in any second) is set aside (folder renamed *_discarded_*) and run again later.

Resumable: a finished job has a DONE file and is skipped. Output: results/cpu_pred/... and
results/cpu_pred_summary.csv (median over reps per job).

Usage (from the project folder, after `make cpu native`; the card is not used):
    python3 scripts/cpu_measure.py --plan
    nohup python3 scripts/cpu_measure.py > results/cpu_measure.log 2>&1 &
    python3 scripts/cpu_measure.py --summary          # summary only (also written at the end)
"""
import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "cpu_pred")
BIN = {"avx512": os.path.join(ROOT, "build", "hq_cpu"), "native": os.path.join(ROOT, "build", "hq_native")}
REPS = 3
WARM_S, WINDOW_S = 3.0, 10.0
STEADY_PATH_STEPS = 1.0e10          # a setting with fewer path-steps also gets a steady-state job
MAX_PAIRS = 2 ** 31
QUIET_CORES, BUSY_MEAN, BUSY_PEAK = 0.5, 1.0, 4.0
MAX_TRIES = 6
HZ = os.sysconf("SC_CLK_TCK")
TUNE = {"ft": ("I", "0.3%"), "qe": ("II", "0.1%")}   # settings used to choose the AVX-512 interleave


# ------------------------------------------------------------------ machine
def node_cpus(n):
    return open(f"/sys/devices/system/node/node{n}/cpulist").read().strip()


def count(spec):
    n = 0
    for part in spec.split(","):
        a, _, b = part.partition("-")
        n += (int(b) - int(a) + 1) if b else 1
    return n


def configs():
    n0 = node_cpus(0)
    n1 = node_cpus(1) if os.path.exists("/sys/devices/system/node/node1") else n0   # (one-node test machines)
    return {"s0_c24": dict(cores=n0.split(",")[0], node="0", places="cores"),
            "s01_t96": dict(cores=f"{n0},{n1}", node="0,1", places="threads")}


# ------------------------------------------------------------------ other processes' CPU (from /proc)
def proc_table():
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            s = open(f"/proc/{d}/stat").read()
            rest = s[s.rindex(")") + 2:].split()
            ppid, flags = int(rest[1]), int(rest[6])
            if flags & 0x00200000 or ppid == 2 or d == "2":
                continue
            out[int(d)] = (ppid, int(rest[11]) + int(rest[12]))
        except (OSError, ValueError, IndexError):
            continue
    return out


def descendants(tab, root):
    kids = {}
    for pid, (ppid, _) in tab.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = set(), [root]
    while todo:
        p = todo.pop()
        if p in out:
            continue
        out.add(p)
        todo += kids.get(p, [])
    return out


class Others:
    """samples, once per second, the CPU (in cores) used by processes outside this script's process tree"""

    def __init__(self):
        self.samples = []          # (t, cores)
        self.lock = threading.Lock()
        self.stop = False
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        me = os.getpid()
        prev, tprev = proc_table(), time.time()
        while not self.stop:
            time.sleep(1.0)
            tab, t = proc_table(), time.time()
            mine = descendants(tab, me)
            used = 0
            for pid, (_, ticks) in tab.items():
                if pid in mine or pid not in prev:
                    continue
                used += max(0, ticks - prev[pid][1])
            with self.lock:
                self.samples.append((t, used / HZ / (t - tprev)))
                self.samples = self.samples[-7200:]
            prev, tprev = tab, t

    def stats(self, t0, t1):
        with self.lock:
            v = [c for t, c in self.samples if t0 <= t <= t1]
        return (sum(v) / len(v), max(v)) if v else (0.0, 0.0)


def wait_quiet(others, log):
    said = 0
    while True:
        time.sleep(1)
        now = time.time()
        mean, _ = others.stats(now - 10, now)
        if len(others.samples) >= 10 and mean < QUIET_CORES:
            return
        if now - said > 300:
            build = subprocess.run(["pgrep", "-f", "vivado|v\\+\\+ |make xclbin"], capture_output=True).returncode == 0
            log(f"waiting: other processes use {mean:.1f} cores" + (" (a card build is still running)" if build else ""))
            said = now


# ------------------------------------------------------------------ jobs
ONLY = None


def settings():
    f = os.path.join(RES, "settings.csv")
    rows = [r for r in csv.DictReader(open(f)) if r["rule"] == "empirical"]
    if ONLY:
        rows = [r for r in rows if r["case"] in ONLY or (r["case"], r["target"]) in TUNE.values()]
    for r in rows:
        r["M"], r["N"] = int(r["steps"]), int(r["pairs"])
        r["tag"] = f"{r['case']}_{r['scheme']}_{r['target'].replace('%', 'pct')}"
    return rows


def tuning():
    f = os.path.join(OUT, "tuning.json")
    return json.load(open(f)) if os.path.exists(f) else None


def jobs(cfgs, reps):
    rows = settings()
    out = []
    for sc, (case, tgt) in TUNE.items():                       # tune: one rep per interleave
        r = next(x for x in rows if x["case"] == case and x["target"] == tgt and x["scheme"] == sc)
        for il in (1, 2, 4):
            out.append(dict(kind="tune", impl="avx512", cfg="s0_c24", case=case, scheme=sc, M=r["M"], N=r["N"],
                            il=il, rep=1, name=f"tune/{sc}_x{il}"))
    steady = {}
    for r in rows:
        if ONLY and r["case"] not in ONLY:
            continue
        k = (r["case"], r["scheme"], r["M"])
        if 2.0 * r["N"] * r["M"] < STEADY_PATH_STEPS:
            n = min(MAX_PAIRS, max(r["N"], int(STEADY_PATH_STEPS / (2 * r["M"]))))
            steady[k] = (n + 63) // 64 * 64
    for rep in range(1, reps + 1):
        for cfg in cfgs:
            for impl in ("avx512", "native"):
                for r in rows:
                    if ONLY and r["case"] not in ONLY:
                        continue
                    out.append(dict(kind="set", impl=impl, cfg=cfg, case=r["case"], scheme=r["scheme"], M=r["M"],
                                    N=r["N"], rep=rep, name=f"{impl}/{cfg}/set_{r['tag']}/rep{rep}"))
                for (case, sc, M), n in sorted(steady.items()):
                    out.append(dict(kind="ss", impl=impl, cfg=cfg, case=case, scheme=sc, M=M, N=n, rep=rep,
                                    name=f"{impl}/{cfg}/ss_{case}_{sc}_M{M}/rep{rep}"))
    return out


def command(j, cfg, tune):
    t = count(cfg["cores"])
    cmd = [BIN[j["impl"]], "--case", j["case"], "--scheme", j["scheme"], "--steps", j["M"], "--a0", 0, "--a1", j["N"],
           "--seed", 20261014 + j["rep"], "--threads", t, "--runs", 1, "--warmup-s", WARM_S, "--window-s", WINDOW_S]
    if j["impl"] == "avx512":
        cmd += ["--impl", "avx512", "--interleave", j.get("il") or tune[j["scheme"]]]
    return [str(x) for x in cmd], t


def run_job(j, cfgs, common, others, log):
    d = os.path.join(OUT, j["name"])
    if os.path.exists(os.path.join(d, "DONE")):
        return True
    if os.path.exists(d):
        shutil.rmtree(d)                                          # a job cut off by a power failure
    cfg = cfgs[j["cfg"]]
    cmd, t = command(j, cfg, tuning())
    env = dict(os.environ, OMP_NUM_THREADS=str(t), OMP_PLACES=cfg["places"], OMP_PROC_BIND="close",
               OMP_DYNAMIC="false")
    wait_quiet(others, log)
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(common, "run_measured.py"), "--out", d, "--cores", cfg["cores"],
                        "--node", cfg["node"], "--timeout", "3600", "--label", j["name"],
                        "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--"] + cmd,
                       env=env, capture_output=True, text=True)
    t1 = time.time()
    mean, peak = others.stats(t0, t1)
    ok = os.path.exists(os.path.join(d, "DONE"))
    json.dump(dict(others_cores_mean=mean, others_cores_peak=peak, t0=t0, t1=t1, cmd=cmd, rc=p.returncode),
              open(os.path.join(d, "contamination.json") if os.path.isdir(d) else os.devnull, "w"), indent=1)
    if ok and (mean > BUSY_MEAN or peak > BUSY_PEAK):
        os.remove(os.path.join(d, "DONE"))
        os.rename(d, f"{d}_discarded_{int(t1)}")
        log(f"  {j['name']}: others used {mean:.2f} cores (peak {peak:.1f}) during the job -> set aside, run again later")
        return False
    if not ok:
        log(f"  {j['name']}: FAILED (rc {p.returncode}); output in {d}\n{p.stdout[-800:]}{p.stderr[-800:]}")
        return False
    r = record(d)
    log(f"  {j['name']}: {r['runs']} runs in {r['window_s']:.1f} s, {r['t_per_run_s'] * 1e3:.3f} ms/run, "
        f"socket {r['P_socket_W']:.0f} W, server {r['P_server_W']:.0f} W, others {mean:.2f} cores")
    return True


# ------------------------------------------------------------------ reading one run
def integrate(rows, t0, t1, col):
    e = 0.0
    for i in range(1, len(rows)):
        a, b = max(rows[i - 1]["t_unix"], t0), min(rows[i]["t_unix"], t1)
        v = rows[i].get(col)
        if b > a and v is not None:
            e += v * (b - a)
    return e


def record(d):
    m = json.load(open(os.path.join(d, "meta.json")))
    txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", txt, re.M))
    rows = []
    for r in csv.DictReader(open(os.path.join(d, "power.csv"))):
        x = {"t_unix": float(r["t_unix"])}
        for c in ("pkg0_w", "dram0_w", "pkg1_w", "dram1_w"):
            try:
                x[c] = float(r[c])
            except (ValueError, KeyError, TypeError):
                x[c] = None
        rows.append(x)
    t0, t1 = m["t_start_marker"], m["t_end_marker"]
    E = {c[:-2]: integrate(rows, t0, t1, c) for c in ("pkg0_w", "dram0_w", "pkg1_w", "dram1_w")}
    w = t1 - t0
    runs = int(res["runs"])
    es, ev = E["pkg0"] + E["dram0"], E["pkg0"] + E["dram0"] + E["pkg1"] + E["dram1"]
    return dict(window_s=w, runs=runs, t_per_run_s=w / runs, path_steps=float(res["path_steps"]),
                E_socket_J=es, E_server_J=ev, P_socket_W=es / w, P_server_W=ev / w,
                E_socket_per_run_J=es / runs, E_server_per_run_J=ev / runs,
                rate_gps=float(res["path_steps"]) * runs / w / 1e9, t_compute_s=float(res["t_compute_s"]),
                ok=int(res.get("ok", 0)))


def summary(cfgs):
    groups = {}
    for j in jobs(list(cfgs), REPS):
        if j["kind"] == "tune":
            continue
        d = os.path.join(OUT, j["name"])
        if not os.path.exists(os.path.join(d, "DONE")):
            continue
        key = j["name"].rsplit("/rep", 1)[0]
        groups.setdefault(key, (j, []))[1].append(record(d))
    out = []
    for key, (j, rs) in sorted(groups.items()):
        med = lambda k: statistics.median(r[k] for r in rs)
        bound = "socket" if j["cfg"] == "s0_c24" else "server"
        out.append(dict(job=key, kind=j["kind"], impl=j["impl"], cfg=j["cfg"], boundary=bound, case=j["case"],
                        scheme=j["scheme"], steps=j["M"], pairs=j["N"], reps=len(rs),
                        t_per_run_s=med("t_per_run_s"), t_per_run_min_s=min(r["t_per_run_s"] for r in rs),
                        t_per_run_max_s=max(r["t_per_run_s"] for r in rs),
                        E_per_run_J=med(f"E_{bound}_per_run_J"), P_W=med(f"P_{bound}_W"),
                        P_socket_W=med("P_socket_W"), P_server_W=med("P_server_W"),
                        rate_gps=med("rate_gps"), runs_per_window=med("runs"), all_ok=int(all(r["ok"] for r in rs))))
    if out:
        f = os.path.join(RES, "cpu_pred_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        print(f"{len(out)} job groups -> {f}")
    return out


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--test", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--only", default="", help="comma-separated cases (default: all)")   # sandbox: short windows, 1 rep
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")),
                    help="folder with paper 1's run_measured.py and power_monitor.py")
    a = ap.parse_args()
    global ONLY, REPS, WARM_S, WINDOW_S, STEADY_PATH_STEPS, QUIET_CORES, BUSY_MEAN, BUSY_PEAK
    if a.test:
        REPS, WARM_S, WINDOW_S, STEADY_PATH_STEPS = 1, 0.2, 0.5, 2e7
        QUIET_CORES, BUSY_MEAN, BUSY_PEAK = 99.0, 99.0, 99.0
    ONLY = [c for c in a.only.split(",") if c] or None
    cfgs = configs()
    if a.summary:
        summary(cfgs)
        return
    js = jobs(list(cfgs), REPS)
    if a.plan:
        left = [j for j in js if not os.path.exists(os.path.join(OUT, j["name"], "DONE"))]
        est = sum(WARM_S + WINDOW_S + 8 + max(0.0, 2 * j["N"] * j["M"] * 4e-9 / count(cfgs[j["cfg"]]["cores"]) - WINDOW_S)
                  for j in left)
        kinds = {k: sum(1 for j in left if j["kind"] == k) for k in ("tune", "set", "ss")}
        print("configs: " + ", ".join(k + " cores " + v["cores"] for k, v in cfgs.items()))
        print(f"jobs left: {len(left)} of {len(js)} ({kinds}); about {est / 3600:.1f} h on a quiet server")
        return
    for f in ("run_measured.py", "power_monitor.py"):
        if not os.path.exists(os.path.join(a.common, f)):
            sys.exit(f"{f} not found in {a.common} (use --common DIR)")
    for b in BIN.values():
        if not os.path.exists(b):
            sys.exit(f"{b} not found: run `make cpu native` first")
    if not a.test:                                       # RAPL must be readable (paper 1 set this up once)
        import glob as _g
        files = _g.glob("/sys/class/powercap/intel-rapl:[0-9]/energy_uj") + _g.glob("/sys/class/powercap/intel-rapl:[0-9]:[0-9]/energy_uj")
        bad = [f for f in files if not os.access(f, os.R_OK)]
        if len(files) < 4 or bad:
            sys.exit("RAPL power counters are not readable. Run once:\n  sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj "
                     "/sys/class/powercap/intel-rapl:*:*/energy_uj\nthen start this script again.")
    os.makedirs(OUT, exist_ok=True)
    logf = open(os.path.join(RES, "cpu_measure_events.log"), "a")

    def log(s):
        line = f"[{time.strftime('%H:%M:%S')}] {s}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    others = Others()
    log(f"cpu_measure started; {len(js)} jobs; configs {cfgs}")
    # 1. tuning (once)
    if tuning() is None:
        best = {}
        for j in [x for x in js if x["kind"] == "tune"]:
            while not run_job(j, cfgs, a.common, others, log):
                pass
            r = record(os.path.join(OUT, j["name"]))
            if j["scheme"] not in best or r["rate_gps"] > best[j["scheme"]][1]:
                best[j["scheme"]] = (j["il"], r["rate_gps"])
        json.dump({k: v[0] for k, v in best.items()}, open(os.path.join(OUT, "tuning.json"), "w"))
        log(f"AVX-512 interleave chosen: {json.load(open(os.path.join(OUT, 'tuning.json')))}")
    # 2. all jobs; a set-aside job goes to the end of the queue
    todo = [j for j in js if j["kind"] != "tune"]
    tries = {}
    while todo:
        j = todo.pop(0)
        log(f"{j['name']} ({len(todo)} left)")
        if not run_job(j, cfgs, a.common, others, log):
            tries[j["name"]] = tries.get(j["name"], 0) + 1
            if tries[j["name"]] < MAX_TRIES:
                todo.append(j)
            else:
                log(f"  {j['name']}: given up after {MAX_TRIES} tries")
    summary(cfgs)
    done = sum(1 for j in js if os.path.exists(os.path.join(OUT, j["name"], "DONE")))
    log("CPU MEASUREMENT FINISHED" if done == len(js) else f"CPU MEASUREMENT INCOMPLETE ({done} of {len(js)} jobs)")
    others.stop = True


if __name__ == "__main__":
    main()
