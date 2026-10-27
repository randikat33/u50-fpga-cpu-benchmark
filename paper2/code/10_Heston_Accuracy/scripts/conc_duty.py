#!/usr/bin/env python3
"""conc_duty.py - engaged power of each device (step 5b of the revision; an input to addendum v0.9, measured before it).

Why: the v0.8 model assumed a device returns to idle power when its work in a pass is done. The v0.8 runs say it does
not: in model_E the card computed 5 ms of a 0.18 s pass, yet frodo drew 17-22 W more than predicted for the whole pass;
the Supermicro drew ~16 W more while its CPU device waited. A device in the plan keeps its worker loaded (the card its
design, the CPUs their spinning threads, the V100 its host thread), and so draws "engaged" power until the pass ends.

Measurement: each device works in a duty cycle like a pass: every tau = 0.2 s it gets k options of one class at once,
works through them, then waits (worker loaded) until the next period. k gives a busy fraction of about 10% or 50%.
Continuous work (f ~ 1) and idle (f = 0, no worker) are measured the same way. Per device, the power at the boundary of
its server is fitted as  P = P_idle + f a + (1 - f) b  (f = the measured busy fraction: the sum of the parts' busy times
over the window), a = busy power above idle, b = ENGAGED power above idle. Same-server interactions: a device's engaged
power while the other device of its server works continuously (card_f10+fcpu, fcpu_f50+card, scpu_f50+gpu, gpu_f50+scpu).

Classes (short options, so k is an integer): card II QE 0.3% (10.6 ms), fcpu II FT 1% 48 threads (7.6 ms), gpu I QE 0.3%
(0.44 ms), scpu III QE 1% 20 threads (0.84 ms). Remote devices through the v0.9 agent (batch, as the scheduler).
Jobs (one repeat, 17): idle; card, card_f10, card_f50; fcpu, fcpu_f10, fcpu_f50; gpu, gpu_f10, gpu_f50; scpu, scpu_f10,
scpu_f50; card_f10+fcpu; fcpu_f50+card; scpu_f50+gpu; gpu_f50+scpu. Each: both servers quiet, 20 s idle, workers started,
10 s warm-up, 40 s window. 3 repeats, about 75 min. Repeat rules as conc_smc.py.
Output: results/conc_duty2/rep<k>/<job>/conc_result.json, results/conc_duty2_summary.csv (run 2, v0.9 agent with
TCP_NODELAY; adds the continuous gpu+scpu pair). Run 1 (results/conc_duty*, 27 Oct 2026, agent without TCP_NODELAY).

  python3 scripts/conc_duty.py --agent 10.8.100.5 --plan
  python3 scripts/conc_duty.py --agent 10.8.100.5 --check          one short repeat (not a result)
  nohup python3 scripts/conc_duty.py --agent 10.8.100.5 > results/conc_duty.log 2>&1 &
  python3 scripts/conc_duty.py --summary
"""
import argparse, csv, json, os, statistics, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import sched_run as SR
import sched_client as SC
import conc_smc as CS

ROOT, RES = S.ROOT, S.RES
REPS, MAX_TRIES, TAU = 3, 5, 0.2
TIMING = dict(run=(20.0, 10.0, 40.0), check=(5.0, 3.0, 8.0))
CLASS = {"card": ("II", "0.3%", "qe"), "fcpu": ("II", "1%", "ft"), "gpu": ("I", "0.3%", "qe"), "scpu": ("III", "1%", "qe")}
BUSY_S = {"card": 0.010573, "fcpu": 0.007574, "gpu": 0.000438, "scpu": 0.000836}     # model busy time per option (v0.8 inputs)
THREADS = {"fcpu": 48, "scpu": 20}


def K(d, f):
    return max(1, round(f * TAU / BUSY_S[d]))


JOBS = {"idle": {}}
for _d in ("card", "fcpu", "gpu", "scpu"):
    JOBS[_d] = {_d: None}
    JOBS[f"{_d}_f10"] = {_d: 0.10}
    JOBS[f"{_d}_f50"] = {_d: 0.50}
JOBS["card_f10+fcpu"] = {"card": 0.10, "fcpu": None}
JOBS["fcpu_f50+card"] = {"fcpu": 0.50, "card": None}
JOBS["scpu_f50+gpu"] = {"scpu": 0.50, "gpu": None}
JOBS["gpu_f50+scpu"] = {"gpu": 0.50, "scpu": None}
JOBS["gpu+scpu"] = {"gpu": None, "scpu": None}         # run 2 (fixed agent): the pair's C under TCP_NODELAY
ORDER = tuple(JOBS)


