#!/usr/bin/env python3
"""calibrate_s24.py - the exploratory calibrations of protocol v0.6 Section 24 (addendum v0.6.1, Section 4), CPU only.
They do not change any registered setting; they feed the RQ2 sensitivity analysis.

  --what fine : the float model (bit-identical to the card) at steps per year 3 x 2^k between the registered grid
                points (QE 3..48; FT 3..3072 for Cases I-III and P1, 3..192 for Case B), with Part A's precision rule
                (SE <= eps_min / 5 or |bias| >= 10 SE). Seeds 401-404 (I, II, III, P1) and 801 (B).
                Output: results/fine/calibration_summary.csv (main cases), results/fine/calibration_b.csv (Case B).
  --what qem  : double-precision QE and QE-M (build/hq_qem, `make qem`) on the QE grid and the finer QE grid
                (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64 per year), same precision rule, with the seeds of Part A's QE
                calibration (301-304, 601), so QE, QE-M and the float QE of Part A share draws.
                Output: results/qem/calibration_summary.csv, results/qem/calibration_b.csv.
It reuses scripts/calibrate.py and calibrate_s.py (same batches, same stopping rule, resumable).

Usage (from the project folder, outside the measurement sessions):
    python3 scripts/calibrate_s24.py --what fine --plan
    nohup nice -n 19 python3 scripts/calibrate_s24.py --what fine --threads 94 > results/calibrate_fine.log 2>&1 &
    nohup nice -n 19 python3 scripts/calibrate_s24.py --what qem --threads 94 > results/calibrate_qem.log 2>&1 &
"""
import argparse, csv, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate as C
import calibrate_s as CS
import hqlib as HQ
import hslib as HS

ROOT = HQ.ROOT
QEM_BIN = os.path.join(ROOT, "build", "hq_qem")
FINE = {"qe": [3, 6, 12, 24, 48], "ft": [3 * 2 ** k for k in range(11)]}          # 3 .. 3072
FINE_B = {"qe": [3, 6, 12, 24, 48], "ft": [3, 6, 12, 24, 48, 96, 192]}
QEG = sorted([1, 2, 4, 8, 16, 32, 64] + [3, 6, 12, 24, 48])


def use_qem_binary():
    for mod in (HQ, HS):
        orig = mod.run
        mod.run = lambda args, threads=None, check=True, binary=None, _o=orig: _o(args, threads, check, QEM_BIN)


def main_cases(what, threads, hours, plan, ts=1.0):
    out = os.path.join(ROOT, "results", what)
    if what == "fine":
        C.GRID, C.SEED = FINE, {"I": 401, "II": 402, "III": 403, "P1": 404}
        base = C.jobs
        C.jobs = lambda only=None: [j for j in base(only) if j["impl"] == "scalar"]
    else:
        C.NS_STEP["qem"] = C.NS_STEP["qe"] * 2.5

        def jobs(only=None):
            res = []
            for case in ("P1", "III", "I", "II"):
                if only and case not in only:
                    continue
                eps_min = min(HQ.TARGETS) * HQ.atm(case)
                for scheme in ("qe", "qem"):
                    for spy in QEG:
                        res.append(dict(case=case, scheme=scheme, impl="double", spy=spy, seed=C.SEED[case], target_se=eps_min / 5 * C.TSCALE))
            return res
        C.jobs = jobs
    whys = []
    rj = C.run_job

    def run_job(j, threads, deadline):
        m, why = rj(j, threads, deadline)
        whys.append(why)
        return m, why
    C.run_job = run_job
    sys.argv = [sys.argv[0], "--results", out, "--threads", str(threads), "--max-hours", str(hours), "--target-scale", str(ts)] + (["--plan"] if plan else [])
    C.main()
    return plan or (len(whys) == len(C.jobs()) and all(w in ("done", "cap", "fixed") for w in whys))


def basket(what, threads, deadline, plan, ts=1.0):
    out = os.path.join(ROOT, "results", what)
    CS.RES, CS.RAW = out, os.path.join(out, "calib_b")
    if what == "fine":
        js = [dict(case="B", scheme=sc, impl="scalar", spy=spy, seed=801, target_se=CS.eps_min("B") / 5 * ts) for sc in ("qe", "ft") for spy in FINE_B[sc]]
    else:
        js = [dict(case="B", scheme=sc, impl="double", spy=spy, seed=601, target_se=CS.eps_min("B") / 5 * ts) for sc in ("qe", "qem") for spy in QEG]
    if plan:
        print(f"Case B: {len(js)} jobs ({', '.join(CS.key(j) for j in js[:3])}, ...)")
        return True
    rows, fin = [], True
    for j in js:
        m, why = CS.run_job(j, threads, deadline)
        print(f"{CS.key(j)}: {why} after {m.pairs:,} pairs, {m.seconds / 60:.1f} min", flush=True)
        fin = fin and why in ("done", "cap")
        s = CS.stats(j, m)
        row = dict(case="B", scheme=j["scheme"], impl=j["impl"], steps_per_year=j["spy"], steps=j["spy"], seed=j["seed"], pairs=m.pairs,
                   cpu_seconds=round(m.seconds, 1), path_steps=m.path_steps, target_se=j["target_se"], stop=why)
        for k in range(HS.NK):
            row.update({f"K{k}": HS.CASES["B"]["K"][k], f"est{k}": s[k]["est"], f"se{k}": s[k]["se"], f"bias{k}": s[k]["bias"]})
        rows.append(row)
        with open(os.path.join(out, "calibration_b.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        if why == "time budget":
            return False
    return fin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", choices=("fine", "qem"), required=True)
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--max-hours", type=float, default=1e9)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--target-scale", type=float, default=1.0, help=argparse.SUPPRESS)   # tests only
    a = ap.parse_args()
    if a.what == "qem":
        if not os.path.exists(QEM_BIN):
            sys.exit("build/hq_qem not found: make qem")
        use_qem_binary()
    os.makedirs(os.path.join(ROOT, "results", a.what), exist_ok=True)
    deadline = time.time() + a.max_hours * 3600
    argv = list(sys.argv)
    ok_main = main_cases(a.what, a.threads, a.max_hours, a.plan, a.target_scale)
    sys.argv = argv
    ok = ok_main and basket(a.what, a.threads, deadline, a.plan, a.target_scale)
    if not a.plan:
        print(f"SECTION 24 {a.what.upper()} CALIBRATION {'FINISHED' if ok else 'INCOMPLETE (run again to continue)'}")


if __name__ == "__main__":
    main()
