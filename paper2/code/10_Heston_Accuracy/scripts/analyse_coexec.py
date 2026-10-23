#!/usr/bin/env python3
"""analyse_coexec.py - co-execution results against the registered prediction (addendum v0.7, Sections 6-7).
Measured per run (median of 3 repeats): makespan (coordinator, per pricing run) and energy at the primary boundary
(window energy / runs). A deduplicated policy is looked up by its kind and CPU fraction (or K).
HC1: static policies with 0 < f < 1, large workloads: |measured/predicted - 1| <= 0.15 for makespan and energy.
HC2 (Supermicro): f_E = 0 for every kernel (prediction) and measured static_T energy > measured acc energy, every kernel.
HC3 (frodo): static_T makespan < acc for every kernel; energy lower / higher as called in the prediction.
HC4: dynamic/static_T makespan <= 1.10 on frodo and > 1.10 on the Supermicro, every kernel.
Usage: python3 scripts/analyse_coexec.py [smc] [frodo]  -> results/analysis_coexec/coexec_<machine>.md, .json
"""
import csv, json, os, statistics as st, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "analysis_coexec")
PRED = json.load(open(os.path.join(RES, "predictions_coexec", "predictions_coexec.json")))
KERNELS = [("I", "ft"), ("I", "qe"), ("II", "ft"), ("II", "qe"), ("III", "ft"), ("III", "qe"), ("P1", "ft"), ("P1", "qe"),
           ("B", "ft"), ("B", "qe"), ("A", "plain"), ("A", "cv")]


def pred(m, wl, case, sc, pol):
    return next(p for p in PRED["predictions"] if (p["machine"], p["workload"], p["case"], p["scheme"], p["policy"]) == (m, wl, case, sc, pol))


def meas(rows, m, wl, case, sc, pol):
    p = pred(m, wl, case, sc, pol)
    if pol == "dynamic":
        kind, f = "dynamic", None
    else:
        f = p["f_cpu"]
        kind = "acc" if f == 0 else ("cpu" if f == 1 else "static")
    for r in rows:
        if (r["case"], r["scheme"], r["workload"], r["kind"]) == (case, sc, wl, kind) and (kind != "static" or abs(float(r["fcpu"]) - round(f, 4)) < 1e-9):
            return r, p
    return None, p


