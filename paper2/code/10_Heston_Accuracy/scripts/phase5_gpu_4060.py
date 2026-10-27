#!/usr/bin/env python3
"""phase5_gpu_4060.py - the RTX 4070 Ti SUPER measurements of addendum v0.9 (Part B), after its registration.

The V100's phase-5 GPU queue (scripts/phase5_gpu.py) with the registered changes for this desktop:
  - blocking wait only (the V100's registered primary mode);
  - the 29 steady-state runs ("ss") behind all 36 settings, 5 repeats per session (HR1-HR3); with --with-set also the
    36 settings themselves ("set", single runs: Proposition 1's fixed cost, descriptive);
  - each job under scripts/gpu_run_4060.py (RAPL package found by name, no numactl, NVML energy counter), boundary
    GPU + CPU package (the desktop has no RAPL DRAM domain and one socket);
  - the SM clock locked at results/rtx4070tis/step4b/results/locked_clock.txt (2340 MHz); a job is repeated if its
    median SM clock is more than 1% away, if gpu_run_4060.py marks it disturbed, or if scripts/guard.py finds another
    login, GPU process, container or CPU user during it (up to 6 tries);
  - before each job: guard.wait_quiet (60 s of quiet after any pause); the program is build/hs_gpu with the sha256
    recorded in step 4A (build/hs_gpu.sha256), checked at the start.
Seeds as the V100's sessions (20261400 + 100 x session + repeat), so the draws are the same.
Two sessions on different days. Output: results/phase5g4060/s<session>/..., results/phase5g4060_summary_s<session>.csv,
results/phase5g4060_s<session>_events.log.

  python3 scripts/phase5_gpu_4060.py --session 1 --plan
  nohup python3 scripts/phase5_gpu_4060.py --session 1 > results/phase5g4060_s1.log 2>&1 &
  python3 scripts/phase5_gpu_4060.py --session 1 --summary
"""
import argparse, csv, hashlib, json, os, re, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase5_gpu as G
import guard

ROOT, RES = G.ROOT, G.RES
OUTD = os.path.join(RES, "phase5g4060")
BIN = G.BIN
SHA_FILE = os.path.join(ROOT, "build", "hs_gpu.sha256")
CLOCK_FILE = os.path.join(RES, "rtx4070tis", "step4b", "results", "locked_clock.txt")
MAX_TRIES = 6


def lock_mhz():
    for f in (CLOCK_FILE, os.path.join(RES, "locked_clock.txt")):
        if os.path.exists(f):
            return int(re.search(r"LOCKED_CLOCK_MHZ=(\d+)", open(f).read()).group(1))
    sys.exit("STOP: locked_clock.txt not found (step 4B)")


def jobs(session, with_set=False):
    return [j for j in G.jobs(session) if j["mode"] == "block" and (j["kind"] == "ss" or with_set)]


def cmd(j):
    return [sys.executable, os.path.join(ROOT, "scripts", "gpu_run_4060.py"), "--out", os.path.join(OUTD, j["name"]), "--",
            BIN, "--case", j["case"], "--scheme", j["scheme"], "--steps", str(j["M"]), "--a0", "0", "--a1", str(j["N"]),
            "--seed", str(j["seed"]), "--sync", j["mode"], "--timed", "--warmup-s", str(G.WARM_S), "--window-s", str(G.WINDOW_S)]


def log(f, msg):
    G.log(f, msg)


