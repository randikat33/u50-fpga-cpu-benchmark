#!/usr/bin/env python3
"""calibrate.py - phase 2 of paper 2: measure the bias and variance of each scheme on the CPU.

Protocol v0.4 (13 Oct 2026): each strike is priced from its call or put payoff (hq::Case::puts; a put gives
the call by put-call parity), because the call payoff has infinite variance in Case II. The v0.3 calibration
(call payoffs, seeds 101-104) is kept in results_calls_v03/ and is not mixed with this one.

For every job (case, scheme, steps per year) the float model (bit-identical to the card) runs in
batches of consecutive pair ranges of one seed until each strike is "done":
    its standard error <= target_se  (target_se = 0.1% of the ATM price / 5, protocol Section 10), or
    its |bias| >= 10 SE              (bias resolved to 10%; enough for the fit).
Every batch is appended to results/raw/<job>.jsonl, so the script can be stopped (Ctrl-C, reboot)
and started again: it continues where it stopped. The summary is written to
results/calibration_summary.csv after every job.

Usage (from the project folder, after `make cpu`):
    python3 scripts/calibrate.py --plan            # list the jobs and the estimated CPU time
    python3 scripts/calibrate.py --threads 46      # run all jobs (resumable)
    python3 scripts/calibrate.py --threads 46 --only P1,III --max-hours 2
"""
import argparse
import csv
import glob
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hqlib as H

RES = os.path.join(H.ROOT, "results")
RAW = os.path.join(RES, "raw")
TSCALE = 1.0                                                    # --target-scale (tests only)
MAXSPY = 10 ** 9                                               # --max-spy (tests only)
SEED = {"I": 301, "II": 302, "III": 303, "P1": 304}          # one seed per case (v0.4; v0.3 used 101-104)
GRID = {"qe": [1, 2, 4, 8, 16, 32, 64], "ft": [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]}
SIGMA = {"I": 13.0, "II": 30.0, "III": 29.0, "P1": 7.0}        # v0.4 payoff forms: SD of one path's payoff (plan only)
NS_STEP = {"qe": 30.0, "ft": 11.0}                              # sandbox scalar ns per path-step and core
MAX_PAIRS = 2 ** 32 - 2 ** 16
RESOLVE = 10.0


def jobs(only=None):
    out = []
    for case in ("P1", "III", "I", "II"):
        if only and case not in only:
            continue
        eps_min = min(H.TARGETS) * H.atm(case)
        for scheme in ("qe", "ft"):
            for spy in GRID[scheme]:
                if spy > MAXSPY:
                    continue
                out.append(dict(case=case, scheme=scheme, impl="scalar", spy=spy, seed=SEED[case],
                                target_se=eps_min / 5 * TSCALE))
        # float32 + table error against double precision on the same paths (2^20 pairs each)
        for scheme, spy in (("qe", 8), ("qe", 64), ("ft", 64), ("ft", 1024)):
            if spy > MAXSPY:
                continue
            out.append(dict(case=case, scheme=scheme, impl="diff", spy=spy, seed=SEED[case] + 1000,
                            target_se=0.0, fixed_pairs=int(2 ** 20 * min(1.0, TSCALE ** -2))))
    return out


def key(j):
    return f"{j['case']}_{j['scheme']}_{j['impl']}_{j['spy']}_s{j['seed']}"


def load(j):
    m = H.Moments(j["impl"])
    a1 = 0
    path = os.path.join(RAW, key(j) + ".jsonl")
    if os.path.exists(path):
        for line in open(path):
            r = json.loads(line)
            assert int(r["a0"]) == a1, f"{path}: gap in pair ranges"
            m.add(r)
            a1 = int(r["a1"])
    return m, a1


def strikes_done(j, m):
    s = m.summary(j["case"])
    done = []
    for k in range(H.NK):
        se = s[f"se{k}"]
        ok = se <= j["target_se"]
        if not ok and j["impl"] in ("scalar", "double"):
            ok = abs(s[f"bias{k}"]) >= RESOLVE * se
        done.append(ok)
    return done, s


def est_pairs(j):
    if j.get("fixed_pairs"):
        return j["fixed_pairs"]
    return min(MAX_PAIRS, int((SIGMA[j["case"]] / j["target_se"]) ** 2 / 2))


def plan(only):
    tot = 0.0
    print(f"{'job':34s} {'steps':>6s} {'pairs (worst case)':>18s} {'core-hours':>10s}")
    for j in jobs(only):
        steps = j["spy"] * H.CASES[j["case"]]["T"]
        n = est_pairs(j)
        ns = NS_STEP[j["scheme"]] * (2.2 if j["impl"] == "diff" else 1.0)
        ch = 2 * n * steps * ns * 1e-9 / 3600
        tot += ch
        print(f"{key(j):34s} {steps:6d} {n:18,d} {ch:10.1f}")
    print(f"worst case total: {tot:.0f} core-hours = {tot / 48:.1f} hours on 48 threads "
          "(much less in practice: coarse steps stop early once the bias is resolved)")


