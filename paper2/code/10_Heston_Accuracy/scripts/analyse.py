#!/usr/bin/env python3
"""analyse.py - the main cases of paper 2 (protocol v0.4): measurements (results/phase5_summary_s<k>.csv) against the
registered prediction (results/predictions/predictions.json, commit 88b30f6).

Measured value of a combination = median over the sessions of the session medians.
  card:  energy per run = card rails + host socket (pkg0 + dram0), or + both packages and DRAM (server)
  CPU:   one socket (24 cores) or whole server (96 threads)
  steady state: energy (time) per path-step of the long ("ss") job of the same platform, scheme and step, times the
          setting's path-steps; for a setting that is itself long (no ss job), the setting's own run.
Tests: H2, H3, H4 (protocol Section 8) on the measurements; H5: energy within 20% (every combination), the predicted
winner of every case x target x boundary, every pairwise verdict (15 pairs of the 6 combinations), and the RMSE of every
setting within 1.1 x target at every strike (pooled runs of all sessions; the CPU programs' one-socket runs, since the
whole-server runs repeat the same seeds and pairs). RQ2: algorithm factor against platform factor (steady state, socket).
Output: results/analysis/analysis.json, analysis.md, measured_vs_predicted.csv.
(Rewritten on 19 Oct 2026 from the definitions of the 15 Oct analysis, whose script was lost with a sandbox reset; it
reproduces that analysis' numbers.)
Usage:  python3 scripts/analyse.py
"""
import csv
import glob
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hqlib as H

RES = os.path.join(H.ROOT, "results")
OUT = os.path.join(RES, "analysis")
CASES = ("I", "II", "III", "P1")
TARGETS = ("1%", "0.3%", "0.1%")
COMBOS = ("card_ft", "card_qe", "cpu_matched_ft", "cpu_matched_qe", "cpu_native_ft", "cpu_native_qe")
PLAT = (("card", "fpga", "card"), ("cpu_matched", "avx512", None), ("cpu_native", "native", None))
CFG = {"socket": "s0_c24", "server": "s01_t96"}


def measured(rows):
    sets, ss = {}, {}
    for r in rows:
        k = r["job"].split("/", 1)[1]
        if r["kind"] == "set":
            sets.setdefault((r["case"], r["target"], r["scheme"], r["impl"], r["cfg"]), []).append(r)
        else:
            ss.setdefault((r["case"], r["scheme"], int(r["steps"]), r["impl"], r["cfg"]), []).append(r)
    out = {}
    for case in CASES:
        for tgt in TARGETS:
            for bound in ("socket", "server"):
                for pname, impl, cfg in PLAT:
                    for sc in ("ft", "qe"):
                        c = cfg or CFG[bound]
                        rs = sets.get((case, tgt, sc, impl, c))
                        if not rs:
                            continue
                        W = float(rs[0]["path_steps"])
                        sr = ss.get((case, sc, int(rs[0]["steps"]), impl, c)) or rs
                        out[(case, tgt, bound, f"{pname}_{sc}")] = dict(
                            E_J=statistics.median(float(r[f"E_{bound}_per_run_J"]) for r in rs),
                            t_s=statistics.median(float(r["t_per_run_s"]) for r in rs),
                            E_ss_J=W * statistics.median(float(r[f"E_{bound}_per_run_J"]) / float(r["path_steps"]) for r in sr),
                            t_ss_s=W * statistics.median(float(r["t_per_run_s"]) / float(r["path_steps"]) for r in sr),
                            path_steps=W)
    return out


def rmse(rows):
    acc = {}
    for r in rows:
        if r["kind"] != "set" or (r["impl"] != "fpga" and r["cfg"] != "s0_c24"):
            continue
        plat = {"fpga": "card", "avx512": "cpu_matched", "native": "cpu_native"}[r["impl"]]
        d = acc.setdefault((r["case"], r["target"], plat, r["scheme"]), dict(n=0, s=[0.0] * 3))
        n = int(r["reps"])
        for j in range(3):
            d["s"][j] += float(r[f"err_rms{j}"]) ** 2 * n
        d["n"] += n
    out = []
    for (case, tgt, plat, sc), d in sorted(acc.items()):
        eps = float(tgt.rstrip("%")) / 100 * H.atm(case)
        v = [math.sqrt(x / d["n"]) / eps for x in d["s"]]
        out.append(dict(case=case, target=tgt, platform=plat, scheme=sc, runs=d["n"], rmse_over_eps=[round(x, 3) for x in v],
                        rmse_over_eps_max=max(v)))
    return out


