#!/usr/bin/env python3
"""phase6.py - the frodo sessions of Part B (protocol v0.6, Section 23 items 1-4; addendum v0.6.1). One session per
call; two sessions on different days. Same harness, steady-clock protocol and quietness guard as phase 5
(scripts/phase5.py, phase5_s.py): common/run_measured.py (RAPL at 10 Hz, card rails, markers, pinning), >= 3 s
untimed warm-up, timed window >= 10 s, a disturbed job set aside and repeated.

Jobs of one repeat (5 repeats per session, interleaved; seed 20261600 + 100 session + rep), main cases (I, II, III,
P1) and secondary cases (B, A), every setting ("set") and the steady state of each short setting ("ss", as phase 5):
  card, --wait xrt       : the registered Part A host wait (HF1 reference, same session)
  card, --wait callback  : the non-polling wait of HF1 (completion callback, host thread asleep on a futex)
  avx512 and native, s0_t48 : 48 threads, both hardware threads of the 24 cores of socket 0 (Section 23 item 1)
  avx512 and native, s0_c24, steady state of every (case, scheme, steps): the 24-thread comparison for the
                           24/48 choice in the same session (the reported 24-thread results stay Part A's)
  card, --wait xrt, exploratory telemetry check: steady state of I FT 1280 steps and P1 QE 1 step with the XRT
                           power profile at 200 ms (cfg xrt_profile_200ms.ini) and off (xrt_no_profile.ini; no card
                           power, host sockets only); the registered ini samples every 20 ms
The card's runs must equal the matched CPU program's (mom_hash) for the same seed and pairs.
Wall power (scripts/wall_logger.py) and CPU clocks (scripts/clock_logger.py) run beside it as root.
Output: results/phase6/s<k>/..., results/phase6_summary_s<k>.csv, results/phase6_s<k>_events.log.

Usage (from the project folder; `make host shost` with the wait modes, xclbins built, RAPL readable):
    python3 scripts/phase6.py --session 1 --plan
    nohup python3 scripts/phase6.py --session 1 > results/phase6_s1.log 2>&1 &
    python3 scripts/phase6.py --session 1 --summary
"""
import argparse, csv, glob, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu_measure as CM
import cpu_measure_s as CMS
import phase5 as P5
import phase5_s as P5S

ROOT, RES = CM.ROOT, CM.RES
OUT = os.path.join(RES, "phase6")
INI_DIR = os.path.join(ROOT, "scripts", "xrt_ini")
REPS, WARM_S, WINDOW_S = 5, 3.0, 10.0
STEADY_PATH_STEPS = 1.0e10
MAX_TRIES = 6
TEL_KEYS = [("I", "ft", 1280), ("P1", "qe", 1)]
TEL_INI = {"tel200": "xrt_profile_200ms.ini", "teloff": "xrt_no_profile.ini"}


