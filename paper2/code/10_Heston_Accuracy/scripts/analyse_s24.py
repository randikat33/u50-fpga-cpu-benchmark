#!/usr/bin/env python3
"""analyse_s24.py - RQ2 sensitivity to the setting rule (protocol v0.6 Section 24, addendum v0.6.1 Section 4).
Exploratory; the registered RQ2 result is not replaced. For Cases I-III, P1 and B at the three targets:
  reg      : registered rule (equal bias/variance split, beta <= eps/sqrt2) on the registered grid (reproduces Part A)
  opt      : work-optimal split on the registered grid (as results/analysis_extra, Section 3)
  fine_reg : registered rule on the registered grid plus the finer grid 3*2^k (float model, seeds 401-404, 801)
  fine_opt : work-optimal split on the same merged grid
  qem_reg / qem_opt : FT as fine_*, the QE method replaced by QE-M (double, Part A's QE seeds), on the QE grid plus the
             finer QE grid; QE-M's energy per path-step = QE's x (QE-M / QE CPU time per path-step, same batches)
             (the stated assumption of Section 24 item 3); qe_dbl_* is double-precision QE on the same draws, as a check
Method gain = (W_FT e_FT) / (W_QE e_QE), e = best platform's measured steady-state energy per path-step at the socket
boundary (Part A); platform gain from Part A (results/figures/fig2_points.json). Margin = method gain / platform gain.
Output: results/analysis_s24/s24.md, s24.json
"""
import csv, json, math, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extra_analysis as X

RES = X.RES
OUT = os.path.join(RES, "analysis_s24")


def rows_of(files, scheme_map=None):
    R = defaultdict(list)
    for f, filt in files:
        p = os.path.join(RES, f)
        for r in csv.DictReader(open(p)):
            if filt(r):
                R[(r["case"], (scheme_map or {}).get(r["scheme"], r["scheme"]))].append(r)
    return R


def merge(*Rs):
    out = defaultdict(list)
    for R in Rs:
        for k, v in R.items():
            have = {int(r["steps_per_year"]) for r in out[k]}
            out[k] += [r for r in v if int(r["steps_per_year"]) not in have]
    for k in out:
        out[k].sort(key=lambda r: int(r["steps_per_year"]))
    return out


def cost_ratio(case):
    """QE-M / QE CPU time per path-step over the same batches (qem/raw or qem/calib_b)"""
    d = os.path.join(RES, "qem", "raw" if case != "B" else "calib_b")
    tot = {"qe": [0.0, 0.0], "qem": [0.0, 0.0]}
    fb = 0
    for f in os.listdir(d):
        if not f.startswith(case + "_"):
            continue
        sc = f.split("_")[1]
        for line in open(os.path.join(d, f)):
            x = json.loads(line)
            tot[sc][0] += x["t_compute_s"]; tot[sc][1] += x["path_steps"]
            fb += x.get("martingale_fallbacks", 0)
    return (tot["qem"][0] / tot["qem"][1]) / (tot["qe"][0] / tot["qe"][1]), fb


