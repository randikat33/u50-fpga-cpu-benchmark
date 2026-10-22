#!/usr/bin/env python3
"""coexec_queue.py - the co-execution measurements of addendum v0.7, one machine per call:
    python3 scripts/coexec_queue.py --machine smc   --plan      (Supermicro: V100 + socket 1)
    python3 scripts/coexec_queue.py --machine frodo --plan      (frodo: U50 + socket 0)
Jobs come from the registered prediction (results/predictions_coexec/predictions_coexec.json, sha256 checked): every
kernel, workload and policy of the machine (static policies with their registered CPU fraction; a static policy whose
fraction equals another policy's is run once, under the first name), 3 repeats, interleaved, seeds 20262100 + rep.
Each job: scripts/coexec_run.py inside the harness (Supermicro: scripts/gpu_run.py --no-pin, GPU clock locked at
1245 MHz, block wait; frodo: common/run_measured.py with the 10 Hz power logger), quietness rules as before (frodo:
other users < 0.5 cores before a job, a job with > 1 core on average or > 4 in any second is repeated; Supermicro: the
same rules inside gpu_run.py). Output: results/coexec_<machine>/..., results/coexec_<machine>_summary.csv.
"""
import argparse, csv, hashlib, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
PRED = os.path.join(RES, "predictions_coexec", "predictions_coexec.json")
REPS, MAX_TRIES = 3, 5


def jobs(machine, pred):
    rows = [p for p in pred["predictions"] if p["machine"] == machine]
    groups = {}
    for p in rows:
        groups.setdefault((p["case"], p["scheme"], p["workload"]), []).append(p)
    plan = []
    for (case, sc, wl), ps in groups.items():
        seen = {}
        for p in ps:
            pol = p["policy"]
            if pol in ("acc", "cpu"):
                key = (pol, 0.0 if pol == "acc" else 1.0)
            elif pol == "dynamic":
                key = ("dynamic", p["K"])
            else:
                f = p["f_cpu"]
                key = ("acc", 0.0) if f == 0 else (("cpu", 1.0) if f == 1 else ("static", round(f, 4)))
            if key in seen:
                continue
            seen[key] = pol
            plan.append(dict(case=case, scheme=sc, workload=wl, M=p["steps"], N=p["pairs"], name=pol, kind=key[0],
                             fcpu=key[1] if key[0] == "static" else (1.0 if key[0] == "cpu" else 0.0), K=p["K"] or 0))
    out = []
    for rep in range(1, REPS + 1):
        for j in plan:
            out.append(dict(j, rep=rep, seed=20262100 + rep, job=f"{machine}/{j['workload']}_{j['case']}_{j['scheme']}_M{j['M']}/{j['name']}/rep{rep}"))
    return out


def cmd(machine, j, common):
    c = [sys.executable, os.path.join(ROOT, "scripts", "coexec_run.py"), "--machine", machine, "--case", j["case"], "--scheme", j["scheme"],
         "--steps", str(j["M"]), "--pairs", str(j["N"]), "--policy", j["kind"], "--fcpu", str(j["fcpu"]), "--K", str(j["K"]), "--seed", str(j["seed"])]
    d = os.path.join(RES, f"coexec_{j['job']}")
    if machine == "smc":
        return [sys.executable, os.path.join(ROOT, "scripts", "gpu_run.py"), "--no-pin", "--out", d, "--"] + c, d
    return [sys.executable, os.path.join(common, "run_measured.py"), "--out", d, "--timeout", "3600", "--label", j["job"],
            "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--cores", f"0-{os.cpu_count() - 1}", "--node", "0", "--"] + c, d


def record(machine, d):
    if machine == "smc":
        import phase5_gpu as G
        r = G.record(d)
        txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
        out = dict(t_per_run_s=r["t_per_run_s"], E_primary_J=r["E_gpu_J"] + r["E_socket1_J"], E_server_J=r["E_gpu_J"] + r["E_socket1_J"] + r["E_socket0_J"],
                   P_dev_W=r["P_gpu_W"], P_host_W=r["P_socket1_W"], P_other_W=r["P_socket0_W"], sm=r["sm_mhz"], ok=r["ok"])
    else:
        import phase5 as P5
        r = P5.record(d)
        txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
        out = dict(t_per_run_s=r["t_per_run_s"], E_primary_J=(r["E_card_J"] + r["E_cpu_socket_J"]) / r["runs"],
                   E_server_J=(r["E_card_J"] + r["E_cpu_server_J"]) / r["runs"], P_dev_W=r["P_card_W"], P_host_W=r["P_socket_W"],
                   P_other_W=r["P_server_W"] - r["P_socket_W"], ok=r["ok"])
    res = dict(l[7:].split("=", 1) for l in txt.splitlines() if l.startswith("RESULT ") and "=" in l)
    for k in ("makespan_median_s", "busy_acc_median_s", "busy_cpu_median_s", "pairs_cpu_mean", "blocks_cpu_mean", "runs"):
        out[k] = float(res.get(k, "nan"))
    for k in range(3):
        out[f"price{k}"] = float(res.get(f"price{k}", "nan"))
    return out


