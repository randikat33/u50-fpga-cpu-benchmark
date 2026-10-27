#!/usr/bin/env python3
"""sched_queue_v09.py - the scheduling runs of addendum v0.9 (three regimes), on frodo (the Supermicro's agent must be
running). sched_queue.py (v0.8) with the v0.9 jobs; the per-job procedure, the quietness rules and the repeat rules are
unchanged.
    python3 scripts/sched_queue_v09.py --agent 10.8.100.5 --plan          jobs left and time
    nohup python3 scripts/sched_queue_v09.py --agent 10.8.100.5 --reps 1-3 > results/sched_v09.log 2>&1 &     (day 1)
    nohup python3 scripts/sched_queue_v09.py --agent 10.8.100.5 --reps 4-5 >> results/sched_v09.log 2>&1 &    (day 2)
    python3 scripts/sched_queue_v09.py --agent 10.8.100.5 --check        short test of every job (not a result)
    python3 scripts/sched_queue_v09.py --summary
Jobs: 5 repeats; in each, in this order: alone_fcpu, alone_card, alone_scpu, alone_gpu, model_E, model_T, model_M,
model_dev, model_E_f (the registered plans of results/predictions_sched_v09/predictions_sched_v09.json, sha256 checked),
then oracle (R1: sched_model.corrected on this repeat's four alone runs, then model_E's algorithm), oracle_M (R2: the
same costs, model_M's algorithm at the deadline D, no margin) and oracle_f (R3: the card's and fcpu's alone runs at
frodo's boundary, then model_E_f's algorithm). R3's alone policies are the R1 alone_card and alone_fcpu runs at frodo's
boundary (the same plans), so they are not run twice. R3 jobs (model_E_f, oracle_f) are measured at frodo's boundary. Each job: scripts/sched_run.py on
CPU 0's sibling, inside common/run_measured.py (all CPUs, 10 Hz power log, MARK window); 1 warm-up pass, then a window
of at least 60 s and 3 passes. Each job waits for quiet, then 20 s idle (5 s in --check). A job is repeated (up to 5 tries) if frodo's other users used > 1 core on average or > 4
in any second, if the Supermicro's agent saw the same, if the GPU's median SM clock was not 1245 +- 12.5 MHz (GPU in the
plan), or if any option's run was not ok.
Output: results/sched/rep<k>/<policy>/..., results/sched_summary.csv, results/sched_events.log
"""
import argparse, csv, hashlib, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

ROOT, RES = S.ROOT, S.RES
PRED = os.path.join(RES, "predictions_sched_v09", "predictions_sched_v09.json")
REPS, MAX_TRIES = 5, 5
SETTLE_S = 20.0          # idle seconds before each job (check 4: frodo's power stayed high after a heavy job)
ORDER = ("alone_fcpu", "alone_card", "alone_scpu", "alone_gpu", "model_E", "model_T", "model_M", "model_dev", "model_E_f",
         "oracle", "oracle_M", "oracle_f")
FRODO_ONLY = ("model_E_f", "oracle_f")
DERIVED = {"alone_card_f": "alone_card", "alone_fcpu_f": "alone_fcpu"}     # R3 alone policies = R1 runs, frodo boundary
# (checks 2-5: the job after alone_fcpu drew up to 65 W more on frodo, so alone_fcpu is followed by the long card job)


def frodo_energy(d):
    import sched_inputs_frodo as SIF
    t0, t1 = SIF.window(d)
    E = SIF.energies(SIF.power_rows(d), t0, t1)
    return sum(E.values()), E


def record(d, smc=True):
    res = json.load(open(os.path.join(d, "sched_result.json")))
    Ef, parts = frodo_energy(d)
    mp = S.measured_pass(res, Ef, smc=smc)
    opts = {o["id"]: o for o in S.book()}
    errs = []
    for p in res["passes"]:
        for x in p["options"]:
            o = opts[x["id"]]
            ref = ref_price(o["case"])
            errs.append(max(abs(x["prices"][k] - ref[k]) for k in range(3)) / o["eps"])
    return dict(T_pass_s=mp["T"], E_pass_J=mp["E"], passes=mp["passes"], pass_median_s=statistics.median(p["T"] for p in res["passes"]),
                E_frodo_J=Ef, E_smc_J=sum(res["smc_energy"]["E_J"].values()), smc_other_mean=res["smc_energy"]["other_mean"],
                smc_other_max=res["smc_energy"]["other_max"], sm_mhz=res["smc_energy"]["sm_mhz_median"], ok=res["ok"],
                err_max_over_eps=max(errs), err_rms_over_eps=(sum(e * e for e in errs) / len(errs)) ** 0.5, res=res)


