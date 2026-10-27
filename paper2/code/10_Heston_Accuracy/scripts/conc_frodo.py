#!/usr/bin/env python3
"""conc_frodo.py - concurrency matrix, frodo part (step 5 of the revision; an input to addendum v0.9, measured before it).

Question: when the card and frodo's CPU device (fcpu, socket 1) run at the same time, does frodo draw more (or less)
power than the sum of their separate increments above idle, and does either device slow down?
  C = P(card + fcpu) - P(card alone) - P(fcpu alone) + P(idle)       (W, frodo boundary: card + both sockets + DRAM)
  s_d = rate of d alone / rate of d with the other device busy

Devices and pinning exactly as in the v0.8 scheduler (scripts/sched_run.py Local workers): the card's serve worker on
CPU 0 with --wait xrt and the registered xrt.ini; fcpu's native serve worker on socket 1 (48 threads, or 24 for the
half load). Each busy device prices the book's options of one class over and over (the classes the v0.8 plans gave
these devices), with no pause between options.

Jobs (one repeat):
  idle                     nothing runs
  card                     card alone: II QE at 0.3% (the class model_T and model_M gave the card)
  fcpu_qe, fcpu_ft         fcpu alone, 48 threads: III QE at 0.1% (model_T's fcpu class) / II FT at 1%
  fcpu_qe_half             fcpu alone, 24 threads (one per core): III QE at 0.1%
  card+fcpu_qe, card+fcpu_ft, card+fcpu_qe_half
Each job: wait for quiet (other users < 0.5 cores), 20 s idle, workers started and warmed for 10 s, then a 60 s window.
3 repeats, jobs interleaved, about 45 minutes. Output: results/conc_frodo/rep<k>/<job>/ and results/conc_frodo_summary.csv.

  python3 scripts/conc_frodo.py --plan
  nohup python3 scripts/conc_frodo.py > results/conc_frodo.log 2>&1 &
  python3 scripts/conc_frodo.py --summary
Inner use (inside common/run_measured.py): conc_frodo.py --one JOB --out DIR
"""
import argparse, csv, json, os, statistics, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import sched_run as SR

ROOT, RES = S.ROOT, S.RES
OUT = os.path.join(RES, "conc_frodo")
REPS, SETTLE_S, WARM_S, WINDOW_S = 3, 20.0, 10.0, 60.0
CLASS = {"card": ("II", "0.3%", "qe"), "fcpu_qe": ("III", "0.1%", "qe"), "fcpu_ft": ("II", "1%", "ft")}
JOBS = {
    "idle": {},
    "card": {"card": "card"},
    "fcpu_qe": {"fcpu": ("fcpu_qe", 48)},
    "fcpu_ft": {"fcpu": ("fcpu_ft", 48)},
    "fcpu_qe_half": {"fcpu": ("fcpu_qe", 24)},
    "card+fcpu_qe": {"card": "card", "fcpu": ("fcpu_qe", 48)},
    "card+fcpu_ft": {"card": "card", "fcpu": ("fcpu_ft", 48)},
    "card+fcpu_qe_half": {"card": "card", "fcpu": ("fcpu_qe", 24)},
}
ORDER = ("idle", "card", "fcpu_qe", "card+fcpu_qe", "fcpu_ft", "card+fcpu_ft", "fcpu_qe_half", "card+fcpu_qe_half")


def class_options(key):
    case, target, m = CLASS[key]
    opts = [o for o in S.book() if o["case"] == case and o["target"] == target]
    s0 = opts[0]["set"][m]
    cls = (case, m, s0["steps"], s0["seed"])
    return cls, [(o["id"], o["set"][m]["a0"], o["set"][m]["a1"], o["set"][m]["pairs"]) for o in opts]


class Half(SR.Local):
    """fcpu worker on the first n CPUs of socket 1 (one hardware thread per core when n = 24)"""

    def __init__(self, n, *a):
        super().__init__(*a)
        self.n = n

    def start(self, case, m, M, seed):
        orig = SR.cpulist
        SR.cpulist = lambda node: orig(node)[:self.n]
        try:
            super().start(case, m, M, seed)
        finally:
            SR.cpulist = orig


def one(job, out, xrt_ini):
    spec = JOBS[job]
    os.makedirs(out, exist_ok=True)
    workers, work = {}, {}
    for d, v in spec.items():
        key, n = (v, None) if d == "card" else v
        workers[d] = SR.Local(d, xrt_ini, out) if (d == "card" or n == 48) else Half(n, d, xrt_ini, out)
        work[d] = class_options(key)
    stop = threading.Event()
    done = {d: [] for d in workers}
    errs = []

    def loop(d):
        cls, parts = work[d]
        k = 0
        try:
            while not stop.is_set():
                res = workers[d].batch(cls, [(k * 1000 + j, a0, a1) for j, (_, a0, a1, _) in enumerate(parts)])
                for (i, _, _, pairs), (ta, busy, got, pr, ok) in zip(parts, res):
                    done[d].append((ta, busy, int(ok and got == pairs)))
                k += 1
        except Exception as e:                 # pragma: no cover
            errs.append(f"{d}: {e}")

    for d, w in workers.items():               # start (and load the card design) before the warm-up
        w.start(*work[d][0])
    th = [threading.Thread(target=loop, args=(d,), daemon=True) for d in workers]
    for t in th:
        t.start()
    time.sleep(WARM_S)
    t0 = time.time()
    print(f"MARK start {t0:.6f}", flush=True)
    time.sleep(WINDOW_S)
    t1 = time.time()
    print(f"MARK end {t1:.6f}", flush=True)
    stop.set()
    for t in th:
        t.join(timeout=600)
    for w in workers.values():
        w.stop()
    res = dict(job=job, t_start=t0, t_end=t1, errors=errs, devices={})
    for d in workers:
        inw = [x for x in done[d] if t0 <= x[0] <= t1]
        res["devices"][d] = dict(cls=list(work[d][0]), options_in_window=len(inw), rate_per_s=len(inw) / (t1 - t0),
                                 busy_median_s=statistics.median(x[1] for x in inw) if inw else None,
                                 ok=int(all(x[2] for x in inw)) if inw else 0, threads=None if d == "card" else spec[d][1])
    ok = int(not errs and all(v["ok"] for v in res["devices"].values()))
    res["ok"] = ok
    json.dump(res, open(os.path.join(out, "conc_result.json"), "w"), indent=1)
    print("RESULT runs=1\nRESULT path_steps=1\nRESULT ok=%d" % ok)
    sys.exit(0 if ok else 2)


