#!/usr/bin/env python3
"""analyse_s.py - the secondary cases (protocol v0.5): measurements (results/phase5s_summary_s<k>.csv) against the
registered prediction (results/predictions_s/predictions_s.json, commit c2f34c2), both variants.

Measured value of a combination = median over the sessions of the session medians (as the main-case analysis).
  card:  energy per run = card rails + host socket (pkg0 + dram0) or + both packages and DRAM (server)
  CPU:   one socket (24 cores) or whole server (96 threads)
  steady state: energy per path-step of the long ("ss") job of the same platform, scheme and step, times the
          setting's path-steps; for a setting that is itself long (no ss job), the setting's own run.
Tests:
  H5 (on variant "same"; "informed" reported alongside): energy within 20% for every combination; the predicted winner
     of every case x target x boundary; every pairwise verdict (15 pairs of the 6 combinations); the RMSE of every
     setting within 1.1 x target at every strike (pooled runs of all sessions, each platform; the CPU programs'
     one-socket runs, since the whole-server runs repeat the same seeds and pairs).
  HB2, HA2, HA3 on the measurements, by the rules registered in the prediction file.
Output: results/analysis_s/analysis_s.json, analysis_s.md, measured_vs_predicted_s.csv.
Usage:  python3 scripts/analyse_s.py
"""
import csv
import glob
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hslib as H

ROOT = H.ROOT
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "analysis_s")
TARGETS = ("1%", "0.3%", "0.1%")
SCHEMES = {"B": ("ft", "qe"), "A": ("plain", "cv")}
PLAT = (("card", "fpga", "card"), ("cpu_matched", "avx512", None), ("cpu_native", "native", None))
CFG = {"socket": "s0_c24", "server": "s01_t96"}


def load_sessions():
    files = sorted(glob.glob(os.path.join(RES, "phase5s_summary_s*.csv")))
    if not files:
        sys.exit("no results/phase5s_summary_s*.csv")
    rows = [r for f in files for r in csv.DictReader(open(f))]
    return rows, [os.path.basename(f) for f in files]


def med_sessions(rows, pred):
    v = [r for r in rows if pred(r)]
    return v


def measured(rows):
    """dict keyed (case, target, boundary, combo) -> dict(E_J, E_ss_J, t_s, sessions)"""
    def key_of(r):
        return r["job"].split("/", 1)[1].rsplit("/", 0)[0]       # without the session prefix

    grp = {}
    for r in rows:
        grp.setdefault(key_of(r), []).append(r)
    out = {}
    sets = {}
    for k, rs in grp.items():
        r0 = rs[0]
        if r0["kind"] == "set":
            sets[(r0["case"], r0["target"], r0["scheme"], r0["impl"], r0["cfg"])] = rs
    ss = {}
    for k, rs in grp.items():
        r0 = rs[0]
        if r0["kind"] == "ss":
            ss[(r0["case"], r0["scheme"], int(r0["steps"]), r0["impl"], r0["cfg"])] = rs
    for case, schemes in SCHEMES.items():
        for tgt in TARGETS:
            for bound in ("socket", "server"):
                for pname, impl, cfg in PLAT:
                    for sc in schemes:
                        c = cfg or CFG[bound]
                        rs = sets.get((case, tgt, sc, impl, c))
                        if not rs:
                            continue
                        e = statistics.median(float(r[f"E_{bound}_per_run_J"]) for r in rs)
                        t = statistics.median(float(r["t_per_run_s"]) for r in rs)
                        W = float(rs[0]["path_steps"])
                        sr = ss.get((case, sc, int(rs[0]["steps"]), impl, c)) or rs
                        eps = statistics.median(float(r[f"E_{bound}_per_run_J"]) / float(r["path_steps"]) for r in sr)
                        out[(case, tgt, bound, f"{pname}_{sc}")] = dict(E_J=e, t_s=t, E_ss_J=eps * W, sessions=len(rs))
    return out


