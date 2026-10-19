#!/usr/bin/env python3
"""native_check_s.py - the CPU-native check of the secondary cases (protocol v0.5, Section 10 item 5), as
scripts/native_check.py does for the main cases.

For each of the 12 chosen settings (results/settings_s.csv: Case B ft/qe, Case A plain/cv) the CPU-native program
(build/hs_native: Box-Muller, FMA, vector math; not bit-identical to the card) runs at the setting's
time step with enough pairs that its standard error is at most eps/5 at every strike
(pairs_check = 12.5 x the setting's pairs, since the setting has N = 2 sigma^2 / eps^2). It reports:
  - the native bias at each strike and how far it is from the matched code's calibrated bias
    (in combined standard errors; |z| <= 3 expected);
  - the native predicted RMSE at the setting, sqrt(b^2 + sigma^2 / N) / eps (<= 1: meets the target).
Batches of consecutive pair ranges are appended to results/native_s/<setting>.jsonl, so the script can be
stopped and started again. Summary: results/native_check_s.csv. Case A uses the fixed beta of results/beta_A.csv.

Usage (from the project folder, after `make snative` and scripts/calibrate_s.py):
    python3 scripts/native_check_s.py --plan
    nohup nice -n 19 python3 scripts/native_check_s.py --threads 46 > results/native_check_s.log 2>&1 &
"""
import argparse
import csv
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hslib as H

RES = os.path.join(H.ROOT, "results")
OUT = os.path.join(RES, "native_s")
SEED = {"B": 701, "A": 711}                                # not the calibration or reference seeds
FACTOR = 12.5                                               # SE <= eps/5  <=>  pairs >= 12.5 N
NS_STEP = {"ft": 16.0, "qe": 39.0, "plain": 1.4, "cv": 1.8}  # sandbox native ns per path-step and core (plan only)
BATCH_PATH_STEPS = 1.0e12                                   # about 20 s per batch on 48 cores
MAX_PAIRS = {"B": 2 ** 29, "A": 2 ** 32 - 2 ** 16}


def settings(only=None):
    f = os.path.join(RES, "settings_s.csv")
    if not os.path.exists(f):
        sys.exit(f"{f} not found: run scripts/calibrate_s.py first")
    rows = [r for r in csv.DictReader(open(f)) if r["rule"] == "empirical" and r.get("steps")]
    if only:
        rows = [r for r in rows if r["case"] in only]
    for r in rows:
        r["M"] = int(r["steps"])
        r["N"] = int(r["pairs"])
        r["N_check"] = min(MAX_PAIRS[r["case"]], int(math.ceil(FACTOR * r["N"] / 64.0)) * 64)
        r["key"] = f"{r['case']}_{r['scheme']}_{r['target'].replace('%', 'pct')}"
    return rows


def load(r):
    m = H.Moments(r["case"], "native")
    f = os.path.join(OUT, r["key"] + ".jsonl")
    if os.path.exists(f):
        for line in open(f):
            line = line.strip()
            if line:
                try:
                    m.add(json.loads(line))
                except (ValueError, KeyError):
                    pass                     # a line cut by a power failure: that batch is run again
    return m


