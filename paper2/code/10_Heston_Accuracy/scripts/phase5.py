#!/usr/bin/env python3
"""phase5.py - the measurements of paper 2 (protocol v0.4, Section 10 item 3), after the registered prediction
(commit 88b30f6). One session per call; the protocol asks for two sessions (run them on different days).

Platforms: the card (one run gives both energy boundaries: card rails + socket 0, and card rails + both
sockets), and the two CPU programs (matched AVX-512, native) on one socket (24 cores) and on the whole server
(96 threads), as in scripts/cpu_measure.py. Every setting of results/settings.csv ("set") and the steady state of
each scheme and time step ("ss": many pairs, only where the setting itself is short) is measured REPS times per
session (seed differs per rep and session), interleaved, under paper 1's harness (common/run_measured.py:
power at 10 Hz incl. the card rails, markers, pinning; card runs with common/xrt_timing.ini) and the
steady-clock protocol (>= 3 s untimed warm-up, timed window >= 10 s; a run shorter than the window is repeated
back to back and its time and energy are the window's divided by the number of runs).

Correctness on every card run: the card's moments must equal the matched CPU program's for the same seed (same
pairs), which is checked against that rep's CPU run (mom_hash). Accuracy: every run's price error per strike.

The server is shared: the same guard as cpu_measure.py (wait until others use < 0.5 cores; set a run aside and
repeat it if others used > 1 core on average or > 4 in any second during it). Optional wall power: if
`sudo -n ipmitool dcmi power reading` works, wall power is logged at about 1 Hz for the whole session.

Usage (from the project folder; xclbins built, `make cpu native host`):
    python3 scripts/phase5.py --session 1 --plan
    nohup python3 scripts/phase5.py --session 1 > results/phase5_s1.log 2>&1 &
    python3 scripts/phase5.py --session 1 --summary
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu_measure as CM   # configs, count, Others, wait_quiet, settings (same rules as the CPU measurement)

ROOT, RES = CM.ROOT, CM.RES
BIN = {"avx512": CM.BIN["avx512"], "native": CM.BIN["native"], "fpga": os.path.join(ROOT, "build", "hq_fpga")}
XCLBIN = {s: os.path.join(ROOT, "build", s, f"hq_{s}.xclbin") for s in ("ft", "qe")}
REPS = 5
WARM_S, WINDOW_S = 3.0, 10.0
STEADY_PATH_STEPS = 1.0e10
MAX_PAIRS = 2 ** 31
MAX_TRIES = 6
REF = {"I": (35.849770, 13.084670, 0.295774), "II": (37.169665, 16.649223, 5.138190),
       "III": (38.772044, 21.795288, 9.983068), "P1": (31.260968, 8.095646, 0.141170)}


def jobs(session, cfgs, reps):
    rows = [r for r in CM.settings() if not CM.ONLY or r["case"] in CM.ONLY]
    steady = {}
    for r in rows:
        if 2.0 * r["N"] * r["M"] < STEADY_PATH_STEPS:
            n = min(MAX_PAIRS, max(r["N"], int(STEADY_PATH_STEPS / (2 * r["M"]))))
            steady[(r["case"], r["scheme"], r["M"])] = (n + 63) // 64 * 64
    plats = [("fpga", "card")] + [(im, c) for im in ("avx512", "native") for c in cfgs]
    out = []
    for rep in range(1, reps + 1):
        seed = 20261100 + 100 * session + rep
        for impl, cfg in plats:
            for r in rows:
                out.append(dict(kind="set", impl=impl, cfg=cfg, case=r["case"], scheme=r["scheme"], target=r["target"],
                                M=r["M"], N=r["N"], rep=rep, seed=seed, name=f"s{session}/{impl}/{cfg}/set_{r['tag']}/rep{rep}"))
            for (case, sc, M), n in sorted(steady.items()):
                out.append(dict(kind="ss", impl=impl, cfg=cfg, case=case, scheme=sc, target="", M=M, N=n, rep=rep,
                                seed=seed, name=f"s{session}/{impl}/{cfg}/ss_{case}_{sc}_M{M}/rep{rep}"))
    return out


def command(j, cfgs, tune):
    a = ["--case", j["case"], "--scheme", j["scheme"], "--steps", j["M"], "--a0", 0, "--a1", j["N"], "--seed", j["seed"],
         "--runs", 1, "--warmup-s", WARM_S, "--window-s", WINDOW_S]
    if j["impl"] == "fpga":
        return [str(x) for x in [BIN["fpga"], "--xclbin", XCLBIN[j["scheme"]], "--warmup", 1] + a], 1
    t = CM.count(cfgs[j["cfg"]]["cores"])
    a += ["--threads", t]
    if j["impl"] == "avx512":
        a += ["--impl", "avx512", "--interleave", tune[j["scheme"]]]
    return [str(x) for x in [BIN[j["impl"]]] + a], t


def record(d):
    m = json.load(open(os.path.join(d, "meta.json")))
    txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", txt, re.M))
    rows = []
    for r in csv.DictReader(open(os.path.join(d, "power.csv"))):
        x = {"t_unix": float(r["t_unix"])}
        for c in ("pkg0_w", "dram0_w", "pkg1_w", "dram1_w", "card_w"):
            try:
                x[c] = float(r[c])
            except (ValueError, KeyError, TypeError):
                x[c] = None
        rows.append(x)
    t0, t1 = m["t_start_marker"], m["t_end_marker"]
    E = {c[:-2]: CM.integrate(rows, t0, t1, c) for c in ("pkg0_w", "dram0_w", "pkg1_w", "dram1_w", "card_w")}
    w, runs = t1 - t0, int(res["runs"])
    sock = E["pkg0"] + E["dram0"]
    serv = sock + E["pkg1"] + E["dram1"]
    out = dict(window_s=w, runs=runs, t_per_run_s=w / runs, path_steps=float(res["path_steps"]),
               E_cpu_socket_J=sock, E_cpu_server_J=serv, E_card_J=E["card"], P_card_W=E["card"] / w,
               P_socket_W=sock / w, P_server_W=serv / w, ok=int(res.get("ok", 0)), mom_hash=res.get("mom_hash", ""),
               card_samples=sum(1 for x in rows if t0 <= x["t_unix"] <= t1 and x["card_w"] is not None))
    for k in range(3):
        if f"price{k}" in res:
            out[f"price{k}"], out[f"se{k}"] = float(res[f"price{k}"]), float(res[f"se{k}"])
    return out


def run_job(j, cfgs, common, others, log, tune):
    d = os.path.join(RES, "phase5", j["name"])
    if os.path.exists(os.path.join(d, "DONE")):
        return True
    if os.path.exists(d):
        shutil.rmtree(d)
    cmd, t = command(j, cfgs, tune)
    if j["impl"] == "fpga":
        c0 = cfgs["s0_c24"]
        env = dict(os.environ, OMP_NUM_THREADS="1")
        extra = ["--cores", c0["cores"], "--node", "0", "--xclbin", XCLBIN[j["scheme"]],
                 "--state-dir", os.path.join(RES, "phase5", ".state")]
        ini = os.path.join(common, "xrt_timing.ini")
        if os.path.exists(ini):
            extra += ["--xrt-ini", ini]
    else:
        cfg = cfgs[j["cfg"]]
        env = dict(os.environ, OMP_NUM_THREADS=str(t), OMP_PLACES=cfg["places"], OMP_PROC_BIND="close", OMP_DYNAMIC="false")
        extra = ["--cores", cfg["cores"], "--node", cfg["node"]]
    CM.wait_quiet(others, log)
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(common, "run_measured.py"), "--out", d, "--timeout", "3600",
                        "--label", j["name"], "--start-marker", "^MARK start", "--end-marker", "^MARK end"] + extra
                       + ["--"] + cmd, env=env, capture_output=True, text=True)
    t1 = time.time()
    mean, peak = others.stats(t0, t1)
    ok = os.path.exists(os.path.join(d, "DONE"))
    if os.path.isdir(d):
        json.dump(dict(others_cores_mean=mean, others_cores_peak=peak, t0=t0, t1=t1, cmd=cmd, rc=p.returncode, job=j),
                  open(os.path.join(d, "contamination.json"), "w"), indent=1)
    if ok and (mean > CM.BUSY_MEAN or peak > CM.BUSY_PEAK):
        os.remove(os.path.join(d, "DONE"))
        os.rename(d, f"{d}_discarded_{int(t1)}")
        log(f"  {j['name']}: others used {mean:.2f} cores (peak {peak:.1f}) -> set aside, run again later")
        return False
    if not ok:
        log(f"  {j['name']}: FAILED (rc {p.returncode})\n{p.stdout[-600:]}{p.stderr[-600:]}")
        return False
    r = record(d)
    if j["impl"] == "fpga" and r["card_samples"] < 5:
        log(f"  {j['name']}: WARNING only {r['card_samples']} card power samples in the window")
    log(f"  {j['name']}: {r['runs']} runs, {r['t_per_run_s'] * 1e3:.3f} ms/run, card {r['P_card_W']:.1f} W, "
        f"socket {r['P_socket_W']:.0f} W, server {r['P_server_W']:.0f} W, ok {r['ok']}")
    return True


def summary(session, cfgs):
    groups, issues = {}, []
    js = jobs(session, list(cfgs), REPS)
    recs = {}
    for j in js:
        d = os.path.join(RES, "phase5", j["name"])
        if os.path.exists(os.path.join(d, "DONE")):
            recs[j["name"]] = (j, record(d))
    # card == matched CPU on the same seed and pairs (both configurations of the matched program)
    for name, (j, r) in recs.items():
        if j["impl"] != "fpga":
            continue
        for cfg in cfgs:
            twin = name.replace("/fpga/card/", f"/avx512/{cfg}/")
            if twin in recs and recs[twin][1]["mom_hash"] != r["mom_hash"]:
                issues.append(f"{name}: card mom_hash {r['mom_hash']} != {twin} {recs[twin][1]['mom_hash']}")
        if not r["ok"]:
            issues.append(f"{name}: ok=0")
    for name, (j, r) in recs.items():
        key = name.rsplit("/rep", 1)[0]
        groups.setdefault(key, (j, []))[1].append(r)
    out = []
    for key, (j, rs) in sorted(groups.items()):
        med = lambda k: statistics.median(x[k] for x in rs)
        row = dict(job=key, session=session, kind=j["kind"], impl=j["impl"], cfg=j["cfg"], case=j["case"],
                   scheme=j["scheme"], target=j["target"], steps=j["M"], pairs=j["N"], reps=len(rs),
                   t_per_run_s=med("t_per_run_s"), t_per_run_min_s=min(x["t_per_run_s"] for x in rs),
                   t_per_run_max_s=max(x["t_per_run_s"] for x in rs), path_steps=rs[0]["path_steps"])
        if j["impl"] == "fpga":
            for b, cpu in (("socket", "E_cpu_socket_J"), ("server", "E_cpu_server_J")):
                row[f"E_{b}_per_run_J"] = statistics.median((x["E_card_J"] + x[cpu]) / x["runs"] for x in rs)
            row["P_card_W"] = med("P_card_W")
            row["P_host_socket_W"], row["P_host_server_W"] = med("P_socket_W"), med("P_server_W")
        else:
            b = "socket" if j["cfg"] == "s0_c24" else "server"
            row[f"E_{b}_per_run_J"] = statistics.median(x[f"E_cpu_{b}_J"] / x["runs"] for x in rs)
            row["P_W"] = med(f"P_{b}_W")
        if j["kind"] == "set":
            for k in range(3):
                errs = [x[f"price{k}"] - REF[j["case"]][k] for x in rs if f"price{k}" in x]
                if errs:
                    row[f"err_mean{k}"] = statistics.mean(errs)
                    row[f"err_rms{k}"] = (sum(e * e for e in errs) / len(errs)) ** 0.5
        row["all_ok"] = int(all(x["ok"] for x in rs))
        out.append(row)
    if out:
        keys = []
        for r in out:
            keys += [k for k in r if k not in keys]
        f = os.path.join(RES, f"phase5_summary_s{session}.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(out)
        print(f"{len(out)} job groups -> {f}")
    print(f"card == matched CPU checks: {'all equal' if not issues else str(len(issues)) + ' PROBLEMS'}")
    for i in issues[:20]:
        print("  " + i)
    return out, issues


def wall_logger(path, stop):
    ok = subprocess.run("sudo -n ipmitool dcmi power reading", shell=True, capture_output=True, text=True)
    if "Instantaneous" not in ok.stdout:
        return False

    def run():
        with open(path, "a") as fh:
            while not stop.is_set():
                o = subprocess.run("sudo -n ipmitool dcmi power reading", shell=True, capture_output=True, text=True).stdout
                m = re.search(r"Instantaneous power reading:\s+(\d+)", o)
                if m:
                    fh.write(f"{time.time():.3f},{m.group(1)}\n")
                    fh.flush()
                stop.wait(0.9)
    threading.Thread(target=run, daemon=True).start()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True, choices=(1, 2))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated cases (tests)")
    ap.add_argument("--test", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    global REPS, WARM_S, WINDOW_S, STEADY_PATH_STEPS
    CM.ONLY = [c for c in a.only.split(",") if c] or None
    if a.test:
        REPS, WARM_S, WINDOW_S, STEADY_PATH_STEPS = 1, 0.2, 0.5, 2e7
        CM.QUIET_CORES, CM.BUSY_MEAN, CM.BUSY_PEAK = 99.0, 99.0, 99.0
    cfgs = CM.configs()
    if a.summary:
        summary(a.session, cfgs)
        return
    js = jobs(a.session, list(cfgs), REPS)
    if a.plan:
        left = [j for j in js if not os.path.exists(os.path.join(RES, "phase5", j["name"], "DONE"))]
        est = 0.0
        for j in left:
            rate = 10e9 if j["impl"] == "fpga" else 2.5e9 * (CM.count(cfgs[j["cfg"]]["cores"]) / 24)
            est += WARM_S + WINDOW_S + 9 + max(0.0, 2 * 2 * j["N"] * j["M"] / rate - WINDOW_S)
        print(f"session {a.session}: {len(left)} of {len(js)} jobs left; about {est / 3600:.1f} h on a quiet server")
        return
    for f in ("run_measured.py", "power_monitor.py"):
        if not os.path.exists(os.path.join(a.common, f)):
            sys.exit(f"{f} not found in {a.common}")
    for b in list(BIN.values()) + list(XCLBIN.values()):
        if not os.path.exists(b):
            sys.exit(f"{b} not found (make cpu native host; xclbins in build/<scheme>/)")
    tune = CM.tuning() if not a.test else {"ft": 1, "qe": 4}
    if tune is None:
        sys.exit("results/cpu_pred/tuning.json not found (it is written by scripts/cpu_measure.py)")
    if not a.test:
        import glob as _g
        files = _g.glob("/sys/class/powercap/intel-rapl:[0-9]/energy_uj") + _g.glob("/sys/class/powercap/intel-rapl:[0-9]:[0-9]/energy_uj")
        if len(files) < 4 or any(not os.access(f, os.R_OK) for f in files):
            sys.exit("RAPL counters not readable: sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj "
                     "/sys/class/powercap/intel-rapl:*:*/energy_uj")
    os.makedirs(os.path.join(RES, "phase5", f"s{a.session}"), exist_ok=True)
    logf = open(os.path.join(RES, f"phase5_s{a.session}_events.log"), "a")

    def log(s):
        line = f"[{time.strftime('%H:%M:%S')}] {s}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    stop = threading.Event()
    wall = wall_logger(os.path.join(RES, "phase5", f"s{a.session}", "wall.csv"), stop)
    others = CM.Others()
    log(f"phase 5, session {a.session}: {len(js)} jobs; wall power {'logged' if wall else 'not available'}")
    todo, tries = list(js), {}
    while todo:
        j = todo.pop(0)
        log(f"{j['name']} ({len(todo)} left)")
        if not run_job(j, cfgs, a.common, others, log, tune):
            tries[j["name"]] = tries.get(j["name"], 0) + 1
            if tries[j["name"]] < MAX_TRIES:
                todo.append(j)
            else:
                log(f"  {j['name']}: given up after {MAX_TRIES} tries")
    _, issues = summary(a.session, cfgs)
    stop.set()
    others.stop = True
    done = sum(1 for j in js if os.path.exists(os.path.join(RES, "phase5", j["name"], "DONE")))
    log(f"PHASE 5 SESSION {a.session} FINISHED" if done == len(js) and not issues
        else f"PHASE 5 SESSION {a.session} INCOMPLETE ({done} of {len(js)} jobs, {len(issues)} card/CPU problems)")


if __name__ == "__main__":
    main()