def main():
    P = json.load(open(os.path.join(RES, "predictions", "predictions.json")))
    files = sorted(glob.glob(os.path.join(RES, "phase5_summary_s*.csv")))
    rows = [r for f in files for r in csv.DictReader(open(f))]
    M = measured(rows)
    preds = {(p["case"], p["target"], p["boundary"]): p for p in P["predictions"]}
    table, wins, wins_ss, pairs, pairs_ok = [], [], [], 0, 0
    for (case, tgt, bound) in [(c, t, b) for c in CASES for t in TARGETS for b in ("socket", "server")]:
        p = preds[(case, tgt, bound)]
        for cb in COMBOS:
            m, pr = M[(case, tgt, bound, cb)], p["combos"][cb]
            table.append(dict(case=case, target=tgt, boundary=bound, combo=cb, E_meas_J=m["E_J"], E_pred_J=pr["E_J"],
                              ratio=m["E_J"] / pr["E_J"], Ess_meas_J=m["E_ss_J"], Ess_pred_J=pr["E_ss_J"],
                              ratio_ss=m["E_ss_J"] / pr["E_ss_J"], t_meas_s=m["t_s"], t_pred_s=pr["t_s"]))
        mw = min(COMBOS, key=lambda c: M[(case, tgt, bound, c)]["E_J"])
        ms = min(COMBOS, key=lambda c: M[(case, tgt, bound, c)]["E_ss_J"])
        wins.append(dict(case=case, target=tgt, boundary=bound, measured=mw, predicted=p["winner_energy"], ok=mw == p["winner_energy"]))
        wins_ss.append(dict(case=case, target=tgt, boundary=bound, measured=ms, predicted=p["winner_energy_steady"], ok=ms == p["winner_energy_steady"]))
        for i in range(6):
            for j in range(i + 1, 6):
                a, b = COMBOS[i], COMBOS[j]
                pairs += 1
                pairs_ok += (p["combos"][a]["E_J"] < p["combos"][b]["E_J"]) == (M[(case, tgt, bound, a)]["E_J"] < M[(case, tgt, bound, b)]["E_J"])
    rel = [abs(t["ratio"] - 1) for t in table]
    rm = rmse(rows)

    def g(case, tgt, bound, cb, f):
        return M[(case, tgt, bound, cb)][f]

    hyp = {}
    h2 = {}
    for pn in ("card", "cpu_matched", "cpu_native"):
        for bound in ("socket", "server"):
            v = [(g(c, t, bound, f"{pn}_qe", "t_ss_s") / g(c, t, bound, f"{pn}_qe", "path_steps")) /
                 (g(c, t, bound, f"{pn}_ft", "t_ss_s") / g(c, t, bound, f"{pn}_ft", "path_steps")) for c in CASES for t in TARGETS]
            h2[f"{pn} {bound}"] = dict(median=statistics.median(v), min=min(v), max=max(v))
    hyp["H2"] = dict(per_platform=h2, holds=all(1.2 <= x["median"] <= 3 for x in h2.values()))
    h3 = {f"{c} {t}": dict(ratio=g(c, t, "socket", "card_ft", "E_J") / g(c, t, "socket", "cpu_matched_qe", "E_J"),
                           ratio_ss=g(c, t, "socket", "card_ft", "E_ss_J") / g(c, t, "socket", "cpu_matched_qe", "E_ss_J"))
          for c in ("I", "II", "III") for t in TARGETS}
    hyp["H3"] = dict(per_setting=h3, holds=all(v["ratio"] > 1 for v in h3.values()), holds_steady=all(v["ratio_ss"] > 1 for v in h3.values()))
    want = {"1%": False, "0.3%": True, "0.1%": False}
    h4 = {t: dict(card_ft_J=g("P1", t, "socket", "card_ft", "E_J"), cpu_qe_J=g("P1", t, "socket", "cpu_matched_qe", "E_J"),
                  card_wins=g("P1", t, "socket", "card_ft", "E_J") < g("P1", t, "socket", "cpu_matched_qe", "E_J"),
                  card_wins_ss=g("P1", t, "socket", "card_ft", "E_ss_J") < g("P1", t, "socket", "cpu_matched_qe", "E_ss_J")) for t in TARGETS}
    hyp["H4"] = dict(per_target=h4, holds=all(h4[t]["card_wins"] == want[t] for t in TARGETS),
                     holds_steady=all(h4[t]["card_wins_ss"] == want[t] for t in TARGETS))
    hyp["H5"] = dict(energy_within20=sum(x <= 0.2 for x in rel), n=len(rel), median_abs_err=statistics.median(rel), max_abs_err=max(rel),
                     card_within20=sum(1 for t in table if t["combo"].startswith("card") and abs(t["ratio"] - 1) <= 0.2),
                     steady_within20=sum(abs(t["ratio_ss"] - 1) <= 0.2 for t in table),
                     winners_ok=sum(w["ok"] for w in wins), winners=len(wins), winners_steady_ok=sum(w["ok"] for w in wins_ss),
                     pairs_ok=pairs_ok, pairs=pairs, rmse_ok=sum(r["rmse_over_eps_max"] <= 1.1 for r in rm), rmse_n=len(rm))
    rq2 = []
    for c in CASES:
        for t in TARGETS:
            best = lambda sc, pl: min(g(c, t, "socket", f"{p}_{sc}", "E_ss_J") for p in pl)
            rq2.append(dict(case=c, target=t, algorithm=best("ft", ("card", "cpu_matched", "cpu_native")) / best("qe", ("card", "cpu_matched", "cpu_native")),
                            platform_qe=best("qe", ("cpu_matched", "cpu_native")) / g(c, t, "socket", "card_qe", "E_ss_J"),
                            platform_ft=best("ft", ("cpu_matched", "cpu_native")) / g(c, t, "socket", "card_ft", "E_ss_J")))
    os.makedirs(OUT, exist_ok=True)
    json.dump(dict(sessions=[os.path.basename(f) for f in files], table=table, winners=wins, winners_steady=wins_ss,
                   rmse=rm, hypotheses=hyp, rq2=rq2), open(os.path.join(OUT, "analysis.json"), "w"), indent=1)
    with open(os.path.join(OUT, "measured_vs_predicted.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
        w.writeheader()
        w.writerows(table)
    h = hyp
    L = [f"# Paper 2 (main cases): measured against predicted ({', '.join(os.path.basename(f) for f in files)})", "",
         "## Energy per pricing run: measured / predicted", "",
         "| Case | Target | Boundary | " + " | ".join(COMBOS) + " | Winner measured (predicted) |", "|---|---|---|" + "---|" * 7]
    for w in wins:
        r = {t["combo"]: t["ratio"] for t in table if (t["case"], t["target"], t["boundary"]) == (w["case"], w["target"], w["boundary"])}
        L.append(f"| {w['case']} | {w['target']} | {w['boundary']} | " + " | ".join(f"{r[c]:.2f}" for c in COMBOS)
                 + f" | {w['measured']} ({'ok' if w['ok'] else 'MISS'}) |")
    L += ["", "## Hypotheses on the measurements", "",
          f"- **H2** (QE/FT cost per path-step 1.2–3 everywhere): {'holds' if h['H2']['holds'] else 'fails'}; "
          + "; ".join(f"{k} {v['median']:.2f}" for k, v in h["H2"]["per_platform"].items()),
          f"- **H3** (I–III: CPU socket QE < card FT in energy): {'holds' if h['H3']['holds'] else 'fails'} (steady state: "
          f"{'holds' if h['H3']['holds_steady'] else 'fails'}); card-FT / CPU-QE: " + ", ".join(f"{k} {v['ratio']:.2f}" for k, v in h["H3"]["per_setting"].items()),
          f"- **H4** (P1: card FT wins at 0.3% only): {'holds' if h['H4']['holds'] else 'fails'} (steady state: {'holds' if h['H4']['holds_steady'] else 'fails'}); "
          + ", ".join(f"{t}: card {v['card_ft_J']:.3g} J vs CPU {v['cpu_qe_J']:.3g} J" for t, v in h["H4"]["per_target"].items()),
          f"- **H5** (model): energy within 20% in {h['H5']['energy_within20']} of {h['H5']['n']} (card {h['H5']['card_within20']} of 48; median |error| "
          f"{h['H5']['median_abs_err'] * 100:.0f}%, max {h['H5']['max_abs_err'] * 100:.0f}%; steady state {h['H5']['steady_within20']} of {h['H5']['n']}); predicted winner right in "
          f"{h['H5']['winners_ok']} of {h['H5']['winners']} (steady state {h['H5']['winners_steady_ok']}); pairwise verdicts right in {h['H5']['pairs_ok']} of "
          f"{h['H5']['pairs']}; RMSE within 1.1 × target in {h['H5']['rmse_ok']} of {h['H5']['rmse_n']}.", ""]
    for r in rm:
        if r["rmse_over_eps_max"] > 1.1:
            L.append(f"  - RMSE miss: {r['case']} {r['scheme']} {r['target']} {r['platform']}: {r['rmse_over_eps']} ({r['runs']} runs)")
    L += ["", "## RQ2 (steady state, socket): algorithm factor against platform factor", "",
          "| Case | Target | Algorithm factor (FT/QE, best platform each) | Platform factor QE (best CPU / card) | Platform factor FT |", "|---|---|---|---|---|"]
    for r in rq2:
        L.append(f"| {r['case']} | {r['target']} | {r['algorithm']:.1f} | {r['platform_qe']:.2f} | {r['platform_ft']:.2f} |")
    md = "\n".join(L) + "\n"
    open(os.path.join(OUT, "analysis.md"), "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
