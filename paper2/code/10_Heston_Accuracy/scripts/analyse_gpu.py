#!/usr/bin/env python3
"""analyse_gpu.py - analysis of the GPU measurements (protocol v0.6, Sections 22, 26, 27) against the registered GPU
prediction (results/predictions_gpu/predictions_gpu.json, commit ccc1471) and the Part A measurements (card, CPU).

Value of a group = median of the two sessions' medians. GPU boundaries: gpu_socket (NVML GPU + RAPL socket 1, the
primary), gpu_alone, gpu_frodo_socket (GPU + frodo's host power 49.151 W x time), gpu_server (GPU + both sockets),
gpu_frodo_server (GPU + 92.940 W x time). Steady state of a setting = its work x the energy per path-step of the
steady-state run of the same kernel and step (or of the setting itself where it has >= 1e11 path-steps).
Wait mode: the primary GPU result uses the mode with less steady-state energy at the primary boundary in session 1
(Section 22b); both modes are reported.
Tests: HG1 (method gain > platform gain, three platforms, steady state, primary boundaries), HG2 (GPU QE/FT time per
path-step, median over the main-case settings, between 2.06 and 3.72), HG3 (GPU fixed cost per run < 0.2 ms),
HG4 (break-even rule for every pair of platforms, same method), HG5 (GPU energy within 30% of the registered
prediction, one run and steady state; steady-state winner right in >= 30 of 36), the open question (V100 vs U50),
reproducibility between the sessions, and the GPU's accuracy (RMSE over the 10 runs).
Writes results/analysis_gpu/analysis_gpu.md, .json, gpu_vs_predicted.csv.
"""
import csv, json, math, os, statistics as st, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "analysis_gpu")
H = {"socket": 49.151, "server": 92.940}
CASES = ("I", "II", "III", "P1", "B", "A")
TARGETS = ("1%", "0.3%", "0.1%")
METH = {c: ("ft", "qe") for c in ("I", "II", "III", "P1", "B")}
METH["A"] = ("plain", "cv")
ATM = {"I": 13.084670, "II": 16.649223, "III": 21.795288, "P1": 8.095646, "B": 3.43639142, "A": 5.78194327}
FR = {"1%": 0.01, "0.3%": 0.003, "0.1%": 0.001}


def med2(vals):
    return st.median(vals)


def load_gpu():
    G = {}
    for s in (1, 2):
        for r in csv.DictReader(open(os.path.join(RES, f"phase5g_summary_s{s}.csv"))):
            key = r["job"].split("/", 1)[1]
            G.setdefault(key, []).append(r)
    out = {}
    for key, rs in G.items():
        r0 = rs[0]
        d = dict(kind=r0["kind"], mode=r0["mode"], case=r0["case"], scheme=r0["scheme"], target=r0["target"], M=int(r0["steps"]),
                 N=int(r0["pairs"]), W=float(r0["path_steps"]), sessions=len(rs))
        for f in ("t_per_run_s", "E_gpu_per_run_J", "E_gpu_socket_per_run_J", "E_gpu_server_per_run_J", "P_gpu_W", "P_socket1_W",
                  "P_socket0_W", "temp_max_c"):
            d[f] = med2([float(x[f]) for x in rs])
        d["per_session_E"] = [float(x["E_gpu_socket_per_run_J"]) for x in rs]
        if r0["kind"] == "set":
            for k in range(3):
                d[f"err_mean{k}"] = st.mean(float(x[f"err_mean{k}"]) for x in rs)
                d[f"err_rms{k}"] = math.sqrt(st.mean(float(x[f"err_rms{k}"]) ** 2 for x in rs))
        out[key] = d
    return out


def bounds(d, t):
    return dict(gpu_socket=d["E_gpu_socket_per_run_J"], gpu_alone=d["E_gpu_per_run_J"], gpu_server=d["E_gpu_server_per_run_J"],
                gpu_frodo_socket=d["E_gpu_per_run_J"] + H["socket"] * t, gpu_frodo_server=d["E_gpu_per_run_J"] + H["server"] * t)


CPU48 = "--cpu48" in sys.argv     # re-evaluation with the CPU socket results of the registered 24/48 choice (addendum v0.6.1)
if CPU48:
    OUT = os.path.join(RES, "analysis_gpu_cpu48")