def main():
    _, g = X.load_mvp()
    pts = {p["case"] + p["target"]: p for p in json.load(open(os.path.join(RES, "figures", "fig2_points.json")))}
    Wreg = X.work()
    REG = X.calib_rows()
    FINE = merge(REG, rows_of([("fine/calibration_summary.csv", lambda r: True), ("fine/calibration_b.csv", lambda r: r["stop"] == "done")]))
    QEM = rows_of([("qem/calibration_summary.csv", lambda r: True), ("qem/calibration_b.csv", lambda r: r["stop"] == "done")])
    out, L = [], ["# RQ2 sensitivity to the setting rule (Section 24, exploratory)", "",
                  "Margin = method gain / platform gain (Part A platform gain, socket boundary). RQ2 (method gain larger) holds where margin > 1.", ""]
    ratios = {c: cost_ratio(c) for c in ("I", "II", "III", "P1", "B")}
    L += ["QE-M / QE CPU time per path-step (same batches): " + ", ".join(f"{c} {r:.3f}" for c, (r, _) in ratios.items())
          + f"; martingale fallbacks {sum(f for _, f in ratios.values())}", "",
          "| Case | Target | reg | opt | fine reg | fine opt | QE-M reg | QE-M opt | QE dbl reg | steps/yr QE: reg, fine opt, QE-M opt |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for case in ("I", "II", "III", "P1", "B"):
        for t in X.TARGETS:
            eps = X.FR[t] * X.ATM[case]
            v = g[(case, t, "socket")]
            e = {m: min(v[f"{p}_{m}"]["Ess_meas_J"] for p in ("card", "cpu_matched", "cpu_native")) / Wreg[(case, t, m)] for m in ("ft", "qe")}
            e["qem"] = e["qe"] * ratios[case][0]
            gp = pts[case + t]["platform_gain"]
            rec = dict(case=case, target=t, platform_gain=gp)
            for name, Rft, Rqe, mq, rule in (("reg", REG, REG, "qe", "registered"), ("opt", REG, REG, "qe", "optimal"),
                                              ("fine_reg", FINE, FINE, "qe", "registered"), ("fine_opt", FINE, FINE, "qe", "optimal"),
                                              ("qem_reg", FINE, QEM, "qem", "registered"), ("qem_opt", FINE, QEM, "qem", "optimal"),
                                              ("qe_dbl_reg", FINE, QEM, "qe", "registered")):
                a = X.choose(Rft[(case, "ft")], eps, rule)
                b = X.choose(Rqe[(case, mq)], eps, rule)
                if not a or not b:
                    rec[name] = None; continue
                gm = (a["W"] * e["ft"]) / (b["W"] * e[mq])
                rec[name] = dict(spy_ft=a["spy"], spy_qe=b["spy"], W_ft=a["W"], W_qe=b["W"], method_gain=gm, margin=gm / gp)
            out.append(rec)
            f = lambda k: f"{rec[k]['margin']:.2f}" if rec[k] else "-"
            L.append(f"| {case} | {t} | {f('reg')} | {f('opt')} | {f('fine_reg')} | {f('fine_opt')} | {f('qem_reg')} | {f('qem_opt')} | {f('qe_dbl_reg')} | "
                     f"{rec['reg']['spy_qe']}, {rec['fine_opt']['spy_qe']}, {rec['qem_opt']['spy_qe'] if rec['qem_opt'] else '-'} |")
    summ = {}
    for k in ("reg", "opt", "fine_reg", "fine_opt", "qem_reg", "qem_opt", "qe_dbl_reg"):
        ms = [r[k]["margin"] for r in out if r[k]]
        summ[k] = dict(n=len(ms), n_over_1=sum(m > 1 for m in ms), min=min(ms), median=sorted(ms)[len(ms) // 2])
    L += ["", "| Rule | settings | margin > 1 | smallest margin | median |", "|---|---|---|---|---|"]
    for k, s in summ.items():
        L.append(f"| {k} | {s['n']} | {s['n_over_1']} | {s['min']:.2f} | {s['median']:.2f} |")
    # W ratios of QE-M vs QE (registered rule, same draws)
    wr = [r["qem_reg"]["W_qe"] / r["qe_dbl_reg"]["W_qe"] for r in out if r["qem_reg"] and r["qe_dbl_reg"]]
    L += ["", f"- QE-M work / QE (double, same draws) under the registered rule: {min(wr):.2f}-{max(wr):.2f} (median {sorted(wr)[len(wr) // 2]:.2f})"]
    os.makedirs(OUT, exist_ok=True)
    json.dump(dict(rows=out, summary=summ, cost_ratio={c: r for c, (r, _) in ratios.items()}), open(os.path.join(OUT, "s24.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, "s24.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