def run_job(j, ev, mhz, evpath):
    d = os.path.join(OUTD, j["name"])
    for t in range(1, MAX_TRIES + 1):
        guard.wait_quiet(own_root=os.getpid(), quiet_s=60, logfile=evpath)
        if os.path.isdir(d):
            shutil.rmtree(d)
        p = subprocess.Popen(cmd(j), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        seen = []
        while p.poll() is None:                       # the guard during the job (every ~5 s)
            r = guard.busy(own_root=os.getpid(), interval=1.0)
            if r:
                seen += r
            time.sleep(4)
        out, err = p.communicate()
        try:
            m = json.load(open(os.path.join(d, "meta.json")))
        except Exception:
            m = {}
        sm = m.get("sm_mhz_median") or 0
        bad = p.returncode != 0 or m.get("disturbed") != 0 or abs(sm - mhz) > 0.01 * mhz or bool(seen)
        if not bad:
            log(ev, f"{j['name']}: ok, {out.strip().splitlines()[-1] if out.strip() else ''}")
            return True
        why = f"rc {p.returncode} {err.strip()[-200:]}" if p.returncode else \
            (f"guard: {'; '.join(sorted(set(seen)))[:200]}" if seen else
             f"disturbed {m.get('disturbed')} (throttle 0x{m.get('throttle_or', 0):x}, Tmax {m.get('temp_max_c')}, SM {sm})")
        log(ev, f"{j['name']}: try {t} set aside: {why}")
        if os.path.exists(os.path.join(d, "DONE")):
            os.remove(os.path.join(d, "DONE"))
        time.sleep(20)
    return False


def record(d):
    m = json.load(open(os.path.join(d, "meta.json")))
    txt = open(os.path.join(d, "stdout.log")).read()
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", txt, re.M))
    runs = int(res["runs"])
    E = m["E_J"]
    out = dict(runs=runs, t_per_run_s=m["window_s"] / runs, path_steps=float(res["path_steps"]), E_gpu_J=E["gpu"] / runs,
               E_pkg_J=(E.get("pkg0", 0.0) + E.get("dram0", 0.0)) / runs, P_gpu_W=m["P_W"]["gpu"],
               P_pkg_W=m["P_W"].get("pkg0", 0.0) + m["P_W"].get("dram0", 0.0), sm_mhz=m["sm_mhz_median"], temp_max_c=m["temp_max_c"],
               ok=int(res.get("ok", "0")), gpu_energy_source=m.get("gpu_energy_source"))
    for k in range(3):
        if f"price{k}" in res:
            out[f"price{k}"] = float(res[f"price{k}"])
    return out


def summary(session, with_set=True):
    groups = {}
    for j in jobs(session, with_set=True):
        d = os.path.join(OUTD, j["name"])
        if os.path.exists(os.path.join(d, "DONE")):
            groups.setdefault(j["name"].rsplit("/rep", 1)[0], (j, []))[1].append(record(d))
    rows = []
    for key, (j, rs) in sorted(groups.items()):
        med = lambda k: statistics.median(x[k] for x in rs)
        row = dict(job=key, session=session, kind=j["kind"], mode=j["mode"], case=j["case"], scheme=j["scheme"], target=j["target"],
                   steps=j["M"], pairs=j["N"], reps=len(rs), t_per_run_s=med("t_per_run_s"),
                   t_per_run_min_s=min(x["t_per_run_s"] for x in rs), t_per_run_max_s=max(x["t_per_run_s"] for x in rs),
                   path_steps=rs[0]["path_steps"], E_gpu_per_run_J=med("E_gpu_J"),
                   E_gpu_pkg_per_run_J=statistics.median(x["E_gpu_J"] + x["E_pkg_J"] for x in rs),
                   P_gpu_W=med("P_gpu_W"), P_pkg_W=med("P_pkg_W"), sm_mhz=med("sm_mhz"), temp_max_c=max(x["temp_max_c"] for x in rs))
        for k in range(3):
            errs = [x[f"price{k}"] - G.REF[j["case"]][k] for x in rs if f"price{k}" in x]
            if errs:
                row[f"err_mean{k}"] = statistics.mean(errs)
                row[f"err_rms{k}"] = (sum(e * e for e in errs) / len(errs)) ** 0.5
        row["all_ok"] = int(all(x["ok"] for x in rs))
        rows.append(row)
    if not rows:
        print("no finished jobs"); return
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    f = os.path.join(RES, f"phase5g4060_summary_s{session}.csv")
    with open(f, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} job groups -> {f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--with-set", action="store_true", help="also the 36 settings' single runs (descriptive)")
    a = ap.parse_args()
    js = jobs(a.session, a.with_set)
    left = [j for j in js if not os.path.exists(os.path.join(OUTD, j["name"], "DONE"))]
    if a.summary:
        return summary(a.session)
    mhz = lock_mhz()
    sha = hashlib.sha256(open(BIN, "rb").read()).hexdigest() if os.path.exists(BIN) else ""
    reg = open(SHA_FILE).read().split()[0] if os.path.exists(SHA_FILE) else None
    if a.plan:
        print(f"{len(left)} of {len(js)} jobs left; about {len(left) * (G.WARM_S + G.WINDOW_S + 6) / 3600:.1f} h; locked clock {mhz} MHz")
        print("GPU (persistence, SM clock, application clock):", G.check_gpu())
        print("binary sha256", "matches step 4A" if reg and sha == reg else f"DOES NOT MATCH ({sha[:12]} vs {str(reg)[:12]})")
        r = guard.busy(own_root=os.getpid())
        print("guard:", "QUIET" if not r else "BUSY: " + "; ".join(r))
        return
    if not reg or sha != reg:
        sys.exit("STOP: build/hs_gpu is not the step 4A binary (sha256 differs)")
    os.makedirs(OUTD, exist_ok=True)
    evpath = os.path.join(RES, f"phase5g4060_s{a.session}_events.log")
    ev = open(evpath, "a")
    q = G.check_gpu()
    log(ev, f"RTX 4070 Ti SUPER, session {a.session}: {len(left)} of {len(js)} jobs left; GPU {q}; locked {mhz} MHz; binary {sha[:12]}")
    if not q.startswith("Enabled"):
        log(ev, "STOP: persistence mode is off (sudo nvidia-smi -pm 1)")
        sys.exit(2)
    failed = 0
    for i, j in enumerate(left, 1):
        if not run_job(j, ev, mhz, evpath):
            failed += 1
        if i % 25 == 0:
            log(ev, f"progress: {i} of {len(left)} jobs this run")
    summary(a.session)
    done = sum(1 for j in js if os.path.exists(os.path.join(OUTD, j["name"], "DONE")))
    log(ev, f"RTX SESSION {a.session} {'FINISHED' if done == len(js) else 'INCOMPLETE'}: {done} of {len(js)} jobs, {failed} not done")


if __name__ == "__main__":
    main()
