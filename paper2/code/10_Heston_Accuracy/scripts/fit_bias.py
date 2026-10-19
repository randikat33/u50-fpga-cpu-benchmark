#!/usr/bin/env python3
"""fit_bias.py - turn the calibration (results/calibration_summary.csv) into the cost-optimal
settings of protocol Section 7, for every case, accuracy target and scheme.

Rules (protocol v0.2, Section 7):
  (protocol v0.3: the empirical rule is used for the prediction, for both schemes; the FT model
   rule is kept for comparison, because its extrapolation missed the target in 3 of 12 FT settings and had no margin in a fourth)
  FT, model rule:     fit |b(h)| = c h^p on the K=100 biases that are resolved (|b| >= 3 SE, h <= 1/4);
                      h_opt = (eps / (c sqrt(1+2p)))^(1/p), rounded down to the grid (2^-j per year);
                      paths N_opt = (1+2p) sigma^2 / (2p eps^2).
  QE (and FT check),  coarsest grid step whose measured |b| + 2 SE_b <= eps / sqrt(2) for all three
  empirical rule:     strikes; N = 2 sigma^2 / eps^2.
  sigma^2 is the variance of one antithetic pair average, taken from the calibration run at the
  chosen step (largest over the three strikes); N is the number of pairs.
  The target eps (RMSE) must hold for all three strikes; eps = 1%, 0.3%, 0.1% of the ATM price.
Outputs:
  results/bias_fit.csv          c, p (with standard errors) per case for FT
  results/settings.csv          one row per case x target x scheme x rule: step, pairs, path-steps,
                                predicted RMSE per strike, and whether the grid was fine enough
  results/float_vs_double.csv   float32 model minus double precision (diff jobs)
"""
import argparse
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hqlib as H

NK = H.NK


def read(path):
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        for k, v in list(r.items()):
            try:
                r[k] = float(v)
            except (TypeError, ValueError):
                pass
    return rows


def fit_power(pts):
    """weighted least squares of ln|b| = ln c + p ln h; weights 1/var(ln|b|) = (b/se)^2"""
    if len(pts) < 3:
        return None
    X = [math.log(h) for h, b, se in pts]
    Y = [math.log(abs(b)) for h, b, se in pts]
    W = [(b / se) ** 2 for h, b, se in pts]
    sw = sum(W)
    mx = sum(w * x for w, x in zip(W, X)) / sw
    my = sum(w * y for w, y in zip(W, Y)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(W, X))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(W, X, Y))
    p = sxy / sxx
    lc = my - p * mx
    res = sum(w * (y - lc - p * x) ** 2 for w, x, y in zip(W, X, Y))
    s2 = res / max(len(pts) - 2, 1)                      # scale by the residual variance (model misfit)
    se_p = math.sqrt(max(s2, 1.0) / sxx)
    return dict(c=math.exp(lc), p=p, se_p=se_p, n=len(pts), chi2_dof=s2)