def caseset(name):
    """(module, settings rows, steady pairs) of the main (P5) or secondary (P5S) cases"""
    mod, cm = (P5, CM) if name == "main" else (P5S, CMS)
    rows = cm.settings()
    maxp = (lambda c: P5.MAX_PAIRS) if name == "main" else (lambda c: P5S.MAX_PAIRS[c])
    keys = {}
    for r in rows:
        k = (r["case"], r["scheme"], r["M"])
        n = min(maxp(r["case"]), max(r["N"], int(STEADY_PATH_STEPS / (2 * r["M"]))))
        keys[k] = max(keys.get(k, 0), (n + 63) // 64 * 64)
    short = {k for k in keys if any(2.0 * r["N"] * r["M"] < STEADY_PATH_STEPS for r in rows if (r["case"], r["scheme"], r["M"]) == k)}
    return mod, rows, keys, short


def jobs(session):
    out = []
    sets = {s: caseset(s) for s in ("main", "sec")}
    for rep in range(1, REPS + 1):
        seed = 20261600 + 100 * session + rep
        for s, (mod, rows, keys, short) in sets.items():
            def add(kind, impl, cfg, r=None, k=None, wait="", ini=""):
                if kind == "set":
                    j = dict(kind="set", impl=impl, cfg=cfg, case=r["case"], scheme=r["scheme"], target=r["target"], M=r["M"], N=r["N"],
                             tag=f"set_{r['tag']}")
                else:
                    j = dict(kind=kind, impl=impl, cfg=cfg, case=k[0], scheme=k[1], target="", M=k[2], N=keys[k], tag=f"ss_{k[0]}_{k[1]}_M{k[2]}")
                plat = f"{impl}_{wait}" if impl == "fpga" else impl
                plat += f"_{ini}" if ini else ""
                j.update(set=s, rep=rep, seed=seed, wait=wait, ini=ini, name=f"s{session}/{plat}/{cfg}/{j['tag']}/rep{rep}")
                out.append(j)
            for wait in ("xrt", "callback"):
                for r in rows:
                    add("set", "fpga", "card", r=r, wait=wait)
                for k in sorted(short):
                    add("ss", "fpga", "card", k=k, wait=wait)
            for impl in ("avx512", "native"):
                for r in rows:
                    add("set", impl, "s0_t48", r=r)
                for k in sorted(short):
                    add("ss", impl, "s0_t48", k=k)
                for k in sorted(keys):
                    add("ss", impl, "s0_c24", k=k)
            if s == "main":
                for ini in TEL_INI:
                    for k in TEL_KEYS:
                        add("ss", "fpga", "card", k=k, wait="xrt", ini=ini)
    return out


def configs():
    c = CM.configs()
    n0 = CM.node_cpus(0)
    c["s0_t48"] = dict(cores=n0, node="0", places="threads")
    return c


def command(j, cfgs, tune):
    mod = P5 if j["set"] == "main" else P5S
    mod.WARM_S, mod.WINDOW_S = WARM_S, WINDOW_S
    cmd, t = mod.command(j, cfgs, tune[j["set"]])
    if j["impl"] == "fpga":
        cmd += ["--wait", j["wait"]]
    return cmd, t


def run_job(j, cfgs, common, others, log, tune):
    d = os.path.join(OUT, j["name"])
    if os.path.exists(os.path.join(d, "DONE")):
        return True
    if os.path.exists(d):
        shutil.rmtree(d)
    cmd, t = command(j, cfgs, tune)
    mod = P5 if j["set"] == "main" else P5S
    if j["impl"] == "fpga":
        env = dict(os.environ, OMP_NUM_THREADS="1")
        ini = os.path.join(INI_DIR, TEL_INI[j["ini"]]) if j["ini"] else os.path.join(common, "xrt_timing.ini")
        extra = ["--cores", cfgs["s0_c24"]["cores"], "--node", "0", "--xclbin", mod.XCLBIN[j["scheme"]],
                 "--state-dir", os.path.join(OUT, ".state"), "--xrt-ini", ini]
    else:
        cfg = cfgs[j["cfg"]]
        env = dict(os.environ, OMP_NUM_THREADS=str(t), OMP_PLACES=cfg["places"], OMP_PROC_BIND="close", OMP_DYNAMIC="false")
        extra = ["--cores", cfg["cores"], "--node", cfg["node"]]
    CM.wait_quiet(others, log)
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(common, "run_measured.py"), "--out", d, "--timeout", "3600", "--label", j["name"],
                        "--start-marker", "^MARK start", "--end-marker", "^MARK end"] + extra + ["--"] + cmd,
                       env=env, capture_output=True, text=True)
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
    log(f"  {j['name']}: {r['runs']} runs, {r['t_per_run_s'] * 1e3:.3f} ms/run, card {r['P_card_W']:.1f} W, "
        f"socket {r['P_socket_W']:.0f} W, server {r['P_server_W']:.0f} W, ok {r['ok']}")
    return True