def ref_price(case):
    import hqlib as HQ, hslib as HS
    return HQ.CASES[case]["ref"] if case in S.MAIN else [HS.ref(case, k) for k in range(3)]


def coordinator_cpu():
    s = open("/sys/devices/system/cpu/cpu0/topology/thread_siblings_list").read().strip().replace("-", ",").split(",")
    return int(s[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="10.8.100.5")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--reps", default="1-5", help="repeats to run, e.g. 1-3 (day 1) and 4-5 (day 2)")
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    pred = json.load(open(PRED))
    sha = hashlib.sha256(open(PRED, "rb").read()).hexdigest()
    if not str(pred.get("version", "")).startswith("0.9") or "PROVISIONAL" in str(pred.get("version")):
        sys.exit(f"{PRED} is not the registered v0.9 prediction (version {pred.get('version')})")
    base = os.path.join(RES, "sched_v09_check" if a.check else "sched_v09")
    r0, r1 = (int(x) for x in a.reps.split("-")) if "-" in a.reps else (int(a.reps), int(a.reps))
    reps = range(1, 2) if a.check else range(r0, r1 + 1)
    jobs = [(r, p) for r in reps for p in ORDER]
    left = [j for j in jobs if not os.path.exists(os.path.join(base, f"rep{j[0]}", j[1], "DONE"))]
    if a.summary:
        return summary(pred)
    if a.plan:
        est = 0.0
        for r, p in left:
            T = pred["predictions"].get(p, pred["predictions"]["model_E"])["T_s"]
            est += 15 + SETTLE_S + T + max(60.0, 3 * T)
        print(f"{len(left)} of {len(jobs)} jobs left; about {est / 3600:.1f} h; prediction sha256 {sha}")
        return
    import cpu_measure as CM
    for f in ("run_measured.py", "power_monitor.py", "xrt_timing.ini"):
        if not os.path.exists(os.path.join(a.common, f)):
            sys.exit(f"{f} not found in {a.common}")
    os.makedirs(base, exist_ok=True)
    ev = open(os.path.join(RES, "sched_v09_check_events.log" if a.check else "sched_v09_events.log"), "a")

    def log(s):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + s
        print(line, flush=True); ev.write(line + "\n"); ev.flush()

    log(f"v0.9 scheduling {'CHECK' if a.check else 'runs'}: {len(left)} of {len(jobs)} jobs left; prediction sha256 {sha}")
    others = CM.Others()
    cc = coordinator_cpu()
    opts = S.book()
    M = S.Model.from_prediction(pred)
    F = S.Model(C=M.C, CH=M.CH, P=M.P, latency=M.latency, conc=M.conc, devices=("card", "fcpu"), boundary=("frodo",))
    F.t_ov = dict(M.t_ov)
    D = pred["regimes"]["R2"]["deadline_s"]
    plan_of = lambda k: {int(i): tuple(x) for i, x in pred["plans"][k].items()}
    failed = 0
    for r, p in left:
        d = os.path.join(base, f"rep{r}", p)
        os.makedirs(d, exist_ok=True)
        if p.startswith("oracle"):
            devs = ("card", "fcpu") if p == "oracle_f" else S.DEVICES
            alone = {}
            for dev in devs:
                da = os.path.join(base, f"rep{r}", f"alone_{dev}")
                x = record(da)
                alone[dev] = (x["res"], x["E_frodo_J"])
            if p == "oracle_f":
                N = S.corrected(F, opts, alone)
                plan = N.optimise(opts, inits=[plan_of("model_E_f")])
            elif p == "oracle_M":
                N = S.corrected(M, opts, alone)
                plan = N.optimise(opts, deadline=D, inits=[plan_of(k) for k in ("model_M", "model_E", "model_T")])
            else:
                N = S.corrected(M, opts, alone)
                plan = N.optimise(opts, inits=[plan_of(k) for k in ("model_E", "model_T", "model_M")])
            evp = N.evaluate(plan, opts)
            pj = dict(policy=p, rep=r, plan=S.plan_json(plan), predicted_with_measured_costs=dict(T_s=evp["T"], E_J=evp["E"], n=evp["n"]))
        else:
            pj = dict(policy=p, rep=r, plan=pred["plans"][p])
        pf = os.path.join(base, f"rep{r}", f"{p}.plan.json")
        json.dump(pj, open(pf, "w"), indent=1)
        uses_gpu = any(x[0] == "gpu" for x in pj["plan"].values())
        win = ["--window-s", "3", "--min-passes", "1"] if a.check else ["--window-s", "60", "--min-passes", "3"]
        cmd = ["taskset", "-c", str(cc), sys.executable, os.path.join(ROOT, "scripts", "sched_run.py"), "--plan", pf, "--agent", a.agent, "--out", d] + win
        ok = False
        for t in range(1, MAX_TRIES + 1):
            CM.wait_quiet(others, log)
            time.sleep(SETTLE_S if not a.check else 5.0)
            if os.path.isdir(d):
                shutil.rmtree(d)
            t0 = time.time()
            pr = subprocess.run([sys.executable, os.path.join(a.common, "run_measured.py"), "--out", d, "--timeout", "3600", "--label", f"sched rep{r} {p}",
                                 "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--cores", f"0-{os.cpu_count() - 1}", "--node", "0,1", "--"] + cmd,
                                capture_output=True, text=True)
            t1 = time.time()
            mean, peak = others.stats(t0, t1)
            if os.path.isdir(d):
                json.dump(dict(frodo_others_mean=mean, frodo_others_peak=peak, t0=t0, t1=t1, try_=t), open(os.path.join(d, "contamination.json"), "w"))
            why = ""
            if not os.path.exists(os.path.join(d, "DONE")) or not os.path.exists(os.path.join(d, "sched_result.json")):
                why = f"failed (rc {pr.returncode}) {pr.stderr.strip()[-300:]}"
            else:
                x = record(d, smc=p not in FRODO_ONLY)
                if mean > CM.BUSY_MEAN or peak > CM.BUSY_PEAK:
                    why = f"frodo others {mean:.2f} cores (peak {peak:.1f})"
                elif x["smc_other_mean"] > CM.BUSY_MEAN or x["smc_other_max"] > CM.BUSY_PEAK:
                    why = f"Supermicro others {x['smc_other_mean']:.2f} cores (peak {x['smc_other_max']:.1f})"
                elif uses_gpu and abs((x["sm_mhz"] or 0) - 1245) > 12.5:
                    why = f"GPU clock {x['sm_mhz']} MHz"
                elif not x["ok"]:
                    why = "an option's run was not ok"
            if not why:
                pp = pred["predictions"].get(p)
                log(f"rep{r} {p}: {x['passes']} passes, T {x['T_pass_s']:.3f} s, E {x['E_pass_J']:.1f} J" +
                    (f" (predicted {pp['T_s']:.3f} s, {pp['E_J']:.1f} J)" if pp else f" (with measured costs {pj['predicted_with_measured_costs']['E_J']:.1f} J)") +
                    f", error max {x['err_max_over_eps']:.2f} eps")
                ok = True
                break
            log(f"rep{r} {p}: try {t} set aside: {why}")
            if os.path.exists(os.path.join(d, "DONE")):
                os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        if not ok:
            failed += 1
            if p.startswith("alone"):
                log(f"rep{r}: {p} not done, so this repeat's oracles cannot run")
    others.stop = True
    if not a.check:
        summary(pred)
    elif not failed:
        log("CHECK: every job ran; the check is not a result")
    done = sum(1 for j in jobs if os.path.exists(os.path.join(base, f"rep{j[0]}", j[1], "DONE")))
    log(f"SCHED {'CHECK ' if a.check else ''}{'FINISHED' if done == len(jobs) else 'INCOMPLETE'}: {done} of {len(jobs)} jobs, {failed} not done")


def regime(p):
    if p in ("model_E_f", "oracle_f") or p in DERIVED:
        return "R3"
    return "R2" if p in ("model_M", "oracle_M") else "R1"


def summary(pred):
    rows = []
    for r in range(1, REPS + 1):
        for p in ORDER + tuple(DERIVED):
            d = os.path.join(RES, "sched_v09", f"rep{r}", DERIVED.get(p, p))
            if os.path.exists(os.path.join(d, "DONE")):
                x = record(d, smc=regime(p) != "R3"); x.pop("res")
                pp = pred["predictions"].get(p, {})
                rows.append(dict(rep=r, regime=regime(p), policy=p, T_pred_s=pp.get("T_s", ""), E_pred_J=pp.get("E_J", ""), **x))
    if rows:
        f = os.path.join(RES, "sched_v09_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} jobs -> {f}")


if __name__ == "__main__":
    main()