def summary(machine, pred):
    import hqlib as HQ, hslib as HS
    groups = {}
    for j in jobs(machine, pred):
        d = os.path.join(RES, f"coexec_{j['job']}")
        if os.path.exists(os.path.join(d, "DONE")):
            groups.setdefault(j["job"].rsplit("/rep", 1)[0], (j, []))[1].append(record(machine, d))
    rows = []
    for key, (j, rs) in sorted(groups.items()):
        med = lambda k: statistics.median(x[k] for x in rs)
        row = dict(group=key, machine=machine, case=j["case"], scheme=j["scheme"], workload=j["workload"], steps=j["M"], pairs=j["N"], policy=j["name"],
                   kind=j["kind"], fcpu=j["fcpu"], K=j["K"], reps=len(rs))
        for k in ("t_per_run_s", "makespan_median_s", "E_primary_J", "E_server_J", "P_dev_W", "P_host_W", "P_other_W", "busy_acc_median_s",
                  "busy_cpu_median_s", "pairs_cpu_mean", "blocks_cpu_mean"):
            row[k] = med(k)
        ref = HQ.CASES[j["case"]]["ref"] if j["case"] in ("I", "II", "III", "P1") else HS.CASES[j["case"]]["ref"]
        for k in range(3):
            e = [x[f"price{k}"] - ref[k] for x in rs]
            row[f"err_mean{k}"] = statistics.mean(e)
        row["all_ok"] = int(all(x["ok"] for x in rs))
        rows.append(row)
    if rows:
        f = os.path.join(RES, f"coexec_{machine}_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} groups -> {f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", choices=("smc", "frodo"), required=True)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--pred-sha", default="")
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    pred = json.load(open(PRED))
    sha = hashlib.sha256(open(PRED, "rb").read()).hexdigest()
    if a.summary:
        summary(a.machine, pred); return
    js = jobs(a.machine, pred)
    left = [j for j in js if not os.path.exists(os.path.join(RES, f"coexec_{j['job']}", "DONE"))]
    pt = {(p["case"], p["scheme"], p["workload"], p["policy"]): p["T_s"] for p in pred["predictions"] if p["machine"] == a.machine}
    est = sum(3 + max(10.0, pt[(j["case"], j["scheme"], j["workload"], j["name"])]) * 1.05 + 8 for j in left)
    if a.plan:
        print(f"{a.machine}: {len(left)} of {len(js)} jobs left; about {est / 3600:.1f} h")
        print(f"prediction sha256 {sha}")
        need = ["hs_coexec", "hq_native_serve", "hs_native_serve"] if a.machine == "smc" else ["hq_fpga", "hs_fpga", "hq_native_serve", "hs_native_serve"]
        for b in need:
            p = os.path.join(ROOT, "build", b)
            print(f"  build/{b}: {'ok' if os.path.exists(p) else 'MISSING'}")
        return
    os.makedirs(os.path.join(RES, f"coexec_{a.machine}"), exist_ok=True)
    ev = open(os.path.join(RES, f"coexec_{a.machine}_events.log"), "a")

    def log(s):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + s
        print(line, flush=True); ev.write(line + "\n"); ev.flush()

    log(f"co-execution, {a.machine}: {len(left)} of {len(js)} jobs left; prediction sha256 {sha}")
    others = None
    if a.machine == "frodo":
        import cpu_measure as CM
        others = CM.Others()
    failed = 0
    for j in left:
        c, d = cmd(a.machine, j, a.common)
        ok = False
        for t in range(1, MAX_TRIES + 1):
            if others:
                CM.wait_quiet(others, log)
            if os.path.isdir(d):
                shutil.rmtree(d)
            t0 = time.time()
            p = subprocess.run(c, capture_output=True, text=True)
            t1 = time.time()
            bad = not os.path.exists(os.path.join(d, "DONE"))
            if a.machine == "smc" and not bad:
                m = json.load(open(os.path.join(d, "meta.json")))
                bad = m.get("disturbed") != 0 or (j["kind"] != "cpu" and abs((m.get("sm_mhz_median") or 0) - 1245) > 12.5)
            if others and not bad:
                mean, peak = others.stats(t0, t1)
                bad = mean > CM.BUSY_MEAN or peak > CM.BUSY_PEAK
            if not bad:
                r = record(a.machine, d)
                log(f"{j['job']}: ok, {r['t_per_run_s']:.4f} s/run, makespan {r['makespan_median_s']:.4f} s, E {r['E_primary_J']:.2f} J, "
                    f"device {r['P_dev_W']:.1f} W, host socket {r['P_host_W']:.1f} W")
                ok = True
                break
            log(f"{j['job']}: try {t} set aside (rc {p.returncode}) {p.stderr.strip()[-300:]}")
            if os.path.exists(os.path.join(d, "DONE")):
                os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        failed += not ok
    summary(a.machine, pred)
    done = sum(1 for j in js if os.path.exists(os.path.join(RES, f"coexec_{j['job']}", "DONE")))
    log(f"COEXEC {a.machine.upper()} {'FINISHED' if done == len(js) else 'INCOMPLETE'}: {done} of {len(js)} jobs, {failed} not done")


if __name__ == "__main__":
    main()
