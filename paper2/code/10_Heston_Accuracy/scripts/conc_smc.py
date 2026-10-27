#!/usr/bin/env python3
"""conc_smc.py - concurrency matrix, Supermicro and cross-server part (step 5 of the revision; an input to addendum
v0.9, measured before it). Runs on frodo, as the v0.8 scheduler does; the Supermicro's agent (scripts/sched_agent.py,
v0.9 version) must be running there with the V100 locked at 1245 MHz.

Questions:
  gpu + scpu (the V100 and the Supermicro's socket 0): C = P(gpu + scpu) - P(gpu) - P(scpu) + P(idle) at the
      Supermicro's boundary (GPU + both sockets + DRAM), at 20 threads and at 10 (one per core: the load check), and
      each device's stretch s = rate alone / rate together.
  cross-server pairs (card + gpu, card + scpu, fcpu + gpu, fcpu + scpu): the same at both servers' boundary; no shared
      hardware, so C should be near 0 - measured, not assumed (the v0.8 model_dev plan, card + V100, used 12% more
      energy than the corrected model gives).
Devices and pinning as in the v0.8 scheduler: card (CPU 0, --wait xrt, registered xrt.ini) and fcpu (socket 1,
48 threads) here; gpu (hs_coexec on CPU 10, block wait) and scpu (socket 0, 20 or 10 threads) through the agent.
Each busy device prices the book's options of one class over and over, with no pause:
  card II QE 0.3% (as conc_frodo), fcpu III QE 0.1% 48 threads (as conc_frodo), gpu I QE 0.3% (80 steps; the class the
  v0.8 plans gave the V100 most), scpu III QE 1% (20 steps; the most common Supermicro CPU class of the v0.8 plans).
  Feeding the agent's devices (--feed):
    batch  (default, run 2) as the scheduler does: 64 commands at once, the next 64 when all have come back
           -> results/conc_smc2/, conc_smc2_summary.csv, conc_cross2_summary.csv
    single (run 1, 26 Oct 2026) one new command per reply, 8 outstanding -> results/conc_smc/, conc_smc_summary.csv,
           conc_cross_summary.csv. Run 1 found that this fine-grained feeding itself costs power: with only the V100
           busy, frodo drew +21 W (the coordinator) and the V100 alone +110 W at the two servers against the model's
           57 W from the single-run inputs; in the v0.8 passes (batched) frodo drew +2 W. Its C values are relative to
           those inflated alone powers, so the matrix uses run 2; run 1 is kept as a descriptive result.
Jobs (one repeat): idle, gpu, scpu, gpu+scpu, scpu_half, gpu+scpu_half, card, fcpu, card+gpu, card+scpu, fcpu+gpu,
  fcpu+scpu. Each: wait until both servers are quiet, 20 s idle, workers started, 10 s warm-up, 60 s window; frodo's
  power from common/run_measured.py, the Supermicro's from the agent over the same window (clock offset by round trips).
  A job is repeated (up to 5 tries) if frodo's or the Supermicro's other users used > 0.5 core on average or > 4 in a
  second, if another GPU process appeared, if the V100's median SM clock was not 1245 +- 12.5 MHz (when used), or if
  any option was not ok. 3 repeats, about 65 min.
Output: results/conc_smc/rep<k>/<job>/, results/conc_smc_summary.csv (Supermicro boundary),
        results/conc_cross_summary.csv (both servers); then scripts/conc_matrix.py builds the matrix.

  python3 scripts/conc_smc.py --agent 10.8.100.5 --plan
  python3 scripts/conc_smc.py --agent 10.8.100.5 --check            one short repeat (5 s settle, 3 s warm-up, 10 s window; not a result)
  nohup python3 scripts/conc_smc.py --agent 10.8.100.5 > results/conc_smc.log 2>&1 &
  python3 scripts/conc_smc.py --summary
"""
import argparse, csv, json, os, shutil, statistics, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import sched_run as SR
import sched_client as SC

ROOT, RES = S.ROOT, S.RES
REPS, MAX_TRIES, OUTSTANDING, BATCH = 3, 5, 8, 64
FEED = "batch"
DRAIN_S, MAX_GAP_S = 20.0, 2.0     # after the window: wait at most 20 s for the outstanding replies; a remote device whose
                                   # replies stop for more than 2 s inside the window makes the job not ok (check run 1:
                                   # the V100's last 8 replies never came back after the window, and the job waited 600 s)