def rmse(rows):
    """per (case, target, platform, scheme): RMSE / eps per strike from all runs (pooled sessions: mean of squares)"""
    out = {}
    for r in rows:
        if r["kind"] != "set":
            continue
        plat = {"fpga": "card", "avx512": "cpu_matched", "native": "cpu_native"}[r["impl"]]
        if r["impl"] != "fpga" and r["cfg"] != "s0_c24":
            continue      # the 96-thread runs use the same seeds and pairs: the same prices, not new samples
        k = (r["case"], r["target"], plat, r["scheme"], r["cfg"])
        n = int(r["reps"])
        d = out.setdefault(k, dict(n=0, s=[0.0] * 3))
        for j in range(3):
            if r.get(f"err_rms{j}"):
                d["s"][j] += float(r[f"err_rms{j}"]) ** 2 * n
        d["n"] += n
    res = {}
    for k, d in out.items():
        eps = float(k[1].rstrip("%")) / 100 * H.atm(k[0])
        res[k] = dict(n=d["n"], rmse_over_eps=[math.sqrt(x / d["n"]) / eps for x in d["s"]])
    return res


def analyse_variant(P, M, rows):
    preds = {(p["case"], p["target"], p["boundary"]): p for p in P["predictions"]}
    rel, table, wins, wins_ss, pairs, pairs_ok = [], [], [], [], 0, 0
    for (case, tgt, bound), p in preds.items():
        combos = list(p["combos"])
        for cb in combos:
            m = M.get((case, tgt, bound, cb))
            if not m:
                continue
            pr = p["combos"][cb]
            row = dict(case=case, target=tgt, boundary=bound, combo=cb, E_meas_J=m["E_J"], E_pred_J=pr["E_J"],
                       ratio=m["E_J"] / pr["E_J"], Ess_meas_J=m["E_ss_J"], Ess_pred_J=pr["E_ss_J"],
                       ratio_ss=m["E_ss_J"] / pr["E_ss_J"], t_meas_s=m["t_s"], t_pred_s=pr["t_s"])
            table.append(row)
            rel.append(row)
        have = [cb for cb in combos if (case, tgt, bound, cb) in M]
        if len(have) == len(combos):
            mw = min(combos, key=lambda c: M[(case, tgt, bound, c)]["E_J"])
            ms = min(combos, key=lambda c: M[(case, tgt, bound, c)]["E_ss_J"])
            wins.append(dict(case=case, target=tgt, boundary=bound, measured=mw, predicted=p["winner_energy"], ok=mw == p["winner_energy"]))
            wins_ss.append(dict(case=case, target=tgt, boundary=bound, measured=ms, predicted=p["winner_energy_steady"],
                                ok=ms == p["winner_energy_steady"]))
            for i in range(len(combos)):
                for j in range(i + 1, len(combos)):
                    a, b = combos[i], combos[j]
                    pv = p["combos"][a]["E_J"] < p["combos"][b]["E_J"]
                    mv = M[(case, tgt, bound, a)]["E_J"] < M[(case, tgt, bound, b)]["E_J"]
                    pairs += 1
                    pairs_ok += pv == mv
    within = [abs(r["ratio"] - 1) <= 0.2 for r in rel]
    return dict(table=table, n=len(rel), within20=sum(within), median_abs_err=statistics.median(abs(r["ratio"] - 1) for r in rel) if rel else None,
                max_abs_err=max(abs(r["ratio"] - 1) for r in rel) if rel else None,
                within20_steady=sum(abs(r["ratio_ss"] - 1) <= 0.2 for r in rel),
                card_within20=sum(1 for r, w in zip(rel, within) if w and r["combo"].startswith("card")),
                card_n=sum(1 for r in rel if r["combo"].startswith("card")),
                winners=wins, winners_ok=sum(w["ok"] for w in wins), winners_steady=wins_ss,
                winners_steady_ok=sum(w["ok"] for w in wins_ss), pairs=pairs, pairs_ok=pairs_ok)


