#!/usr/bin/env python3
"""analyse_sched_v09.py - the v0.9 scheduling results against addendum v0.9 (HS5-HS10 and the descriptive outputs).
Values are medians over the 5 repeats, per pass; R1 and R2 at the two-server boundary, R3 at frodo's.
Input: results/sched_v09_summary.csv (scripts/sched_queue_v09.py --summary). Output: results/analysis_sched_v09/sched_v09.md, .json
"""
import csv, json, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

RES = S.RES
OUT = os.path.join(RES, "analysis_sched_v09")
PLANNED = ("alone_card", "alone_fcpu", "alone_gpu", "alone_scpu", "model_E", "model_T", "model_dev", "model_M",
           "alone_card_f", "alone_fcpu_f", "model_E_f")
ORACLE = {"R1": ("model_E", "oracle"), "R2": ("model_M", "oracle_M"), "R3": ("model_E_f", "oracle_f")}


def main():
    pred = json.load(open(os.path.join(RES, "predictions_sched_v09", "predictions_sched_v09.json")))
    H = pred["hypotheses"]
    rows = list(csv.DictReader(open(os.path.join(RES, "sched_v09_summary.csv"))))
    by = defaultdict(dict)
    for r in rows:
        by[r["policy"]][int(r["rep"])] = r
    val = lambda p, k: [float(x[k]) for _, x in sorted(by[p].items())]
    E = {p: st.median(val(p, "E_pass_J")) for p in by}
    T = {p: st.median(val(p, "T_pass_s")) for p in by}
    res = {}
    # HS5
    hs5 = {}
    for g, (m, o) in ORACLE.items():
        rr = [float(by[m][k]["E_pass_J"]) / float(by[o][k]["E_pass_J"]) for k in sorted(by[m]) if k in by[o]]
        hs5[g] = dict(ratios=rr, median=st.median(rr) if rr else None, ok=bool(rr) and st.median(rr) <= 1.05)
    res["HS5"] = dict(per=hs5, holds=all(v["ok"] for v in hs5.values()))
    # HS6
    r6 = E["model_E"] / E["alone_gpu"]
    res["HS6"] = dict(ratio=r6, holds=r6 <= 1.02, predicted=H["HS6"]["predicted_ratio"])
    # HS7
    D = H["HS7"]["deadline_s"]
    met = sum(t <= D for t in val("model_M", "T_pass_s"))
    c7 = H["HS7"]["call"] == "called"
    lower = E["model_M"] < E["model_T"]
    res["HS7"] = dict(deadline_s=D, met=met, of=len(by["model_M"]), E_T_over_M=E["model_T"] / E["model_M"], call=H["HS7"]["call"],
                      holds=met >= 4 and (lower if c7 else True))
    # HS8
    s8 = {k: 1 - E["model_E_f"] / E[k] for k in ("alone_card_f", "alone_fcpu_f")}
    res["HS8"] = dict(saving=s8, holds=all(v >= 0.05 for v in s8.values()), predicted=H["HS8"]["predicted_saving"])
    # HS9
    hs9 = {p: dict(E_ratio=E[p] / pred["predictions"][p]["E_J"], T_ratio=T[p] / pred["predictions"][p]["T_s"]) for p in PLANNED if p in by}
    for v in hs9.values():
        v["ok"] = 0.90 <= v["E_ratio"] <= 1.10 and 0.90 <= v["T_ratio"] <= 1.10
    res["HS9"] = dict(per=hs9, n_ok=sum(v["ok"] for v in hs9.values()), n=len(hs9), holds=all(v["ok"] for v in hs9.values()))
    # HS10
    c10 = H["HS10"]["call"] == "called"
    res["HS10"] = dict(ratio=E["model_T"] / E["model_E"], call=H["HS10"]["call"], holds=(E["model_E"] < E["model_T"]) if c10 else None)
    # descriptive: per-domain power of each job (frodo and Supermicro shares), accuracy, the oracles' own predictions
    L = ["# Scheduling, addendum v0.9: results against the registered prediction", "",
         "Per pass; medians of 5 repeats [range]. R1, R2: two-server boundary; R3: frodo (card + both sockets + DRAM).", "",
         "| Regime | Policy | T meas (s) | T pred | T ratio | E meas (J) | E pred | E ratio | error max / eps |", "|---|---|---|---|---|---|---|---|---|"]
    for p in PLANNED + ("oracle", "oracle_M", "oracle_f"):
        if p not in by:
            continue
        pp = pred["predictions"].get(p)
        es, ts = val(p, "E_pass_J"), val(p, "T_pass_s")
        err = max(val(p, "err_max_over_eps"))
        g = by[p][min(by[p])]["regime"]
        if pp:
            L.append(f"| {g} | {p} | {T[p]:.4f} [{min(ts):.4f}-{max(ts):.4f}] | {pp['T_s']:.4f} | {T[p] / pp['T_s']:.3f} | {E[p]:.2f} [{min(es):.2f}-{max(es):.2f}] | "
                     f"{pp['E_J']:.2f} | {E[p] / pp['E_J']:.3f} | {err:.2f} |")
        else:
            L.append(f"| {g} | {p} | {T[p]:.4f} [{min(ts):.4f}-{max(ts):.4f}] | - | - | {E[p]:.2f} [{min(es):.2f}-{max(es):.2f}] | - | - | {err:.2f} |")
    yn = lambda b: "holds" if b else "fails"
    L += ["", "## Hypotheses", "",
          "- **HS5 " + yn(res["HS5"]["holds"]) + "**: " + "; ".join(f"{g} {ORACLE[g][0]} / {ORACLE[g][1]} median {v['median']:.3f} ({', '.join(f'{x:.3f}' for x in v['ratios'])})"
                                                        for g, v in hs5.items() if v["median"] is not None) + " (registered <= 1.05).",
          f"- **HS6 {yn(res['HS6']['holds'])}**: R1 model_E / alone_gpu {r6:.3f} (registered <= 1.02; predicted {res['HS6']['predicted']:.3f}).",
          f"- **HS7 {yn(res['HS7']['holds'])}**: model_M met D = {D:.4f} s in {met} of {res['HS7']['of']} repeats; model_T / model_M energy {res['HS7']['E_T_over_M']:.3f} ({H['HS7']['call']}).",
          f"- **HS8 {yn(res['HS8']['holds'])}**: R3 model_E_f saves " + ", ".join(f"{100 * v:.1f}% against {k}" for k, v in s8.items()) + " (registered >= 5% each).",
          f"- **HS9 {yn(res['HS9']['holds'])}** ({res['HS9']['n_ok']} of {res['HS9']['n']} within 0.90-1.10 in both): " +
          "; ".join(f"{p} E {v['E_ratio']:.3f} T {v['T_ratio']:.3f}{'' if v['ok'] else ' OUT'}" for p, v in hs9.items()) + ".",
          f"- **HS10 {'no call' if res['HS10']['holds'] is None else yn(res['HS10']['holds'])}**: model_T / model_E energy {res['HS10']['ratio']:.3f} ({H['HS10']['call']})."]
    os.makedirs(OUT, exist_ok=True)
    json.dump(dict(E=E, T=T, **res), open(os.path.join(OUT, "sched_v09.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, "sched_v09.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