def run_job(j, threads, deadline):
    os.makedirs(RAW, exist_ok=True)
    m, a1 = load(j)
    steps = j["spy"] * H.CASES[j["case"]]["T"]
    path = os.path.join(RAW, key(j) + ".jsonl")
    while True:
        if j.get("fixed_pairs"):
            if m.pairs >= j["fixed_pairs"]:
                return m, "fixed"
            want = j["fixed_pairs"] - m.pairs
        else:
            done, s = strikes_done(j, m) if m.pairs >= 2 ** 16 else ([False] * H.NK, None)
            if all(done):
                return m, "done"
            if m.pairs >= MAX_PAIRS:
                return m, "cap"
            if m.pairs < 2 ** 16:
                want = 2 ** 16 - m.pairs
            else:   # pairs still needed for the slowest strike, +10%, at most doubling
                need = 0
                for k in range(H.NK):
                    if not done[k]:
                        need = max(need, m.pairs * (s[f"se{k}"] / j["target_se"]) ** 2 * 1.1 - m.pairs)
                want = int(min(max(need, 2 ** 16), m.pairs))
        # keep one batch under 20 minutes (measured speed)
        if m.path_steps > 0:
            per_pair = m.seconds / m.pairs
            want = max(2 ** 12, min(want, int(1200 / per_pair)))
        want = int(min(want, MAX_PAIRS - a1))
        if time.time() > deadline:
            return m, "time budget"
        r = H.run(["--case", j["case"], "--scheme", j["scheme"], "--impl", j["impl"], "--steps", steps,
                   "--seed", j["seed"], "--a0", a1, "--a1", a1 + want], threads=threads)
        with open(path, "a") as f:
            f.write(json.dumps(r) + "\n")
        m.add(r)
        a1 += want
        s = m.summary(j["case"])
        print(f"  {key(j)}: {m.pairs:,} pairs, K=100 est {s['est1']:.6f} se {s['se1']:.6f}"
              + (f" bias {s['bias1']:+.6f}" if "bias1" in s else "")
              + f"  ({float(r['t_compute_s']):.0f} s)", flush=True)


def write_summary():
    rows = []
    for j in jobs():
        m, _ = load(j)
        if m.pairs == 0:
            continue
        s = m.summary(j["case"])
        row = dict(case=j["case"], scheme=j["scheme"], impl=j["impl"], steps_per_year=j["spy"],
                   steps=j["spy"] * H.CASES[j["case"]]["T"], seed=j["seed"], pairs=m.pairs,
                   payoff_overflows=m.ovf, cpu_seconds=round(m.seconds, 1), path_steps=m.path_steps,
                   target_se=j["target_se"])
        for k in range(H.NK):
            row[f"K{k}"] = H.CASES[j["case"]]["K"][k]
            row[f"est{k}"] = s[f"est{k}"]
            row[f"se{k}"] = s[f"se{k}"]
            row[f"bias{k}"] = s.get(f"bias{k}", "")
        rows.append(row)
    if rows:
        os.makedirs(RES, exist_ok=True)
        with open(os.path.join(RES, "calibration_summary.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return rows


def main():
    global RES, RAW, TSCALE, MAXSPY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true", help="list the jobs and the worst-case CPU time, run nothing")
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--only", default="", help="comma-separated cases, e.g. P1,III")
    ap.add_argument("--max-hours", type=float, default=1e9, help="stop starting new batches after this time")
    ap.add_argument("--summary", action="store_true", help="only rewrite results/calibration_summary.csv")
    ap.add_argument("--results", default=os.path.join(H.ROOT, "results"), help=argparse.SUPPRESS)          # tests only
    ap.add_argument("--target-scale", type=float, default=1.0, help=argparse.SUPPRESS)   # tests only
    ap.add_argument("--max-spy", type=int, default=10 ** 9, help=argparse.SUPPRESS)      # tests only
    a = ap.parse_args()
    RES, RAW, TSCALE, MAXSPY = a.results, os.path.join(a.results, "raw"), a.target_scale, a.max_spy
    only = set(a.only.split(",")) if a.only else None
    if a.plan:
        plan(only)
        return
    if a.summary:
        print(f"{len(write_summary())} jobs in results/calibration_summary.csv")
        return
    if not os.path.exists(H.BIN):
        sys.exit("build/hq_cpu not found: run `make cpu` first")
    deadline = time.time() + a.max_hours * 3600
    for j in jobs(only):
        m, why = run_job(j, a.threads, deadline)
        print(f"{key(j)}: {why} after {m.pairs:,} pairs, {m.seconds / 60:.1f} min", flush=True)
        write_summary()
        if why == "time budget":
            print("time budget reached; run the same command again to continue")
            break
    else:
        print("ALL CALIBRATION JOBS FINISHED")


if __name__ == "__main__":
    main()
