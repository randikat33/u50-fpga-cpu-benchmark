#!/usr/bin/env python3
"""phase5_gpu.py - the GPU measurements of paper 2 (protocol v0.6, Section 22b), after the registered GPU
prediction (GitHub commit ccc1471, Zenodo 10.5281/zenodo.23495010). One session per call; two sessions on
different days.

Every setting of results/settings.csv and results/settings_s.csv ("set": the registered steps and pairs) and the
steady state of each kernel and time step ("ss": many pairs, where the setting itself is short) runs REPS times per
session, in both host wait modes (spin, block), interleaved, with the steady-clock protocol: >= 3 s untimed warm-up,
timed window >= 10 s, a run shorter than the window repeated back to back (time and energy = window / runs).
Each job runs under scripts/gpu_run.py: pinned to socket 1, RAPL of both sockets and NVML (energy counter,
power, temperature, SM clock, throttle reasons) during the window.

A job is repeated (at most MAX_TRIES times) if gpu_run.py marks it disturbed (throttling, > 83 C, other users'
processes > 1 core on average or > 4 in any second) or the median SM clock differs from the locked clock by > 1%.
Before each job the script waits until other users' processes use < 0.5 cores. It stops if persistence mode is
off or the clock is not locked. It continues after a stop or power cut (finished jobs have DONE).

Usage (from the project folder, after `make -f gpu/Makefile`; GPU clock locked, RAPL readable):
    python3 scripts/phase5_gpu.py --session 1 --plan
    nohup python3 scripts/phase5_gpu.py --session 1 > results/phase5g_s1.log 2>&1 &
    python3 scripts/phase5_gpu.py --session 1 --summary
"""
import argparse, csv, json, os, re, shutil, statistics, subprocess, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUTD = os.path.join(RES, "phase5g")
BIN = os.path.join(ROOT, "build", "hs_gpu")
REPS, WARM_S, WINDOW_S, MAX_TRIES = 5, 3.0, 10.0, 6
STEADY_PATH_STEPS, MAX_PAIRS = 1.0e11, 2 ** 34
LOCK_MHZ = 1245
MODES = ("spin", "block")
REF = {"I": (35.849770, 13.084670, 0.295774), "II": (37.169665, 16.649223, 5.138190),
       "III": (38.772044, 21.795288, 9.983068), "P1": (31.260968, 8.095646, 0.141170),
       "B": (11.39530252, 3.43639142, 0.32110944), "A": (12.61135515, 5.78194327, 2.00405513)}


def settings():
    rows = []
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("steps"):
                tag = f"{r['case']}_{r['scheme']}_{r['target'].replace('%', 'pct')}"
                rows.append(dict(case=r["case"], scheme=r["scheme"], target=r["target"], M=int(r["steps"]), N=int(r["pairs"]), tag=tag))
    return rows


def jobs(session):
    rows = settings()
    steady = {}
    for r in rows:
        if 2.0 * r["N"] * r["M"] < STEADY_PATH_STEPS:
            n = min(MAX_PAIRS, max(r["N"], int(STEADY_PATH_STEPS / (2 * r["M"]))))
            steady[(r["case"], r["scheme"], r["M"])] = (n + 255) // 256 * 256
    out = []
    for rep in range(1, REPS + 1):
        seed = 20261400 + 100 * session + rep
        for mode in MODES:
            for r in rows:
                out.append(dict(kind="set", mode=mode, case=r["case"], scheme=r["scheme"], target=r["target"], M=r["M"], N=r["N"],
                                rep=rep, seed=seed, name=f"s{session}/{mode}/set_{r['tag']}/rep{rep}"))
            for (case, sc, M), n in sorted(steady.items()):
                out.append(dict(kind="ss", mode=mode, case=case, scheme=sc, target="", M=M, N=n, rep=rep, seed=seed,
                                name=f"s{session}/{mode}/ss_{case}_{sc}_M{M}/rep{rep}"))
    return out


def cmd(j):
    return [sys.executable, os.path.join(ROOT, "scripts", "gpu_run.py"), "--out", os.path.join(OUTD, j["name"]), "--",
            BIN, "--case", j["case"], "--scheme", j["scheme"], "--steps", str(j["M"]), "--a0", "0", "--a1", str(j["N"]),
            "--seed", str(j["seed"]), "--sync", j["mode"], "--timed", "--warmup-s", str(WARM_S), "--window-s", str(WINDOW_S)]


def other_cores(seconds=2.0):
    def tot():
        t = 0
        me = os.getpid()
        for p in os.listdir("/proc"):
            if not p.isdigit() or int(p) == me:
                continue
            try:
                v = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
                t += int(v[11]) + int(v[12])
            except Exception:
                pass
        return t / os.sysconf("SC_CLK_TCK")
    a = tot(); time.sleep(seconds); b = tot()
    return (b - a) / seconds