def pair_var(r, k):
    return r[f"se{k}"] ** 2 * (r["pairs"] - 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(H.ROOT, "results"))
    a = ap.parse_args()
    rows = read(os.path.join(a.results, "calibration_summary.csv"))
    cal = [r for r in rows if r["impl"] == "scalar"]
    fits, out = [], []
    for case in H.CASES:
        T = H.CASES[case]["T"]
        for scheme in ("ft", "qe"):
            R = sorted([r for r in cal if r["case"] == case and r["scheme"] == scheme], key=lambda r: r["steps_per_year"])
            if not R:
                continue
            fit = None
            if scheme == "ft":
                pts = [(1 / r["steps_per_year"], r["bias1"], r["se1"]) for r in R
                       if r["steps_per_year"] >= 4 and abs(r["bias1"]) >= 3 * r["se1"] and r["bias1"] > 0]
                fit = fit_power(pts)
                if fit:
                    fits.append(dict(case=case, scheme=scheme, c=fit["c"], p=fit["p"], se_p=fit["se_p"],
                                     points=fit["n"], chi2_per_dof=fit["chi2_dof"]))
            for frac in H.TARGETS:
                eps = frac * H.atm(case)
                rules = ["empirical"] + (["model"] if fit else [])
                for rule in rules:
                    row = dict(case=case, target=f"{frac * 100:g}%", eps=eps, scheme=scheme, rule=rule)
                    if rule == "empirical":
                        ok = [r for r in R if all(abs(r[f"bias{k}"]) + 2 * r[f"se{k}"] <= eps / math.sqrt(2) for k in range(NK))]
                        if not ok:
                            row.update(steps_per_year="", note="no grid step meets the bias budget: extend the grid")
                            out.append(row)
                            continue
                        at = ok[0]
                        spy = int(at["steps_per_year"])
                        pairs = max(pair_var(at, k) for k in range(NK)) / (eps ** 2 / 2)
                    else:
                        p = max(fit["p"], 0.25)
                        h = (eps / (fit["c"] * math.sqrt(1 + 2 * p))) ** (1 / p)
                        spy = 2 ** math.ceil(math.log2(1 / h))
                        cal_at = [r for r in R if r["steps_per_year"] == spy]
                        at = cal_at[0] if cal_at else None
                        near = at or min(R, key=lambda r: abs(math.log(r["steps_per_year"] / spy)))
                        pairs = (1 + 2 * p) * max(pair_var(near, k) for k in range(NK)) / (2 * p * eps ** 2)
                        row["calibrated_at_step"] = "yes" if at else "no (extrapolated)"
                    pairs = int(math.ceil(pairs / 64) * 64)               # whole work units
                    steps = int(spy * T)
                    row.update(steps_per_year=spy, steps=steps, pairs=pairs, path_steps=2.0 * pairs * steps)
                    rm = []
                    for k in range(NK):
                        if at is not None:
                            b, bse, var = at[f"bias{k}"], at[f"se{k}"], pair_var(at, k) / pairs
                        else:   # model rule beyond the calibrated grid: fitted K=100 bias only
                            b = fit["c"] * (1 / spy) ** fit["p"] if k == 1 else float("nan")
                            bse, var = float("nan"), pair_var(near, k) / pairs
                        row[f"bias{k}"] = b
                        row[f"bias_se{k}"] = bse
                        row[f"pred_rmse{k}"] = math.sqrt((b if b == b else 0.0) ** 2 + var)
                        rm.append(row[f"pred_rmse{k}"])
                    row["pred_rmse_max_over_eps"] = max(rm) / eps
                    out.append(row)
    os.makedirs(a.results, exist_ok=True)
    if fits:
        with open(os.path.join(a.results, "bias_fit.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fits[0].keys()))
            w.writeheader()
            w.writerows(fits)
    keys = []
    for r in out:
        keys += [k for k in r if k not in keys]
    with open(os.path.join(a.results, "settings.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(out)
    diff = [r for r in rows if r["impl"] == "diff"]
    if diff:
        with open(os.path.join(a.results, "float_vs_double.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["case", "scheme", "steps_per_year", "pairs"] +
                       [x for k in range(NK) for x in (f"mean_diff{k}", f"se{k}", f"diff_over_eps_min{k}")])
            for r in diff:
                em = min(H.TARGETS) * H.atm(r["case"])
                w.writerow([r["case"], r["scheme"], int(r["steps_per_year"]), int(r["pairs"])] +
                           [x for k in range(NK) for x in (r[f"est{k}"], r[f"se{k}"], r[f"est{k}"] / em)])
    # printed report
    print("FT bias fit |b| = c h^p (K = 100):")
    for f_ in fits:
        print(f"  {f_['case']:>3}: c = {f_['c']:.3f}, p = {f_['p']:.3f} +- {f_['se_p']:.3f} ({f_['points']} points)")
    print("\nsettings (pairs = antithetic pairs; path-steps = 2 x pairs x steps):")
    print(f"  {'case':>4} {'target':>6} {'scheme':>6} {'rule':>9} {'steps/yr':>8} {'pairs':>14} {'path-steps':>11} {'RMSE/eps':>8}")
    for r in out:
        if r.get("steps_per_year") == "":
            print(f"  {r['case']:>4} {r['target']:>6} {r['scheme']:>6} {r['rule']:>9}  -- {r['note']}")
            continue
        print(f"  {r['case']:>4} {r['target']:>6} {r['scheme']:>6} {r['rule']:>9} {r['steps_per_year']:>8} "
              f"{r['pairs']:>14,d} {r['path_steps']:>11.3g} {r['pred_rmse_max_over_eps']:>8.2f}"
              + ("  ! above target" if r['pred_rmse_max_over_eps'] > 1 else "")
              + ("" if r.get("calibrated_at_step", "yes") == "yes" else "  (step extrapolated)"))
    # settings used for the prediction (protocol v0.3): the empirical rule for both schemes; the
    # FT model rule is reported for comparison only (it extrapolates and can miss the target)
    print("\nwork ratio, empirical rule (settings that meet the target at all three strikes):")
    for case in H.CASES:
        for tg in H.TARGETS:
            t = f"{tg * 100:g}%"
            sel = {sc: [r for r in out if r["case"] == case and r["target"] == t and r["scheme"] == sc
                        and r["rule"] == "empirical" and r.get("path_steps")] for sc in ("ft", "qe")}
            if sel["ft"] and sel["qe"]:
                print(f"  {case:>4} {t:>6}: path-steps FT/QE = {sel['ft'][0]['path_steps'] / sel['qe'][0]['path_steps']:.1f}")
    print(f"\nwritten to {a.results}: bias_fit.csv, settings.csv" + (", float_vs_double.csv" if diff else ""))


if __name__ == "__main__":
    main()
