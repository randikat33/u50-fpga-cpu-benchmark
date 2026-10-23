#!/usr/bin/env python3
"""replicates.py - the high-replicate accuracy runs of protocol v0.6, Section 23 items 6-7 (addendum v0.6.1, Section 4).
Accuracy only: no time or energy is an outcome. Run on frodo outside the measurement sessions, at nice 19.

Each of the 36 settings (results/settings.csv, settings_s.csv) runs 200 times at its own steps and pairs:
  matched code (hq_cpu / hs_cpu --impl avx512, bit-identical to the card): seeds 20000-20199
  CPU-native code (hq_native / hs_native):                               seeds 30000-30199
Every replicate's three prices are appended to results/replicates/<setting>_<code>.jsonl (resumable). The summary
(results/replicates_summary.csv) gives per setting, code and strike: RMSE/eps against the reference price, the mean
error/eps and its standard error. The bootstrap intervals and the HF2 verdict are computed in the analysis.

Usage (from the project folder):
    python3 scripts/replicates.py --plan
    nohup nice -n 19 python3 scripts/replicates.py > results/replicates.log 2>&1 &
"""
import argparse, csv, json, math, os, statistics, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hqlib as HQ
import hslib as HS
import cpu_measure as CM
import cpu_measure_s as CMS

ROOT = HQ.ROOT
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "replicates")
NREP = 200
SEED0 = {"matched": 20000, "native": 30000}
BIN = {("main", "matched"): HQ.BIN, ("main", "native"): HQ.BIN_NATIVE, ("sec", "matched"): HS.BIN, ("sec", "native"): HS.BIN_NATIVE}


def settings():
    out = []
    for s, f in (("main", "settings.csv"), ("sec", "settings_s.csv")):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("steps"):
                out.append(dict(set=s, case=r["case"], scheme=r["scheme"], target=r["target"], eps=float(r["eps"]), M=int(r["steps"]), N=int(r["pairs"]),
                                key=f"{r['case']}_{r['scheme']}_{r['target'].replace('%', 'pct')}"))
    return out


def args(r, code, seed, tune):
    if r["set"] == "main":
        a = ["--case", r["case"], "--scheme", r["scheme"], "--steps", r["M"]]
    else:
        a = HS.args_for(r["case"], r["scheme"], steps=r["M"])
    a += ["--a0", 0, "--a1", r["N"], "--seed", seed]
    if code == "matched":
        t = tune["main"][r["scheme"]] if r["set"] == "main" else tune["sec"][CMS.TKEY[r["scheme"]]]
        a += ["--impl", "avx512", "--interleave", t]
    return a


def prices(r, code, res):
    impl = "avx512" if code == "matched" else "native"
    if r["set"] == "main":
        m = HQ.Moments(impl); m.add(res)
        return [m.estimate(r["case"], k)[0] for k in range(3)]
    m = HS.Moments(r["case"], impl); m.add(res)
    beta = HS.load_beta() if r["case"] == "A" else None
    return [m.estimate(r["scheme"], k, beta[k] if beta else None)[0] for k in range(3)]


def ref(r, k):
    return HQ.CASES[r["case"]]["ref"][k] if r["set"] == "main" else HS.ref(r["case"], k)


def path(r, code):
    return os.path.join(OUT, f"{r['key']}_{code}.jsonl")


def done_seeds(r, code):
    out = {}
    if os.path.exists(path(r, code)):
        for line in open(path(r, code)):
            try:
                x = json.loads(line)
                out[int(x["seed"])] = x
            except (ValueError, KeyError):
                pass
    return out


def summary(rows):
    out = []
    for r in rows:
        for code in ("matched", "native"):
            d = done_seeds(r, code)
            if not d:
                continue
            row = dict(case=r["case"], scheme=r["scheme"], target=r["target"], code=code, replicates=len(d), steps=r["M"], pairs=r["N"], eps=r["eps"])
            for k in range(3):
                e = [x["price"][k] - ref(r, k) for x in d.values()]
                row[f"rmse_over_eps{k}"] = math.sqrt(sum(v * v for v in e) / len(e)) / r["eps"]
                row[f"mean_err_over_eps{k}"] = statistics.mean(e) / r["eps"]
                row[f"se_mean_over_eps{k}"] = (statistics.stdev(e) / math.sqrt(len(e)) / r["eps"]) if len(e) > 1 else float("nan")
            row["rmse_over_eps_max"] = max(row[f"rmse_over_eps{k}"] for k in range(3))
            out.append(row)
    if out:
        f = os.path.join(RES, "replicates_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0]))
            w.writeheader(); w.writerows(out)
        for o in out:
            print(f"  {o['case']:>3} {o['scheme']:<5} {o['target']:<5} {o['code']:<7} {o['replicates']:>3} reps  RMSE/eps max {o['rmse_over_eps_max']:.3f}")
        print(f"{len(out)} rows -> {f}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--reps", type=int, default=NREP, help=argparse.SUPPRESS)      # tests only
    a = ap.parse_args()
    rows = settings()
    if a.summary:
        summary(rows)
        return
    tune = {"main": CM.tuning(), "sec": CMS.tuning()}
    if not tune["main"] or not tune["sec"]:
        sys.exit("results/cpu_pred/tuning.json or results/cpu_pred_s/tuning.json not found")
    left = [(r, c) for r in rows for c in ("matched", "native") if len(done_seeds(r, c)) < a.reps]
    if a.plan:
        t = {}
        for f in ("phase5_summary_s1.csv", "phase5s_summary_s1.csv"):
            p = os.path.join(RES, f)
            if os.path.exists(p):
                for x in csv.DictReader(open(p)):
                    if x["kind"] == "set" and x["cfg"] == "s01_t96":
                        t[(x["case"], x["scheme"], x["target"], "matched" if x["impl"] == "avx512" else "native")] = float(x["t_per_run_s"])
        est = sum((a.reps - len(done_seeds(r, c))) * (t.get((r["case"], r["scheme"], r["target"], c), 0.0) + 0.05) for r, c in left)
        print(f"{len(left)} of {2 * len(rows)} setting-code pairs left; about {est / 3600:.1f} h on {a.threads} threads of a quiet frodo")
        return
    os.makedirs(OUT, exist_ok=True)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"replicates: {len(left)} setting-code pairs left", flush=True)
    for r, code in left:
        have = done_seeds(r, code)
        t0 = time.time()
        with open(path(r, code), "a") as fh:
            for i in range(a.reps):
                seed = SEED0[code] + i
                if seed in have:
                    continue
                res = (HQ.run if r["set"] == "main" else HS.run)(args(r, code, seed, tune), threads=a.threads, binary=BIN[(r["set"], code)])
                fh.write(json.dumps(dict(seed=seed, price=prices(r, code, res), pairs=int(res["pairs"]), t=float(res["t_compute_s"]))) + "\n")
                fh.flush()
        print(time.strftime("%Y-%m-%d %H:%M:%S"), f"{r['key']} {code}: {a.reps} replicates ({(time.time() - t0) / 60:.1f} min)", flush=True)
    out = summary(rows)
    full = sum(1 for r in rows for c in ("matched", "native") if len(done_seeds(r, c)) >= a.reps)
    print("REPLICATES FINISHED" if full == 2 * len(rows) else f"REPLICATES INCOMPLETE ({full} of {2 * len(rows)})")


if __name__ == "__main__":
    main()