def class_options(dev):
    case, target, m = CLASS[dev]
    opts = [o for o in S.book() if o["case"] == case and o["target"] == target]
    s0 = opts[0]["set"][m]
    return (case, m, s0["steps"], s0["seed"]), [(o["id"], o["set"][m]["a0"], o["set"][m]["a1"], o["set"][m]["pairs"]) for o in opts]


RUN = "conc_duty2"      # run 2: with the v0.9 agent (TCP_NODELAY); run 1 (results/conc_duty) had the agent's Nagle delays


def out_dir(check):
    return os.path.join(RES, RUN + ("_check" if check else ""))


def one(job, out, agent, port, xrt_ini, warm_s, window_s):
    spec = JOBS[job]
    os.makedirs(out, exist_ok=True)
    ag = SC.Agent(agent, port)
    agc = SC.Agent(agent, port + 1)
    sync0 = ag.sync(50)
    agc.sync(10)
    stop = threading.Event()
    done = {d: [] for d in spec}                  # (arrival time, busy s, ok)
    late = {d: 0 for d in spec}
    errs = []
    local = {}
    for d in ("card", "fcpu"):
        if d in spec:
            local[d] = SR.Local(d, xrt_ini, out)
            local[d].start(*class_options(d)[0])

    def remote_batch(d, a, items, k0):
        """send the items at once and wait for all replies: [(t, busy, ok)]"""
        cls, parts = class_options(d)
        case, m, M, seed = cls
        for j, (_, a0, a1, _) in enumerate(items):
            job_ = dict(id=k0 + j + 1, dev="gpu" if d == "gpu" else "cpu", case=case, scheme=m, steps=M, a0=a0, a1=a1, seed=seed)
            if d == "scpu":
                job_["threads"] = THREADS["scpu"]
            a.run(job_)
        out_, t_last = [], time.time()
        while len(out_) < len(items):
            try:
                _, r = a.runs.get(timeout=0.5)
            except Exception:
                if time.time() - t_last > 30:
                    raise RuntimeError(f"{d}: no reply for 30 s ({len(items) - len(out_)} outstanding)")
                continue
            if r is None:
                raise RuntimeError("agent closed")
            t_last = time.time()
            pairs = parts[(r["id"] - 1) % len(parts)][3]
            out_.append((r["t_recv"], r["busy"], int(r["ok"] and r["pairs"] == pairs)))
        return out_

    def loop(d):
        cls, parts = class_options(d)
        n = len(parts)
        f = spec[d]
        k = K(d, f) if f else (n if d in local else 64)     # continuous: one class batch (local) or 64 commands (remote)
        a = ag if d == "gpu" else agc
        sent = 0
        try:
            t_next = time.time()
            while not stop.is_set():
                items = [parts[(sent + j) % n] for j in range(k)]
                if d in local:
                    res = local[d].batch(cls, [(sent + j + 1, a0, a1) for j, (_, a0, a1, _) in enumerate(items)])
                    got = [(ta, busy, int(ok and pr_n == items[j][3])) for j, (ta, busy, pr_n, pr, ok) in enumerate(res)]
                else:
                    got = remote_batch(d, a, items, sent)
                sent += k
                done[d].extend(got)
                if f:                                        # wait for the next period (worker loaded, idle)
                    t_next += TAU
                    w = t_next - time.time()
                    if w > 0:
                        stop.wait(w)
                    else:
                        late[d] += 1
                        t_next = time.time()
        except Exception as e:                               # pragma: no cover
            errs.append(f"{d}: {e}")

    th = [threading.Thread(target=loop, args=(d,), daemon=True) for d in spec]
    for t in th:
        t.start()
    time.sleep(warm_s)
    ag.sync(30)
    t0 = time.time()
    print(f"MARK start {t0:.6f}", flush=True)
    time.sleep(window_s)
    t1 = time.time()
    print(f"MARK end {t1:.6f}", flush=True)
    stop.set()
    for t in th:
        t.join(timeout=120)
    smc = ag.energy(t0, t1)
    sync2 = ag.sync(30)
    for x in (ag, agc):
        try:
            x.stop()
        except Exception as e:                               # pragma: no cover
            errs.append(f"agent stop: {e}")
        x.close()
    for w in local.values():
        w.stop()
    res = dict(job=job, tau_s=TAU, t_start=t0, t_end=t1, errors=errs, devices={}, smc_energy=smc, sync=[sync0, sync2])
    for d in spec:
        inw = [x for x in done[d] if t0 <= x[0] <= t1]
        busy = sum(x[1] for x in inw)
        res["devices"][d] = dict(cls=list(class_options(d)[0]), duty_target=spec[d], k_per_period=K(d, spec[d]) if spec[d] else None,
                                 options_in_window=len(inw), rate_per_s=len(inw) / (t1 - t0), busy_fraction=busy / (t1 - t0),
                                 busy_median_s=statistics.median(x[1] for x in inw) if inw else None, late_periods=late[d],
                                 ok=int(all(x[2] for x in inw)) if inw else 0)
    ok = int(not errs and all(v["ok"] for v in res["devices"].values()))
    res["ok"] = ok
    json.dump(res, open(os.path.join(out, "conc_result.json"), "w"), indent=1)
    print("RESULT runs=1\nRESULT path_steps=1\nRESULT ok=%d" % ok)
    sys.exit(0 if ok else 2)


