#!/usr/bin/env python3
"""calibrate_s.py - calibration of the secondary cases (protocol v0.5, Sections 4b and 10 item 5), CPU only.

The float model (bit-identical to the card) runs in batches of consecutive pair ranges of one seed:
  Case B, FT and QE at each grid step: until each strike's SE <= eps_min/5 or its |bias| >= 10 SE
          (as the main calibration, scripts/calibrate.py); bias against results/reference_s.csv.
  Case A (250 dates, no step choice): one run, both estimators from the same paths, until the plain
          estimator's SE <= eps_min/5 at every strike (the CV estimator's SE is then about 20x smaller).
          Its own beta estimate per strike is the fixed beta of the protocol (results/beta_A.csv).
  float32 minus double precision on the same paths (--impl diff): B at QE 8 and 64, FT 64 and 256 per year;
          A once (2^20 pairs each; 2^17 for the basket, whose pair has 8 assets).
Then the settings (protocol Section 7, empirical rule; Case A: N = 2 sigma^2 / eps^2 at its one step):
  results/calibration_s.csv, results/beta_A.csv, results/settings_s.csv, results/float_vs_double_s.csv.
Resumable: every batch is appended to results/calib_s/<job>.jsonl.
Usage (after `make scpu` and scripts/reference_s.py):
    python3 scripts/calibrate_s.py --plan
    nohup nice -n 19 python3 scripts/calibrate_s.py --threads 46 > results/calibrate_s.log 2>&1 &
    python3 scripts/calibrate_s.py --settings      # only rewrite the summary and the settings
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
RAW = os.path.join(RES, "calib_s")
SEED = {"B": 601, "A": 611}
GRID = {"qe": [1, 2, 4, 8, 16, 32, 64], "ft": [1, 2, 4, 8, 16, 32, 64, 128, 256]}
NS = {("B", "qe"): 35.0 * 8, ("B", "ft"): 13.0 * 8, ("A", "a"): 23.0}   # sandbox scalar ns per path-step and core (plan)
SIG = {"B": 2.15, "A": 3.95}
RESOLVE = 10.0
TSCALE = 1.0      # --target-scale (tests only)


def eps_min(case):
    return min(H.TARGETS) * H.atm(case)


def jobs():
    out = []
    for sc in ("qe", "ft"):
        for spy in GRID[sc]:
            out.append(dict(case="B", scheme=sc, impl="scalar", spy=spy, seed=SEED["B"], target_se=eps_min("B") / 5 * TSCALE))
    out.append(dict(case="A", scheme="a", impl="scalar", spy=250, seed=SEED["A"], target_se=eps_min("A") / 5 * TSCALE))
    for sc, spy in (("qe", 8), ("qe", 64), ("ft", 64), ("ft", 256)):
        out.append(dict(case="B", scheme=sc, impl="diff", spy=spy, seed=SEED["B"] + 1000, fixed=int(2 ** 17 / min(TSCALE, 8) ** 2) or 64))
    out.append(dict(case="A", scheme="a", impl="diff", spy=250, seed=SEED["A"] + 1000, fixed=int(2 ** 20 / min(TSCALE, 8) ** 2) or 64))
    return out


def key(j):
    return f"{j['case']}_{j['scheme']}_{j['impl']}_{j['spy']}_s{j['seed']}"


def cli(j):
    if j["case"] == "B":
        return ["--case", "B", "--scheme", j["scheme"], "--steps-per-year", j["spy"]]
    return ["--case", "A"]


def load(j):
    m = H.Moments(j["case"], "double" if j["impl"] == "diff" else j["impl"])
    m.diff = j["impl"] == "diff"
    a1 = 0
    f = os.path.join(RAW, key(j) + ".jsonl")
    if os.path.exists(f):
        for line in open(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if int(r["a0"]) != a1:
                continue
            if j["impl"] == "diff":
                m.pairs += int(r["pairs"]); m.seconds += float(r["t_compute_s"]); m.path_steps += float(r["path_steps"])
                m.ovf += int(r.get("payoff_overflows", 0))
                m.rows = getattr(m, "rows", []) + [r]
            else:
                m.add(r)
            a1 = int(r["a1"])
    return m, a1


def stats(j, m):
    """per strike: (estimate, SE, bias) of the job's main estimator(s)"""
    out = {}
    for k in range(H.NK):
        if j["case"] == "B":
            est, se, _ = m.basket(k)
            out[k] = dict(est=est, se=se, bias=est - H.ref("B", k))
        else:
            a = m.asian(k)
            out[k] = dict(est=a["plain"], se=a["se_plain"], bias=a["plain"] - H.ref("A", k),
                          cv=a["cv"], se_cv=a["se_cv"], bias_cv=a["cv"] - H.ref("A", k), beta_hat=a["beta_hat"])
    return out


