#!/usr/bin/env python3
"""extra_analysis.py - analyses of the existing (registered and measured) data asked for by the internal review
(19 Oct 2026). Exploratory: none of these was registered. Writes results/analysis_extra/extra.md and .json.

 1. Prediction skill without the CPU: H5 energy criterion for the card alone; winner prediction against null rules.
 2. Break-even rule (review E1): the card wins a single run iff  e_card W + P_sys t0 < e_cpu W, i.e. W > W* =
    P_sys t0 / (e_cpu - e_card), with e = steady-state energy per path-step (measured) and P_sys the card's
    system power at the boundary (measured, steady state). Tested on all 36 single-run comparisons.
 3. RQ2 sensitivity to the setting rule (review M7a): the registered rule splits the error budget 50/50 between
    bias and variance; the alternative picks, on the same calibration grid, the step that minimises the work with
    N = max_k sigma_k^2 / (eps^2 - beta_k^2), beta_k = |b_k| + 2 SE_k. Method gain recomputed with the measured
    steady-state energy per path-step; platform gain is unchanged.
 4. Resource use of the five routed designs (review minor 8).
"""
import csv, json, math, os, statistics, sys
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "analysis_extra")
TARGETS = ("1%", "0.3%", "0.1%")
FR = {"1%": 0.01, "0.3%": 0.003, "0.1%": 0.001}
CASES = ("I", "II", "III", "P1", "B", "A")
METH = {c: ("ft", "qe") for c in ("I", "II", "III", "P1", "B")}
METH["A"] = ("plain", "cv")
ATM = {"I": 13.084670, "II": 16.649223, "III": 21.795288, "P1": 8.095646, "B": 3.43639142, "A": 5.78194327}
T = {"I": 10, "II": 15, "III": 5, "P1": 1, "B": 1, "A": 1}


def load_mvp():
    rows = list(csv.DictReader(open(os.path.join(RES, "analysis", "measured_vs_predicted.csv"))))
    rows += [r for r in csv.DictReader(open(os.path.join(RES, "analysis_s", "measured_vs_predicted_s.csv"))) if r["variant"] == "same"]
    for r in rows:
        for k in ("E_meas_J", "E_pred_J", "Ess_meas_J", "Ess_pred_J", "t_meas_s", "t_pred_s"):
            r[k] = float(r[k])
    g = defaultdict(dict)
    for r in rows:
        g[(r["case"], r["target"], r["boundary"])][r["combo"]] = r
    return rows, g


def work():
    W = {}
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("path_steps"):
                W[(r["case"], r["target"], r["scheme"])] = float(r["path_steps"])
    for t in TARGETS:   # Case A: settings_s uses scheme names plain / cv already
        pass
    return W


