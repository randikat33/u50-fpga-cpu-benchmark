#!/usr/bin/env python3
"""sched_queue_v11t.py - the scheduling runs of addendum v0.11, on frodo (the Supermicro's v0.9 agent must be running).
sched_queue_v10.py with the v0.11 jobs: three books, three policies each; the per-job procedure, the quietness rules and
the repeat rules are v0.9's and v0.10's, unchanged.
    python3 scripts/sched_queue_v11t.py --agent 10.8.100.5 --plan          jobs left and time
    python3 scripts/sched_queue_v11t.py --agent 10.8.100.5 --check         run-code check: every job for 3 s (after registration;
                                                                            it logs only whether each job ran, no numbers)
    nohup python3 scripts/sched_queue_v11t.py --agent 10.8.100.5 --reps 1-3 > results/sched_v11.log 2>&1 &     (day 1)
    nohup python3 scripts/sched_queue_v11t.py --agent 10.8.100.5 --reps 4-5 >> results/sched_v11.log 2>&1 &    (day 2)
    python3 scripts/sched_queue_v11t.py --summary
Jobs: 5 repeats; in each, the books N3, N2, S in this order, and in each book the policies alone_gpu, model_E, plan_v09
rotated by the repeat (repeat k starts with policy (k - 1) mod 3), with the registered plans of
results/predictions_sched_v11/predictions_sched_v11.json (SHA-256 printed). The book of a job is passed to sched_run.py
through the SCHED_BOOK environment variable (sched_model.book). Each job: 1 warm-up pass, then a window of at least
60 s and 3 passes, after the quietness wait and 20 s idle. A job is repeated (up to 5 tries) if frodo's or the
Supermicro's other users used > 1 core on average or > 4 in any second, if the GPU's median SM clock was not
1245 +- 12.5 MHz, or if any option's run was not ok.
Output: results/sched_v11/<book>/rep<k>/<policy>/..., results/sched_v11_summary.csv, results/sched_v11_events.log
"""
import argparse, csv, hashlib, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

ROOT, RES = S.ROOT, S.RES
PRED = os.path.join(RES, "predictions_sched_v11", "predictions_sched_v11.json")
REPS, MAX_TRIES = 5, 5
SETTLE_S = 20.0          # idle seconds before each job (check 4: frodo's power stayed high after a heavy job)
BOOKS = {"N3": (10, 10, 0), "N2": (6, 12, 2), "S": (20, 0, 0)}
POLICIES = ("alone_gpu", "model_E", "plan_v09")


def order(r):
    """the jobs of repeat r: books in order, policies rotated by the repeat"""
    k = (r - 1) % len(POLICIES)
    return tuple((b, p) for b in BOOKS for p in POLICIES[k:] + POLICIES[:k])