def hypotheses_measured(M):
    def g(case, tgt, bound, cb, f):
        return M[(case, tgt, bound, cb)][f]

    def best(case, tgt, bound, sc, f, plats=("card", "cpu_matched", "cpu_native")):
        return min(g(case, tgt, bound, f"{p}_{sc}", f) for p in plats)
    out = {}
    try:
        hb2 = {t: best("B", t, "socket", "ft", "E_ss_J") / best("B", t, "socket", "qe", "E_ss_J") for t in TARGETS}
        out["HB2"] = dict(algorithm_factor_steady_socket=hb2, holds=all(1 <= v <= 10 for v in hb2.values()))
    except KeyError:
        pass
    try:
        ha2 = {}
        for t in TARGETS:
            for bound in ("socket", "server"):
                for f, o in (("E_J", "primary"), ("E_ss_J", "steady")):
                    alg = best("A", t, bound, "plain", f) / best("A", t, bound, "cv", f)
                    pf = {sc: best("A", t, bound, sc, f, ("cpu_matched", "cpu_native")) / g("A", t, bound, f"card_{sc}", f) for sc in ("plain", "cv")}
                    mag = max(max(v, 1 / v) for v in pf.values())
                    ha2[f"{t} {bound} {o}"] = dict(algorithm_factor=alg, platform_gain=mag, holds=alg > mag)
            ha2[f"{t} socket CPU-CV < card-plain"] = dict(holds=g("A", t, "socket", "cpu_matched_cv", "E_J") < g("A", t, "socket", "card_plain", "E_J"))
        out["HA2"] = dict(per=ha2, holds=all(v["holds"] for v in ha2.values()))
        ha3 = {t: dict(ratio_cpu_over_card=best("A", t, "socket", "cv", "E_ss_J", ("cpu_matched", "cpu_native")) / g("A", t, "socket", "card_cv", "E_ss_J"))
               for t in TARGETS}
        for v in ha3.values():
            v["holds"] = v["ratio_cpu_over_card"] > 1
        out["HA3"] = dict(per_target=ha3, holds=all(v["holds"] for v in ha3.values()))
    except KeyError:
        pass
    return out


