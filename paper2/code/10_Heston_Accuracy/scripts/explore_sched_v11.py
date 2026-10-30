#!/usr/bin/env python3
"""explore_sched_v11.py - EXPLORATORY analysis of the v0.11 scheduling runs (not registered; written after the
registered analysis, scripts/analyse_sched_v11.py). It asks why the v0.11 model's own plans missed.

1. Per server: measured energy above idle (frodo, Supermicro) and pass-time idle energy, medians of the 5 registered
   repeats, against the v0.11 model's split of the same plan.
2. Per device: measured finish time in the pass (dev_T, median over the passes of repeat 1, warm-up excluded) against
   the model's busy time; the V100's stream decomposed into same-class intervals, class boundaries and kernel busy time.
3. The V100's per-class interval in each plan against the calibrated value.
4. The robustness repeat (rep 6, same date, about 6 h later; not registered) against the registered medians.
Output: results/analysis_sched_v11/explore_v11.md
  python3 scripts/explore_sched_v11.py
"""
import csv, json, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import calib_model_v11 as CM

RES = S.RES
BOOKS = {"N3": (10, 10, 0), "N2": (6, 12, 2), "S": (20, 0, 0)}
POL = ("alone_gpu", "model_E", "plan_v09")


def main():
    M, cal = CM.build()
    P = cal["P_idle_W"]
    pred = json.load(open(os.path.join(RES, "predictions_sched_v11", "predictions_sched_v11.json")))
    rows = list(csv.DictReader(open(os.path.join(RES, "sched_v11_summary.csv"))))
    tcal = {tuple(k.split("|")): v for k, v in cal["t_cal"].items()}
    L = ["# v0.11 scheduling: exploratory diagnosis (not registered)", "",
         "Written after the registered analysis (analysis_sched_v11/sched_v11.md). Nothing here changes a verdict.", "",
         "## 1. Energy per server (medians of the 5 registered repeats; J per pass)", "",
         "| Book | Policy | frodo above idle: meas / model | Supermicro above idle: meas / model | idle x T: meas / model |", "|---|---|---|---|---|"]
    for b, mix in BOOKS.items():
        opts = S.book(mix)
        for pol in POL:
            rs = [r for r in rows if r["book"] == b and r["policy"] == pol]
            T = st.median(float(r["T_pass_s"]) for r in rs)
            fr = st.median(float(r["E_frodo_J"]) / int(r["passes"]) - P["frodo"] * float(r["T_pass_s"]) for r in rs)
            sm = st.median(float(r["E_smc_J"]) / int(r["passes"]) - P["smc"] * float(r["T_pass_s"]) for r in rs)
            plan = {int(k): tuple(v) for k, v in pred["plans"][f"{b}/{pol}"].items()}
            ev = M.evaluate(plan, opts)
            loads = {d: M.device_load(d, [(o, plan[o["id"]][1]) for o in opts if plan[o["id"]][0] == d]) for d in S.DEVICES}
            msmc = sum(loads[d][1] - loads[d][2] for d in ("gpu", "scpu"))
            mfr = ev["E"] - M.Pidle * ev["T"] - msmc
            L.append(f"| {b} | {pol} | {fr:.2f} / {mfr:.2f} | {sm:.2f} / {msmc:.2f} | {M.Pidle * T:.2f} / {M.Pidle * ev['T']:.2f} |")
    L += ["", "## 2. Device finish times and the V100's stream (repeat 1, passes after the first; ms)", "",
          "| Book | Policy | dev_T measured | busy predicted | V100: options, classes, first result, same-class sum, boundaries, kernel busy |", "|---|---|---|---|---|"]
    gpu_iv = {}
    for b, mix in BOOKS.items():
        byid = {o["id"]: o for o in S.book(mix)}
        for pol in POL:
            r = json.load(open(os.path.join(RES, "sched_v11", b, "rep1", pol, "sched_result.json")))
            ps = r["passes"][1:]
            dT = {d: st.median(p["dev_T"][d] for p in ps) for d in ps[0]["dev_T"]}
            pb = pred["predictions"][f"{b}/{pol}"]["busy_s"]
            first, inner, bound, busy = [], [], [], []
            iv = defaultdict(list)
            for p in ps:
                xs = sorted([x for x in p["options"] if x["dev"] == "gpu"], key=lambda x: x["t1"])
                if not xs:
                    continue
                first.append(xs[0]["t1"] - xs[0]["t0"])
                ii = bi = 0.0
                for a, c in zip(xs, xs[1:]):
                    oa, oc = byid[a["id"]], byid[c["id"]]
                    if S.ckey(oa, a["m"]) == S.ckey(oc, c["m"]):
                        ii += c["t1"] - a["t1"]
                        iv[("gpu", oc["case"], oc["target"], c["m"])].append(c["t1"] - a["t1"])
                    else:
                        bi += c["t1"] - a["t1"]
                inner.append(ii); bound.append(bi); busy.append(sum(x["busy"] for x in xs))
            gpu_iv[(b, pol)] = {k: st.median(v) for k, v in iv.items()}
            ncls = len({S.ckey(byid[x["id"]], x["m"]) for x in ps[0]["options"] if x["dev"] == "gpu"})
            nopt = sum(1 for x in ps[0]["options"] if x["dev"] == "gpu")
            L.append(f"| {b} | {pol} | {', '.join(f'{d} {v * 1e3:.2f}' for d, v in dT.items())} | "
                     f"{', '.join(f'{d} {v * 1e3:.2f}' for d, v in pb.items() if v > 0)} | "
                     f"{nopt}, {ncls}, {st.median(first) * 1e3:.2f}, {st.median(inner) * 1e3:.2f}, {st.median(bound) * 1e3:.2f}, {st.median(busy) * 1e3:.2f} |")
    L += ["", "## 2b. The V100's time per class per pass (ms; mean over the passes of repeat 1; sum of same-class intervals)", ""]
    for b, mix in BOOKS.items():
        byid = {o["id"]: o for o in S.book(mix)}
        for pol in POL:
            r = json.load(open(os.path.join(RES, "sched_v11", b, "rep1", pol, "sched_result.json")))
            tot = defaultdict(list)
            for p in r["passes"][1:]:
                xs = sorted([x for x in p["options"] if x["dev"] == "gpu"], key=lambda x: x["t1"])
                per = defaultdict(float)
                for a, c in zip(xs, xs[1:]):
                    k = S.ckey(byid[c["id"]], c["m"])
                    if S.ckey(byid[a["id"]], a["m"]) == k:
                        per[k] += c["t1"] - a["t1"]
                for k, v in per.items():
                    tot[k].append(v)
            if tot:
                L.append(f"- **{b} {pol}:** " + "; ".join(f"{k[0]} {k[1]} {k[2]} steps {st.mean(v) * 1e3:.2f}" for k, v in sorted(tot.items(), key=lambda x: S.class_order(x[0]))) +
                         f"; total {sum(st.mean(v) for v in tot.values()) * 1e3:.2f}")
    L += ["", "## 3. The V100's interval between results of one class (ms; repeat 1): measured in the plan / calibrated", ""]
    for (b, pol), d in gpu_iv.items():
        if d:
            L.append(f"- **{b} {pol}:** " + "; ".join(f"{k[1]} {k[2]} {k[3]} {v * 1e3:.3f} / {tcal.get(k, M.C[k]['t']) * 1e3:.3f}" for k, v in sorted(d.items())))
    L += ["", "## 4. Robustness repeat (rep 6; 29 Oct 2026 21:08-21:25 UTC, about 6 h after the registered runs; not registered)", "",
          "| Book | Policy | rep 6 E (J) | registered median (J) | difference |", "|---|---|---|---|---|"]
    reg = {(r["book"], r["policy"]): [] for r in rows}
    for r in rows:
        reg[(r["book"], r["policy"])].append(float(r["E_pass_J"]))
    S.os.environ["SCHED_BOOK"] = ""
    import sched_queue_v11t as Q
    e6 = {}
    for b in BOOKS:
        Q.set_book(b)
        for pol in POL:
            d = os.path.join(RES, "sched_v11", b, "rep6", pol)
            if not os.path.exists(os.path.join(d, "DONE")):
                continue
            x = Q.record(d) if os.path.exists(os.path.join(d, "power.csv")) and _has_frodo_tools() else None
            if x is None:
                continue
            e6[(b, pol)] = x["E_pass_J"]
            m = st.median(reg[(b, pol)])
            L.append(f"| {b} | {pol} | {x['E_pass_J']:.2f} | {m:.2f} | {100 * (x['E_pass_J'] / m - 1):+.1f}% |")
    if e6:
        L += ["", "Ratios in rep 6: " + "; ".join(
            f"{b} model_E/alone_gpu {e6[(b, 'model_E')] / e6[(b, 'alone_gpu')]:.3f}, model_E/plan_v09 {e6[(b, 'model_E')] / e6[(b, 'plan_v09')]:.3f}"
            for b in BOOKS if (b, "model_E") in e6) + "."]
    os.makedirs(os.path.join(RES, "analysis_sched_v11"), exist_ok=True)
    open(os.path.join(RES, "analysis_sched_v11", "explore_v11.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


def _has_frodo_tools():
    try:
        import sched_inputs_frodo  # noqa: F401
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