def why_bad(res, job):
    import cpu_measure as CM
    e = res["smc_energy"]
    if e["other_mean"] > CM.BUSY_MEAN or e["other_max"] > CM.BUSY_PEAK:
        return f"Supermicro others {e['other_mean']:.2f} cores (peak {e['other_max']:.1f})"
    if (e.get("gpu_other_procs") or 0) > 0:
        return f"{e['gpu_other_procs']} other GPU process(es)"
    if "gpu" in JOBS[job] and abs((e.get("sm_mhz_median") or 0) - 1245) > 12.5:
        return f"V100 clock {e.get('sm_mhz_median')} MHz"
    if not res["ok"]:
        return "not ok: " + "; ".join(res["errors"])[-300:]
    return ""


SERVER = {"card": "frodo", "fcpu": "frodo", "gpu": "smc", "scpu": "smc"}


def summary(check=False):
    base = out_dir(check)
    rows = []
    for r in range(1, REPS + 1):
        for j in ORDER:
            d = os.path.join(base, f"rep{r}", j)
            if not os.path.exists(os.path.join(d, "DONE")):
                continue
            res = json.load(open(os.path.join(d, "conc_result.json")))
            Pf, Ps = CS.frodo_power(d), CS.smc_power(res)
            row = dict(rep=r, job=j, P_frodo_W=sum(Pf.values()), P_smc_W=sum(Ps.values()),
                       **{f"P_{k}_W": v for k, v in Pf.items()}, **{f"P_smc_{k}_W": v for k, v in Ps.items()})
            for dev, v in res["devices"].items():
                row[f"{dev}_rate_per_s"] = v["rate_per_s"]
                row[f"{dev}_busy_fraction"] = v["busy_fraction"]
                row[f"{dev}_late_periods"] = v["late_periods"]
            rows.append(row)
    if not rows:
        print("no finished jobs"); return
    keys = ["rep", "job"] + sorted({k for x in rows for k in x} - {"rep", "job"})
    with open(os.path.join(RES, RUN + ("_check" if check else "") + "_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    med = lambda j, k: statistics.median(x[k] for x in rows if x["job"] == j and x.get(k) is not None)
    have = lambda *js: all(any(x["job"] == j for x in rows) for j in js)
    print(f"{'job':<16} {'frodo (W)':>9} {'Supermicro (W)':>14}   busy fraction, rate")
    for j in ORDER:
        if have(j):
            print(f"{j:<16} {med(j, 'P_frodo_W'):>9.1f} {med(j, 'P_smc_W'):>14.1f}   " +
                  ", ".join(f"{dv} f {med(j, f'{dv}_busy_fraction'):.3f} ({med(j, f'{dv}_rate_per_s'):.0f}/s)" for dv in JOBS[j]))
    if not have("idle"):
        return
    print("\nfit per device, its server's power: P = P_idle + f a + (1 - f) b   (a busy, b engaged; W above idle)")
    for dv in ("card", "fcpu", "gpu", "scpu"):
        js = [j for j in (dv, f"{dv}_f10", f"{dv}_f50") if have(j)]
        if len(js) < 2:
            continue
        key = "P_frodo_W" if SERVER[dv] == "frodo" else "P_smc_W"
        Pi = med("idle", key)
        pts = [(med(j, f"{dv}_busy_fraction"), med(j, key) - Pi) for j in js]
        # least squares for y = f a + (1 - f) b
        sff = sum(f * f for f, _ in pts); sgg = sum((1 - f) ** 2 for f, _ in pts); sfg = sum(f * (1 - f) for f, _ in pts)
        sfy = sum(f * y for f, y in pts); sgy = sum((1 - f) * y for f, y in pts)
        det = sff * sgg - sfg * sfg
        a, b = (sfy * sgg - sgy * sfg) / det, (sgy * sff - sfy * sfg) / det
        resid = max(abs(y - (f * a + (1 - f) * b)) for f, y in pts)
        print(f"{dv:<5} a {a:6.1f} W  b {b:6.1f} W   points " + ", ".join(f"f {f:.2f}: +{y:.1f} W" for f, y in pts) + f"   max residual {resid:.1f} W")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="10.8.100.5")
    ap.add_argument("--port", type=int, default=50555)
    ap.add_argument("--one")
    ap.add_argument("--out")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--warm-s", type=float)
    ap.add_argument("--window-s", type=float)
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    ap.add_argument("--xrt-ini", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common", "xrt_timing.ini")))
    a = ap.parse_args()
    settle, warm, window = TIMING["check" if a.check else "run"]
    if a.one:
        return one(a.one, a.out, a.agent, a.port, a.xrt_ini, a.warm_s or warm, a.window_s or window)
    if a.summary:
        return summary(a.check)
    base = out_dir(a.check)
    reps = 1 if a.check else REPS
    jobs = [(r, j) for r in range(1, reps + 1) for j in ORDER]
    left = [x for x in jobs if not os.path.exists(os.path.join(base, f"rep{x[0]}", x[1], "DONE"))]
    if a.plan:
        for d in CLASS:
            print(f"{d}: class {class_options(d)[0]}, k per {TAU} s: f10 {K(d, 0.1)}, f50 {K(d, 0.5)}")
        print(f"{len(left)} of {len(jobs)} jobs left; about {len(left) * (settle + warm + window + 20) / 60:.0f} min")
        return
    import cpu_measure as CM
    import sched_inputs_frodo as SIF
    import sched_queue as SQ
    os.makedirs(base, exist_ok=True)
    ev = open(os.path.join(RES, RUN + ("_check" if a.check else "") + "_events.log"), "a")
    log = lambda s: SIF.log(ev, s)
    others = CM.Others()
    cc = SQ.coordinator_cpu()
    log(f"engaged power (duty cycles){' CHECK' if a.check else ''}: {len(left)} of {len(jobs)} jobs left")
    for r, j in left:
        d = os.path.join(base, f"rep{r}", j)
        kept = False
        for t in range(1, MAX_TRIES + 1):
            CS.smc_quiet(a.agent, a.port, log)
            time.sleep(settle)
            cmd = ["taskset", "-c", str(cc), sys.executable, os.path.abspath(__file__), "--one", j, "--out", d, "--agent", a.agent,
                   "--port", str(a.port), "--xrt-ini", a.xrt_ini, "--warm-s", str(warm), "--window-s", str(window)]
            if not SIF.measured(d, cmd, f"0-{os.cpu_count() - 1}", "0,1", dict(os.environ), others, ev, a.common, f"conc_duty rep{r} {j}"):
                break
            res = json.load(open(os.path.join(d, "conc_result.json")))
            why = why_bad(res, j)
            if not why:
                kept = True
                Pf, Ps = CS.frodo_power(d), CS.smc_power(res)
                log(f"rep{r} {j}: frodo {sum(Pf.values()):.1f} W, Supermicro {sum(Ps.values()):.1f} W; " +
                    ", ".join(f"{dv} f {v['busy_fraction']:.3f} ({v['rate_per_s']:.0f}/s, late {v['late_periods']})" for dv, v in res["devices"].items()))
                break
            log(f"rep{r} {j}: try {t} set aside: {why}")
            os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        if not kept:
            log(f"rep{r} {j}: NOT DONE")
    others.stop = True
    summary(a.check)
    n = sum(1 for x in jobs if os.path.exists(os.path.join(base, f"rep{x[0]}", x[1], "DONE")))
    log(f"CONC DUTY {'CHECK ' if a.check else ''}{'FINISHED' if n == len(jobs) else 'INCOMPLETE'}: {n} of {len(jobs)} jobs")


if __name__ == "__main__":
    main()