def power(d):
    import sched_inputs_frodo as SIF
    t0, t1 = SIF.window(d)
    E = SIF.energies(SIF.power_rows(d), t0, t1)
    return {k: v / (t1 - t0) for k, v in E.items() if v is not None}


def summary():
    rows = []
    for r in range(1, REPS + 1):
        for j in ORDER:
            d = os.path.join(OUT, f"rep{r}", j)
            if not os.path.exists(os.path.join(d, "DONE")):
                continue
            P = power(d)
            res = json.load(open(os.path.join(d, "conc_result.json")))
            row = dict(rep=r, job=j, P_total_W=sum(P.values()), **{f"P_{k}_W": v for k, v in P.items()})
            for dev, v in res["devices"].items():
                row[f"{dev}_rate_per_s"] = v["rate_per_s"]
                row[f"{dev}_busy_median_s"] = v["busy_median_s"]
            rows.append(row)
    if not rows:
        print("no finished jobs"); return
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("rep", "job", "P_total_W"), k))
    with open(os.path.join(RES, "conc_frodo_summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
    med = lambda j, k: statistics.median(r[k] for r in rows if r["job"] == j and k in r and r[k] is not None)
    print(f"{'job':<20} {'P total (W)':>11}   per-domain medians (W)")
    for j in ORDER:
        if any(r["job"] == j for r in rows):
            dom = {k[2:-2]: med(j, k) for k in rows[0] if k.startswith("P_") and k != "P_total_W"}
            print(f"{j:<20} {med(j, 'P_total_W'):>11.1f}   " + "  ".join(f"{k} {v:.1f}" for k, v in dom.items()))
    try:
        Pi = med("idle", "P_total_W")
        for load in ("fcpu_qe", "fcpu_ft", "fcpu_qe_half"):
            C = med(f"card+{load}", "P_total_W") - med("card", "P_total_W") - med(load, "P_total_W") + Pi
            s_card = med("card", "card_rate_per_s") / med(f"card+{load}", "card_rate_per_s")
            s_cpu = med(load, "fcpu_rate_per_s") / med(f"card+{load}", "fcpu_rate_per_s")
            print(f"card + {load:<13} C = {C:+6.1f} W   slowdown card {s_card:.3f}, fcpu {s_cpu:.3f}")
    except Exception as e:
        print("C not computed yet:", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--one")
    ap.add_argument("--out")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    ap.add_argument("--xrt-ini", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common", "xrt_timing.ini")))
    a = ap.parse_args()
    if a.one:
        return one(a.one, a.out, a.xrt_ini)
    if a.summary:
        return summary()
    jobs = [(r, j) for r in range(1, REPS + 1) for j in ORDER]
    left = [x for x in jobs if not os.path.exists(os.path.join(OUT, f"rep{x[0]}", x[1], "DONE"))]
    if a.plan:
        for k in CLASS:
            cls, parts = class_options(k)
            print(f"class {k}: {cls}, {len(parts)} options")
        print(f"{len(left)} of {len(jobs)} jobs left; about {len(left) * (SETTLE_S + WARM_S + WINDOW_S + 15) / 60:.0f} min")
        return
    import cpu_measure as CM
    import sched_inputs_frodo as SIF
    import sched_queue as SQ
    os.makedirs(OUT, exist_ok=True)
    ev = open(os.path.join(RES, "conc_frodo_events.log"), "a")
    others = CM.Others()
    cc = SQ.coordinator_cpu()
    SIF.log(ev, f"concurrency matrix (frodo): {len(left)} of {len(jobs)} jobs left")
    for r, j in left:
        d = os.path.join(OUT, f"rep{r}", j)
        time.sleep(SETTLE_S)
        cmd = ["taskset", "-c", str(cc), sys.executable, os.path.abspath(__file__), "--one", j, "--out", d, "--xrt-ini", a.xrt_ini]
        ok = SIF.measured(d, cmd, f"0-{os.cpu_count() - 1}", "0,1", dict(os.environ), others, ev, a.common, f"conc rep{r} {j}")
        if ok:
            P = power(d)
            res = json.load(open(os.path.join(d, "conc_result.json")))
            SIF.log(ev, f"rep{r} {j}: frodo {sum(P.values()):.1f} W; " + ", ".join(f"{dv} {v['rate_per_s']:.2f} options/s" for dv, v in res["devices"].items()))
    others.stop = True
    summary()
    done = sum(1 for x in jobs if os.path.exists(os.path.join(OUT, f"rep{x[0]}", x[1], "DONE")))
    SIF.log(ev, f"CONC FRODO {'FINISHED' if done == len(jobs) else 'INCOMPLETE'}: {done} of {len(jobs)} jobs")


if __name__ == "__main__":
    main()
