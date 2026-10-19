#!/usr/bin/env python3
"""predict.py - phase 4 of paper 2 (protocol v0.4, Section 10 item 2): predicted time and energy to target of
every combination of platform and scheme, made BEFORE any card run, from
  - the chosen settings                 results/settings.csv (calibration v0.4, registered in commit b790a13)
  - the CPU-only measurements           results/cpu_pred_summary.csv (scripts/cpu_measure.py)
  - the routed card designs             build/<scheme>/FREQ_HZ, build/<scheme>/reports/ (timing, power), build log
  - paper 1's frozen model constants    (below; paper 1, JSA revision round 4 / round 6)
Nothing here is measured on the card. Output: results/predictions/predictions.json and predictions.md, each
with a .sha256 file; both are committed to GitHub before the first card run.

Card model (paper 1's time-energy model, Section 6.4 there, with this kernel's exact cycle count):
  cycles of one CU = max over its L lanes of [ max(ring, settle) + IL + depth ] + start-up and collect words,
     ring   = (W*M + M - 1)*IL + s + 1   (the kernel's own trip count; W, s from n_lane - 1 and IL)
     settle = 3 * n_lane                  (one strike per cycle: the lane waits for it when M < 3)
  the host splits the pairs evenly over the 2 CUs, each CU over its L lanes (as host/hq_fpga.cpp and the kernel)
  t_kernel = cycles / f;   one pricing run: t = t_kernel + t0   (t0: paper 1's fixed launch overhead per call)
  P_card = P0 + d (P_viv - P0), d = t_kernel / t   (duty-weighted Vivado estimate, paper 1)
  P_F = H(polling host, boundary) + P_card;   E = t * P_F
  steady state (protocol v0.4 secondary outcome): the same with t0 = 0 and many pairs (d = 1)
CPU: medians of the measured runs; socket boundary = pkg0 + dram0 of the 24-core runs, server boundary =
     both packages and DRAM of the 96-thread runs; steady state from the long runs ("ss") or, for settings that
     are themselves long, from the setting's own runs.
naive model (as in paper 1, for comparison only): card at its peak rate and 75 W, no launch overhead.

Usage (from the project folder):  python3 scripts/predict.py
"""
import csv
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
RES = os.path.join(ROOT, "results")
OUTD = os.path.join(RES, "predictions")

# ---- paper 1's frozen constants (not fitted to anything in paper 2)
P0_W = 17.0                                   # card power with the kernel idle (paper 1 model_constants.json)
H_W = {"socket": 49.151, "server": 92.940}    # host power while a polling card kernel runs (paper 1)
T0_S = 0.7e-3                                 # fixed launch overhead per card call (paper 1, MC Heston smallest size)
P_NAIVE_W = 75.0                              # naive model: the card at its rated power
TIE = (1 / 1.2, 1.2)                          # time verdict tie band (paper 1)
# ---- kernel facts (kernel/hq_kernel.h, HLS logs: all loops II = 1)
IL = {"ft": 128, "qe": 256}
DEPTH = {"ft": 53, "qe": 146}                 # RING pipeline depth (HLS log, build v0.4)
SETTLE_DEPTH = 65
JOB_WORDS, SET_WORDS, RES_WORDS = 26, 7, 14
TARGETS = ("1%", "0.3%", "0.1%")
CASES = ("I", "II", "III", "P1")


def sha(f):
    return hashlib.sha256(open(f, "rb").read()).hexdigest()


