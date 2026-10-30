#!/usr/bin/env python3
"""Addendum v0.9, Part B: the registered tests on the RTX 4070 Ti SUPER (HR1, HR2, HR3 and the open question).

Measured steady state per (case, method, steps): the median over both sessions' block-wait runs (5 repeats each) of
energy per path-step at GPU + CPU package (E_gpu_pkg_per_run_J / path_steps) and time per path-step. A setting's
steady-state energy is its path-steps W times that. Predictions: results/predictions_gpu_4070tis/predictions_gpu_4070tis.json
(SHA-256 4bee4fe5...). The card, CPU and V100 energies are the measured ones the prediction used (analysis_gpu.json).

  python3 scripts/analyse_rtx_v09.py   -> results/analysis_rtx_v09/rtx_v09.{md,json}
"""
import csv, hashlib, json, os, statistics, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "analysis_rtx_v09")
PRED = os.path.join(RES, "predictions_gpu_4070tis", "predictions_gpu_4070tis.json")
PRED_SHA = "4bee4fe52c25385cd05cfad1c4c7c1c86a0ed91a555e9dfa76a84f8da3ba3eb5"
H_FRODO = 49.151
CASES = ("I", "II", "III", "P1", "B", "A")
TARGETS = ("1%", "0.3%", "0.1%")
METH = {c: ("ft", "qe") for c in CASES[:5]}
METH["A"] = ("plain", "cv")
CALL, OQ_BAND, TOL = 1.3, 1.05, 0.30
med = statistics.median


def load_meas(sessions=(1, 2)):
    G = {}
    for s in sessions:
        for r in csv.DictReader(open(os.path.join(RES, f"phase5g4060_summary_s{s}.csv"))):
            assert r["mode"] == "block" and r["kind"] == "ss" and r["all_ok"] == "1"
            G.setdefault((r["case"], r["scheme"], int(r["steps"])), []).append((s, r))
    M = {}
    for k, g in G.items():
        f = lambda key: [float(r[key]) / float(r["path_steps"]) for _, r in g]
        per_s = {s: med([float(r["E_gpu_pkg_per_run_J"]) / float(r["path_steps"]) for t, r in g if t == s]) for s in sessions}
        M[k] = dict(e=med(f("E_gpu_pkg_per_run_J")), e_gpu=med(f("E_gpu_per_run_J")), tau=med(f("t_per_run_s")),
                    P_gpu=med(float(r["P_gpu_W"]) for _, r in g), P_pkg=med(float(r["P_pkg_W"]) for _, r in g),
                    n=len(g), per_session=per_s)
    return M


