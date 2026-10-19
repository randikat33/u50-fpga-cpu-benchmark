#!/usr/bin/env python3
"""reference_s.py - reference prices of the secondary cases (protocol v0.5, Section 4b), double precision on the CPU.

Case B (basket; no closed form): QE at h = 1/128 is the reference; FT at h = 1/512 and QE at h = 1/64 check it.
  The protocol asks for a standard error of at most eps_min/10 at every strike (eps_min = 0.1% of the ATM
  price). The runs here go further, to eps_min/40, so that the step-halving check ("QE 1/64 -> 1/128 changes
  the price by less than one standard error", read as: by less than the required reference precision eps_min/10)
  is decided by the step bias and not by noise: with SE eps_min/40 per run, noise alone exceeds eps_min/10 with
  a chance of about 0.5%. Accepted if, at every strike, |QE128 - FT512| <= 3 combined SE and
  |QE128 - QE64| < eps_min/10. Otherwise both runs are repeated at half the step (QE 1/256, FT 1/1024) and the
  QE 1/128 run becomes the step-halving check (protocol: "this is reported").
Case A (Asian): double-precision run, control-variate estimator with the run's own beta (its O(1/N) bias is
  negligible at this size), standard error at most eps_min/100 (protocol: at most eps_min/20); accepted if an
  independent plain-estimator run (other seed, SE <= eps_min/20) agrees within 3 combined SE at every strike.

Runs go in batches of consecutive pair ranges, appended to results/reference_s/<job>.jsonl (resumable).
Writes results/reference_s.csv (ref0..2 per case, with the checks).
Usage (after `make scpu`):
    python3 scripts/reference_s.py --plan
    nohup nice -n 19 python3 scripts/reference_s.py --threads 46 > results/reference_s.log 2>&1 &
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
OUT = os.path.join(RES, "reference_s")
NS = {("B", "qe"): 272.0, ("B", "ft"): 133.0, ("A", "cv"): 20.0, ("A", "plain"): 20.0}   # sandbox double ns/path-step/core
SIG = {"B": 2.15, "A_cv": 0.20, "A_plain": 3.95}                                           # pilot SD of a pair average (max)
BATCH_SECONDS = 600
SE_SCALE = 1.0                # --se-scale (tests only)


def eps_min(case):
    return min(H.TARGETS) * H.CASES[case]["atm_approx"]


def jobs(level=0):
    """level 0: QE 1/128 (reference), QE 1/64, FT 1/512; level 1 (fallback): QE 1/256, FT 1/1024"""
    em = eps_min("B") * SE_SCALE
    q = 128 * 2 ** level
    out = [dict(key=f"B_qe_{q}", case="B", scheme="qe", spy=q, seed=501 + 10 * level, se=em / 40),
           dict(key=f"B_qe_{q // 2}", case="B", scheme="qe", spy=q // 2, seed=502 + 10 * level, se=em / 40),
           dict(key=f"B_ft_{4 * q}", case="B", scheme="ft", spy=4 * q, seed=503 + 10 * level, se=em / 40)]
    ea = eps_min("A") * SE_SCALE
    out += [dict(key="A_cv", case="A", scheme="cv", spy=250, seed=511, se=ea / 100),
            dict(key="A_plain", case="A", scheme="plain", spy=250, seed=512, se=ea / 20)]
    return out


def load(j):
    m = H.Moments(j["case"], "double")
    f = os.path.join(OUT, j["key"] + ".jsonl")
    a1 = 0
    if os.path.exists(f):
        for line in open(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                  # a line cut by a power failure
            if int(r["a0"]) != a1:
                continue
            m.add(r)
            a1 = int(r["a1"])
    return m, a1


def ses(j, m):
    if m.pairs < 2:
        return [float("inf")] * H.NK
    return [m.estimate(j["scheme"], k)[1] for k in range(H.NK)]


def run_job(j, threads, deadline):
    os.makedirs(OUT, exist_ok=True)
    m, a1 = load(j)
    f = os.path.join(OUT, j["key"] + ".jsonl")
    steps = int(j["spy"] * H.CASES[j["case"]]["T"])
    while True:
        s = ses(j, m)
        if m.pairs >= 2 ** 14 and max(s) <= j["se"]:
            return m, "done"
        if time.time() > deadline:
            return m, "time budget"
        if m.pairs < 2 ** 14:
            want = 2 ** 14
        else:
            want = int(m.pairs * (max(s) / j["se"]) ** 2 * 1.05) - m.pairs
            want = max(2 ** 14, min(want, 4 * m.pairs))
        if m.seconds > 0:
            want = min(want, max(2 ** 12, int(BATCH_SECONDS / (m.seconds / m.pairs))))
        amax = 2 ** 29 if j["case"] == "B" else 2 ** 32
        want = min(want, amax - a1)
        if want <= 0:
            return m, "cap"
        r = H.run(H.args_for(j["case"], j["scheme"], spy=j["spy"]) + ["--impl", "double", "--seed", j["seed"],
                  "--a0", a1, "--a1", a1 + want], threads=threads)
        with open(f, "a") as fh:
            fh.write(json.dumps(r) + "\n")
        m.add(r)
        a1 += want
        s = ses(j, m)
        p = m.estimate(j["scheme"], 1)[0]
        print(f"  {j['key']}: {m.pairs:,} pairs, K=100 {p:.6f}, SE max {max(s):.2e} (target {j['se']:.2e}), "
              f"{float(r['t_compute_s']):.0f} s", flush=True)


def evaluate(level):
    js = {j["key"]: j for j in jobs(level)}
    rows = []
    # Case B
    q = 128 * 2 ** level
    ref, half, ft = js[f"B_qe_{q}"], js[f"B_qe_{q // 2}"], js[f"B_ft_{4 * q}"]
    mr, mh, mf = load(ref)[0], load(half)[0], load(ft)[0]
    if min(mr.pairs, mh.pairs, mf.pairs) >= 2:
        em = eps_min("B")
        row = dict(case="B", method=f"double QE h=1/{q}; checks FT h=1/{4 * q}, QE h=1/{q // 2}", level=level,
                   pairs=mr.pairs)
        acc = True
        for k in range(H.NK):
            pr, sr, _ = mr.estimate("qe", k)
            ph, sh, _ = mh.estimate("qe", k)
            pf, sf, _ = mf.estimate("ft", k)
            z_ft = (pr - pf) / math.hypot(sr, sf)
            d_half = pr - ph
            ok = abs(z_ft) <= 3 and abs(d_half) < em / 10 and sr <= em / 10
            acc = acc and ok
            row.update({f"ref{k}": f"{pr:.8f}", f"se{k}": f"{sr:.2e}", f"ft{k}": f"{pf:.8f}", f"z_ft{k}": f"{z_ft:+.2f}",
                        f"qe_half{k}": f"{ph:.8f}", f"d_half{k}": f"{d_half:+.2e}", f"d_half_over_eps_min_10_{k}": f"{d_half / (em / 10):+.2f}"})
        row["accepted"] = int(acc)
        rows.append(row)
    # Case A
    mc_, mp = load(js["A_cv"])[0], load(js["A_plain"])[0]
    if min(mc_.pairs, mp.pairs) >= 2:
        row = dict(case="A", method="double, control variate with the run's own beta; check: independent plain run",
                   level=0, pairs=mc_.pairs)
        acc = True
        for k in range(H.NK):
            a = mc_.asian(k)
            pp, sp_, _ = mp.estimate("plain", k)
            z = (a["cv"] - pp) / math.hypot(a["se_cv"], sp_)
            ok = abs(z) <= 3 and a["se_cv"] <= eps_min("A") / 20
            acc = acc and ok
            row.update({f"ref{k}": f"{a['cv']:.8f}", f"se{k}": f"{a['se_cv']:.2e}", f"beta_hat{k}": f"{a['beta_hat']:.6f}",
                        f"plain{k}": f"{pp:.8f}", f"se_plain{k}": f"{sp_:.2e}", f"z_plain{k}": f"{z:+.2f}",
                        f"geo_mc{k}": f"{a['geo_mc']:.8f}", f"geo_cf{k}": f"{a['geo_cf']:.8f}"})
        row["accepted"] = int(acc)
        rows.append(row)
    return rows


def write(rows):
    if not rows:
        return
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    os.makedirs(os.path.dirname(H.REF_CSV), exist_ok=True)
    with open(H.REF_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['case']}: " + ", ".join(f"K{k} {r[f'ref{k}']} (SE {r[f'se{k}']})" for k in range(H.NK))
              + f"  -> {'ACCEPTED' if r['accepted'] else 'NOT ACCEPTED'}")
        if r["case"] == "B":
            print("   FT check z: " + " ".join(r[f"z_ft{k}"] for k in range(H.NK)) +
                  "   step halving / (eps_min/10): " + " ".join(r[f"d_half_over_eps_min_10_{k}"] for k in range(H.NK)))
        else:
            print("   plain check z: " + " ".join(r[f"z_plain{k}"] for k in range(H.NK)))


def main():
    global SE_SCALE, RES, OUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--max-hours", type=float, default=1e9)
    ap.add_argument("--se-scale", type=float, default=1.0, help=argparse.SUPPRESS)   # tests only
    ap.add_argument("--results", default=RES, help=argparse.SUPPRESS)                # tests only
    a = ap.parse_args()
    SE_SCALE, RES, OUT = a.se_scale, a.results, os.path.join(a.results, "reference_s")
    H.REF_CSV = os.path.join(RES, "reference_s.csv")
    if a.plan:
        tot = 0
        for j in jobs(0):
            sig = SIG["B"] if j["case"] == "B" else SIG["A_" + j["scheme"]]
            n = (sig / j["se"]) ** 2
            steps = j["spy"] * H.CASES[j["case"]]["T"]
            ch = 2 * n * steps * NS[(j["case"], j["scheme"])] * 1e-9 / 3600
            tot += ch
            print(f"{j['key']:<12} pairs ~{n:12,.0f}  ~{ch:6.1f} core-hours")
        print(f"total ~{tot:.0f} core-hours, ~{tot / min(a.threads, 46) * 60:.0f} min with {a.threads} threads (sandbox speed)")
        return
    if not os.path.exists(H.BIN):
        sys.exit("build/hs_cpu not found: run `make scpu` first")
    deadline = time.time() + a.max_hours * 3600
    level = 0
    while True:
        for j in jobs(level):
            if level > 0 and j["case"] == "A":
                continue
            m, why = run_job(j, a.threads, deadline)
            print(f"{j['key']}: {why} after {m.pairs:,} pairs", flush=True)
            if why == "time budget":
                print("time budget reached; run the same command again to continue")
                return
        rows = evaluate(level)
        write(rows)
        b = [r for r in rows if r["case"] == "B"]
        if b and not b[0]["accepted"] and level == 0:
            print("Case B reference not accepted at level 0: repeating at half the step (protocol 4b)")
            level = 1
            continue
        break
    ok = all(r["accepted"] for r in rows) and len(rows) == 2
    print("REFERENCE FINISHED: " + ("both accepted" if ok else "NOT ALL ACCEPTED - send me results/reference_s.log"))


if __name__ == "__main__":
    main()