def main():
    os.makedirs(OUT, exist_ok=True)
    G = load_gpu()
    P = json.load(open(os.path.join(RES, "predictions_gpu", "predictions_gpu.json")))
    pred = {(r["case"], r["target"], r["scheme"], r["wait"], r["power"]): r for r in P["predictions"]}
    # Part A measured (card, CPU): energies per setting and boundary
    A = {}
    for f, filt in (("analysis/measured_vs_predicted.csv", None), ("analysis_s/measured_vs_predicted_s.csv", "same")):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if filt and r["variant"] != filt:
                continue
            A[(r["case"], r["target"], r["boundary"], r["combo"])] = dict(E=float(r["E_meas_J"]), Ess=float(r["Ess_meas_J"]), t=float(r["t_meas_s"]))
    tau48 = {}
    if CPU48:
        for r in csv.DictReader(open(os.path.join(RES, "analysis_phase6", "cpu_socket_better.csv"))):
            A[(r["case"], r["target"], "socket", r["combo"])] = dict(E=float(r["E_J"]), Ess=float(r["Ess_J"]), t=float(r["t_s"]))
            if r["combo"].startswith("cpu_native"):
                tau48[(r["case"], r["target"], r["scheme"])] = float(r["tau_s"])
    # GPU per setting and mode
    S = {}
    for key, d in G.items():
        if d["kind"] != "set":
            continue
        ss = G.get(f"{d['mode']}/ss_{d['case']}_{d['scheme']}_M{d['M']}")
        src = ss if ss else d
        e_per = {b: v / src["W"] for b, v in bounds(src, src["t_per_run_s"]).items()}
        tau = src["t_per_run_s"] / src["W"]
        S[(d["case"], d["target"], d["scheme"], d["mode"])] = dict(d=d, E=bounds(d, d["t_per_run_s"]), Ess={b: e * d["W"] for b, e in e_per.items()},
                                                                  e=e_per, tau=tau, t=d["t_per_run_s"], W=d["W"])
    # wait mode: session-1 steady-state energy at the primary boundary, over all steady keys
    s1 = {r["job"].split("/", 1)[1]: r for r in csv.DictReader(open(os.path.join(RES, "phase5g_summary_s1.csv")))}
    tot = {m: 0.0 for m in ("spin", "block")}
    nb = 0
    keys = [k.split("/", 1)[1] for k in s1 if k.startswith("spin/ss_")]
    for k in keys:
        es = float(s1["spin/" + k]["E_gpu_socket_per_run_J"]) / float(s1["spin/" + k]["path_steps"])
        eb = float(s1["block/" + k]["E_gpu_socket_per_run_J"]) / float(s1["block/" + k]["path_steps"])
        tot["spin"] += math.log(es); tot["block"] += math.log(eb); nb += eb < es
    MODE = "block" if tot["block"] < tot["spin"] else "spin"
    mode_info = dict(primary=MODE, block_lower_in=nb, of=len(keys), geo_ratio_block_over_spin=math.exp((tot["block"] - tot["spin"]) / len(keys)))
    # reproducibility
    diffs = [abs(d["per_session_E"][0] / d["per_session_E"][1] - 1) for d in G.values() if d["sessions"] == 2]
    repro = dict(median=st.median(diffs), max=max(diffs), over5=sum(x > 0.05 for x in diffs), n=len(diffs))
    # table: measured vs registered prediction
    table = []
    for (case, t, sc, mode), v in sorted(S.items()):
        for var in ("registered", "mix"):
            p = pred[(case, t, sc, mode, var)]
            for b in ("gpu_socket", "gpu_server", "gpu_alone"):
                table.append(dict(case=case, target=t, scheme=sc, mode=mode, power=var, boundary=b, E_meas_J=v["E"][b], E_pred_J=p["E_J"][b],
                                  ratio=v["E"][b] / p["E_J"][b], Ess_meas_J=v["Ess"][b], Ess_pred_J=p["Ess_J"][b], ratio_ss=v["Ess"][b] / p["Ess_J"][b],
                                  t_meas_s=v["t"], t_pred_s=p["t_run_s"]))
    with open(os.path.join(OUT, "gpu_vs_predicted.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0])); w.writeheader(); w.writerows(table)

    def h5(var, mode, b="gpu_socket"):
        sel = [x for x in table if x["power"] == var and x["mode"] == mode and x["boundary"] == b]
        return dict(n=len(sel), one_run=sum(abs(x["ratio"] - 1) <= 0.3 for x in sel), steady=sum(abs(x["ratio_ss"] - 1) <= 0.3 for x in sel),
                    time=sum(abs(x["t_meas_s"] / x["t_pred_s"] - 1) <= 0.3 for x in sel),
                    med_ratio=st.median(x["ratio"] for x in sel), med_ratio_ss=st.median(x["ratio_ss"] for x in sel),
                    med_time=st.median(x["t_meas_s"] / x["t_pred_s"] for x in sel))
    hg5 = {f"{var} {mode}": h5(var, mode) for var in ("registered", "mix") for mode in ("spin", "block")}
    # winners with three platforms (GPU in its primary mode)
    win = []
    gb = {"socket": "gpu_socket", "server": "gpu_server"}
    for case in CASES:
        for t in TARGETS:
            for b in ("socket", "server"):
                cand = {}
                for m in METH[case]:
                    for p in ("card", "cpu_matched", "cpu_native"):
                        a = A[(case, t, b, f"{p}_{m}")]
                        cand[f"{p}_{m}"] = (a["E"], a["Ess"])
                    g = S[(case, t, m, MODE)]
                    cand[f"gpu_{m}"] = (g["E"][gb[b]], g["Ess"][gb[b]])
                pw = next(w for w in P["winners"] if w["case"] == case and w["target"] == t and w["boundary"] == b)
                mw, ms = min(cand, key=lambda c: cand[c][0]), min(cand, key=lambda c: cand[c][1])
                win.append(dict(case=case, target=t, boundary=b, measured_primary=mw, measured_steady=ms, predicted_primary=pw["winner_primary"],
                                predicted_steady=pw["winner_steady"], E={c: v[0] for c, v in cand.items()}, Ess={c: v[1] for c, v in cand.items()}))
    hg5["winners_steady_right"] = sum(w["measured_steady"] == w["predicted_steady"] for w in win)
    hg5["winners_primary_right"] = sum(w["measured_primary"] == w["predicted_primary"] for w in win)
    hg5["holds"] = all(v["one_run"] == v["n"] and v["steady"] == v["n"] for k, v in hg5.items() if k == f"registered {MODE}") and hg5["winners_steady_right"] >= 30
    # HG1
    hg1 = []
    for w in win:
        if w["boundary"] != "socket":
            continue
        a, b2 = METH[w["case"]]
        plats = ("card", "cpu_matched", "cpu_native", "gpu")
        best = {m: min(w["Ess"][f"{p}_{m}"] for p in plats) for m in (a, b2)}
        bm = min(best, key=best.get)
        gm = max(best.values()) / min(best.values())
        vals = sorted(w["Ess"][f"{p}_{bm}"] for p in plats)
        gp = vals[1] / vals[0]
        hg1.append(dict(case=w["case"], target=w["target"], method_gain=gm, platform_gain=gp, holds=gm > gp,
                        best_platform=min(plats, key=lambda p: w["Ess"][f"{p}_{bm}"])))
    # HG2: GPU QE/FT time per path-step (steady, main cases, median over settings), primary mode
    r2 = [S[(c, t, "qe", MODE)]["tau"] / S[(c, t, "ft", MODE)]["tau"] for c in ("I", "II", "III", "P1") for t in TARGETS]
    hg2 = dict(median=st.median(r2), min=min(r2), max=max(r2), holds=2.06 <= st.median(r2) <= 3.72)
    # HG3: GPU fixed cost per run = run time - work x steady time per path-step, short settings (kernel < 1 ms)
    def t0s(mode):
        v = [s["t"] - s["W"] * s["tau"] for (c, t, sc, m), s in S.items() if m == mode and s["W"] * s["tau"] < 1e-3]
        return dict(median=st.median(v), min=min(v), max=max(v), n=len(v))
    hg3 = {m: t0s(m) for m in ("spin", "block")}
    hg3["holds"] = hg3[MODE]["median"] < 0.2e-3
    # HG4: break-even rule, every pair of {card, cpu_native, gpu}, same method, per setting and boundary
    # platform constants: steady energy per path-step e (setting's Ess / W), system power P = e / tau, fixed cost t0
    plat_e, plat_tau = {}, {}
    W = {}
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("path_steps"):
                W[(r["case"], r["target"], r["scheme"])] = float(r["path_steps"])
    # steady time per path-step for card and CPU from the Part A summaries (ss runs, or the setting itself)
    tauA = {}
    for f in ("phase5_summary_s1.csv", "phase5_summary_s2.csv", "phase5s_summary_s1.csv", "phase5s_summary_s2.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["impl"] == "avx512":
                continue
            plat = "card" if r["impl"] == "fpga" else "cpu_native"
            bl = ["socket", "server"] if plat == "card" else (["socket"] if r["cfg"] == "s0_c24" else ["server"])
            for b in bl:
                tauA.setdefault((plat, b, r["kind"], r["case"], r["scheme"], int(r["steps"]), r["target"]), []).append(
                    float(r["t_per_run_s"]) / float(r["path_steps"]))
    def tau_A(plat, b, case, sc, M, t):
        if CPU48 and plat == "cpu_native" and b == "socket":
            return tau48[(case, t, sc)]
        k = (plat, b, "ss", case, sc, M, "")
        if k in tauA:
            return st.median(tauA[k])
        return st.median(tauA[(plat, b, "set", case, sc, M, t)])
    Mstep = {}
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("steps"):
                Mstep[(r["case"], r["target"], r["scheme"])] = int(r["steps"])
    info = {}
    for plat in ("card", "cpu_native", "gpu"):
        for b in ("socket", "server"):
            v = []
            for case in CASES:
                for t in TARGETS:
                    for m in METH[case]:
                        w_ = W[(case, t, m)]
                        if plat == "gpu":
                            s = S[(case, t, m, MODE)]
                            e, tau, tr = s["e"][gb[b]], s["tau"], s["t"]
                        else:
                            a = A[(case, t, b, f"{plat}_{m}")]
                            e, tau, tr = a["Ess"] / w_, tau_A(plat, b, case, m, Mstep[(case, t, m)], t), a["t"]
                        info[(plat, b, case, t, m)] = (e, tau, tr)
                        if w_ * tau < 1e-3:
                            v.append(tr - w_ * tau)
            plat_e[(plat, b)] = st.median(v) if v else 0.0
    hg4 = []
    for b in ("socket", "server"):
        for case in CASES:
            for t in TARGETS:
                for m in METH[case]:
                    w_ = W[(case, t, m)]
                    for p1, p2 in (("card", "cpu_native"), ("card", "gpu"), ("cpu_native", "gpu")):
                        def pred_E(p):
                            e, tau, _ = info[(p, b, case, t, m)]
                            return e * w_ + (e / tau) * max(plat_e[(p, b)], 0.0)
                        def meas_E(p):
                            if p == "gpu":
                                return S[(case, t, m, MODE)]["E"][gb[b]]
                            return A[(case, t, b, f"{p}_{m}")]["E"]
                        ok = (pred_E(p1) < pred_E(p2)) == (meas_E(p1) < meas_E(p2))
                        hg4.append(dict(boundary=b, case=case, target=t, method=m, pair=f"{p1}-{p2}", ok=ok, ratio_meas=meas_E(p1) / meas_E(p2)))
    hg4_res = dict(right=sum(x["ok"] for x in hg4), n=len(hg4), t0={f"{p} {b}": v for (p, b), v in plat_e.items()},
                   wrong=[x for x in hg4 if not x["ok"]], holds=all(x["ok"] for x in hg4))
    # exploratory: the fixed cost as measured energy E0 = E_run - e W (median over the short settings), not P_sys t0
    def mE(p, b, case, t, m):
        return S[(case, t, m, MODE)]["E"][gb[b]] if p == "gpu" else A[(case, t, b, f"{p}_{m}")]["E"]
    E0 = {}
    for plat in ("card", "cpu_native", "gpu"):
        for b in ("socket", "server"):
            v = [mE(plat, b, c, t, m) - info[(plat, b, c, t, m)][0] * W[(c, t, m)] for c in CASES for t in TARGETS for m in METH[c]
                 if W[(c, t, m)] * info[(plat, b, c, t, m)][1] < 1e-3]
            E0[(plat, b)] = max(st.median(v), 0.0) if v else 0.0
    ok2 = []
    for x in hg4:
        p1, p2 = x["pair"].split("-")
        pe = {p: info[(p, x["boundary"], x["case"], x["target"], x["method"])][0] * W[(x["case"], x["target"], x["method"])] + E0[(p, x["boundary"])] for p in (p1, p2)}
        ok2.append(((pe[p1] < pe[p2]) == (x["ratio_meas"] < 1), x))
    hg4_res["exploratory_E0"] = dict(right=sum(o for o, _ in ok2), n=len(ok2), E0_mJ={f"{p} {b}": v * 1e3 for (p, b), v in E0.items()},
                                     wrong=[x for o, x in ok2 if not o])
    # P_sys t0 against the measured fixed-cost energy, GPU
    hg3["gpu_fixed_energy_mJ"] = {b: E0[("gpu", b)] * 1e3 for b in ("socket", "server")}
    hg3["gpu_Psys_t0_mJ"] = {b: st.median(info[("gpu", b, c, t, m)][0] / info[("gpu", b, c, t, m)][1] for c in CASES for t in TARGETS for m in METH[c]) * plat_e[("gpu", b)] * 1e3 for b in ("socket", "server")}
    hg3["card_t0_ms"] = plat_e[("card", "socket")] * 1e3
    hg3["half_card"] = hg3[MODE]["median"] < 0.5 * plat_e[("card", "socket")]
    hg3["holds"] = hg3["holds"] and hg3["half_card"]
    hg3["short_settings"] = sorted(((c, t, sc, round((s["t"] - s["W"] * s["tau"]) * 1e3, 3)) for (c, t, sc, m), s in S.items() if m == MODE and s["W"] * s["tau"] < 1e-3), key=lambda z: z[3])
    # open question
    oq = []
    for w in win:
        if w["boundary"] != "socket":
            continue
        a, b2 = METH[w["case"]]
        g = min(w["Ess"][f"gpu_{m}"] for m in (a, b2))
        c = min(w["Ess"][f"card_{m}"] for m in (a, b2))
        oq.append(dict(case=w["case"], target=w["target"], gpu_over_card=g / c))
    # accuracy of the GPU code (10 runs, primary mode; both modes use the same seeds and pairs)
    acc = []
    for (case, t, sc, mode), s in S.items():
        if mode != MODE:
            continue
        eps = FR[t] * ATM[case]
        d = s["d"]
        acc.append(dict(case=case, target=t, scheme=sc, rmse_over_eps=[d[f"err_rms{k}"] / eps for k in range(3)],
                        mean_err_over_eps=[d[f"err_mean{k}"] / eps for k in range(3)]))
    res = dict(mode=mode_info, reproducibility=repro, HG5=hg5, HG1=dict(per_setting=hg1, holds=all(x["holds"] for x in hg1)),
               HG2=hg2, HG3=hg3, HG4=hg4_res, open_question=oq, winners=win, accuracy=acc)
    json.dump(res, open(os.path.join(OUT, "analysis_gpu.json"), "w"), indent=1, default=float)
    # report
    L = ["# GPU measurements against the registered GPU prediction (protocol v0.6)", "",
         f"- Sessions: 2 x 650 jobs, all done; GPU sessions ran on the same day (deviation from Section 22b).",
         f"- Wait mode (session-1 steady state, primary boundary): **{MODE}**; block lower in {nb} of {len(keys)} steady keys, geometric mean block/spin {mode_info['geo_ratio_block_over_spin']:.3f}.",
         f"- Reproducibility (energy per run, session 1 vs 2): median |diff| {repro['median'] * 100:.2f}%, max {repro['max'] * 100:.1f}%, {repro['over5']} of {repro['n']} groups over 5%.", "",
         "## HG5 (GPU prediction): energy within 30% at the primary boundary (GPU + socket 1)", "",
         "| Power rule | Wait | One run | Steady state | Time | median meas/pred, one run | steady | time |", "|---|---|---|---|---|---|---|---|"]
    for k, v in hg5.items():
        if isinstance(v, dict):
            L.append(f"| {k.split()[0]} | {k.split()[1]} | {v['one_run']}/{v['n']} | {v['steady']}/{v['n']} | {v['time']}/{v['n']} | {v['med_ratio']:.2f} | {v['med_ratio_ss']:.2f} | {v['med_time']:.2f} |")
    L += ["", f"- Lowest-energy combination right: steady state {hg5['winners_steady_right']} of 36, one run {hg5['winners_primary_right']} of 36.",
          f"- **HG5 {'holds' if hg5['holds'] else 'fails'}** (registered power rule, {MODE} mode).", "",
          "## Winners with three platforms (measured; GPU in its primary mode)", "",
          "| Case | Target | Boundary | One run | Steady state | Predicted steady |", "|---|---|---|---|---|---|"]
    for w in win:
        L.append(f"| {w['case']} | {w['target']} | {w['boundary']} | {w['measured_primary']} | {w['measured_steady']} | {w['predicted_steady']} |")
    L += ["", "## HG1: method gain against platform gain (steady state, primary boundaries)", "", "| Case | Target | Method gain | Platform gain | Best platform | Holds |", "|---|---|---|---|---|---|"]
    for x in hg1:
        L.append(f"| {x['case']} | {x['target']} | {x['method_gain']:.1f} | {x['platform_gain']:.1f} | {x['best_platform']} | {'yes' if x['holds'] else 'no'} |")
    L += ["", f"**HG1 {'holds' if res['HG1']['holds'] else 'fails'}** ({sum(x['holds'] for x in hg1)} of {len(hg1)} settings).", "",
          f"## HG2, HG3, HG4", "",
          f"- **HG2 {'holds' if hg2['holds'] else 'fails'}**: GPU QE/FT time per path-step, median {hg2['median']:.2f} (range {hg2['min']:.2f}-{hg2['max']:.2f}); registered range 2.06-3.72.",
          f"- **HG3 {'holds' if hg3['holds'] else 'fails'}**: GPU fixed cost per run, {MODE} {hg3[MODE]['median'] * 1e3:.3f} ms (range {hg3[MODE]['min'] * 1e3:.3f}-{hg3[MODE]['max'] * 1e3:.3f}, {hg3[MODE]['n']} short settings); "
          f"spin {hg3['spin']['median'] * 1e3:.3f} ms, block {hg3['block']['median'] * 1e3:.3f} ms.",
          f"- **HG4 {'holds' if hg4_res['holds'] else 'fails'}**: break-even rule right in {hg4_res['right']} of {hg4_res['n']} pairwise single-run comparisons; fixed costs (ms): "
          + ", ".join(f"{k} {v * 1e3:.3f}" for k, v in hg4_res["t0"].items()), ""]
    for x in hg4_res["wrong"][:20]:
        L.append(f"  - wrong: {x['boundary']} {x['case']} {x['target']} {x['method']} {x['pair']} (measured ratio {x['ratio_meas']:.3f})")
    x4 = hg4_res["exploratory_E0"]
    L += [f"- HG3 detail: card fixed cost {hg3['card_t0_ms']:.3f} ms, so the 'less than half the card' part {'holds' if hg3['half_card'] else 'fails'}. "
          f"GPU fixed-cost energy measured {hg3['gpu_fixed_energy_mJ']['socket']:.2f} mJ (socket), against P_sys x t0 = {hg3['gpu_Psys_t0_mJ']['socket']:.2f} mJ.",
          f"- Exploratory (not registered): the same rule with the measured fixed-cost energy E0 = E_run - e W in place of P_sys x t0 is right in {x4['right']} of {x4['n']}; E0 (mJ): "
          + ", ".join(f"{k} {v:.2f}" for k, v in x4["E0_mJ"].items())]
    for x in x4["wrong"]:
        L.append(f"  - wrong: {x['boundary']} {x['case']} {x['target']} {x['method']} {x['pair']} (measured ratio {x['ratio_meas']:.3f})")
    L.append("- GPU fixed cost per short setting (ms): " + ", ".join(f"{c} {t} {sc} {v}" for c, t, sc, v in hg3["short_settings"]))
    n_less = sum(x["gpu_over_card"] < 1 for x in oq)
    L += ["", f"## Open question: V100 against U50 (steady state, primary boundaries, better method each)", "",
          f"The V100 needs less energy in {n_less} of {len(oq)} case-target pairs; GPU/card energy " +
          f"{min(x['gpu_over_card'] for x in oq):.3f}-{max(x['gpu_over_card'] for x in oq):.3f} (median {st.median(x['gpu_over_card'] for x in oq):.3f}).", "",
          "## Accuracy of the GPU code (10 runs per setting)", ""]
    bad = [a for a in acc if max(a["rmse_over_eps"]) > 1.1]
    L.append(f"- RMSE within 1.1 x target at all strikes in {len(acc) - len(bad)} of {len(acc)} settings.")
    for a in bad:
        L.append(f"  - {a['case']} {a['target']} {a['scheme']}: RMSE/eps " + ", ".join(f"{v:.2f}" for v in a["rmse_over_eps"]) +
                 "; mean error/eps " + ", ".join(f"{v:+.2f}" for v in a["mean_err_over_eps"]))
    open(os.path.join(OUT, "analysis_gpu.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