def main():
    sessions = tuple(int(x) for x in sys.argv[1:]) or (1, 2)
    assert hashlib.sha256(open(PRED, "rb").read()).hexdigest() == PRED_SHA, "prediction file changed"
    D = json.load(open(PRED))
    rows = D["predictions"]
    M = load_meas(sessions)
    WIN = {(w["case"], w["target"], w["boundary"]): w for w in json.load(open(os.path.join(RES, "analysis_gpu", "analysis_gpu.json")))["winners"]}
    Wset = {(r["case"], r["target"], r["scheme"]): r["W"] for r in rows}
    row = lambda c, t, m: next(r for r in rows if r["case"] == c and r["target"] == t and r["scheme"] == m)

    # ---------------------------------------------------------------- HR1 and HR3, per setting
    per = []
    for r in rows:
        k = (r["case"], r["scheme"], r["M"])
        proxy = k not in M      # II ft 0.1% (M 30720) has >= 1e11 path-steps: no steady-state run; the same kernel's largest M stands in
        if proxy:
            k = max((x for x in M if x[:2] == k[:2]), key=lambda x: x[2])
        m = M[k]
        Ess = r["W"] * m["e"]
        per.append(dict(case=r["case"], target=r["target"], scheme=r["scheme"], M=r["M"], run_M=k[2], proxy=proxy, bind_ss=r["bind_ss"],
                        Ess_meas_J=Ess, Ess_pred_J=r["Ess_J"]["socket"], e_ratio=Ess / r["Ess_J"]["socket"],
                        t_ratio=m["tau"] * r["rate_ss_path_steps_per_s"],
                        P_gpu_meas=m["P_gpu"], P_gpu_pred=r["P_gpu_ss_W"], P_ratio=m["P_gpu"] / r["P_gpu_ss_W"],
                        Ess_frodo_socket_J=r["W"] * (m["e_gpu"] + H_FRODO * m["tau"])))
    n1 = sum(abs(x["e_ratio"] - 1) <= TOL for x in per)
    n3 = sum(abs(x["t_ratio"] - 1) <= TOL for x in per)
    er, tr = [x["e_ratio"] for x in per], [x["t_ratio"] for x in per]
    HR1 = dict(within=n1, of=36, needed=30, holds=n1 >= 30, median=med(er), min=min(er), max=max(er))
    HR3 = dict(within=n3, of=36, needed=30, holds=n3 >= 30, median=med(tr), min=min(tr), max=max(tr))

    # ---------------------------------------------------------------- HR2: Proposition 2 with the measured RTX energies
    plats = ("card", "cpu_matched", "cpu_native", "rtx")
    pred_p2 = {(p["case"], p["target"]): p for p in D["hypotheses"]["HR2"]["per_pair"]}
    Pm = {(x["case"], x["target"], x["scheme"]): x for x in per}
    p2 = []
    for c in CASES:
        m1, m2 = METH[c]
        w01 = WIN[(c, "0.1%", "socket")]
        E01 = {(p, m): w01["Ess"][f"{p}_{m}"] for p in plats[:3] for m in (m1, m2)}
        for m in (m1, m2):
            E01[("rtx", m)] = Pm[(c, "0.1%", m)]["Ess_meas_J"]
        e = {(p, m): E01[(p, m)] / Wset[(c, "0.1%", m)] for p in plats for m in (m1, m2)}
        bp = min(plats, key=lambda p: e[(p, m2)])
        rp = min((p for p in plats if p != bp), key=lambda p: e[(p, m2)])
        kappa, G = e[(bp, m2)] / e[(bp, m1)], e[(rp, m2)] / e[(bp, m2)]
        for t in TARGETS:
            Mg = Wset[(c, t, m1)] / Wset[(c, t, m2)]
            mg = Mg / (kappa * G)
            pp = pred_p2[(c, t)]
            p2.append(dict(case=c, target=t, M=Mg, kappa=kappa, G=G, margin=mg, best=bp, runner_up=rp, meas_holds=mg > 1,
                           pred_margin=pp["margin"], pred_holds=pp["predicted_holds"], called=pp["called"],
                           agree=(mg > 1) == pp["predicted_holds"]))
    called = [x for x in p2 if x["called"]]
    HR2 = dict(called=len(called), agree=sum(x["agree"] for x in called), holds=all(x["agree"] for x in called),
               inside_band_agree=sum(x["agree"] for x in p2 if not x["called"]), inside_band=sum(not x["called"] for x in p2))

    # ---------------------------------------------------------------- open question: RTX (measured) against the V100 (measured)
    pred_oq = {(x["case"], x["target"]): x for x in D["open_question"]}
    oq = []
    for c in CASES:
        for t in TARGETS:
            po = pred_oq[(c, t)]
            rtx = min(Pm[(c, t, m)]["Ess_meas_J"] for m in METH[c])
            mr = min(METH[c], key=lambda m: Pm[(c, t, m)]["Ess_meas_J"])
            rtx_f = min(Pm[(c, t, m)]["Ess_frodo_socket_J"] for m in METH[c])
            oq.append(dict(case=c, target=t, method_rtx=mr, method_v100=po["method_v100"], Ess_v100_J=po["Ess_v100_J"], Ess_rtx_J=rtx,
                           ratio=rtx / po["Ess_v100_J"], pred_ratio=po["rtx_over_v100"], rtx_less=rtx < po["Ess_v100_J"],
                           ratio_frodo=rtx_f / po["Ess_v100_frodo_socket_J"], pred_ratio_frodo=po["rtx_over_v100_frodo_socket"]))
    OQ = dict(rtx_less=sum(x["rtx_less"] for x in oq), predicted_less=sum(x["pred_ratio"] < 1 for x in oq),
              rtx_less_frodo=sum(x["ratio_frodo"] < 1 for x in oq), predicted_less_frodo=sum(x["pred_ratio_frodo"] < 1 for x in oq))

    # ---------------------------------------------------------------- winners with the measured RTX (steady state, socket)
    win = []
    for c in CASES:
        for t in TARGETS:
            w = WIN[(c, t, "socket")]
            cand = {f"{p}_{m}": w["Ess"][f"{p}_{m}"] for p in ("card", "cpu_matched", "cpu_native", "gpu") for m in METH[c]}
            cand.update({f"rtx_{m}": Pm[(c, t, m)]["Ess_meas_J"] for m in METH[c]})
            win.append(dict(case=c, target=t, winner=min(cand, key=cand.get),
                            winner_no_v100=min((k for k in cand if not k.startswith("gpu_")), key=cand.get)))

    # ---------------------------------------------------------------- session agreement
    sess = {f"{k[0]}_{k[1]}_{k[2]}": v["per_session"][2] / v["per_session"][1] for k, v in M.items()} if sessions == (1, 2) else {}

    doc = dict(prediction_sha256=PRED_SHA, sessions=sessions, HR1=HR1, HR2=HR2, HR3=HR3, open_question=OQ, per_setting=per,
               prop2=p2, oq=oq, winners_steady_socket=win, session2_over_session1_energy=sess,
               steady_keys={f"{k[0]}_{k[1]}_{k[2]}": v for k, v in M.items()})
    os.makedirs(OUT, exist_ok=True)
    json.dump(doc, open(os.path.join(OUT, "rtx_v09.json"), "w"), indent=1, default=float)

    yn = lambda b: "**holds**" if b else "**fails**"
    L = ["# RTX 4070 Ti SUPER: the registered tests (addendum v0.9, Part B)", "",
         f"Sessions {', '.join(map(str, sessions))}; block wait; 29 steady-state runs x 5 repeats per session; SM locked at 2,340 MHz. "
         f"Measured value per run key: the median over all repeats. II FT 0.1% (M 30,720, over 1e11 path-steps, so no steady-state run of its own) uses the M 7,680 run, as marked. Prediction SHA-256 {PRED_SHA[:8]}.", "",
         "## Verdicts", "",
         f"- **HR1 (energy):** {n1} of 36 within 30% (needs 30): {yn(HR1['holds'])}. Median measured / predicted {HR1['median']:.3f}, range {HR1['min']:.3f}-{HR1['max']:.3f}.",
         f"- **HR2 (Proposition 2):** the measured verdict agrees at {HR2['agree']} of {HR2['called']} called pairs: {yn(HR2['holds'])}. "
         f"Inside the band (reported only): {HR2['inside_band_agree']} of {HR2['inside_band']} agree.",
         f"- **HR3 (rate):** {n3} of 36 within 30% (needs 30): {yn(HR3['holds'])}. Median measured / predicted time per path-step {HR3['median']:.3f}, range {HR3['min']:.3f}-{HR3['max']:.3f}.",
         f"- **Open question:** the RTX needs less energy than the V100 at {OQ['rtx_less']} of 18 pairs at their own hosts (predicted {OQ['predicted_less']}); "
         f"at a matched host (frodo's socket) at {OQ['rtx_less_frodo']} of 18 (predicted {OQ['predicted_less_frodo']}).", "",
         "## Every setting (steady state, GPU + CPU package)", "",
         "| Case | Target | Method | M | Bound (pred.) | E meas (J) | E pred (J) | E meas/pred | t meas/pred | P_gpu meas (W) | P_gpu pred (W) |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in per:
        L.append(f"| {x['case']} | {x['target']} | {x['scheme']} | {x['M']}{' (run ' + str(x['run_M']) + ')' if x['proxy'] else ''} | {x['bind_ss']} | {x['Ess_meas_J']:.4g} | {x['Ess_pred_J']:.4g} | "
                 f"{x['e_ratio']:.3f} | {x['t_ratio']:.3f} | {x['P_gpu_meas']:.1f} | {x['P_gpu_pred']:.1f} |")
    L += ["", "## HR2: Proposition 2 with the measured RTX energies (socket boundary)", "",
          "| Case | Target | best / runner-up | kappa | G | margin meas | margin pred | verdict meas | verdict pred | called | agree |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    vv = lambda b: "method" if b else "platform"
    for x in p2:
        L.append(f"| {x['case']} | {x['target']} | {x['best']} / {x['runner_up']} | {x['kappa']:.2f} | {x['G']:.2f} | {x['margin']:.2f} | "
                 f"{x['pred_margin']:.2f} | {vv(x['meas_holds'])} | {vv(x['pred_holds'])} | {'yes' if x['called'] else 'no'} | {'yes' if x['agree'] else 'NO'} |")
    L += ["", "## Open question: RTX against the V100 (both measured; steady state, better method each)", "",
          "| Case | Target | V100 (J) | RTX (J) | RTX / V100 | predicted | matched host | predicted |", "|---|---|---|---|---|---|---|---|"]
    for x in oq:
        L.append(f"| {x['case']} | {x['target']} | {x['Ess_v100_J']:.4g} | {x['Ess_rtx_J']:.4g} | {x['ratio']:.3f} | {x['pred_ratio']:.3f} | "
                 f"{x['ratio_frodo']:.3f} | {x['pred_ratio_frodo']:.3f} |")
    L += ["", "## Lowest steady-state energy with the measured RTX (socket boundary)", "", "| Case | Target | All five devices | Without the V100 |", "|---|---|---|---|"]
    for w in win:
        L.append(f"| {w['case']} | {w['target']} | {w['winner']} | {w['winner_no_v100']} |")
    if sess:
        v = list(sess.values())
        L += ["", f"Session 2 / session 1 energy per path-step over the 29 run keys: median {med(v):.4f}, range {min(v):.4f}-{max(v):.4f}."]
    open(os.path.join(OUT, "rtx_v09.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
