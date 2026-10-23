#!/usr/bin/env python3
"""analyse_sched.py - the scheduling results against addendum v0.8 (HS1-HS4 and the descriptive outputs).
Values are medians over the 5 repeats (two-server boundary, per pass). Output: results/analysis_sched/sched.md, .json
"""
import csv, json, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

RES = S.RES
OUT = os.path.join(RES, "analysis_sched")
PLANNED = ("alone_card", "alone_fcpu", "alone_gpu", "alone_scpu", "round_robin", "model_E", "model_T", "model_M", "model_dev")


def main():
    pred = json.load(open(os.path.join(RES, "predictions_sched", "predictions_sched.json")))
    rows = list(csv.DictReader(open(os.path.join(RES, "sched_summary.csv"))))
    by = defaultdict(dict)
    for r in rows:
        by[r["policy"]][int(r["rep"])] = r
    med = lambda p, k: st.median(float(x[k]) for x in by[p].values())
    E = {p: med(p, "E_pass_J") for p in by}
    T = {p: med(p, "T_pass_s") for p in by}
    res = {}
    L = ["# Scheduling (addendum v0.8): results against the registered prediction", "",
         "Two-server boundary, per pass; medians of 5 repeats (range in brackets).", "",
         "| Policy | T meas (s) | T pred | T ratio | E meas (J) | E pred | E ratio | E / model_E | frodo / Supermicro share |",
         "|---|---|---|---|---|---|---|---|---|"]
    for p in PLANNED + ("oracle",):
        pp = pred["predictions"].get(p)
        es = [float(x["E_pass_J"]) for x in by[p].values()]
        ts = [float(x["T_pass_s"]) for x in by[p].values()]
        fr = st.median(float(x["E_frodo_J"]) / (float(x["E_frodo_J"]) + float(x["E_smc_J"])) for x in by[p].values())
        L.append(f"| {p} | {T[p]:.4f} [{min(ts):.4f}-{max(ts):.4f}] | {pp['T_s']:.4f} | {T[p] / pp['T_s']:.3f} | {E[p]:.1f} [{min(es):.1f}-{max(es):.1f}] | "
                 f"{pp['E_J']:.1f} | {E[p] / pp['E_J']:.3f} | {E[p] / E['model_E']:.3f} | {fr:.2f} / {1 - fr:.2f} |" if pp else
                 f"| {p} | {T[p]:.4f} [{min(ts):.4f}-{max(ts):.4f}] | - | - | {E[p]:.1f} [{min(es):.1f}-{max(es):.1f}] | - | - | {E[p] / E['model_E']:.3f} | {fr:.2f} / {1 - fr:.2f} |")
    # HS1
    rr = [float(by["model_E"][k]["E_pass_J"]) / float(by["oracle"][k]["E_pass_J"]) for k in sorted(by["model_E"]) if k in by["oracle"]]
    res["HS1"] = dict(ratios=rr, median=st.median(rr), holds=st.median(rr) <= 1.05)
    # HS2
    calls = pred["hypotheses"]["HS2"]["calls"]
    hs2 = {p: dict(ratio=E[p] / E["model_E"], call=calls[p], ok=(E["model_E"] < E[p]) if calls[p] == "lower" else None) for p in calls}
    res["HS2"] = dict(per=hs2, holds=all(v["ok"] for v in hs2.values() if v["call"] == "lower"))
    # HS3
    hs3 = {p: dict(E_ratio=E[p] / pred["predictions"][p]["E_J"], T_ratio=T[p] / pred["predictions"][p]["T_s"]) for p in PLANNED}
    for v in hs3.values():
        v["ok"] = 0.85 <= v["E_ratio"] <= 1.15 and 0.85 <= v["T_ratio"] <= 1.15
    res["HS3"] = dict(per=hs3, holds=all(v["ok"] for v in hs3.values()), n_ok=sum(v["ok"] for v in hs3.values()))
    # HS4
    c4 = pred["hypotheses"]["HS4"]["calls"]
    hs4 = {p: dict(ratio=E[p] / E["model_E"], call=c4[p], ok=(E["model_E"] < E[p]) if c4[p] == "lower" else None) for p in c4}
    res["HS4"] = dict(per=hs4, holds=all(v["ok"] for v in hs4.values() if v["call"] == "lower"))
    L += ["", "## Hypotheses", "",
          f"- **HS1 {'holds' if res['HS1']['holds'] else 'fails'}**: model_E / oracle per repeat {', '.join(f'{x:.3f}' for x in rr)}; median {res['HS1']['median']:.3f} (registered <= 1.05).",
          f"- **HS2 {'holds' if res['HS2']['holds'] else 'fails'}**: " + "; ".join(f"{p} x{v['ratio']:.3f} ({v['call']}{'' if v['ok'] is None else (', ok' if v['ok'] else ', WRONG')})" for p, v in hs2.items()) + ".",
          f"- **HS3 {'holds' if res['HS3']['holds'] else 'fails'}** ({res['HS3']['n_ok']} of 9 policies within 0.85-1.15 in both): " +
          "; ".join(f"{p} E {v['E_ratio']:.3f} T {v['T_ratio']:.3f}{'' if v['ok'] else ' OUT'}" for p, v in hs3.items()) + ".",
          f"- **HS4 {'holds' if res['HS4']['holds'] else 'fails'}**: " + "; ".join(f"{p} x{v['ratio']:.3f} ({v['call']}{'' if v['ok'] is None else (', ok' if v['ok'] else ', WRONG')})" for p, v in hs4.items()) + "."]
    # descriptive: model_M deadline, oracle predicted, per-device busy / communication, accuracy
    D = pred["deadline_M_s"]
    mts = [float(x["T_pass_s"]) for x in by["model_M"].values()]
    L += ["", "## Descriptive", "",
          f"- model_M's deadline {D:.4f} s: met in {sum(t <= D for t in mts)} of {len(mts)} repeats (median pass {T['model_M']:.4f} s).",
          f"- Energy-makespan points (measured medians): " + ", ".join(f"{p} ({T[p]:.3f} s, {E[p]:.1f} J)" for p in ("model_T", "model_M", "model_E", "model_dev", "alone_gpu", "oracle")) + "."]
    # communication-to-computation ratio per remote device (gap / busy) from model_E and alone runs
    opts = S.book(); byid = {o["id"]: o for o in opts}
    cc = defaultdict(list)
    for p in ("model_E", "alone_gpu", "alone_scpu"):
        for k in by[p]:
            r = json.load(open(os.path.join(RES, "sched", f"rep{k}", p, "sched_result.json")))
            for ps in r["passes"][:50]:
                for d in ("gpu", "scpu"):
                    xs = [x for x in ps["options"] if x["dev"] == d]
                    if xs:
                        busy = sum(x["busy"] for x in xs)
                        cc[(p, d)].append((ps["dev_T"][d] - busy) / busy)
    L.append("- Communication + coordination / computation (device timeline minus part times, over part times), median: " +
             ", ".join(f"{d} in {p} {st.median(v):.2f}" for (p, d), v in sorted(cc.items())) + ".")
    err = {p: st.median(float(x["err_rms_over_eps"]) for x in by[p].values()) for p in by}
    L.append("- Accuracy, RMS of the options' worst-strike error / eps over all passes: " + ", ".join(f"{p} {v:.2f}" for p, v in err.items()) + ".")
    orc = []
    for k in sorted(by["oracle"]):
        pj = json.load(open(os.path.join(RES, "sched", f"rep{k}", "oracle.plan.json")))
        orc.append((pj["predicted_with_measured_costs"]["E_J"], pj["predicted_with_measured_costs"]["n"]))
    L.append("- Oracle's own predicted energy (corrected costs) per repeat: " + ", ".join(f"{e:.1f} J {list(n.values())}" for e, n in orc) + ".")
    # card break-even batch: options of one class per pass at which a design load pays off (energy), from measured change cost
    ch = pred["changes"]["card_other"]
    L.append(f"- Card design load: {ch['t']:.2f} s and {ch['dE']:.0f} J above idle, i.e. {ch['dE'] + pred['P_idle_total_W'] * ch['t']:.0f} J at the two-server boundary per load "
             f"(about {(ch['dE'] + pred['P_idle_total_W'] * ch['t']) / E['model_E']:.0f} passes of model_E).")
    os.makedirs(OUT, exist_ok=True)
    json.dump(dict(E=E, T=T, **res), open(os.path.join(OUT, "sched.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, "sched.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
