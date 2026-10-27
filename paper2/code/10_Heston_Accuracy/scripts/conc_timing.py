#!/usr/bin/env python3
"""conc_timing.py - pass-time check with the fixed agent (step 5c of the revision; before addendum v0.9).

Why: with the v0.8 agent, mixed plans ran 7-32% longer than predicted. In the v0.8 passes the Supermicro CPU's replies
reached frodo in bursts (intervals between results ~2% of the options' times), so the pass ended late: the agent's
replies were held back by TCP's Nagle algorithm. The v0.9 agent sets TCP_NODELAY (and reads and writes on separate
streams, the Python 3.10 fix). This script runs registered v0.8 plans again with that agent, to see whether the pass
times now match the model, and re-measures the V100's and the Supermicro CPU's gap between options (the model's input,
from their alone runs) under the fixed agent. Not a test of v0.8 or v0.9: a pre-registration input and check.

Jobs (one repeat): alone_gpu, alone_scpu, model_E, model_T, model_M, model_dev (the plans of the registered v0.8
prediction file). Each: scripts/sched_run.py inside common/run_measured.py exactly as sched_queue.py runs it, 20 s idle
first, 1 warm-up pass, a window of at least 20 s and 3 passes. 3 repeats, about 20 min.
Output: results/timing_v09/rep<k>/<policy>/, results/timing_v09_summary.csv; --summary prints measured against
predicted pass time (v0.8 model, and with the re-measured gaps) and each device's finish time.

  python3 scripts/conc_timing.py --agent 10.8.100.5 --plan
  nohup python3 scripts/conc_timing.py --agent 10.8.100.5 > results/timing_v09.log 2>&1 &
  python3 scripts/conc_timing.py --summary
"""
import argparse, csv, json, os, shutil, statistics as st, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

ROOT, RES = S.ROOT, S.RES
OUT = os.path.join(RES, "timing_v09")
PRED = os.path.join(RES, "predictions_sched", "predictions_sched.json")
REPS, SETTLE_S, MAX_TRIES = 3, 20.0, 5
ORDER = ("alone_gpu", "alone_scpu", "model_E", "model_T", "model_M", "model_dev")


def gaps_measured():
    """the gap between consecutive results of one class beyond the part's busy time, per device, in this script's alone
    runs (median per repeat, then the median over the repeats) - the same rule as sched_model.check_gaps"""
    reps = [os.path.join(OUT, f"rep{r}") for r in range(1, REPS + 1) if os.path.isdir(os.path.join(OUT, f"rep{r}"))]
    g = S.check_gaps(sources=reps)
    return {d: v for d, v in g.items() if d in ("gpu", "scpu")}