# ------------------------------------------------------------------ design facts from the routed builds
def design(scheme):
    b = os.path.join(ROOT, "build", scheme)
    rep = os.path.join(b, "reports")
    f_hz = int(open(os.path.join(b, "FREQ_HZ")).read().strip())
    tim = open(os.path.join(rep, "impl_1_hw_bb_locked_timing_summary_routed.rpt")).read()
    m = re.search(r"Design Timing Summary.*?\n\s*-+.*?\n\s*-+\s*-+.*?\n\s*([-\d.]+)\s+([-\d.]+)", tim, re.S)
    wns = float(m.group(1)) if m else float("nan")
    m = re.search(r"^\s*clk_out1_ulp_clk_wiz_0\s+\{[\d.\s]+\}\s+([\d.]+)\s+([\d.]+)", tim, re.M)
    f_rpt = float(m.group(2)) * 1e6 if m else float("nan")
    pw = open(os.path.join(rep, "impl_1_hw_bb_locked_power_routed.rpt")).read()
    p_viv = float(re.search(r"Total On-Chip Power \(W\)\s*\|\s*([\d.]+)", pw).group(1))
    util = open(os.path.join(rep, "impl_1_kernel_util_routed.rpt")).read()
    n_cu = len(set(re.findall(rf"\|\s+(hq_{scheme}_\d+)\s+\|", util)))
    log = open(os.path.join(ROOT, "build", "build_xclbins.log")).read()
    lanes = int(re.findall(rf"{scheme.upper()} lanes/CU (\d+)", log)[-1])
    files = {k: os.path.relpath(os.path.join(rep, v), ROOT) for k, v in
             (("timing", "impl_1_hw_bb_locked_timing_summary_routed.rpt"), ("power", "impl_1_hw_bb_locked_power_routed.rpt"),
              ("util", "impl_1_kernel_util_routed.rpt"))}
    return dict(scheme=scheme, f_hz=f_hz, f_hz_timing_report=f_rpt, wns_ns=wns, P_viv_W=p_viv, n_cu=n_cu, lanes=lanes,
                IL=IL[scheme], ring_depth=DEPTH[scheme], peak_gps=n_cu * lanes * 2 * f_hz / 1e9,
                files={k: dict(path=v, sha256=sha(os.path.join(ROOT, v))) for k, v in files.items()})


def lane_cycles(scheme, n, M):
    if n == 0:
        return 0
    il = IL[scheme]
    nm1 = n - 1
    W, s = nm1 // il, nm1 % il
    ring = (W * M + M - 1) * il + s + 1
    settle = 3 * n
    return max(ring, settle) + il + DEPTH[scheme] + SETTLE_DEPTH


def kernel_seconds(d, M, N):
    """one call: the pairs split evenly over the CUs (host), each CU's pairs over its lanes (kernel split)"""
    worst = 0
    for i in range(d["n_cu"]):
        nc = N * (i + 1) // d["n_cu"] - N * i // d["n_cu"]
        q, rem = divmod(nc, d["lanes"])
        lane0 = lane_cycles(d["scheme"], q + (1 if rem else 0), M)
        over = 32 + d["lanes"] * (JOB_WORDS + SET_WORDS) + d["lanes"] * RES_WORDS + 14 + 100
        worst = max(worst, lane0 + over)
    return worst / d["f_hz"]


def card_run(d, bound, M, N, t0=T0_S):
    tk = kernel_seconds(d, M, N)
    t = tk + t0
    duty = tk / t
    p_card = P0_W + duty * (d["P_viv_W"] - P0_W)
    p = H_W[bound] + p_card
    return dict(t_s=t, t_kernel_s=tk, duty=duty, P_card_W=p_card, P_W=p, E_J=t * p)


def card_steady_per_step(d, bound, M):
    N = 2 ** 31
    r = card_run(d, bound, M, N, t0=0.0)
    ps = 2.0 * N * M
    return dict(E_per_step_J=r["E_J"] / ps, t_per_step_s=r["t_s"] / ps, P_W=r["P_W"], rate_gps=ps / r["t_s"] / 1e9)


# ------------------------------------------------------------------ CPU measurements
def cpu_table():
    t = {}
    for r in csv.DictReader(open(os.path.join(RES, "cpu_pred_summary.csv"))):
        t[(r["kind"], r["impl"], r["boundary"], r["case"], r["scheme"], int(r["steps"]), int(r["pairs"]))] = r
    return t


def cpu_lookup(tab, kind, impl, bound, case, scheme, M, N=None):
    for k, r in tab.items():
        if k[:6] == (kind, impl, bound, case, scheme, M) and (N is None or k[6] == N):
            return r
    return None


