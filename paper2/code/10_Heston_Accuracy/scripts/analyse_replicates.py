#!/usr/bin/env python3
"""analyse_replicates.py - HF2 and the CPU-native bias from the 200-replicate runs (addendum v0.6.1, Section 4).
HF2 holds if the matched code's RMSE/eps <= 1.1 and its upper 95% bound (percentile bootstrap, 10,000 resamples of the
200 replicates) <= 1.25, at all three strikes of all 36 settings. CPU-native bias: mean error of the 200 native
replicates (200 x N pairs), its SE, against the matched code's mean (same settings, other seeds) and against Part A's
12.5x check (results/native_check*.csv). Output: results/analysis_replicates/replicates.md, .csv, .json
"""
import csv, json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replicates as R

ROOT = R.ROOT
OUT = os.path.join(ROOT, "results", "analysis_replicates")
B, SEED = 10000, 20261022


def load(r, code):
    f = os.path.join(R.OUT, f"{r['key']}_{code}.jsonl")
    d = {}
    for line in open(f):
        x = json.loads(line); d[x["seed"]] = x["price"]
    return np.array([d[s] for s in sorted(d)])


def main():
    rng = np.random.default_rng(SEED)
    chk = {}
    for f in ("native_check.csv", "native_check_s.csv"):
        for x in csv.DictReader(open(os.path.join(ROOT, "results", f))):
            chk[(x["case"], x["scheme"], x["target"])] = x
    rows = []
    for r in R.settings():
        ref = np.array([R.ref(r, k) for k in range(3)])
        em = (load(r, "matched") - ref) / r["eps"]
        en = (load(r, "native") - ref) / r["eps"]
        idx = rng.integers(0, len(em), size=(B, len(em)))
        for k in range(3):
            rm = math.sqrt(np.mean(em[:, k] ** 2))
            boot = np.sqrt(np.mean(em[idx, k] ** 2, axis=1))
            lo, hi = np.percentile(boot, [2.5, 97.5])
            rn = math.sqrt(np.mean(en[:, k] ** 2))
            bn, sn = en[:, k].mean(), en[:, k].std(ddof=1) / math.sqrt(len(en))
            bm, sm = em[:, k].mean(), em[:, k].std(ddof=1) / math.sqrt(len(em))
            c = chk.get((r["case"], r["scheme"], r["target"]))
            pa = (float(c[f"bias_native{k}"]) / r["eps"], float(c[f"se_native{k}"]) / r["eps"]) if c else (float("nan"),) * 2
            rows.append(dict(case=r["case"], scheme=r["scheme"], target=r["target"], strike=k, n_matched=len(em), n_native=len(en),
                             rmse_matched=rm, lo95=lo, hi95=hi, hf2_ok=int(rm <= 1.1 and hi <= 1.25),
                             rmse_native=rn, bias_native=bn, se_native=sn, z_native=bn / sn, bias_matched=bm, se_matched=sm,
                             z_native_vs_matched=(bn - bm) / math.hypot(sn, sm),
                             partA_bias_native=pa[0], partA_se_native=pa[1], z_vs_partA=(bn - pa[0]) / math.hypot(sn, pa[1])))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "replicates.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    n_ok = sum(x["hf2_ok"] for x in rows)
    worst = max(rows, key=lambda x: x["hi95"])
    res = dict(HF2=dict(holds=n_ok == len(rows), ok=n_ok, n=len(rows), rmse_max=max(x["rmse_matched"] for x in rows),
                        hi95_max=worst["hi95"], worst=f"{worst['case']} {worst['scheme']} {worst['target']} strike {worst['strike']}"))
    L = ["# HF2 and the CPU-native bias (200 replicates per setting and code)", "",
         f"**HF2 {'holds' if res['HF2']['holds'] else 'fails'}**: {n_ok} of {len(rows)} setting-strikes meet RMSE/eps <= 1.1 and upper 95% bound <= 1.25 "
         f"(matched code). Largest RMSE/eps {res['HF2']['rmse_max']:.3f}; largest upper bound {worst['hi95']:.3f} ({res['HF2']['worst']}).", "",
         "| Setting | strike | matched RMSE/eps [95%] | native RMSE/eps | native bias/eps (z) | native - matched z | vs Part A z |", "|---|---|---|---|---|---|---|"]
    for x in rows:
        L.append(f"| {x['case']} {x['scheme']} {x['target']} | {x['strike']} | {x['rmse_matched']:.3f} [{x['lo95']:.3f}, {x['hi95']:.3f}]{'' if x['hf2_ok'] else ' FAIL'} | "
                 f"{x['rmse_native']:.3f} | {x['bias_native']:+.3f} ({x['z_native']:+.1f}) | {x['z_native_vs_matched']:+.2f} | {x['z_vs_partA']:+.2f} |")
    zs = [x["z_native_vs_matched"] for x in rows]
    res["native"] = dict(rmse_native_max=max(x["rmse_native"] for x in rows), n_native_over_1=sum(x["rmse_native"] > 1 for x in rows),
                         abs_z_vs_matched_max=max(abs(z) for z in zs), n_abs_z_gt2=sum(abs(z) > 2 for z in zs), n_abs_z_gt3=sum(abs(z) > 3 for z in zs),
                         abs_z_vs_partA_max=max(abs(x["z_vs_partA"]) for x in rows if not math.isnan(x["z_vs_partA"])),
                         abs_bias_max=max(abs(x["bias_native"]) for x in rows))
    n = res["native"]
    L += ["", f"- CPU-native: RMSE/eps max {n['rmse_native_max']:.3f} ({n['n_native_over_1']} of {len(rows)} above 1); |bias|/eps max {n['abs_bias_max']:.3f}; "
              f"native vs matched mean |z| max {n['abs_z_vs_matched_max']:.2f} ({n['n_abs_z_gt2']} above 2, {n['n_abs_z_gt3']} above 3 of {len(rows)}); "
              f"vs Part A's 12.5x check |z| max {n['abs_z_vs_partA_max']:.2f}"]
    json.dump(res, open(os.path.join(OUT, "replicates.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, "replicates.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L[:3])); print(L[-1])


if __name__ == "__main__":
    main()