TIMING = dict(run=(20.0, 10.0, 60.0), check=(5.0, 3.0, 10.0))       # settle, warm-up, window (s)
CLASS = {"card": ("II", "0.3%", "qe"), "fcpu": ("III", "0.1%", "qe"), "gpu": ("I", "0.3%", "qe"), "scpu": ("III", "1%", "qe")}
JOBS = {
    "idle": {},
    "gpu": {"gpu": None}, "scpu": {"scpu": 20}, "gpu+scpu": {"gpu": None, "scpu": 20},
    "scpu_half": {"scpu": 10}, "gpu+scpu_half": {"gpu": None, "scpu": 10},
    "card": {"card": None}, "fcpu": {"fcpu": 48},
    "card+gpu": {"card": None, "gpu": None}, "card+scpu": {"card": None, "scpu": 20},
    "fcpu+gpu": {"fcpu": 48, "gpu": None}, "fcpu+scpu": {"fcpu": 48, "scpu": 20},
}
ORDER = ("idle", "gpu", "scpu", "gpu+scpu", "scpu_half", "gpu+scpu_half", "card", "fcpu", "card+gpu", "card+scpu", "fcpu+gpu", "fcpu+scpu")
SMC_JOBS = ("idle", "gpu", "scpu", "gpu+scpu", "scpu_half", "gpu+scpu_half")
CROSS_JOBS = ("idle", "card", "fcpu", "gpu", "scpu", "card+gpu", "card+scpu", "fcpu+gpu", "fcpu+scpu")


def class_options(dev):
    case, target, m = CLASS[dev]
    opts = [o for o in S.book() if o["case"] == case and o["target"] == target]
    s0 = opts[0]["set"][m]
    return (case, m, s0["steps"], s0["seed"]), [(o["id"], o["set"][m]["a0"], o["set"][m]["a1"], o["set"][m]["pairs"]) for o in opts]


def tag():
    return "conc_smc2" if FEED == "batch" else "conc_smc"


def out_dir(check):
    return os.path.join(RES, tag() + ("_check" if check else ""))


