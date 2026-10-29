#!/usr/bin/env python3
"""sched_queue_v11.py - the calibration runs that precede addendum v0.11 (input measurements, before registration), on
frodo (the Supermicro's v0.9 agent must be running). The per-job procedure, quietness and repeat rules are v0.9's.
    python3 scripts/sched_queue_v11.py --agent 10.8.100.5 --plan
    nohup python3 scripts/sched_queue_v11.py --agent 10.8.100.5 > results/sched_calib_v11.log 2>&1 &
    python3 scripts/sched_queue_v11.py --summary
Calibration jobs (2 repeats): each of fcpu, gpu and scpu alone on book K1 (10 / 0 / 0 per case: 1% options only) and
on book K2 (0 / 10 / 0: 0.3% options only), with the v0.9 model's lower-energy method on that device; and the card on
book K (10 / 10 / 0) with only its Heston QE options (design qe) and only its Asian CV options (design asian).
Plans are made here from the registered v0.9 model (deterministic) and saved with each job.
Output: results/sched_calib_v11/<job>/rep<k>/..., results/sched_calib_v11_summary.csv, results/sched_calib_v11_events.log
"""
import argparse, csv, hashlib, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

ROOT, RES = S.ROOT, S.RES
REPS, MAX_TRIES = 5, 5
SETTLE_S = 20.0          # idle seconds before each job (check 4: frodo's power stayed high after a heavy job)
PRED9 = os.path.join(RES, "predictions_sched_v09", "predictions_sched_v09.json")
REPS = 2
K1, K2, K = (10, 0, 0), (0, 10, 0), (10, 10, 0)
JOBS = [("K1_alone_fcpu", K1, "fcpu"), ("K1_alone_gpu", K1, "gpu"), ("K1_alone_scpu", K1, "scpu"),
        ("K2_alone_fcpu", K2, "fcpu"), ("K2_alone_gpu", K2, "gpu"), ("K2_alone_scpu", K2, "scpu"),
        ("K_card_qe", K, "card_qe"), ("K_card_asian", K, "card_asian")]


def set_book(mix):
    os.environ["SCHED_BOOK"] = ",".join(str(x) for x in mix)