def summary():
    import sched_queue as SQ
    pred = json.load(open(PRED))
    opts = S.book()
    M8 = S.Model()
    gm = gaps_measured()
    M9 = S.Model(gaps=gm) if gm else None
    rows = []
    for r in range(1, REPS + 1):
        for p in ORDER:
            d = os.path.join(OUT, f"rep{r}", p)
            if not os.path.exists(os.path.join(d, "DONE")):
                continue
            x = SQ.record(d)
            res = x.pop("res")
            dev_T = {k: st.median(ps["dev_T"].get(k, 0) for ps in res["passes"]) for k in S.DEVICES if any(k in ps["dev_T"] for ps in res["passes"])}
            rows.append(dict(rep=r, policy=p, **{k: v for k, v in x.items() if not isinstance(v, (dict, list))},
                             **{f"finish_{k}_s": v for k, v in dev_T.items()}))
    if not rows:
        print("no finished jobs"); return
    keys = ["rep", "policy"] + sorted({k for x in rows for k in x} - {"rep", "policy"})
    with open(os.path.join(RES, "timing_v09_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    print("gaps re-measured (alone runs, fixed agent):", {d: f"{v['gap_s'] * 1e3:.3f} ms" for d, v in gm.items()},
          " v0.8 inputs:", {d: f"{v['gap_s'] * 1e3:.3f} ms" for d, v in S.check_gaps().items() if d in ("gpu", "scpu")})
    print(f"{'policy':<11} {'T meas (s)':>10} {'T v0.8 model':>12} {'ratio':>6} {'T new gaps':>10} {'ratio':>6}   finish per device (meas / v0.8 model, ms)")
    for p in ORDER:
        xs = [x for x in rows if x["policy"] == p]
        if not xs:
            continue
        Tm = st.median(x["T_pass_s"] for x in xs)
        plan = {int(i): tuple(v) for i, v in pred["plans"][p].items()}
        e8 = M8.evaluate(plan, opts)
        e9 = M9.evaluate(plan, opts) if M9 else None
        fin = "  ".join(f"{k} {st.median(x[f'finish_{k}_s'] for x in xs if f'finish_{k}_s' in x) * 1e3:.1f}/{e8['busy'][k] * 1e3:.1f}"
                        for k in S.DEVICES if any(f"finish_{k}_s" in x for x in xs))
        print(f"{p:<11} {Tm:>10.4f} {e8['T']:>12.4f} {Tm / e8['T']:>6.3f} " +
              (f"{e9['T']:>10.4f} {Tm / e9['T']:>6.3f}" if e9 else f"{'-':>10} {'-':>6}") + f"   {fin}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="10.8.100.5")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    if a.summary:
        return summary()
    pred = json.load(open(PRED))
    jobs = [(r, p) for r in range(1, REPS + 1) for p in ORDER]
    left = [j for j in jobs if not os.path.exists(os.path.join(OUT, f"rep{j[0]}", j[1], "DONE"))]
    if a.plan:
        est = sum(SETTLE_S + 25 + max(20.0, 3 * pred["predictions"][p]["T_s"]) + pred["predictions"][p]["T_s"] for _, p in left)
        print(f"{len(left)} of {len(jobs)} jobs left; about {est / 60:.0f} min")
        return
    import cpu_measure as CM
    import sched_queue as SQ
    import sched_inputs_frodo as SIF
    os.makedirs(OUT, exist_ok=True)
    ev = open(os.path.join(RES, "timing_v09_events.log"), "a")
    log = lambda s: SIF.log(ev, s)
    others = CM.Others()
    cc = SQ.coordinator_cpu()
    log(f"pass-time check (fixed agent): {len(left)} of {len(jobs)} jobs left")
    for r, p in left:
        d = os.path.join(OUT, f"rep{r}", p)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        pf = os.path.join(OUT, f"rep{r}", f"{p}.plan.json")
        json.dump(dict(policy=p, rep=r, plan=pred["plans"][p]), open(pf, "w"), indent=1)
        cmd = ["taskset", "-c", str(cc), sys.executable, os.path.join(ROOT, "scripts", "sched_run.py"), "--plan", pf, "--agent", a.agent,
               "--out", d, "--window-s", "20", "--min-passes", "3"]
        ok = False
        for t in range(1, MAX_TRIES + 1):
            CM.wait_quiet(others, log)
            time.sleep(SETTLE_S)
            if os.path.isdir(d):
                shutil.rmtree(d)
            t0 = time.time()
            pr = subprocess.run([sys.executable, os.path.join(a.common, "run_measured.py"), "--out", d, "--timeout", "1800", "--label", f"timing rep{r} {p}",
                                 "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--cores", f"0-{os.cpu_count() - 1}", "--node", "0,1", "--"] + cmd,
                                capture_output=True, text=True)
            mean, peak = others.stats(t0, time.time())
            why = ""
            if not os.path.exists(os.path.join(d, "DONE")) or not os.path.exists(os.path.join(d, "sched_result.json")):
                why = f"failed (rc {pr.returncode}) {pr.stderr.strip()[-300:]}"
            else:
                x = SQ.record(d)
                if mean > CM.BUSY_MEAN or peak > CM.BUSY_PEAK:
                    why = f"frodo others {mean:.2f} cores"
                elif x["smc_other_mean"] > CM.BUSY_MEAN or x["smc_other_max"] > CM.BUSY_PEAK:
                    why = f"Supermicro others {x['smc_other_mean']:.2f} cores"
                elif abs((x["sm_mhz"] or 0) - 1245) > 12.5 and any(v[0] == "gpu" for v in pred["plans"][p].values()):
                    why = f"GPU clock {x['sm_mhz']} MHz"
                elif not x["ok"]:
                    why = "an option's run was not ok"
            if not why:
                pp = pred["predictions"][p]
                log(f"rep{r} {p}: {x['passes']} passes, T {x['T_pass_s']:.4f} s (v0.8 model {pp['T_s']:.4f}), E {x['E_pass_J']:.1f} J (v0.8 model {pp['E_J']:.1f})")
                ok = True
                break
            log(f"rep{r} {p}: try {t} set aside: {why}")
            if os.path.exists(os.path.join(d, "DONE")):
                os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        if not ok:
            log(f"rep{r} {p}: NOT DONE")
    others.stop = True
    summary()
    n = sum(1 for j in jobs if os.path.exists(os.path.join(OUT, f"rep{j[0]}", j[1], "DONE")))
    log(f"TIMING CHECK {'FINISHED' if n == len(jobs) else 'INCOMPLETE'}: {n} of {len(jobs)} jobs")


if __name__ == "__main__":
    main()