def done(j, m):
    if m.pairs < 2 ** 14:
        return False, None
    s = stats(j, m)
    ok = []
    for k in range(H.NK):
        se, b = s[k]["se"], s[k]["bias"]
        ok.append(se <= j["target_se"] or (j["case"] == "B" and abs(b) >= RESOLVE * se))
    return all(ok), s


def run_job(j, threads, deadline):
    os.makedirs(RAW, exist_ok=True)
    m, a1 = load(j)
    f = os.path.join(RAW, key(j) + ".jsonl")
    steps = int(j["spy"] * H.CASES[j["case"]]["T"])
    amax = 2 ** 29 if j["case"] == "B" else 2 ** 32
    while True:
        if j["impl"] == "diff":
            if m.pairs >= j["fixed"]:
                return m, "fixed"
            want = j["fixed"] - m.pairs
        else:
            fin, s = done(j, m)
            if fin:
                return m, "done"
            if m.pairs < 2 ** 14:
                want = 2 ** 14 - m.pairs
            else:
                need = 0
                for k in range(H.NK):
                    if s[k]["se"] > j["target_se"]:
                        need = max(need, m.pairs * (s[k]["se"] / j["target_se"]) ** 2 * 1.1 - m.pairs)
                want = int(min(max(need, 2 ** 14), m.pairs))
        if m.seconds > 0:
            want = max(2 ** 10, min(want, int(1200 / (m.seconds / m.pairs))))
        want = int(min(want, amax - a1))
        if want <= 0:
            return m, "cap"
        if time.time() > deadline:
            return m, "time budget"
        r = H.run(cli(j) + ["--impl", j["impl"], "--seed", j["seed"], "--a0", a1, "--a1", a1 + want], threads=threads)
        with open(f, "a") as fh:
            fh.write(json.dumps(r) + "\n")
        a1 += want
        if j["impl"] == "diff":
            m.pairs += int(r["pairs"]); m.seconds += float(r["t_compute_s"]); m.path_steps += float(r["path_steps"])
            print(f"  {key(j)}: {m.pairs:,} pairs", flush=True)
            continue
        m.add(r)
        s = stats(j, m)
        print(f"  {key(j)}: {m.pairs:,} pairs, K=100 est {s[1]['est']:.6f} se {s[1]['se']:.6f} bias {s[1]['bias']:+.6f} "
              f"({float(r['t_compute_s']):.0f} s)", flush=True)


def diff_summary(j):
    """mean float-minus-double per strike from the diff runs (pair-sum differences / 2), weighted by pairs"""
    m, _ = load(j)
    rows = getattr(m, "rows", [])
    if not rows:
        return None
    n = sum(int(r["pairs"]) for r in rows)
    out = dict(case=j["case"], scheme=j["scheme"], steps_per_year=j["spy"], pairs=n)
    em = eps_min(j["case"])
    for k in range(H.NK):
        md = sum(float(r[f"mean_diff{k}"]) * int(r["pairs"]) for r in rows) / n
        out[f"mean_diff{k}"] = md
        out[f"diff_over_eps_min{k}"] = md / em
        if j["case"] == "A":
            out[f"mean_diff_geo{k}"] = sum(float(r[f"mean_diff_geo{k}"]) * int(r["pairs"]) for r in rows) / n
    return out