def set_book(b):
    """the job's book, for sched_model.book() here and in sched_run.py (inherited environment)"""
    os.environ["SCHED_BOOK"] = ",".join(str(x) for x in BOOKS[b])


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
    if str(pred.get("version", "")) != "0.11":
        sys.exit(f"{PRED} is not the v0.11 prediction (version {pred.get('version')})")
    base = os.path.join(RES, "sched_v11_check" if a.check else "sched_v11")
    r0, r1 = (int(x) for x in a.reps.split("-")) if "-" in a.reps else (int(a.reps), int(a.reps))
    reps = range(1, 2) if a.check else range(r0, r1 + 1)
    jobs = [(r, b, p) for r in reps for b, p in order(r)]
    jdir = lambda r, b, p: os.path.join(base, b, f"rep{r}", p)
    left = [j for j in jobs if not os.path.exists(os.path.join(jdir(*j), "DONE"))]
    if a.summary:
        return summary(pred)
    if a.plan:
        est = 0.0
        for r, b, p in left:
            T = pred["predictions"][f"{b}/{p}"]["T_s"]
            est += 15 + SETTLE_S + T + max(60.0, 3 * T)
        print(f"{len(left)} of {len(jobs)} jobs left; about {est / 3600:.1f} h; prediction sha256 {sha}")
        return
    import cpu_measure as CM
    for f in ("run_measured.py", "power_monitor.py", "xrt_timing.ini"):
        if not os.path.exists(os.path.join(a.common, f)):
            sys.exit(f"{f} not found in {a.common}")
    os.makedirs(base, exist_ok=True)
    ev = open(os.path.join(RES, "sched_v11_check_events.log" if a.check else "sched_v11_events.log"), "a")

    def log(s):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + s
        print(line, flush=True); ev.write(line + "\n"); ev.flush()

    log(f"v0.11 scheduling {'CHECK' if a.check else 'runs'}: {len(left)} of {len(jobs)} jobs left; prediction sha256 {sha}")
    others = CM.Others()
    cc = coordinator_cpu()
    failed = 0
    for r, b, p in left:
        set_book(b)
        d = jdir(r, b, p)
        os.makedirs(d, exist_ok=True)
        pj = dict(policy=p, book=b, mix=list(BOOKS[b]), rep=r, plan=pred["plans"][f"{b}/{p}"])
        pf = os.path.join(base, b, f"rep{r}", f"{p}.plan.json")
        json.dump(pj, open(pf, "w"), indent=1)
        win = ["--window-s", "3", "--min-passes", "1"] if a.check else ["--window-s", "60", "--min-passes", "3"]
        cmd = ["taskset", "-c", str(cc), sys.executable, os.path.join(ROOT, "scripts", "sched_run.py"), "--plan", pf, "--agent", a.agent, "--out", d] + win
        ok = False
        for t in range(1, MAX_TRIES + 1):
            CM.wait_quiet(others, log)
            time.sleep(SETTLE_S if not a.check else 5.0)
            if os.path.isdir(d):
                shutil.rmtree(d)
            t0 = time.time()
            pr = subprocess.run([sys.executable, os.path.join(a.common, "run_measured.py"), "--out", d, "--timeout", "3600", "--label", f"sched v0.11 {b} rep{r} {p}",
                                 "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--cores", f"0-{os.cpu_count() - 1}", "--node", "0,1", "--"] + cmd,
                                capture_output=True, text=True)
            t1 = time.time()
            mean, peak = others.stats(t0, t1)
            if os.path.isdir(d):
                json.dump(dict(frodo_others_mean=mean, frodo_others_peak=peak, t0=t0, t1=t1, try_=t, book=b), open(os.path.join(d, "contamination.json"), "w"))
            why = ""
            if not os.path.exists(os.path.join(d, "DONE")) or not os.path.exists(os.path.join(d, "sched_result.json")):
                why = f"failed (rc {pr.returncode}) {pr.stderr.strip()[-300:]}"
            else:
                x = record(d)
                if mean > CM.BUSY_MEAN or peak > CM.BUSY_PEAK:
                    why = f"frodo others {mean:.2f} cores (peak {peak:.1f})"
                elif x["smc_other_mean"] > CM.BUSY_MEAN or x["smc_other_max"] > CM.BUSY_PEAK:
                    why = f"Supermicro others {x['smc_other_mean']:.2f} cores (peak {x['smc_other_max']:.1f})"
                elif abs((x["sm_mhz"] or 0) - 1245) > 12.5:
                    why = f"GPU clock {x['sm_mhz']} MHz"
                elif not x["ok"]:
                    why = "an option's run was not ok"
            if not why:
                pp = pred["predictions"][f"{b}/{p}"]
                if a.check:
                    log(f"{b} rep{r} {p}: ran ({x['passes']} passes, every option ok)")
                else:
                    log(f"{b} rep{r} {p}: {x['passes']} passes, T {x['T_pass_s'] * 1e3:.1f} ms, E {x['E_pass_J']:.2f} J "
                        f"(predicted {pp['T_s'] * 1e3:.1f} ms, {pp['E_J']:.2f} J), error max {x['err_max_over_eps']:.2f} eps")
                ok = True
                break
            log(f"{b} rep{r} {p}: try {t} set aside: {why}")
            if os.path.exists(os.path.join(d, "DONE")):
                os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        if not ok:
            failed += 1
    others.stop = True
    if not a.check:
        summary(pred)
    elif not failed:
        log("CHECK: every job ran; the check is not a result")
    done = sum(1 for j in jobs if os.path.exists(os.path.join(jdir(*j), "DONE")))
    log(f"SCHED {'CHECK ' if a.check else ''}{'FINISHED' if done == len(jobs) else 'INCOMPLETE'}: {done} of {len(jobs)} jobs, {failed} not done")


def summary(pred):
    rows = []
    for r in range(1, REPS + 1):
        for b, p in order(r):
            d = os.path.join(RES, "sched_v11", b, f"rep{r}", p)
            if os.path.exists(os.path.join(d, "DONE")):
                set_book(b)
                x = record(d); x.pop("res")
                pp = pred["predictions"][f"{b}/{p}"]
                rows.append(dict(rep=r, book=b, policy=p, T_pred_s=pp["T_s"], E_pred_J=pp["E_J"], **x))
    if rows:
        f = os.path.join(RES, "sched_v11_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} jobs -> {f}")


if __name__ == "__main__":
    main()