def record(d):
    r = P5.record(d)
    txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
    for k in ("host_cpu_frac", "wait"):
        m = [l.split("=", 1)[1].strip() for l in txt.splitlines() if l.startswith(f"RESULT {k}=")]
        if m:
            r[k] = m[-1] if k == "wait" else float(m[-1])
    return r


def summary(session):
    js = jobs(session)
    recs = {}
    for j in js:
        d = os.path.join(OUT, j["name"])
        if os.path.exists(os.path.join(d, "DONE")):
            recs[j["name"]] = (j, record(d))
    issues = []
    for name, (j, r) in recs.items():
        if j["impl"] != "fpga":
            continue
        if not r["ok"]:
            issues.append(f"{name}: ok=0")
        if j["kind"] == "set":
            twin = name.replace(f"/fpga_{j['wait']}/card/", "/avx512/s0_t48/")
            if twin in recs and recs[twin][1]["mom_hash"] != r["mom_hash"]:
                issues.append(f"{name}: card mom_hash {r['mom_hash']} != {twin} {recs[twin][1]['mom_hash']}")
    groups = {}
    for name, (j, r) in recs.items():
        groups.setdefault(name.rsplit("/rep", 1)[0], (j, []))[1].append(r)
    rows = []
    for key, (j, rs) in sorted(groups.items()):
        med = lambda k: statistics.median(x[k] for x in rs)
        row = dict(job=key, session=session, kind=j["kind"], impl=j["impl"], wait=j["wait"], ini=j["ini"] or "registered", cfg=j["cfg"],
                   case=j["case"], scheme=j["scheme"], target=j["target"], steps=j["M"], pairs=j["N"], reps=len(rs),
                   t_per_run_s=med("t_per_run_s"), t_per_run_min_s=min(x["t_per_run_s"] for x in rs),
                   t_per_run_max_s=max(x["t_per_run_s"] for x in rs), path_steps=rs[0]["path_steps"])
        if j["impl"] == "fpga":
            for b, cpu in (("socket", "E_cpu_socket_J"), ("server", "E_cpu_server_J")):
                row[f"E_{b}_per_run_J"] = statistics.median((x["E_card_J"] + x[cpu]) / x["runs"] for x in rs)
            row["P_card_W"], row["P_host_socket_W"], row["P_host_server_W"] = med("P_card_W"), med("P_socket_W"), med("P_server_W")
            row["host_cpu_frac"] = statistics.median(x.get("host_cpu_frac", float("nan")) for x in rs)
        else:
            row["E_socket_per_run_J"] = statistics.median(x["E_cpu_socket_J"] / x["runs"] for x in rs)
            row["P_W"] = med("P_socket_W")
        if j["kind"] == "set":
            ref = (P5.REF if j["set"] == "main" else P5S.REF)[j["case"]]
            for k in range(3):
                errs = [x[f"price{k}"] - ref[k] for x in rs if f"price{k}" in x]
                if errs:
                    row[f"err_mean{k}"] = statistics.mean(errs)
                    row[f"err_rms{k}"] = (sum(e * e for e in errs) / len(errs)) ** 0.5
        row["all_ok"] = int(all(x["ok"] for x in rs))
        rows.append(row)
    if rows:
        keys = []
        for r in rows:
            keys += [k for k in r if k not in keys]
        f = os.path.join(RES, f"phase6_summary_s{session}.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} job groups -> {f}")
    print(f"card == matched CPU checks: {'all equal' if not issues else str(len(issues)) + ' PROBLEMS'}")
    for i in issues[:20]:
        print("  " + i)
    return rows, issues


