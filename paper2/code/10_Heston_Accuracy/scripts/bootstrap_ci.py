#!/usr/bin/env python3
"""bootstrap_ci.py - 95% bootstrap intervals over the 10 repeats (2 sessions x 5) of every measured group
(protocol Section 11), for the energy per pricing run (set runs) and the energy per path-step (steady-state runs).
Reads the per-run folders (results/phase5/, results/phase5s/, or the unpacked phase5_runs.tar.gz /
phase5s_runs.tar.gz given with --runs). Writes results/analysis_ci/ci.csv and ci.md.
Usage: python3 scripts/bootstrap_ci.py [--runs DIR]   (DIR contains phase5/ and phase5s/)"""
import argparse, csv, glob, os, random, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase5 as P5

def per_rep(d, impl, cfg):
    r = P5.record(d)
    out = {}
    if impl == "fpga":
        out["socket"] = (r["E_card_J"] + r["E_cpu_socket_J"]) / r["runs"]
        out["server"] = (r["E_card_J"] + r["E_cpu_server_J"]) / r["runs"]
    else:
        out["socket" if cfg == "s0_c24" else "server"] = r[f"E_cpu_{'socket' if cfg == 's0_c24' else 'server'}_J"] / r["runs"]
    return out, r["path_steps"]

def boot(xs, n=10000, seed=1):
    rnd = random.Random(seed)
    ms = sorted(statistics.median(rnd.choices(xs, k=len(xs))) for _ in range(n))
    return statistics.median(xs), ms[int(0.025 * n)], ms[int(0.975 * n) - 1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=os.path.join(P5.RES))
    a = ap.parse_args()
    groups = {}
    for top in ("phase5", "phase5s"):
        for d in glob.glob(os.path.join(a.runs, top, "s[12]", "*", "*", "*", "rep*")):
            if not os.path.exists(os.path.join(d, "DONE")):
                continue
            parts = d.split(os.sep)
            impl, cfg, job = parts[-4], parts[-3], parts[-2]
            e, ps = per_rep(d, impl, cfg)
            for b, v in e.items():
                kind = job.split("_")[0]
                val = v if kind == "set" else v / ps     # steady state: J per path-step
                groups.setdefault((top, impl, job, b), []).append(val)
    out = os.path.join(P5.RES, "analysis_ci")
    os.makedirs(out, exist_ok=True)
    rows = []
    for (top, impl, job, b), xs in sorted(groups.items()):
        m, lo, hi = boot(xs)
        rows.append(dict(study="main" if top == "phase5" else "secondary", impl=impl, job=job, boundary=b, n=len(xs),
                         median=m, lo=lo, hi=hi, half_width_rel=(hi - lo) / 2 / m))
    with open(os.path.join(out, "ci.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    hw = sorted(r["half_width_rel"] for r in rows)
    lines = [f"# Bootstrap 95% intervals of the median over repeats ({len(rows)} groups)", "",
             f"- repeats per group: {min(r['n'] for r in rows)}-{max(r['n'] for r in rows)}",
             f"- relative half-width: median {statistics.median(hw):.2%}, 90th percentile {hw[int(0.9*len(hw))]:.2%}, max {hw[-1]:.2%}", ""]
    for r in sorted(rows, key=lambda r: -r["half_width_rel"])[:12]:
        lines.append(f"- widest: {r['study']} {r['impl']} {r['job']} {r['boundary']}: {r['median']:.4g} [{r['lo']:.4g}, {r['hi']:.4g}] (n={r['n']})")
    open(os.path.join(out, "ci.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

if __name__ == "__main__":
    main()