# ------------------------------------------------------------------ one job (inside run_measured.py on frodo)
def one(job, out, agent, port, xrt_ini, warm_s, window_s):
    spec = JOBS[job]
    os.makedirs(out, exist_ok=True)
    ag = SC.Agent(agent, port)                       # the GPU worker and the sampler
    agc = SC.Agent(agent, port + 1)                  # the CPU worker
    sync0 = ag.sync(50)
    agc.sync(10)
    stop = threading.Event()
    done = {d: [] for d in spec}
    errs = []
    lost = {}
    local = {}
    for d in ("card", "fcpu"):
        if d in spec:
            local[d] = SR.Local(d, xrt_ini, out)
            local[d].start(*class_options(d)[0])     # start (and load the card design) before the warm-up

    def loop_local(d):
        cls, parts = class_options(d)
        k = 0
        try:
            while not stop.is_set():
                res = local[d].batch(cls, [(k * 1000 + j, a0, a1) for j, (_, a0, a1, _) in enumerate(parts)])
                for (i, _, _, pairs), (ta, busy, got, pr, ok) in zip(parts, res):
                    done[d].append((ta, busy, int(ok and got == pairs)))
                k += 1
        except Exception as e:                       # pragma: no cover
            errs.append(f"{d}: {e}")

    def loop_remote(d):
        a = ag if d == "gpu" else agc
        cls, parts = class_options(d)
        case, m, M, seed = cls
        n, sent, back = len(parts), 0, 0

        def send():
            nonlocal sent
            _, a0, a1, _ = parts[sent % n]
            job = dict(id=sent + 1, dev="gpu" if d == "gpu" else "cpu", case=case, scheme=m, steps=M, a0=a0, a1=a1, seed=seed)
            if d == "scpu":
                job["threads"] = spec[d]
            a.run(job)
            sent += 1
        t_stop, t_last = None, time.time()
        try:
            for _ in range(BATCH if FEED == "batch" else OUTSTANDING):
                send()
            while back < sent:
                if stop.is_set() and t_stop is None:
                    t_stop = time.time()
                if t_stop is not None and time.time() - t_stop > DRAIN_S:
                    lost[d] = sent - back                 # replies after the window: not needed, recorded only
                    return
                try:
                    _, r = a.runs.get(timeout=0.5)        # short waits, so the end of the window is seen
                except Exception:
                    if t_stop is None and time.time() - t_last > 600:
                        raise RuntimeError(f"no reply for 600 s ({sent - back} outstanding)")
                    continue
                t_last = time.time()
                if r is None:
                    raise RuntimeError("agent closed")
                back += 1
                pairs = parts[(r["id"] - 1) % n][3]
                done[d].append((r["t_recv"], r["busy"], int(r["ok"] and r["pairs"] == pairs)))
                if not stop.is_set():
                    if FEED != "batch":
                        send()
                    elif back == sent:                    # the whole batch is back: the next batch
                        for _ in range(BATCH):
                            send()
        except Exception as e:                       # pragma: no cover
            errs.append(f"{d}: {e}")

    th = [threading.Thread(target=loop_local if d in local else loop_remote, args=(d,), daemon=True) for d in spec]
    for t in th:
        t.start()
    time.sleep(warm_s)
    sync1 = ag.sync(30)
    t0 = time.time()
    print(f"MARK start {t0:.6f}", flush=True)
    time.sleep(window_s)
    t1 = time.time()
    print(f"MARK end {t1:.6f}", flush=True)
    stop.set()
    for t in th:
        t.join(timeout=600)
    smc = ag.energy(t0, t1)
    sync2 = ag.sync(30)
    for x in (ag, agc):
        try:
            x.stop()
        except Exception as e:                       # pragma: no cover
            errs.append(f"agent stop: {e}")
        x.close()
    for w in local.values():
        w.stop()
    res = dict(job=job, feed=FEED, t_start=t0, t_end=t1, errors=errs, devices={}, smc_energy=smc, sync=[sync0, sync1, sync2])
    for d in spec:
        inw = [x for x in done[d] if t0 <= x[0] <= t1]
        ts = [t0] + sorted(x[0] for x in done[d] if t0 <= x[0] <= t1) + [t1]      # the window's ends count as well
        gap = max((b - a for a, b in zip(ts, ts[1:])), default=None)
        okd = int(all(x[2] for x in inw)) if inw else 0
        if d not in local and (gap is None or gap > MAX_GAP_S):
            okd = 0
            errs.append(f"{d}: replies stopped for {gap} s inside the window")
        res["devices"][d] = dict(cls=list(class_options(d)[0]), options_in_window=len(inw), rate_per_s=len(inw) / (t1 - t0),
                                 busy_median_s=statistics.median(x[1] for x in inw) if inw else None,
                                 ok=okd, threads=spec[d], max_gap_s=gap, replies_after_window_missing=lost.get(d, 0),
                                 last_reply_after_t1_s=(max(x[0] for x in done[d]) - t1) if done[d] else None)
    ok = int(not errs and all(v["ok"] for v in res["devices"].values()))
    res["ok"] = ok
    json.dump(res, open(os.path.join(out, "conc_result.json"), "w"), indent=1)
    print("RESULT runs=1\nRESULT path_steps=1\nRESULT ok=%d" % ok)
    sys.exit(0 if ok else 2)


# ------------------------------------------------------------------ the queue (on frodo)
def frodo_power(d):
    import sched_inputs_frodo as SIF
    t0, t1 = SIF.window(d)
    E = SIF.energies(SIF.power_rows(d), t0, t1)
    return {k: v / (t1 - t0) for k, v in E.items() if v is not None}


def smc_power(res):
    w = res["t_end"] - res["t_start"]
    return {k: v / w for k, v in res["smc_energy"]["E_J"].items()}


def smc_quiet(agent, port, log):
    """wait until the Supermicro is quiet: other users' CPU over the last 20 s and other GPU processes, from the agent"""
    import cpu_measure as CM
    while True:
        try:
            a = SC.Agent(agent, port)
            a.sync(10)
            now = time.time()
            e = a.energy(now - 20, now)
            a.close()
        except Exception as ex:
            log(f"agent not reachable at {agent}:{port} ({ex}); waiting 60 s")
            time.sleep(60)
            continue
        busy = e["other_mean"] > CM.BUSY_MEAN or e["other_max"] > CM.BUSY_PEAK or (e.get("gpu_other_procs") or 0) > 0
        if not busy:
            return e
        log(f"Supermicro busy (others {e['other_mean']:.2f} cores, peak {e['other_max']:.1f}, other GPU processes {e.get('gpu_other_procs')}); waiting 60 s")
        time.sleep(60)


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
        return "an option was not ok: " + "; ".join(res["errors"])[-300:]
    return ""