def p_sys():
    """card system power (card + host) at each boundary, steady-state runs, per case and scheme"""
    P = defaultdict(list)
    for f in ("phase5_summary_s1.csv", "phase5_summary_s2.csv", "phase5s_summary_s1.csv", "phase5s_summary_s2.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["kind"] == "ss" and r["impl"] == "fpga":
                t = float(r["t_per_run_s"])
                P[(r["case"], r["scheme"], "socket")].append(float(r["E_socket_per_run_J"]) / t)
                P[(r["case"], r["scheme"], "server")].append(float(r["E_server_per_run_J"]) / t)
    return {k: statistics.median(v) for k, v in P.items()}


def part1(rows, g):
    card = [r for r in rows if r["combo"].startswith("card")]
    within = sum(abs(r["E_meas_J"] / r["E_pred_J"] - 1) <= 0.2 for r in card)
    within_ss = sum(abs(r["Ess_meas_J"] / r["Ess_pred_J"] - 1) <= 0.2 for r in card)
    res = dict(card_n=len(card), card_within20=within, card_within20_steady=within_ss)
    W = work()
    rules = {}
    for field, pfield, lab in (("E_meas_J", "E_pred_J", "primary"), ("Ess_meas_J", "Ess_pred_J", "steady")):
        hits = defaultdict(int)
        n = 0
        for (case, t, b), v in g.items():
            n += 1
            win = min(v, key=lambda c: v[c][field])
            a, s = METH[case]
            better = s if W[(case, t, s)] * 1.0 < W[(case, t, a)] else a   # lower-work method
            hits["registered model"] += win == min(v, key=lambda c: v[c][pfield])
            hits["better method on the card"] += win == f"card_{better}"
            hits["better method on CPU-native"] += win == f"cpu_native_{better}"
        rules[lab] = dict(n=n, **hits)
    res["winner_rules"] = rules
    return res


def part2(g, P):
    out = {}
    for t0 in (0.53e-3, 0.7e-3):
        ok, rows = 0, []
        for (case, t, b), v in sorted(g.items()):
            s = METH[case][1]
            c, n = v[f"card_{s}"], v[f"cpu_native_{s}"]
            W = c["Ess_meas_J"] and None
            ws = work()[(case, t, s)]
            e_card, e_cpu = c["Ess_meas_J"] / ws, n["Ess_meas_J"] / ws
            Ps = P[(case, s, b)]
            wstar = Ps * t0 / (e_cpu - e_card) if e_cpu > e_card else math.inf
            pred_card = ws > wstar
            meas_card = c["E_meas_J"] < n["E_meas_J"]
            ok += pred_card == meas_card
            rows.append(dict(case=case, target=t, boundary=b, W=ws, Wstar=wstar, W_over_Wstar=ws / wstar if wstar != math.inf else 0,
                             predicted="card" if pred_card else "cpu", measured="card" if meas_card else "cpu"))
        out[f"t0_{t0 * 1e3:.2f}ms"] = dict(correct=ok, n=len(rows), rows=rows)
    return out


def calib_rows():
    R = defaultdict(list)
    for r in csv.DictReader(open(os.path.join(RES, "calibration_summary.csv"))):
        if r["impl"] == "scalar":
            R[(r["case"], r["scheme"])].append(r)
    for r in csv.DictReader(open(os.path.join(RES, "calibration_s.csv"))):
        if r["case"] == "B":
            R[("B", r["scheme"])].append(r)
    for k in R:
        R[k] = sorted(R[k], key=lambda r: int(r["steps_per_year"]))
    return R


def choose(rows, eps, rule):
    best = None
    for r in rows:
        steps, pairs = int(r["steps"]), int(r["pairs"])
        beta = [abs(float(r[f"bias{k}"])) + 2 * float(r[f"se{k}"]) for k in range(3)]
        var = [float(r[f"se{k}"]) ** 2 * (pairs - 1) for k in range(3)]
        if rule == "registered":
            if all(b <= eps / math.sqrt(2) for b in beta):
                N = math.ceil(max(var) / (eps ** 2 / 2) / 64) * 64
                return dict(spy=int(r["steps_per_year"]), N=N, W=2.0 * N * steps)
        else:
            if all(b < eps for b in beta):
                N = math.ceil(max(v / (eps ** 2 - b ** 2) for v, b in zip(var, beta)) / 64) * 64
                c = dict(spy=int(r["steps_per_year"]), N=N, W=2.0 * N * steps)
                if best is None or c["W"] < best["W"]:
                    best = c
    return best


def part3(g):
    R = calib_rows()
    pts = {p["case"] + p["target"]: p for p in json.load(open(os.path.join(RES, "figures", "fig2_points.json")))}
    Wreg = work()
    out = []
    for case in ("I", "II", "III", "P1", "B"):
        for t in TARGETS:
            eps = FR[t] * ATM[case]
            v = g[(case, t, "socket")]
            e = {}
            for m in ("ft", "qe"):
                best = min(v[f"{p}_{m}"]["Ess_meas_J"] for p in ("card", "cpu_matched", "cpu_native"))
                e[m] = best / Wreg[(case, t, m)]          # best-platform steady energy per path-step
            reg = {m: choose(R[(case, m)], eps, "registered") for m in ("ft", "qe")}
            alt = {m: choose(R[(case, m)], eps, "optimal") for m in ("ft", "qe")}
            gm_reg = (Wreg[(case, t, "ft")] * e["ft"]) / (Wreg[(case, t, "qe")] * e["qe"])
            gm_alt = (alt["ft"]["W"] * e["ft"]) / (alt["qe"]["W"] * e["qe"]) if alt["ft"] and alt["qe"] else None
            gp = pts[case + t]["platform_gain"]
            out.append(dict(case=case, target=t, reproduced=all(reg[m] and abs(reg[m]["W"] / Wreg[(case, t, m)] - 1) < 0.02 for m in reg),
                            spy_reg={m: reg[m]["spy"] if reg[m] else None for m in reg}, spy_alt={m: alt[m]["spy"] for m in alt},
                            W_alt_over_reg={m: alt[m]["W"] / Wreg[(case, t, m)] for m in alt},
                            method_gain_reg=gm_reg, method_gain_alt=gm_alt, platform_gain=gp,
                            margin_reg=gm_reg / gp, margin_alt=gm_alt / gp if gm_alt else None))
    return out


def part4():
    base = "/tmp/claude-0/repo/paper2"
    designs = [("Heston FT", "predictions/inputs/ft"), ("Heston QE", "predictions/inputs/qe"), ("Basket FT", "predictions_s/inputs/bft"),
               ("Basket QE", "predictions_s/inputs/bqe"), ("Asian", "predictions_s/inputs/asian")]
    out = []
    for name, d in designs:
        p = os.path.join(base, d)
        if not os.path.exists(p):
            continue
        line = next(l for l in open(os.path.join(p, "impl_1_kernel_util_routed.rpt")) if "Used Resources" in l)
        cells = [c.strip() for c in line.split("|")[2:8]]
        out.append(dict(design=name, MHz=int(open(os.path.join(p, "FREQ_HZ")).read()) // 1000000,
                        LUT=cells[0], REG=cells[2], BRAM=cells[3], DSP=cells[5]))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    rows, g = load_mvp()
    P = p_sys()
    r1, r2, r3, r4 = part1(rows, g), part2(g, P), part3(g), part4()
    json.dump(dict(prediction=r1, breakeven=r2, sensitivity=r3, resources=r4), open(os.path.join(OUT, "extra.json"), "w"), indent=1, default=str)
    L = ["# Extra analyses of the existing data (exploratory, not registered)", ""]
    L += ["## 1. Prediction skill without the CPU", "",
          f"- Card energy within 20%, primary: {r1['card_within20']} of {r1['card_n']}; steady state: {r1['card_within20_steady']} of {r1['card_n']}.", "",
          "| Rule | Winner right, primary | Winner right, steady |", "|---|---|---|"]
    for rule in ("registered model", "better method on the card", "better method on CPU-native"):
        L.append(f"| {rule} | {r1['winner_rules']['primary'][rule]} / 36 | {r1['winner_rules']['steady'][rule]} / 36 |")
    L += ["", "## 2. Break-even rule (card wins one run iff W > W*)", ""]
    for k, v in r2.items():
        L.append(f"- {k}: {v['correct']} of {v['n']} single-run comparisons (best method, card vs CPU-native) right.")
    L += ["", "| Case | Target | Boundary | W | W* (t0 0.53 ms) | W/W* | predicted | measured |", "|---|---|---|---|---|---|---|---|"]
    for r in r2["t0_0.53ms"]["rows"]:
        L.append(f"| {r['case']} | {r['target']} | {r['boundary']} | {r['W']:.3g} | {r['Wstar']:.3g} | {r['W_over_Wstar']:.2f} | {r['predicted']} | {r['measured']} |")
    L += ["", "## 3. RQ2 under the work-optimal error split (same calibration grid)", "",
          "| Case | Target | steps/yr FT, QE (registered → optimal) | W optimal / registered (FT, QE) | Method gain reg → opt | Platform gain | Margin reg → opt |",
          "|---|---|---|---|---|---|---|"]
    for r in r3:
        L.append(f"| {r['case']} | {r['target']} | {r['spy_reg']['ft']}, {r['spy_reg']['qe']} → {r['spy_alt']['ft']}, {r['spy_alt']['qe']} | "
                 f"{r['W_alt_over_reg']['ft']:.2f}, {r['W_alt_over_reg']['qe']:.2f} | {r['method_gain_reg']:.1f} → {r['method_gain_alt']:.1f} | "
                 f"{r['platform_gain']:.2f} | {r['margin_reg']:.2f} → {r['margin_alt']:.2f} |" + ("" if r["reproduced"] else " (registered setting not reproduced)"))
    L += ["", "## 4. Resource use of the routed designs (share of the user region of the U50)", "",
          "| Design | Clock (MHz) | LUT | Registers | BRAM | DSP |", "|---|---|---|---|---|---|"]
    for r in r4:
        L.append(f"| {r['design']} | {r['MHz']} | {r['LUT']} | {r['REG']} | {r['BRAM']} | {r['DSP']} |")
    open(os.path.join(OUT, "extra.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