# ------------------------------------------------------------------ main
def main():
    for f in ("settings.csv", "cpu_pred_summary.csv"):
        if not os.path.exists(os.path.join(RES, f)):
            sys.exit(f"results/{f} not found")
    D = {s: design(s) for s in ("ft", "qe")}
    rows = [r for r in csv.DictReader(open(os.path.join(RES, "settings.csv"))) if r["rule"] == "empirical"]
    S = {(r["case"], r["target"], r["scheme"]): r for r in rows}
    tab = cpu_table()
    combos = [("card", "ft"), ("card", "qe"), ("cpu_matched", "ft"), ("cpu_matched", "qe"), ("cpu_native", "ft"),
              ("cpu_native", "qe")]
    impl_of = {"cpu_matched": "avx512", "cpu_native": "native"}
    pred = []
    for case in CASES:
        for tgt in TARGETS:
            for bound in ("socket", "server"):
                entry = dict(case=case, target=tgt, boundary=bound, combos={})
                for plat, sc in combos:
                    s = S[(case, tgt, sc)]
                    M, N = int(s["steps"]), int(s["pairs"])
                    W = 2.0 * N * M
                    if plat == "card":
                        r = card_run(D[sc], bound, M, N)
                        ss = card_steady_per_step(D[sc], bound, M)
                        nv_t = W / (D[sc]["peak_gps"] * 1e9)
                        c = dict(t_s=r["t_s"], E_J=r["E_J"], P_W=r["P_W"], t_kernel_s=r["t_kernel_s"], duty=r["duty"],
                                 E_ss_J=W * ss["E_per_step_J"], t_ss_s=W * ss["t_per_step_s"],
                                 naive_t_s=nv_t, naive_E_J=nv_t * (H_W[bound] + P_NAIVE_W))
                    else:
                        im = impl_of[plat]
                        m = cpu_lookup(tab, "set", im, bound, case, sc, M, N)
                        if m is None:
                            sys.exit(f"missing CPU measurement: set {im} {bound} {case} {sc} M={M} N={N}")
                        ssr = cpu_lookup(tab, "ss", im, bound, case, sc, M) or m
                        ps_ss = 2.0 * int(ssr["pairs"]) * int(ssr["steps"])
                        c = dict(t_s=float(m["t_per_run_s"]), E_J=float(m["E_per_run_J"]), P_W=float(m["P_W"]),
                                 E_ss_J=W * float(ssr["E_per_run_J"]) / ps_ss, t_ss_s=W * float(ssr["t_per_run_s"]) / ps_ss,
                                 steady_from=ssr["job"])
                    c.update(steps=M, pairs=N, path_steps=W)
                    entry["combos"][f"{plat}_{sc}"] = c
                cs = entry["combos"]
                entry["winner_energy"] = min(cs, key=lambda k: cs[k]["E_J"])
                entry["winner_energy_steady"] = min(cs, key=lambda k: cs[k]["E_ss_J"])
                entry["winner_time"] = min(cs, key=lambda k: cs[k]["t_s"])
                entry["work_ratio_ft_over_qe"] = cs["card_ft"]["path_steps"] / cs["card_qe"]["path_steps"]
                pred.append(entry)

    def find(case, tgt, bound):
        return next(p for p in pred if p["case"] == case and p["target"] == tgt and p["boundary"] == bound)

    # ---- hypotheses (protocol v0.4, Section 8), predicted
    hyp = {}
    # H1 is a calibration result (no card involved): restated here for completeness
    h1 = {f"{c} {t}": find(c, t, "socket")["work_ratio_ft_over_qe"] for c in ("I", "II", "III") for t in TARGETS}
    hyp["H1"] = dict(ratios=h1, holds=all(v >= (8 if k.endswith(" 1%") else 30) for k, v in h1.items()),
                     note="from the registered calibration; not a card prediction")
    # H2: QE/FT time per path-step in the steady state, every platform (card: model; CPU: measured), cases' settings
    h2 = {}
    for plat in ("card", "cpu_matched", "cpu_native"):
        for bound in ("socket", "server"):
            v = []
            for case in CASES:
                for tgt in TARGETS:
                    cs = find(case, tgt, bound)["combos"]
                    v.append((cs[f"{plat}_qe"]["t_ss_s"] / cs[f"{plat}_qe"]["path_steps"]) /
                             (cs[f"{plat}_ft"]["t_ss_s"] / cs[f"{plat}_ft"]["path_steps"]))
            h2[f"{plat} {bound}"] = dict(median=statistics.median(v), min=min(v), max=max(v))
    hyp["H2"] = dict(qe_over_ft_time_per_step=h2, holds=all(1.2 <= x["median"] <= 3.0 for x in h2.values()),
                     rule="median over the 12 settings of each platform within [1.2, 3]")
    # H3: I-III, every target: CPU socket running QE (matched) needs less energy than the card running FT (socket)
    h3 = {}
    for case in ("I", "II", "III"):
        for tgt in TARGETS:
            cs = find(case, tgt, "socket")["combos"]
            h3[f"{case} {tgt}"] = dict(cpu_qe_J=cs["cpu_matched_qe"]["E_J"], card_ft_J=cs["card_ft"]["E_J"],
                                       ratio_card_ft_over_cpu_qe=cs["card_ft"]["E_J"] / cs["cpu_matched_qe"]["E_J"],
                                       holds=cs["cpu_matched_qe"]["E_J"] < cs["card_ft"]["E_J"],
                                       holds_steady=cs["cpu_matched_qe"]["E_ss_J"] < cs["card_ft"]["E_ss_J"])
    hyp["H3"] = dict(per_setting=h3, holds=all(v["holds"] for v in h3.values()),
                     holds_steady=all(v["holds_steady"] for v in h3.values()))
    # H4: P1: card FT (socket) needs less energy than CPU QE (matched, socket) at 0.3% only
    h4 = {}
    for tgt in TARGETS:
        cs = find("P1", tgt, "socket")["combos"]
        h4[tgt] = dict(card_ft_J=cs["card_ft"]["E_J"], cpu_qe_J=cs["cpu_matched_qe"]["E_J"],
                       card_ft_wins=cs["card_ft"]["E_J"] < cs["cpu_matched_qe"]["E_J"],
                       card_ft_wins_steady=cs["card_ft"]["E_ss_J"] < cs["cpu_matched_qe"]["E_ss_J"])
    want = {"1%": False, "0.3%": True, "0.1%": False}
    hyp["H4"] = dict(per_target=h4, holds=all(h4[t]["card_ft_wins"] == want[t] for t in TARGETS),
                     holds_steady=all(h4[t]["card_ft_wins_steady"] == want[t] for t in TARGETS),
                     registered_pattern=want)

    inputs = {f: dict(sha256=sha(os.path.join(RES, f))) for f in ("settings.csv", "cpu_pred_summary.csv")}
    out = dict(created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), protocol="v0.4 (commit b790a13)",
               constants=dict(P0_W=P0_W, H_W=H_W, T0_S=T0_S, P_NAIVE_W=P_NAIVE_W, tie_band_time=TIE, IL=IL,
                              ring_depth=DEPTH, settle_depth=SETTLE_DEPTH),
               design=D, inputs=inputs, predictions=pred, hypotheses=hyp)
    os.makedirs(OUTD, exist_ok=True)
    jf = os.path.join(OUTD, "predictions.json")
    json.dump(out, open(jf, "w"), indent=1)
    md = markdown(out)
    mf = os.path.join(OUTD, "predictions.md")
    open(mf, "w").write(md)
    for f in (jf, mf):
        h = sha(f)
        open(f + ".sha256", "w").write(f"{h}  {os.path.basename(f)}\n")
        print(f"SHA-256 {h}  {os.path.relpath(f, ROOT)}")
    print(md)