def make_plan(M, mix, what):
    opts = S.book(mix)
    if what == "card_qe":
        return {o["id"]: ("card", "qe") for o in opts if o["case"] in S.MAIN}
    if what == "card_asian":
        return {o["id"]: ("card", "cv") for o in opts if o["case"] == "A"}
    return M.alone(what, opts)


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
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    p9 = json.load(open(PRED9))
    M = S.Model.from_prediction(p9)
    base = os.path.join(RES, "sched_calib_v11")
    jobs = [(r, j) for r in range(1, REPS + 1) for j in JOBS]
    jdir = lambda r, j: os.path.join(base, j[0], f"rep{r}")
    left = [x for x in jobs if not os.path.exists(os.path.join(jdir(*x), "DONE"))]
    if a.summary:
        return summary(M)
    if a.plan:
        est = 0.0
        for r, j in left:
            pl = make_plan(M, j[1], j[2])
            ev = M.evaluate(pl, [o for o in S.book(j[1]) if o["id"] in pl])
            est += 15 + SETTLE_S + ev["T"] + max(30.0, 3 * ev["T"])
        print(f"{len(left)} of {len(jobs)} jobs left; about {est / 3600:.1f} h")
        return
    import cpu_measure as CM
    for f in ("run_measured.py", "power_monitor.py", "xrt_timing.ini"):
        if not os.path.exists(os.path.join(a.common, f)):
            sys.exit(f"{f} not found in {a.common}")
    os.makedirs(base, exist_ok=True)
    ev = open(os.path.join(RES, "sched_calib_v11_events.log"), "a")

    def log(s):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + s
        print(line, flush=True); ev.write(line + "\n"); ev.flush()

    log(f"v0.11 calibration: {len(left)} of {len(jobs)} jobs left (input measurements, before registration)")
    others = CM.Others()
    cc = coordinator_cpu()
    failed = 0
    for r, j in left:
        name, mix, what = j
        set_book(mix)
        d = jdir(r, j)
        os.makedirs(d, exist_ok=True)
        plan = make_plan(M, mix, what)
        pj = dict(policy=name, book=name.split("_")[0], mix=list(mix), rep=r, plan=S.plan_json(plan))
        pf = os.path.join(base, name, f"rep{r}.plan.json")
        json.dump(pj, open(pf, "w"), indent=1)
        uses_gpu = any(x[0] == "gpu" for x in plan.values())
        cmd = ["taskset", "-c", str(cc), sys.executable, os.path.join(ROOT, "scripts", "sched_run.py"), "--plan", pf, "--agent", a.agent, "--out", d,
               "--window-s", "30", "--min-passes", "3"]
        ok = False
        for t in range(1, MAX_TRIES + 1):
            CM.wait_quiet(others, log)
            time.sleep(SETTLE_S)
            if os.path.isdir(d):
                shutil.rmtree(d)
            t0 = time.time()
            pr = subprocess.run([sys.executable, os.path.join(a.common, "run_measured.py"), "--out", d, "--timeout", "3600", "--label", f"calib v0.11 {name} rep{r}",
                                 "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--cores", f"0-{os.cpu_count() - 1}", "--node", "0,1", "--"] + cmd,
                                capture_output=True, text=True)
            t1 = time.time()
            mean, peak = others.stats(t0, t1)
            if os.path.isdir(d):
                json.dump(dict(frodo_others_mean=mean, frodo_others_peak=peak, t0=t0, t1=t1, try_=t, job=name), open(os.path.join(d, "contamination.json"), "w"))
            why = ""
            if not os.path.exists(os.path.join(d, "DONE")) or not os.path.exists(os.path.join(d, "sched_result.json")):
                why = f"failed (rc {pr.returncode}) {pr.stderr.strip()[-300:]}"
            else:
                x = record(d)
                if mean > CM.BUSY_MEAN or peak > CM.BUSY_PEAK:
                    why = f"frodo others {mean:.2f} cores (peak {peak:.1f})"
                elif x["smc_other_mean"] > CM.BUSY_MEAN or x["smc_other_max"] > CM.BUSY_PEAK:
                    why = f"Supermicro others {x['smc_other_mean']:.2f} cores (peak {x['smc_other_max']:.1f})"
                elif uses_gpu and abs((x["sm_mhz"] or 0) - 1245) > 12.5:
                    why = f"GPU clock {x['sm_mhz']} MHz"
                elif not x["ok"]:
                    why = "an option's run was not ok"
            if not why:
                log(f"{name} rep{r}: {x['passes']} passes, T {x['T_pass_s'] * 1e3:.1f} ms, E {x['E_pass_J']:.2f} J (frodo {x['E_frodo_J'] / x['passes']:.2f}, "
                    f"Supermicro {x['E_smc_J'] / x['passes']:.2f} J per pass), error max {x['err_max_over_eps']:.2f} eps")
                ok = True
                break
            log(f"{name} rep{r}: try {t} set aside: {why}")
            if os.path.exists(os.path.join(d, "DONE")):
                os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        if not ok:
            failed += 1
    others.stop = True
    summary(M)
    done = sum(1 for x in jobs if os.path.exists(os.path.join(jdir(*x), "DONE")))
    log(f"CALIBRATION {'FINISHED' if done == len(jobs) else 'INCOMPLETE'}: {done} of {len(jobs)} jobs, {failed} not done")


def summary(M):
    rows = []
    for r in range(1, REPS + 1):
        for name, mix, what in JOBS:
            d = os.path.join(RES, "sched_calib_v11", name, f"rep{r}")
            if os.path.exists(os.path.join(d, "DONE")):
                set_book(mix)
                x = record(d); x.pop("res")
                rows.append(dict(rep=r, job=name, mix="/".join(map(str, mix)), what=what, **x))
    if rows:
        f = os.path.join(RES, "sched_calib_v11_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} jobs -> {f}")


if __name__ == "__main__":
    main()