def main():
    pj = os.path.join(RES, "predictions_s", "predictions_s.json")
    P = json.load(open(pj))
    rows, files = load_sessions()
    M = measured(rows)
    R = rmse(rows)
    V = {v: analyse_variant(P["variants"][v], M, rows) for v in ("same", "informed")}
    HM = hypotheses_measured(M)
    HP = P["variants"]["same"]["hypotheses"]
    rm_rows = []
    for (case, tgt, plat, sc, cfg), d in sorted(R.items()):
        rm_rows.append(dict(case=case, target=tgt, platform=plat, scheme=sc, cfg=cfg, runs=d["n"],
                            rmse_over_eps_max=max(d["rmse_over_eps"]), rmse_over_eps=[round(x, 3) for x in d["rmse_over_eps"]]))
    rmse_ok = sum(r["rmse_over_eps_max"] <= 1.1 for r in rm_rows)
    os.makedirs(OUT, exist_ok=True)
    out = dict(sessions=files, prediction_created=P["created_utc"], variants={k: {kk: vv for kk, vv in v.items() if kk != "table"} for k, v in V.items()},
               rmse=rm_rows, rmse_within_1_1=rmse_ok, hypotheses_measured=HM)
    json.dump(out, open(os.path.join(OUT, "analysis_s.json"), "w"), indent=1, default=str)
    with open(os.path.join(OUT, "measured_vs_predicted_s.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["variant"] + list(V["same"]["table"][0].keys()))
        w.writeheader()
        for v in ("same", "informed"):
            for r in V[v]["table"]:
                w.writerow(dict(variant=v, **r))
    L = [f"# Paper 2, secondary cases: measured against predicted ({', '.join(files)})", "",
         "## Energy per pricing run: measured / predicted (variant same; informed in brackets)", "",
         "| Case | Target | Boundary | " + " | ".join(c for c in ("card", "matched", "native") for _ in (0, 1)) + " | Winner measured (predicted) |", "|---|---|---|" + "---|" * 7]
    for case in ("B", "A"):
        a, b = SCHEMES[case]
        for tgt in TARGETS:
            for bound in ("socket", "server"):
                cells = []
                for pn in ("card", "cpu_matched", "cpu_native"):
                    for sc in (a, b):
                        cb = f"{pn}_{sc}"
                        rs = next((r for r in V["same"]["table"] if (r["case"], r["target"], r["boundary"], r["combo"]) == (case, tgt, bound, cb)), None)
                        ri = next((r for r in V["informed"]["table"] if (r["case"], r["target"], r["boundary"], r["combo"]) == (case, tgt, bound, cb)), None)
                        cells.append(f"{sc} {rs['ratio']:.2f} ({ri['ratio']:.2f})" if rs else "–")
                w = next((x for x in V["same"]["winners"] if (x["case"], x["target"], x["boundary"]) == (case, tgt, bound)), None)
                L.append(f"| {case} | {tgt} | {bound} | " + " | ".join(cells) + f" | {w['measured'] if w else '–'} ({'ok' if w and w['ok'] else 'MISS' if w else '–'}) |")
    L.append("")
    for v in ("same", "informed"):
        x = V[v]
        L += [f"## H5 ({v})", "",
              f"- Energy within 20%: {x['within20']} of {x['n']} (card {x['card_within20']} of {x['card_n']}); median |error| {x['median_abs_err'] * 100:.1f}%, max {x['max_abs_err'] * 100:.0f}%. Steady state within 20%: {x['within20_steady']} of {x['n']}.",
              f"- Predicted winner right: {x['winners_ok']} of {len(x['winners'])}; steady state {x['winners_steady_ok']} of {len(x['winners_steady'])}.",
              f"- Pairwise verdicts right: {x['pairs_ok']} of {x['pairs']}.", ""]
    L += [f"- RMSE within 1.1 × target (all strikes): {rmse_ok} of {len(rm_rows)} platform-settings.", ""]
    bad = [r for r in rm_rows if r["rmse_over_eps_max"] > 1.1]
    for r in bad:
        L.append(f"  - {r['case']} {r['scheme']} {r['target']} {r['platform']} {r['cfg']}: RMSE/eps {r['rmse_over_eps']} ({r['runs']} runs)")
    L += ["", "## Hypotheses: measured (predicted, variant same)", ""]
    if "HB2" in HM:
        L.append(f"- **HB2** {'holds' if HM['HB2']['holds'] else 'fails'} (predicted {'hold' if HP['HB2']['holds'] else 'fail'}): "
                 + ", ".join(f"{t} {v:.2f}" for t, v in HM["HB2"]["algorithm_factor_steady_socket"].items()))
    if "HA2" in HM:
        L.append(f"- **HA2** {'holds' if HM['HA2']['holds'] else 'fails'} (predicted {'hold' if HP['HA2']['holds'] else 'fail'}): "
                 + "; ".join(f"{k} {v['algorithm_factor']:.0f} vs {v['platform_gain']:.2f}" for k, v in HM["HA2"]["per"].items() if "algorithm_factor" in v and "socket" in k)
                 + "; failing: " + (", ".join(k for k, v in HM["HA2"]["per"].items() if not v["holds"]) or "none"))
    if "HA3" in HM:
        L.append(f"- **HA3** {'holds' if HM['HA3']['holds'] else 'fails'} (predicted {'hold' if HP['HA3']['holds'] else 'fail'}): "
                 + ", ".join(f"{t} best CPU / card {v['ratio_cpu_over_card']:.2f}" for t, v in HM["HA3"]["per_target"].items()))
    L += ["", "HB1 and HA1 are calibration results (registered: hold)."]
    md = "\n".join(L) + "\n"
    open(os.path.join(OUT, "analysis_s.md"), "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