def fmt_e(x):
    return f"{x:.3g}"


def markdown(o):
    L = ["# Paper 2: predictions made before any card run", "",
         f"Created (UTC): {o['created_utc']}. Protocol {o['protocol']}. Inputs: settings.csv sha256 "
         f"{o['inputs']['settings.csv']['sha256'][:16]}…, cpu_pred_summary.csv sha256 {o['inputs']['cpu_pred_summary.csv']['sha256'][:16]}….", "",
         "## Card designs (routed)", "",
         "| Kernel | Clock (MHz) | WNS (ns) | CUs × lanes | Peak (G path-steps/s) | Vivado power (W) |",
         "|---|---|---|---|---|---|"]
    for s in ("ft", "qe"):
        d = o["design"][s]
        L.append(f"| {s.upper()} | {d['f_hz'] / 1e6:.0f} | {d['wns_ns']:+.3f} | {d['n_cu']} × {d['lanes']} | {d['peak_gps']:.2f} | {d['P_viv_W']:.2f} |")
    c = o["constants"]
    L += ["", f"Constants (paper 1, frozen): P0 = {c['P0_W']} W, host while a polling card runs H = {c['H_W']['socket']} W "
          f"(socket) / {c['H_W']['server']} W (server), launch overhead t0 = {c['T0_S'] * 1e3:.1f} ms per call.", "",
          "## Energy to target per pricing run (J), primary outcome, and the predicted winner", "",
          "Columns: card, CPU matched (AVX-512, bit-identical to the card), CPU native; FT and QE each. "
          "Steady state (secondary outcome: no start-up cost, many options per run) in the second table.", ""]
    keys = ["card_ft", "card_qe", "cpu_matched_ft", "cpu_matched_qe", "cpu_native_ft", "cpu_native_qe"]
    for title, field, win in (("Primary (one pricing run, start-up included)", "E_J", "winner_energy"),
                              ("Steady state (secondary)", "E_ss_J", "winner_energy_steady")):
        L += [f"### {title}", "", "| Case | Target | Boundary | " + " | ".join(keys) + " | Winner |",
              "|---|---|---|" + "---|" * len(keys) + "---|"]
        for p in o["predictions"]:
            L.append(f"| {p['case']} | {p['target']} | {p['boundary']} | " +
                     " | ".join(fmt_e(p["combos"][k][field]) for k in keys) + f" | {p[win]} |")
        L.append("")
    L += ["### Time per pricing run (s), primary", "", "| Case | Target | Boundary | " + " | ".join(keys) + " | Fastest |",
          "|---|---|---|" + "---|" * len(keys) + "---|"]
    for p in o["predictions"]:
        L.append(f"| {p['case']} | {p['target']} | {p['boundary']} | " +
                 " | ".join(fmt_e(p["combos"][k]["t_s"]) for k in keys) + f" | {p['winner_time']} |")
    h = o["hypotheses"]
    L += ["", "## Hypotheses (protocol v0.4, Section 8): predicted verdicts", "",
          f"- **H1** (work ratio; calibration, not a card prediction): {'holds' if h['H1']['holds'] else 'fails'}; "
          + ", ".join(f"{k} {v:.1f}" for k, v in h["H1"]["ratios"].items()) + ".",
          f"- **H2** (QE/FT cost per path-step within 1.2–3 on every platform): predicted to "
          f"{'hold' if h['H2']['holds'] else 'FAIL'}. Median (range) by platform: "
          + "; ".join(f"{k} {v['median']:.2f} ({v['min']:.2f}–{v['max']:.2f})" for k, v in h["H2"]["qe_over_ft_time_per_step"].items()) + ".",
          f"- **H3** (cases I–III: CPU socket running QE needs less energy than the card running FT, every target): "
          f"predicted to {'hold' if h['H3']['holds'] else 'FAIL'} (steady state: {'holds' if h['H3']['holds_steady'] else 'fails'}). "
          "Card-FT energy / CPU-QE energy: " + ", ".join(f"{k} {v['ratio_card_ft_over_cpu_qe']:.2f}" for k, v in h["H3"]["per_setting"].items()) + ".",
          f"- **H4** (P1: the card running FT beats the CPU running QE at 0.3% only): predicted to "
          f"{'hold' if h['H4']['holds'] else 'FAIL'} (steady state: {'holds' if h['H4']['holds_steady'] else 'fails'}). "
          + ", ".join(f"{t}: card FT {v['card_ft_J']:.3g} J vs CPU QE {v['cpu_qe_J']:.3g} J" for t, v in h["H4"]["per_target"].items()) + ".",
          "- **H5** is tested against these predictions after the card runs; **H6** is not measured on the card.", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