def write_all():
    rows, diffs = [], []
    beta = None
    for j in jobs():
        if j["impl"] == "diff":
            d = diff_summary(j)
            if d:
                diffs.append(d)
            continue
        m, _ = load(j)
        if m.pairs < 2:
            continue
        s = stats(j, m)
        if j["case"] == "B":
            row = dict(case="B", scheme=j["scheme"], steps_per_year=j["spy"], steps=j["spy"], seed=j["seed"], pairs=m.pairs,
                       payoff_overflows=m.ovf, cpu_seconds=round(m.seconds, 1), path_steps=m.path_steps, target_se=j["target_se"])
            for k in range(H.NK):
                row.update({f"K{k}": H.CASES["B"]["K"][k], f"est{k}": s[k]["est"], f"se{k}": s[k]["se"], f"bias{k}": s[k]["bias"]})
            rows.append(row)
        else:
            beta = [s[k]["beta_hat"] for k in range(H.NK)]
            for sc in ("plain", "cv"):
                row = dict(case="A", scheme=sc, steps_per_year=250, steps=250, seed=j["seed"], pairs=m.pairs, payoff_overflows=m.ovf,
                           cpu_seconds=round(m.seconds, 1), path_steps=m.path_steps, target_se=j["target_se"])
                for k in range(H.NK):
                    if sc == "plain":
                        e, se, b = s[k]["est"], s[k]["se"], s[k]["bias"]
                    else:   # CV with the fixed beta (= this run's own estimate)
                        a = m.asian(k, beta[k])
                        e, se, b = a["cv"], a["se_cv"], a["cv"] - H.ref("A", k)
                    row.update({f"K{k}": H.CASES["A"]["K"][k], f"est{k}": e, f"se{k}": se, f"bias{k}": b})
                rows.append(row)
    os.makedirs(RES, exist_ok=True)
    if rows:
        keys = []
        for r in rows:
            keys += [k for k in r if k not in keys]
        with open(os.path.join(RES, "calibration_s.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    if beta:
        with open(os.path.join(RES, "beta_A.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["case", "source", "beta0", "beta1", "beta2"])
            w.writerow(["A", f"calibration seed {SEED['A']} (the run's own estimate; fixed from here on)"] + [f"{b:.9f}" for b in beta])
    if diffs:
        keys = []
        for r in diffs:
            keys += [k for k in r if k not in keys]
        with open(os.path.join(RES, "float_vs_double_s.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(diffs)
    return rows, diffs, beta


def settings(rows):
    """empirical rule of protocol Section 7, for B (per scheme over the grid) and A (one step, per estimator)"""
    out = []
    for case in ("B", "A"):
        for sc in H.CASES[case]["schemes"]:
            R = sorted([r for r in rows if r["case"] == case and r["scheme"] == sc], key=lambda r: r["steps_per_year"])
            if not R:
                continue
            for frac in H.TARGETS:
                eps = frac * H.atm(case)
                row = dict(case=case, target=f"{frac * 100:g}%", eps=eps, scheme=sc, rule="empirical")
                ok = [r for r in R if all(abs(r[f"bias{k}"]) + 2 * r[f"se{k}"] <= eps / math.sqrt(2) for k in range(H.NK))]
                if not ok:
                    row.update(steps_per_year="", note="no grid step meets the bias budget")
                    out.append(row)
                    continue
                at = ok[0]
                var = [at[f"se{k}"] ** 2 * (at["pairs"] - 1) for k in range(H.NK)]
                pairs = int(math.ceil(max(var) / (eps ** 2 / 2) / 64) * 64)
                steps = int(at["steps"])
                row.update(steps_per_year=int(at["steps_per_year"]), steps=steps, pairs=pairs, path_steps=2.0 * pairs * steps)
                rm = []
                for k in range(H.NK):
                    b = at[f"bias{k}"]
                    row.update({f"bias{k}": b, f"bias_se{k}": at[f"se{k}"], f"pred_rmse{k}": math.sqrt(b * b + var[k] / pairs)})
                    rm.append(row[f"pred_rmse{k}"])
                row["pred_rmse_max_over_eps"] = max(rm) / eps
                out.append(row)
    keys = []
    for r in out:
        keys += [k for k in r if k not in keys]
    with open(os.path.join(RES, "settings_s.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(out)
    print("\nsettings (empirical rule; pairs = antithetic pairs; path-steps = 2 x pairs x steps):")
    print(f"  {'case':>4} {'target':>6} {'scheme':>6} {'steps/yr':>8} {'pairs':>12} {'path-steps':>11} {'RMSE/eps':>8}")
    for r in out:
        if r.get("steps_per_year") == "":
            print(f"  {r['case']:>4} {r['target']:>6} {r['scheme']:>6}  -- {r['note']}")
            continue
        print(f"  {r['case']:>4} {r['target']:>6} {r['scheme']:>6} {r['steps_per_year']:>8} {r['pairs']:>12,d} "
              f"{r['path_steps']:>11.3g} {r['pred_rmse_max_over_eps']:>8.2f}")
    print("\nwork ratio (path-steps):")
    for case, a, b in (("B", "ft", "qe"), ("A", "plain", "cv")):
        for tg in H.TARGETS:
            t = f"{tg * 100:g}%"
            x = [r for r in out if r["case"] == case and r["target"] == t and r["scheme"] == a and r.get("path_steps")]
            y = [r for r in out if r["case"] == case and r["target"] == t and r["scheme"] == b and r.get("path_steps")]
            if x and y:
                print(f"  {case} {t:>5}: {a}/{b} = {x[0]['path_steps'] / y[0]['path_steps']:.1f}")
    return out


def read_rows():
    f = os.path.join(RES, "calibration_s.csv")
    rows = list(csv.DictReader(open(f)))
    for r in rows:
        for k, v in list(r.items()):
            try:
                r[k] = float(v)
            except (TypeError, ValueError):
                pass
    return rows


def main():
    global RES, RAW, TSCALE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--max-hours", type=float, default=1e9)
    ap.add_argument("--settings", action="store_true", help="only rewrite the summary and the settings")
    ap.add_argument("--results", default=RES, help=argparse.SUPPRESS)
    ap.add_argument("--target-scale", type=float, default=1.0, help=argparse.SUPPRESS)
    a = ap.parse_args()
    RES, RAW, TSCALE = a.results, os.path.join(a.results, "calib_s"), a.target_scale
    if H.CASES["B"]["ref"] is None or H.CASES["A"]["ref"] is None:
        H.load_refs(os.path.join(RES, "reference_s.csv"))
    if a.plan:
        tot = 0
        for j in jobs():
            n = j.get("fixed") or (SIG[j["case"]] / j["target_se"]) ** 2
            steps = j["spy"] * H.CASES[j["case"]]["T"]
            ch = 2 * n * steps * NS[(j["case"], j["scheme"])] * (2.5 if j["impl"] == "diff" else 1) * 1e-9 / 3600
            tot += ch
            print(f"{key(j):<26} worst-case pairs {n:14,.0f}  {ch:6.1f} core-hours")
        print(f"worst case ~{tot:.0f} core-hours (coarse steps stop early once the bias is resolved)")
        return
    if H.CASES["B"]["ref"] is None or H.CASES["A"]["ref"] is None:
        sys.exit("no accepted reference prices in results/reference_s.csv: run scripts/reference_s.py first")
    if not a.settings:
        deadline = time.time() + a.max_hours * 3600
        for j in jobs():
            m, why = run_job(j, a.threads, deadline)
            print(f"{key(j)}: {why} after {m.pairs:,} pairs, {m.seconds / 60:.1f} min", flush=True)
            write_all()
            if why == "time budget":
                print("time budget reached; run the same command again to continue")
                return
    rows, diffs, beta = write_all()
    if beta:
        print("Case A beta (fixed from here on): " + ", ".join(f"{b:.5f}" for b in beta))
    for d in diffs:
        print(f"float - double {d['case']} {d['scheme']} {d['steps_per_year']}/yr: " +
              " ".join(f"{d[f'diff_over_eps_min{k}']:+.4f}" for k in range(H.NK)) + " (in units of eps_min)")
    settings(read_rows())
    if not a.settings:
        print("ALL SECONDARY CALIBRATION JOBS FINISHED")


if __name__ == "__main__":
    main()