def check_gpu():
    q = subprocess.run(["nvidia-smi", "--query-gpu=persistence_mode,clocks.sm,clocks.applications.graphics", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True).stdout.strip()
    return q


def log(f, msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    f.write(line + "\n"); f.flush()


def run_job(j, ev):
    d = os.path.join(OUTD, j["name"])
    for t in range(1, MAX_TRIES + 1):
        waited = 0
        while other_cores() > 0.5:
            if waited % 300 == 0:
                log(ev, f"{j['name']}: waiting, other users busy")
            time.sleep(30); waited += 30
        if os.path.isdir(d):
            shutil.rmtree(d)
        p = subprocess.run(cmd(j), capture_output=True, text=True)
        try:
            m = json.load(open(os.path.join(d, "meta.json")))
        except Exception:
            m = {}
        sm = m.get("sm_mhz_median") or 0
        bad = p.returncode != 0 or m.get("disturbed") != 0 or abs(sm - LOCK_MHZ) > 0.01 * LOCK_MHZ
        if not bad:
            log(ev, f"{j['name']}: ok, {p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''}")
            return True
        why = f"rc {p.returncode}" if p.returncode else f"disturbed {m.get('disturbed')} (throttle 0x{m.get('throttle_or', 0):x}, Tmax {m.get('temp_max_c')}, other {m.get('other_cores_avg', 0):.2f}/{m.get('other_cores_max', 0):.2f}, SM {sm})"
        log(ev, f"{j['name']}: try {t} set aside: {why}")
        if os.path.exists(os.path.join(d, "DONE")):
            os.remove(os.path.join(d, "DONE"))
        if p.returncode not in (0,) and "persistence" in (p.stdout + p.stderr):
            log(ev, "STOP: persistence mode is off (sudo nvidia-smi -pm 1)")
            sys.exit(2)
        time.sleep(20)
    return False


def record(d):
    m = json.load(open(os.path.join(d, "meta.json")))
    txt = open(os.path.join(d, "stdout.log")).read()
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", txt, re.M))
    runs = int(res["runs"])
    E = m["E_J"]
    out = dict(runs=runs, t_per_run_s=m["window_s"] / runs, path_steps=float(res["path_steps"]),
               E_gpu_J=E["gpu"] / runs, E_socket1_J=(E["pkg1"] + E["dram1"]) / runs, E_socket0_J=(E["pkg0"] + E["dram0"]) / runs,
               P_gpu_W=m["P_W"]["gpu"], P_socket1_W=m["P_W"]["pkg1"] + m["P_W"]["dram1"], P_socket0_W=m["P_W"]["pkg0"] + m["P_W"]["dram0"],
               sm_mhz=m["sm_mhz_median"], temp_max_c=m["temp_max_c"], ok=int(res.get("ok", "0")))
    for k in range(3):
        if f"price{k}" in res:
            out[f"price{k}"] = float(res[f"price{k}"])
    return out


def summary(session):
    js = jobs(session)
    groups = {}
    for j in js:
        d = os.path.join(OUTD, j["name"])
        if os.path.exists(os.path.join(d, "DONE")):
            groups.setdefault(j["name"].rsplit("/rep", 1)[0], (j, []))[1].append(record(d))
    rows = []
    for key, (j, rs) in sorted(groups.items()):
        med = lambda k: statistics.median(x[k] for x in rs)
        row = dict(job=key, session=session, kind=j["kind"], mode=j["mode"], case=j["case"], scheme=j["scheme"], target=j["target"],
                   steps=j["M"], pairs=j["N"], reps=len(rs), t_per_run_s=med("t_per_run_s"),
                   t_per_run_min_s=min(x["t_per_run_s"] for x in rs), t_per_run_max_s=max(x["t_per_run_s"] for x in rs),
                   path_steps=rs[0]["path_steps"],
                   E_gpu_per_run_J=med("E_gpu_J"),
                   E_gpu_socket_per_run_J=statistics.median(x["E_gpu_J"] + x["E_socket1_J"] for x in rs),
                   E_gpu_server_per_run_J=statistics.median(x["E_gpu_J"] + x["E_socket1_J"] + x["E_socket0_J"] for x in rs),
                   P_gpu_W=med("P_gpu_W"), P_socket1_W=med("P_socket1_W"), P_socket0_W=med("P_socket0_W"),
                   sm_mhz=med("sm_mhz"), temp_max_c=max(x["temp_max_c"] for x in rs))
        if j["kind"] == "set":
            for k in range(3):
                errs = [x[f"price{k}"] - REF[j["case"]][k] for x in rs if f"price{k}" in x]
                row[f"err_mean{k}"] = statistics.mean(errs)
                row[f"err_rms{k}"] = (sum(e * e for e in errs) / len(errs)) ** 0.5
        row["all_ok"] = int(all(x["ok"] for x in rs))
        rows.append(row)
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    f = os.path.join(RES, f"phase5g_summary_s{session}.csv")
    with open(f, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} job groups -> {f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    js = jobs(a.session)
    left = [j for j in js if not os.path.exists(os.path.join(OUTD, j["name"], "DONE"))]
    if a.plan:
        print(f"{len(left)} of {len(js)} jobs left; about {len(left) * (WARM_S + WINDOW_S + 4) / 3600:.1f} h")
        print("GPU (persistence, SM clock, application clock):", check_gpu())
        return
    if a.summary:
        summary(a.session)
        return
    os.makedirs(OUTD, exist_ok=True)
    ev = open(os.path.join(RES, f"phase5g_s{a.session}_events.log"), "a")
    q = check_gpu()
    log(ev, f"phase 5 GPU, session {a.session}: {len(left)} of {len(js)} jobs left; GPU {q}")
    if not q.startswith("Enabled"):
        log(ev, "STOP: persistence mode is off (sudo nvidia-smi -pm 1)")
        sys.exit(2)
    failed = 0
    for i, j in enumerate(left, 1):
        if not run_job(j, ev):
            failed += 1
        if i % 50 == 0:
            log(ev, f"progress: {i} of {len(left)} jobs this run")
    summary(a.session)
    done = sum(1 for j in js if os.path.exists(os.path.join(OUTD, j["name"], "DONE")))
    log(ev, f"PHASE 5 GPU SESSION {a.session} {'FINISHED' if done == len(js) else 'INCOMPLETE'}: {done} of {len(js)} jobs, {failed} not done")


if __name__ == "__main__":
    main()