def run_setting(r, threads, deadline):
    m = load(r)
    per_pair = 2.0 * r["M"]
    batch = max(64, int(BATCH_PATH_STEPS / per_pair) // 64 * 64)
    f = os.path.join(OUT, r["key"] + ".jsonl")
    while m.pairs < r["N_check"]:
        if time.time() > deadline:
            return False
        a0 = m.pairs
        a1 = min(r["N_check"], a0 + batch)
        res = H.run(H.args_for(r["case"], r["scheme"], steps=r["M"]) + ["--a0", a0, "--a1", a1,
                     "--seed", SEED[r["case"]]], threads=threads, binary=H.BIN_NATIVE)
        with open(f, "a") as fh:
            fh.write(json.dumps(res) + "\n")
        m.add(res)
        print(f"  {r['key']}: pairs {m.pairs:,} of {r['N_check']:,} ({res['t_compute_s']:.1f} s)", flush=True)
    return True


def summary(rows):
    out = []
    for r in rows:
        m = load(r)
        if m.pairs < r["N_check"]:
            continue
        case, eps = r["case"], float(r["eps"])
        row = dict(case=case, target=r["target"], scheme=r["scheme"], steps_per_year=r["steps_per_year"],
                   pairs_setting=r["N"], pairs_check=m.pairs)
        worst, zmax = 0.0, 0.0
        for k in range(H.NK):
            beta = H.load_beta()
            b, se, _ = m.estimate(r["scheme"], k, beta[k] if (case == "A" and beta) else None)
            b -= H.ref(case, k)
            sig2 = se * se * (m.pairs - 1)                      # variance of one pair average
            rmse = math.sqrt(b * b + sig2 / r["N"])
            bm, sem = float(r[f"bias{k}"]), float(r[f"bias_se{k}"])
            z = (b - bm) / math.hypot(se, sem) if sem > 0 else float("nan")
            row.update({f"bias_native{k}": b, f"se_native{k}": se, f"bias_matched{k}": bm, f"se_matched{k}": sem,
                        f"z{k}": z, f"rmse_over_eps{k}": rmse / eps})
            worst = max(worst, rmse / eps)
            zmax = max(zmax, abs(z))
        row["rmse_over_eps_max"] = worst
        row["z_abs_max"] = zmax
        row["meets_target"] = int(worst <= 1.0)
        row["agrees_with_matched"] = int(zmax <= 3.0)
        out.append(row)
    if out:
        f = os.path.join(RES, "native_check_s.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        print(f"\n{'setting':<16} {'steps/yr':>8} {'pairs':>12} {'RMSE/eps':>9} {'|z| max':>8}  verdict")
        for o in out:
            v = ("meets target" if o["meets_target"] else "MISSES TARGET") + \
                ("" if o["agrees_with_matched"] else ", differs from matched code")
            print(f"{o['case'] + ' ' + o['scheme'] + ' ' + o['target']:<16} {o['steps_per_year']:>8} "
                  f"{o['pairs_check']:>12,} {o['rmse_over_eps_max']:>9.3f} {o['z_abs_max']:>8.2f}  {v}")
        print(f"\n{len(out)} of {len(rows)} settings done; written {f}")
    return out


def main():
    global RES, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--only", default="", help="comma-separated cases, e.g. P1,III")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--max-hours", type=float, default=1e9)
    ap.add_argument("--results", default=RES, help=argparse.SUPPRESS)   # tests only
    a = ap.parse_args()
    RES, OUT = a.results, os.path.join(a.results, "native_s")
    if H.CASES["B"]["ref"] is None:
        H.load_refs(os.path.join(RES, "reference_s.csv"))
    only = [c for c in a.only.split(",") if c] or None
    rows = settings(only)
    if a.plan:
        tot = 0.0
        for r in rows:
            m = load(r)
            left = max(0, r["N_check"] - m.pairs) * 2.0 * r["M"]
            s = left * NS_STEP[r["scheme"]] * 1e-9 / max(1, min(a.threads, 48))
            tot += s
            print(f"{r['key']:<16} M {r['M']:>6}  pairs {r['N_check']:>12,}  left {s / 60:7.1f} min")
        print(f"total about {tot / 60:.0f} min with {a.threads} threads (sandbox speed; frodo may differ)")
        return
    if not os.path.exists(H.BIN_NATIVE):
        sys.exit("build/hs_native not found: run `make snative` first")
    os.makedirs(OUT, exist_ok=True)
    deadline = time.time() + a.max_hours * 3600
    for r in rows:
        print(f"== {r['key']} (steps {r['M']}, pairs {r['N_check']:,})", flush=True)
        if not run_setting(r, a.threads, deadline):
            print("stopped: --max-hours reached (run again to continue)")
            break
    out = summary(rows)
    print("NATIVE CHECK FINISHED" if len(out) == len(rows) else "NATIVE CHECK INCOMPLETE (run again to continue)")


if __name__ == "__main__":
    main()