def analyse(m):
    f = os.path.join(RES, f"coexec_{m}_summary.csv")
    rows = list(csv.DictReader(open(f)))
    res, L = {}, [f"# Co-execution on {'the Supermicro (V100 + socket 1)' if m == 'smc' else 'frodo (U50 + socket 0)'}: measured against the registered prediction", ""]
    L += ["Large workloads, primary boundary; T = makespan per run, E = energy per run; ratios measured/predicted.", "",
          "| Kernel | policy | f | T meas (s) | T pred | ratio | E meas (J) | E pred | ratio |", "|---|---|---|---|---|---|---|---|---|"]
    hc1, tab = [], {}
    for case, sc in KERNELS:
        for pol in ("acc", "static_E", "static_M", "static_T", "dynamic", "cpu"):
            try:
                r, p = meas(rows, m, "large", case, sc, pol)
            except StopIteration:
                continue
            if r is None:
                continue
            T, E = float(r["makespan_median_s"]), float(r["E_primary_J"])
            tab[(case, sc, pol)] = dict(T=T, E=E, Tp=p["T_s"], Ep=p["E_primary_J"], f=p["f_cpu"])
            L.append(f"| {case} {sc} | {pol} | {p['f_cpu']:.3f} | {T:.3f} | {p['T_s']:.3f} | {T / p['T_s']:.3f} | {E:.1f} | {p['E_primary_J']:.1f} | {E / p['E_primary_J']:.3f} |")
            if pol.startswith("static") and 0 < p["f_cpu"] < 1:
                hc1.append(dict(case=case, scheme=sc, policy=pol, T_ratio=T / p["T_s"], E_ratio=E / p["E_primary_J"]))
    res["HC1"] = dict(per=hc1, holds=all(abs(x["T_ratio"] - 1) <= 0.15 and abs(x["E_ratio"] - 1) <= 0.15 for x in hc1),
                      within=sum(abs(x["T_ratio"] - 1) <= 0.15 and abs(x["E_ratio"] - 1) <= 0.15 for x in hc1), n=len(hc1))
    L += ["", f"- **HC1 {'holds' if res['HC1']['holds'] else 'fails'} on this machine**: {res['HC1']['within']} of {res['HC1']['n']} static (0<f<1) runs within 15% in both makespan and energy."]
    if hc1:
        L.append(f"  makespan ratios {min(x['T_ratio'] for x in hc1):.3f}-{max(x['T_ratio'] for x in hc1):.3f}, energy ratios {min(x['E_ratio'] for x in hc1):.3f}-{max(x['E_ratio'] for x in hc1):.3f}")
    # HC2 / HC3
    if m == "smc":
        per = [dict(case=c, scheme=s, E_T_over_acc=tab[(c, s, "static_T")]["E"] / tab[(c, s, "acc")]["E"], T_T_over_acc=tab[(c, s, "static_T")]["T"] / tab[(c, s, "acc")]["T"])
               for c, s in KERNELS]
        res["HC2"] = dict(per=per, holds=all(x["E_T_over_acc"] > 1 for x in per))
        L += [f"- **HC2 {'holds' if res['HC2']['holds'] else 'fails'}**: static_T / GPU alone, energy {min(x['E_T_over_acc'] for x in per):.3f}-{max(x['E_T_over_acc'] for x in per):.3f}, "
              f"makespan {min(x['T_T_over_acc'] for x in per):.3f}-{max(x['T_T_over_acc'] for x in per):.3f} (f_E = 0 for every kernel by the prediction)"]
    else:
        calls = {x["case"] + " " + x["scheme"]: x["call"] for x in PRED["hypotheses"]["HC3"]["per_kernel"]}
        per = []
        for c, s in KERNELS:
            et, tt = tab[(c, s, "static_T")]["E"] / tab[(c, s, "acc")]["E"], tab[(c, s, "static_T")]["T"] / tab[(c, s, "acc")]["T"]
            call = calls[f"{c} {s}"]
            ok = tt < 1 and (call == "no call" or (call == "lower" and et < 1) or (call == "higher" and et > 1))
            per.append(dict(case=c, scheme=s, call=call, E_ratio=et, T_ratio=tt, ok=ok))
        res["HC3"] = dict(per=per, holds=all(x["ok"] for x in per))
        L += [f"- **HC3 {'holds' if res['HC3']['holds'] else 'fails'}**:"] + [f"  - {x['case']} {x['scheme']}: makespan {x['T_ratio']:.3f}, energy {x['E_ratio']:.3f} (called {x['call']}) {'ok' if x['ok'] else 'WRONG'}" for x in per]
    per4 = [dict(case=c, scheme=s, ratio=tab[(c, s, "dynamic")]["T"] / tab[(c, s, "static_T")]["T"]) for c, s in KERNELS]
    ok4 = [(x["ratio"] <= 1.10) == (m == "frodo") for x in per4]
    res["HC4_part"] = dict(per=per4, holds_on_machine=all(ok4))
    L += [f"- **HC4 ({m} part) {'holds' if all(ok4) else 'fails'}**: dynamic / static_T makespan {min(x['ratio'] for x in per4):.3f}-{max(x['ratio'] for x in per4):.3f} "
          f"(registered: {'<= 1.10' if m == 'frodo' else '> 1.10'} for every kernel; {sum(ok4)} of 12 as registered)"]
    # setting workloads and accuracy (descriptive)
    L += ["", "## Setting workloads (descriptive)", "", "| Kernel | acc T (ms) | best static T/acc | its E/acc | dynamic T/acc |", "|---|---|---|---|---|"]
    for c, s in KERNELS:
        rs = [r for r in rows if (r["case"], r["scheme"], r["workload"]) == (c, s, "setting")]
        a = next((r for r in rs if r["kind"] == "acc"), None)
        if not a:
            continue
        st_ = [r for r in rs if r["kind"] == "static"]
        best = min(st_, key=lambda r: float(r["makespan_median_s"])) if st_ else None
        dy = next((r for r in rs if r["kind"] == "dynamic"), None)
        Ta, Ea = float(a["makespan_median_s"]), float(a["E_primary_J"])
        L.append(f"| {c} {s} | {Ta * 1e3:.2f} | {float(best['makespan_median_s']) / Ta:.3f} | {float(best['E_primary_J']) / Ea:.3f} | "
                 f"{float(dy['makespan_median_s']) / Ta:.3f} |" if best and dy else f"| {c} {s} | {Ta * 1e3:.2f} | - | - | - |")
    ok_all = all(r["all_ok"] == "1" for r in rows)
    L += ["", f"- all runs ok (part counts and finite prices): {ok_all}"]
    os.makedirs(OUT, exist_ok=True)
    json.dump(res, open(os.path.join(OUT, f"coexec_{m}.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, f"coexec_{m}.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    for m in (sys.argv[1:] or ["smc", "frodo"]):
        if os.path.exists(os.path.join(RES, f"coexec_{m}_summary.csv")):
            analyse(m)
