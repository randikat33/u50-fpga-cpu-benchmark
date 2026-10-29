#!/usr/bin/env python3
"""analyse_sched_v11.py - the v0.11 scheduling results against addendum v0.11 (HC1-HC6).
Medians over the 5 repeats, per pass, two-server boundary.
Input: results/sched_v11_summary.csv (scripts/sched_queue_v11t.py --summary). Output: results/analysis_sched_v11/sched_v11.{md,json}
"""
import csv, json, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

RES = S.RES
OUT = os.path.join(RES, "analysis_sched_v11")
BOOKS = ("N3", "N2", "S")
POLICIES = ("alone_gpu", "model_E", "plan_v09")


def main():
    pred = json.load(open(os.path.join(RES, "predictions_sched_v11", "predictions_sched_v11.json")))
    H = pred["hypotheses"]
    rows = list(csv.DictReader(open(os.path.join(RES, "sched_v11_summary.csv"))))
    by = defaultdict(dict)
    for r in rows:
        by[(r["book"], r["policy"])][int(r["rep"])] = r
    val = lambda k, f: [float(x[f]) for _, x in sorted(by[k].items())]
    E = {k: st.median(val(k, "E_pass_J")) for k in by}
    T = {k: st.median(val(k, "T_pass_s")) for k in by}
    has = lambda b, p: (b, p) in E
    ratio = {b: E[(b, "model_E")] / E[(b, "alone_gpu")] for b in BOOKS if has(b, "model_E") and has(b, "alone_gpu")}
    vs09 = {b: E[(b, "model_E")] / E[(b, "plan_v09")] for b in BOOKS if has(b, "model_E") and has(b, "plan_v09")}
    acc = {}
    for (b, p) in sorted(by):
        pp = pred["predictions"][f"{b}/{p}"]
        e, t = E[(b, p)] / pp["E_J"], T[(b, p)] / pp["T_s"]
        acc[f"{b}/{p}"] = dict(E_ratio=e, T_ratio=t, ok=0.90 <= e <= 1.10 and 0.90 <= t <= 1.10)
    res = {}
    res["HC1"] = dict(ratio=ratio.get("N3"), predicted=H["HC1"]["predicted_ratio"], holds=ratio.get("N3", 9) <= H["HC1"]["threshold"])
    res["HC2"] = dict(ratio={b: vs09.get(b) for b in H["HC2"]["books"]}, predicted=H["HC2"]["predicted_ratio"],
                      holds=all(vs09.get(b, 9) <= H["HC2"]["threshold"] for b in H["HC2"]["books"]))
    a3 = {k: v for k, v in acc.items() if k.split("/")[0] in H["HC3"]["books"]}
    res["HC3"] = dict(per=a3, n_ok=sum(v["ok"] for v in a3.values()), n=len(a3), holds=len(a3) == 6 and all(v["ok"] for v in a3.values()))
    res["HC4"] = dict(ratio=ratio.get("N2"), predicted=H["HC4"]["predicted_ratio"], holds=ratio.get("N2", 0) >= H["HC4"]["threshold"])
    order = sorted(ratio, key=ratio.get)
    res["HC5"] = dict(measured_order=order, predicted_order=H["HC5"]["predicted_order"], holds=order == H["HC5"]["predicted_order"])
    a6 = {k: v for k, v in acc.items() if k.startswith("S/")}
    res["HC6"] = dict(ratio=ratio.get("S"), predicted=H["HC6"]["predicted_ratio"], per=a6,
                      holds=ratio.get("S", 9) <= H["HC6"]["threshold"] and len(a6) == 3 and all(v["ok"] for v in a6.values()))
    yn = lambda x: "holds" if x else "fails"
    L = ["# Scheduling, addendum v0.11: the calibrated model on three books", "",
         "Per pass, two-server boundary; medians of 5 repeats [range].", "",
         "| Book | Policy | T meas (ms) | T pred (ms) | T ratio | E meas (J) | E pred (J) | E ratio | error max / eps |", "|---|---|---|---|---|---|---|---|---|"]
    for b in BOOKS:
        for p in POLICIES:
            if (b, p) not in by:
                continue
            pp = pred["predictions"][f"{b}/{p}"]
            es, ts = val((b, p), "E_pass_J"), val((b, p), "T_pass_s")
            L.append(f"| {b} | {p} | {T[(b, p)] * 1e3:.1f} [{min(ts) * 1e3:.1f}-{max(ts) * 1e3:.1f}] | {pp['T_s'] * 1e3:.1f} | {T[(b, p)] / pp['T_s']:.3f} | "
                     f"{E[(b, p)]:.2f} [{min(es):.2f}-{max(es):.2f}] | {pp['E_J']:.2f} | {E[(b, p)] / pp['E_J']:.3f} | {max(val((b, p), 'err_max_over_eps')):.2f} |")
    L += ["", "| Book | model_E / alone_gpu: measured | predicted | model_E / plan_v09: measured | predicted |", "|---|---|---|---|---|"]
    for b in BOOKS:
        if b in ratio:
            L.append(f"| {b} | {ratio[b]:.3f} | {pred['books'][b]['ratio_model_E_over_alone_gpu']:.3f} | "
                     f"{vs09.get(b, float('nan')):.3f} | {pred['books'][b]['ratio_model_E_over_plan_v09']:.3f} |")
    f3 = lambda d: "; ".join(f"{k} E {v['E_ratio']:.3f} T {v['T_ratio']:.3f}{'' if v['ok'] else ' OUT'}" for k, v in d.items())
    L += ["", "## Hypotheses", "",
          f"- **HC1 {yn(res['HC1']['holds'])}** (primary): book N3 ratio {res['HC1']['ratio']:.3f} (registered <= 0.96; predicted {res['HC1']['predicted']:.3f}).",
          f"- **HC2 {yn(res['HC2']['holds'])}** (primary): model_E / plan_v09 " + ", ".join(f"{b} {v:.3f}" for b, v in res["HC2"]["ratio"].items() if v) +
          " (registered <= 0.95 in both).",
          f"- **HC3 {yn(res['HC3']['holds'])}** (primary, {res['HC3']['n_ok']} of {res['HC3']['n']} within 0.90-1.10 in both): {f3(a3)}.",
          f"- **HC4 {yn(res['HC4']['holds'])}** (primary): book N2 ratio {res['HC4']['ratio']:.3f} (registered >= 0.95; predicted {res['HC4']['predicted']:.3f}).",
          f"- **HC5 {yn(res['HC5']['holds'])}** (primary): measured order {' < '.join(order)} (predicted {' < '.join(res['HC5']['predicted_order'])}).",
          f"- **HC6 {yn(res['HC6']['holds'])}** (secondary): book S ratio {res['HC6']['ratio']:.3f} (registered <= 0.85; predicted {res['HC6']['predicted']:.3f}); {f3(a6)}."]
    os.makedirs(OUT, exist_ok=True)
    json.dump(dict(E={f"{b}/{p}": v for (b, p), v in E.items()}, T={f"{b}/{p}": v for (b, p), v in T.items()}, ratio=ratio, vs_plan_v09=vs09, accuracy=acc, **res),
              open(os.path.join(OUT, "sched_v11.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, "sched_v11.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