def summary(check=False):
    base = out_dir(check)
    rows = []
    for r in range(1, REPS + 1):
        for j in ORDER:
            d = os.path.join(base, f"rep{r}", j)
            if not os.path.exists(os.path.join(d, "DONE")):
                continue
            res = json.load(open(os.path.join(d, "conc_result.json")))
            Pf, Ps = frodo_power(d), smc_power(res)
            row = dict(rep=r, job=j, P_frodo_W=sum(Pf.values()), P_smc_W=sum(Ps.values()),
                       **{f"P_{k}_W": v for k, v in Pf.items()}, **{f"P_smc_{k}_W": v for k, v in Ps.items()},
                       smc_other_mean=res["smc_energy"]["other_mean"], sm_mhz=res["smc_energy"].get("sm_mhz_median"))
            for dev, v in res["devices"].items():
                row[f"{dev}_rate_per_s"] = v["rate_per_s"]
                row[f"{dev}_busy_median_s"] = v["busy_median_s"]
            rows.append(row)
    if not rows:
        print("no finished jobs"); return
    keys = sorted({k for x in rows for k in x}, key=lambda k: (k not in ("rep", "job", "P_total_W"), k))
    sfx = "_check" if check else ""
    names = ("conc_smc2", "conc_cross2") if FEED == "batch" else ("conc_smc", "conc_cross")
    for name, jobs, tot in ((names[0], SMC_JOBS, lambda x: x["P_smc_W"]), (names[1], CROSS_JOBS + ("gpu+scpu", "gpu+scpu_half", "scpu_half"), lambda x: x["P_frodo_W"] + x["P_smc_W"])):
        sel = [dict(x, P_total_W=tot(x)) for x in rows if x["job"] in jobs]
        if name == names[0]:                        # the Supermicro's boundary only
            sel = [{k: v for k, v in x.items() if not (k.startswith("P_") and not k.startswith("P_smc_") and k != "P_total_W")} for x in sel]
        ks = ["rep", "job", "P_total_W"] + [k for k in keys if k not in ("rep", "job") and any(k in x for x in sel)]
        with open(os.path.join(RES, f"{name}{sfx}_summary.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ks); w.writeheader(); w.writerows(sel)
    med = lambda j, k: statistics.median(x[k] for x in rows if x["job"] == j and x.get(k) is not None)
    have = lambda *js: all(any(x["job"] == j for x in rows) for j in js)
    print(f"{'job':<16} {'frodo (W)':>9} {'Supermicro (W)':>14}   Supermicro domains (W)                      rates (options/s)")
    for j in ORDER:
        if have(j):
            dom = "  ".join(f"{k[6:-2]} {med(j, k):.1f}" for k in sorted(rows[0]) if k.startswith("P_smc_") and k != "P_smc_W")
            rt = ", ".join(f"{dv} {med(j, f'{dv}_rate_per_s'):.1f}" for dv in JOBS[j])
            print(f"{j:<16} {med(j, 'P_frodo_W'):>9.1f} {med(j, 'P_smc_W'):>14.1f}   {dom}   {rt}")
    try:                                            # each device alone against the model's energy per option (v0.8 inputs)
        M = S.Model()
        Pi = med("idle", "P_frodo_W") + med("idle", "P_smc_W")
        for dv in ("card", "fcpu", "gpu", "scpu"):
            if have(dv):
                c, t, m = CLASS[dv]
                e_meas = (med(dv, "P_frodo_W") + med(dv, "P_smc_W") - Pi) / med(dv, f"{dv}_rate_per_s")
                e_mod = M.C[(dv, c, t, m)]["dE"]
                print(f"{dv:<5} alone: {e_meas * 1e3:8.2f} mJ per option above idle (two servers); model {e_mod * 1e3:8.2f} mJ; ratio {e_meas / e_mod:.3f}")
    except Exception as ex:                         # pragma: no cover
        print("model comparison not available:", ex)
    for a, b, ab, key in (("gpu", "scpu", "gpu+scpu", "P_smc_W"), ("gpu", "scpu_half", "gpu+scpu_half", "P_smc_W"),
                          ("card", "gpu", "card+gpu", None), ("card", "scpu", "card+scpu", None), ("fcpu", "gpu", "fcpu+gpu", None), ("fcpu", "scpu", "fcpu+scpu", None)):
        if not have(a, b, ab, "idle"):
            continue
        P = (lambda j: med(j, key)) if key else (lambda j: med(j, "P_frodo_W") + med(j, "P_smc_W"))
        C = P(ab) - P(a) - P(b) + P("idle")
        da, db = a.split("_")[0], b.split("_")[0]
        sa = med(a, f"{da}_rate_per_s") / med(ab, f"{da}_rate_per_s")
        sb = med(b, f"{db}_rate_per_s") / med(ab, f"{db}_rate_per_s")
        print(f"{ab:<16} C = {C:+6.1f} W   slowdown {da} {sa:.3f}, {db} {sb:.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="10.8.100.5")
    ap.add_argument("--port", type=int, default=50555)
    ap.add_argument("--one")
    ap.add_argument("--out")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--feed", choices=("batch", "single"), default="batch")
    ap.add_argument("--warm-s", type=float)
    ap.add_argument("--window-s", type=float)
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    ap.add_argument("--xrt-ini", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common", "xrt_timing.ini")))
    a = ap.parse_args()
    global FEED
    FEED = a.feed
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
        for k in CLASS:
            cls, parts = class_options(k)
            print(f"class {k}: {cls}, {len(parts)} options")
        print(f"{len(left)} of {len(jobs)} jobs left; about {len(left) * (settle + warm + window + 20) / 60:.0f} min")
        return
    import cpu_measure as CM
    import sched_inputs_frodo as SIF
    import sched_queue as SQ
    os.makedirs(base, exist_ok=True)
    ev = open(os.path.join(RES, tag() + ("_check" if a.check else "") + "_events.log"), "a")
    log = lambda s: SIF.log(ev, s)
    others = CM.Others()
    cc = SQ.coordinator_cpu()
    log(f"concurrency matrix (Supermicro + cross-server, feed {FEED}){' CHECK' if a.check else ''}: {len(left)} of {len(jobs)} jobs left")
    failed = 0
    for r, j in left:
        d = os.path.join(base, f"rep{r}", j)
        kept = False
        for t in range(1, MAX_TRIES + 1):
            e = smc_quiet(a.agent, a.port, log)
            time.sleep(settle)
            cmd = ["taskset", "-c", str(cc), sys.executable, os.path.abspath(__file__), "--one", j, "--out", d, "--agent", a.agent, "--feed", FEED,
                   "--port", str(a.port), "--xrt-ini", a.xrt_ini, "--warm-s", str(warm), "--window-s", str(window)]
            if not SIF.measured(d, cmd, f"0-{os.cpu_count() - 1}", "0,1", dict(os.environ), others, ev, a.common, f"conc_smc rep{r} {j}"):
                break                                         # frodo was busy or the job failed 5 times
            res = json.load(open(os.path.join(d, "conc_result.json")))
            why = why_bad(res, j)
            if not why:
                kept = True
                Pf, Ps = frodo_power(d), smc_power(res)
                log(f"rep{r} {j}: frodo {sum(Pf.values()):.1f} W, Supermicro {sum(Ps.values()):.1f} W (GPU {Ps.get('gpu', 0):.1f}); " +
                    ", ".join(f"{dv} {v['rate_per_s']:.2f} options/s" for dv, v in res["devices"].items()) +
                    (f"; V100 {res['smc_energy'].get('sm_mhz_median')} MHz" if "gpu" in JOBS[j] else ""))
                break
            log(f"rep{r} {j}: try {t} set aside: {why}")
            os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        if not kept:
            failed += 1
            log(f"rep{r} {j}: NOT DONE")
    others.stop = True
    summary(a.check)
    done = sum(1 for x in jobs if os.path.exists(os.path.join(base, f"rep{x[0]}", x[1], "DONE")))
    log(f"CONC SMC {'CHECK ' if a.check else ''}{'FINISHED' if done == len(jobs) else 'INCOMPLETE'}: {done} of {len(jobs)} jobs")


if __name__ == "__main__":
    main()