def main():
    global REPS, WARM_S, WINDOW_S, STEADY_PATH_STEPS
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True, choices=(1, 2))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--test", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    if a.test:
        REPS, WARM_S, WINDOW_S = 1, 0.2, 0.5
        CM.QUIET_CORES, CM.BUSY_MEAN, CM.BUSY_PEAK = 99.0, 99.0, 99.0
    if a.summary:
        summary(a.session)
        return
    cfgs = configs()
    js = jobs(a.session)
    left = [j for j in js if not os.path.exists(os.path.join(OUT, j["name"], "DONE"))]
    if a.plan:
        est = 0.0
        for j in left:
            rate = 10e9 if j["impl"] == "fpga" else 2.5e9          # path-steps/s (24 cores; SMT adds little), as phase 5's plan
            if j["case"] == "B":
                rate /= 8
            est += WARM_S + WINDOW_S + 9 + max(0.0, 2 * 2 * j["N"] * j["M"] / rate - WINDOW_S)
        print(f"session {a.session}: {len(left)} of {len(js)} jobs left; about {est / 3600:.1f} h on a quiet server")
        print("cores: s0_c24", cfgs["s0_c24"]["cores"], "| s0_t48", cfgs["s0_t48"]["cores"], f"({CM.count(cfgs['s0_t48']['cores'])} threads)")
        return
    for f in ("run_measured.py", "power_monitor.py", "xrt_timing.ini"):
        if not os.path.exists(os.path.join(a.common, f)):
            sys.exit(f"{f} not found in {a.common}")
    for f in TEL_INI.values():
        if not os.path.exists(os.path.join(INI_DIR, f)):
            sys.exit(f"scripts/xrt_ini/{f} not found")
    for b in list(P5.BIN.values()) + list(P5S.BIN.values()) + list(P5.XCLBIN.values()) + list(P5S.XCLBIN.values()):
        if not os.path.exists(b):
            sys.exit(f"{b} not found")
    for h in (P5.BIN["fpga"], P5S.BIN["fpga"]):
        if "--wait" not in subprocess.run([h, "--help"], capture_output=True, text=True).stdout:
            sys.exit(f"{h} has no --wait option: make host shost (step F1)")
    if CM.count(cfgs["s0_t48"]["cores"]) != 48 and not a.test:
        sys.exit(f"socket 0 has {CM.count(cfgs['s0_t48']['cores'])} hardware threads, not 48")
    tune = {"main": CM.tuning(), "sec": CMS.tuning()} if not a.test else {"main": {"ft": 1, "qe": 4}, "sec": {"ft": 1, "qe": 1, "asian": 1}}
    if not tune["main"] or not tune["sec"]:
        sys.exit("results/cpu_pred/tuning.json or results/cpu_pred_s/tuning.json not found")
    if not a.test:
        files = glob.glob("/sys/class/powercap/intel-rapl:[0-9]/energy_uj") + glob.glob("/sys/class/powercap/intel-rapl:[0-9]:[0-9]/energy_uj")
        if len(files) < 4 or any(not os.access(f, os.R_OK) for f in files):
            sys.exit("RAPL counters not readable: sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj /sys/class/powercap/intel-rapl:*:*/energy_uj")
    os.makedirs(os.path.join(OUT, f"s{a.session}"), exist_ok=True)
    logf = open(os.path.join(RES, f"phase6_s{a.session}_events.log"), "a")

    def log(s):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {s}"
        print(line, flush=True)
        logf.write(line + "\n"); logf.flush()

    others = CM.Others()
    log(f"phase 6, session {a.session}: {len(left)} of {len(js)} jobs left")
    todo, tries = list(left), {}
    while todo:
        j = todo.pop(0)
        log(f"{j['name']} ({len(todo)} left)")
        if not run_job(j, cfgs, a.common, others, log, tune):
            tries[j["name"]] = tries.get(j["name"], 0) + 1
            if tries[j["name"]] < MAX_TRIES:
                todo.append(j)
            else:
                log(f"  {j['name']}: given up after {MAX_TRIES} tries")
    _, issues = summary(a.session)
    others.stop = True
    done = sum(1 for j in js if os.path.exists(os.path.join(OUT, j["name"], "DONE")))
    log(f"PHASE 6 SESSION {a.session} FINISHED" if done == len(js) and not issues
        else f"PHASE 6 SESSION {a.session} INCOMPLETE ({done} of {len(js)} jobs, {len(issues)} card/CPU problems)")


if __name__ == "__main__":
    main()
