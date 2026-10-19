#!/usr/bin/env python3
"""predict_s.py - the predictions of the secondary cases (protocol v0.5, Section 8b), made BEFORE any card run of the
secondary-case kernels, from
  - the chosen settings                 results/settings_s.csv (scripts/calibrate_s.py)
  - the CPU-only measurements           results/cpu_pred_summary_s.csv (scripts/cpu_measure_s.py)
  - the routed card designs             build/{bft,bqe,asian}/FREQ_HZ, build/<s>/reports/ (timing, power, util), build log
  - paper 1's frozen model constants    (as scripts/predict.py)
  - for the INFORMED variant only: the main-case card measurements (results/phase5_summary_s1.csv, _s2.csv) and the
    registered main-case prediction (results/predictions/predictions.json), by the fixed rules below.
Nothing here is measured on the card with the new kernels. Output: results/predictions_s/predictions_s.json and
predictions_s.md, each with a .sha256 file; both are committed to GitHub before the first card run.

Two predictions (protocol v0.5, Section 8b):
  same      the method of the main cases: t0 = 0.7 ms (paper 1), P_card = P0 + d (P_viv - P0)
  informed  t0 = median over the main-case card runs whose predicted kernel time is below 1 ms of (measured time per
            run - predicted kernel time); P_card = P0 + d c (P_viv - P0), with c = median over the main-case card
            steady-state runs of (measured P_card - P0) / (P_viv - P0). Host power H as in "same".
H5 is tested on "same"; the paper reports how much "informed" improves it.

Card cycle model (exact trip counts of the kernels, as predict.py):
  basket (hb_ft, hb_qe): a lane gets n basket pairs = 8 n units; ring = (W M + M - 1) IL + s + 1 with W, s from
          8 n - 1; settle = 8 n + 3 (one unit per cycle, then the three strikes of the last basket);
          lane cycles = max(ring, settle) + IL + ring depth + settle depth
  Asian  (ha_asian): ring over n pairs as above; settle = 3 n; same sum
  one call: pairs split evenly over the CUs (host), then over the lanes (kernel); plus start-up/result words.
Work: path-steps W = 2 N M (for the basket a path-step is a basket path-step = 8 asset-steps).
Usage (from the project folder):  python3 scripts/predict_s.py
"""
import csv
import hashlib
import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
RES = os.path.join(ROOT, "results")
OUTD = os.path.join(RES, "predictions_s")

# ---- paper 1's frozen constants (as predict.py)
P0_W = 17.0
H_W = {"socket": 49.151, "server": 92.940}
T0_S = 0.7e-3
P_NAIVE_W = 75.0
# ---- kernel facts (kernel/hb_kernel.h, kernel/ha_kernel.h; HLS logs of the routed builds: all loops II = 1)
KERNEL = {"bft": "hb_ft", "bqe": "hb_qe", "asian": "ha_asian"}
IL = {"bft": 128, "bqe": 256, "asian": 128}
DEPTH = {"bft": 53, "bqe": 146, "asian": 79}          # RING pipeline depth (HLS logs, 15-16 Oct builds)
SETTLE_DEPTH = {"bft": 79, "bqe": 79, "asian": 58}
WORDS = {"bft": (48, 26, 14, 14, 14), "bqe": (48, 26, 14, 14, 14), "asian": (16, 11, 7, 32, 32)}   # fpar, job, set, res, out
TARGETS = ("1%", "0.3%", "0.1%")
BUILD = {("B", "ft"): "bft", ("B", "qe"): "bqe", ("A", "plain"): "asian", ("A", "cv"): "asian"}
SCHEMES = {"B": ("ft", "qe"), "A": ("plain", "cv")}
MAXN = {"B": 2 ** 29, "A": 2 ** 31}


def sha(f):
    return hashlib.sha256(open(f, "rb").read()).hexdigest()


# ------------------------------------------------------------------ design facts from the routed builds
def design(s):
    b = os.path.join(ROOT, "build", s)
    rep = os.path.join(b, "reports")
    f_hz = int(open(os.path.join(b, "FREQ_HZ")).read().strip())
    tim = open(os.path.join(rep, "impl_1_hw_bb_locked_timing_summary_routed.rpt")).read()
    m = re.search(r"Design Timing Summary.*?\n\s*-+.*?\n\s*-+\s*-+.*?\n\s*([-\d.]+)\s+([-\d.]+)", tim, re.S)
    wns = float(m.group(1)) if m else float("nan")
    pw = open(os.path.join(rep, "impl_1_hw_bb_locked_power_routed.rpt")).read()
    p_viv = float(re.search(r"Total On-Chip Power \(W\)\s*\|\s*([\d.]+)", pw).group(1))
    util = open(os.path.join(rep, "impl_1_kernel_util_routed.rpt")).read()
    n_cu = len(set(re.findall(rf"\|\s+({KERNEL[s]}_\d+)\s+\|", util)))
    log = open(os.path.join(ROOT, "build", "build_xclbins_s.log")).read()
    lanes = int(re.findall(rf"=== {s} \({KERNEL[s]}, (\d+) lanes per CU", log)[-1])
    files = {k: os.path.relpath(os.path.join(rep, v), ROOT) for k, v in
             (("timing", "impl_1_hw_bb_locked_timing_summary_routed.rpt"), ("power", "impl_1_hw_bb_locked_power_routed.rpt"),
              ("util", "impl_1_kernel_util_routed.rpt"))}
    per_cycle = 2 / 8 if s.startswith("b") else 2      # path-steps per lane-cycle (basket: 8 asset-steps per path-step)
    return dict(build=s, kernel=KERNEL[s], f_hz=f_hz, wns_ns=wns, P_viv_W=p_viv, n_cu=n_cu, lanes=lanes, IL=IL[s],
                ring_depth=DEPTH[s], settle_depth=SETTLE_DEPTH[s], peak_gps=n_cu * lanes * per_cycle * f_hz / 1e9,
                files={k: dict(path=v, sha256=sha(os.path.join(ROOT, v))) for k, v in files.items()})


def lane_cycles(s, n, M):
    if n == 0:
        return 0
    il = IL[s]
    units = 8 * n if s.startswith("b") else n
    W, r = (units - 1) // il, (units - 1) % il
    ring = (W * M + M - 1) * il + r + 1
    settle = 8 * n + 3 if s.startswith("b") else 3 * n
    return max(ring, settle) + il + DEPTH[s] + SETTLE_DEPTH[s]


def kernel_seconds(d, M, N):
    s = d["build"]
    fw, jw, sw, rw, ow = WORDS[s]
    worst = 0
    for i in range(d["n_cu"]):
        nc = N * (i + 1) // d["n_cu"] - N * i // d["n_cu"]
        q, rem = divmod(nc, d["lanes"])
        lane0 = lane_cycles(s, q + (1 if rem else 0), M)
        over = fw + d["lanes"] * (jw + sw) + d["lanes"] * rw + ow + 100
        worst = max(worst, lane0 + over)
    return worst / d["f_hz"]


def card_run(d, bound, M, N, t0, c_pow):
    tk = kernel_seconds(d, M, N)
    t = tk + t0
    duty = tk / t
    p_card = P0_W + duty * c_pow * (d["P_viv_W"] - P0_W)
    p = H_W[bound] + p_card
    return dict(t_s=t, t_kernel_s=tk, duty=duty, P_card_W=p_card, P_W=p, E_J=t * p)


def card_steady_per_step(d, bound, M, nmax, c_pow):
    r = card_run(d, bound, M, nmax, 0.0, c_pow)
    ps = 2.0 * nmax * M
    return dict(E_per_step_J=r["E_J"] / ps, t_per_step_s=r["t_s"] / ps, P_W=r["P_W"], rate_gps=ps / r["t_s"] / 1e9)


# ------------------------------------------------------------------ informed constants (fixed rules, docstring)
def informed_constants():
    pj = json.load(open(os.path.join(RES, "predictions", "predictions.json")))
    pk = {}
    for p in pj["predictions"]:
        if p["boundary"] == "socket":
            for sc in ("ft", "qe"):
                pk[(p["case"], p["target"], sc)] = p["combos"][f"card_{sc}"]["t_kernel_s"]
    viv = {s: pj["design"][s]["P_viv_W"] for s in ("ft", "qe")}
    rows = [x for f in ("phase5_summary_s1.csv", "phase5_summary_s2.csv") for x in csv.DictReader(open(os.path.join(RES, f)))
            if x["impl"] == "fpga"]
    over = [float(x["t_per_run_s"]) - pk[(x["case"], x["target"], x["scheme"])] for x in rows
            if x["kind"] == "set" and pk[(x["case"], x["target"], x["scheme"])] < 1e-3]
    ratio = [(float(x["P_card_W"]) - P0_W) / (viv[x["scheme"]] - P0_W) for x in rows if x["kind"] == "ss"]
    return dict(t0_s=statistics.median(over), t0_runs=len(over), t0_min_s=min(over), t0_max_s=max(over),
                c_pow=statistics.median(ratio), c_runs=len(ratio), c_min=min(ratio), c_max=max(ratio),
                sources={f: sha(os.path.join(RES, f)) for f in ("phase5_summary_s1.csv", "phase5_summary_s2.csv",
                                                                os.path.join("predictions", "predictions.json"))})


# ------------------------------------------------------------------ CPU measurements
def cpu_table():
    t = {}
    for r in csv.DictReader(open(os.path.join(RES, "cpu_pred_summary_s.csv"))):
        t[(r["kind"], r["impl"], r["boundary"], r["case"], r["scheme"], int(r["steps"]), int(r["pairs"]))] = r
    return t


def cpu_lookup(tab, kind, impl, bound, case, scheme, M, N=None):
    for k, r in tab.items():
        if k[:6] == (kind, impl, bound, case, scheme, M) and (N is None or k[6] == N):
            return r
    return None


def predict(D, S, tab, t0, c_pow):
    impl_of = {"cpu_matched": "avx512", "cpu_native": "native"}
    pred = []
    for case in ("B", "A"):
        for tgt in TARGETS:
            for bound in ("socket", "server"):
                entry = dict(case=case, target=tgt, boundary=bound, combos={})
                for plat in ("card", "cpu_matched", "cpu_native"):
                    for sc in SCHEMES[case]:
                        s = S[(case, tgt, sc)]
                        M, N = int(s["steps"]), int(s["pairs"])
                        W = 2.0 * N * M
                        if plat == "card":
                            d = D[BUILD[(case, sc)]]
                            r = card_run(d, bound, M, N, t0, c_pow)
                            ss = card_steady_per_step(d, bound, M, MAXN[case], c_pow)
                            nv_t = W / (d["peak_gps"] * 1e9)
                            c = dict(t_s=r["t_s"], E_J=r["E_J"], P_W=r["P_W"], P_card_W=r["P_card_W"], t_kernel_s=r["t_kernel_s"],
                                     duty=r["duty"], E_ss_J=W * ss["E_per_step_J"], t_ss_s=W * ss["t_per_step_s"],
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
                a, b = SCHEMES[case]
                entry["work_ratio"] = cs[f"card_{a}"]["path_steps"] / cs[f"card_{b}"]["path_steps"]
                pred.append(entry)
    return pred


def hypotheses(pred):
    def find(case, tgt, bound):
        return next(p for p in pred if p["case"] == case and p["target"] == tgt and p["boundary"] == bound)

    def best(cs, sc, field):
        return min(cs[f"{p}_{sc}"][field] for p in ("card", "cpu_matched", "cpu_native"))

    def best_cpu(cs, sc, field):
        return min(cs[f"{p}_{sc}"][field] for p in ("cpu_matched", "cpu_native"))

    hyp = {}
    hb1 = {t: find("B", t, "socket")["work_ratio"] for t in TARGETS}
    hyp["HB1"] = dict(work_ratio_ft_over_qe=hb1, holds=all(2 <= v <= 20 for v in hb1.values()),
                      note="from the calibration; not a card prediction")
    hb2 = {}
    for t in TARGETS:
        cs = find("B", t, "socket")["combos"]
        hb2[t] = best(cs, "ft", "E_ss_J") / best(cs, "qe", "E_ss_J")
    hyp["HB2"] = dict(algorithm_factor_steady_socket=hb2, holds=all(1 <= v <= 10 for v in hb2.values()),
                      rule="FT/QE energy in the steady state, best platform for each scheme, socket; within [1, 10] at every target")
    ha1 = {t: find("A", t, "socket")["work_ratio"] for t in TARGETS}
    hyp["HA1"] = dict(work_ratio_plain_over_cv=ha1, holds=all(v >= 100 for v in ha1.values()),
                      note="from the calibration; not a card prediction")
    ha2 = {}
    for t in TARGETS:
        for bound in ("socket", "server"):
            cs = find("A", t, bound)["combos"]
            for field, out in (("E_J", "primary"), ("E_ss_J", "steady")):
                alg = best(cs, "plain", field) / best(cs, "cv", field)
                pf = {sc: best_cpu(cs, sc, field) / cs[f"card_{sc}"][field] for sc in ("plain", "cv")}
                mag = max(max(v, 1 / v) for v in pf.values())
                ha2[f"{t} {bound} {out}"] = dict(algorithm_factor=alg, platform_factor_best_cpu_over_card=pf,
                                                 platform_gain=mag, holds=alg > mag)
        cs = find("A", t, "socket")["combos"]
        ha2[f"{t} socket CPU-CV < card-plain"] = dict(cpu_cv_J=cs["cpu_matched_cv"]["E_J"], card_plain_J=cs["card_plain"]["E_J"],
                                                     holds=cs["cpu_matched_cv"]["E_J"] < cs["card_plain"]["E_J"])
    hyp["HA2"] = dict(per=ha2, holds=all(v["holds"] for v in ha2.values()),
                      rule="algorithm factor (plain/CV, best platform each) > platform gain (best CPU vs card, larger of the "
                           "ratio and its inverse, either estimator), every target, both boundaries, both outcomes; and the "
                           "CPU socket running CV (matched code) needs less energy than the card running plain")
    ha3 = {}
    for t in TARGETS:
        cs = find("A", t, "socket")["combos"]
        ha3[t] = dict(card_cv_J=cs["card_cv"]["E_ss_J"], best_cpu_cv_J=best_cpu(cs, "cv", "E_ss_J"),
                      ratio_cpu_over_card=best_cpu(cs, "cv", "E_ss_J") / cs["card_cv"]["E_ss_J"],
                      holds=cs["card_cv"]["E_ss_J"] < best_cpu(cs, "cv", "E_ss_J"))
    hyp["HA3"] = dict(per_target=ha3, holds=all(v["holds"] for v in ha3.values()),
                      rule="steady state, socket: the card running CV needs less energy than the best CPU code running CV, every target")
    return hyp


def main():
    for f in ("settings_s.csv", "cpu_pred_summary_s.csv", "phase5_summary_s1.csv", "phase5_summary_s2.csv",
              os.path.join("predictions", "predictions.json")):
        if not os.path.exists(os.path.join(RES, f)):
            sys.exit(f"results/{f} not found")
    D = {s: design(s) for s in ("bft", "bqe", "asian")}
    rows = [r for r in csv.DictReader(open(os.path.join(RES, "settings_s.csv"))) if r["rule"] == "empirical" and r.get("steps")]
    S = {(r["case"], r["target"], r["scheme"]): r for r in rows}
    tab = cpu_table()
    inf = informed_constants()
    variants = {}
    for name, t0, c_pow in (("same", T0_S, 1.0), ("informed", inf["t0_s"], inf["c_pow"])):
        pred = predict(D, S, tab, t0, c_pow)
        variants[name] = dict(t0_s=t0, c_pow=c_pow, predictions=pred, hypotheses=hypotheses(pred))
    inputs = {f: dict(sha256=sha(os.path.join(RES, f))) for f in ("settings_s.csv", "cpu_pred_summary_s.csv", "reference_s.csv", "beta_A.csv")}
    out = dict(created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), protocol="v0.5 (commit d16fe83)",
               constants=dict(P0_W=P0_W, H_W=H_W, T0_S=T0_S, P_NAIVE_W=P_NAIVE_W, IL=IL, ring_depth=DEPTH, settle_depth=SETTLE_DEPTH),
               informed_constants=inf, design=D, inputs=inputs, variants=variants)
    os.makedirs(OUTD, exist_ok=True)
    jf = os.path.join(OUTD, "predictions_s.json")
    json.dump(out, open(jf, "w"), indent=1)
    md = markdown(out)
    mf = os.path.join(OUTD, "predictions_s.md")
    open(mf, "w").write(md)
    for f in (jf, mf):
        h = sha(f)
        open(f + ".sha256", "w").write(f"{h}  {os.path.basename(f)}\n")
        print(f"SHA-256 {h}  {os.path.relpath(f, ROOT)}")
    print(md)


def fmt(x):
    return f"{x:.3g}"


def markdown(o):
    L = ["# Paper 2, secondary cases: predictions made before any card run of the new kernels", "",
         f"Created (UTC): {o['created_utc']}. Protocol {o['protocol']}. Inputs (sha256): " +
         ", ".join(f"{k} {v['sha256'][:16]}…" for k, v in o["inputs"].items()) + ".", "",
         "## Card designs (routed)", "",
         "| Kernel | Clock (MHz) | WNS (ns) | CUs × lanes | Peak (G path-steps/s) | Vivado power (W) |", "|---|---|---|---|---|---|"]
    for s in ("bft", "bqe", "asian"):
        d = o["design"][s]
        L.append(f"| {d['kernel']} | {d['f_hz'] / 1e6:.0f} | {d['wns_ns']:+.3f} | {d['n_cu']} × {d['lanes']} | {d['peak_gps']:.3f} | {d['P_viv_W']:.2f} |")
    i = o["informed_constants"]
    L += ["", "Basket rates are in basket path-steps (8 asset-steps each).", "",
          f"**Two variants.** *same*: t0 = {o['constants']['T0_S'] * 1e3:.2f} ms, card power P0 + d (P_viv − P0) (paper 1). "
          f"*informed*: t0 = {i['t0_s'] * 1e3:.3f} ms (median of {i['t0_runs']} main-case card runs with a predicted kernel time "
          f"below 1 ms; range {i['t0_min_s'] * 1e3:.2f}–{i['t0_max_s'] * 1e3:.2f} ms), card power P0 + d c (P_viv − P0) with "
          f"c = {i['c_pow']:.3f} (median of {i['c_runs']} main-case steady-state card runs; range {i['c_min']:.2f}–{i['c_max']:.2f}). "
          f"Host H = {o['constants']['H_W']['socket']} W (socket) / {o['constants']['H_W']['server']} W (server) in both. "
          "H5 is tested on *same*. (Protocol v0.5, Section 8b, quoted the measured start-up cost as \"about 0.2 ms\": that is "
          "the value of the very smallest main-case runs. The rule above was fixed before any card run of the new kernels and "
          "gives the value shown.)", ""]
    for vname, v in o["variants"].items():
        L += [f"## Variant: {vname}", ""]
        for case, keys in (("B", ["card_ft", "card_qe", "cpu_matched_ft", "cpu_matched_qe", "cpu_native_ft", "cpu_native_qe"]),
                           ("A", ["card_plain", "card_cv", "cpu_matched_plain", "cpu_matched_cv", "cpu_native_plain", "cpu_native_cv"])):
            for title, field, win in (("energy per pricing run (J), primary", "E_J", "winner_energy"),
                                      ("energy, steady state (J), secondary", "E_ss_J", "winner_energy_steady"),
                                      ("time per pricing run (s)", "t_s", "winner_time")):
                L += [f"### Case {case}: {title}", "", "| Target | Boundary | " + " | ".join(keys) + " | Winner |",
                      "|---|---|" + "---|" * len(keys) + "---|"]
                for p in v["predictions"]:
                    if p["case"] == case:
                        L.append(f"| {p['target']} | {p['boundary']} | " + " | ".join(fmt(p["combos"][k][field]) for k in keys) + f" | {p[win]} |")
                L.append("")
        h = v["hypotheses"]
        L += [f"### Hypotheses, variant {vname}", "",
              f"- **HB1** (FT/QE work 2–20; calibration): {'holds' if h['HB1']['holds'] else 'fails'}; "
              + ", ".join(f"{t} {x:.1f}" for t, x in h["HB1"]["work_ratio_ft_over_qe"].items()) + ".",
              f"- **HB2** (basket algorithm factor 1–10, steady state, socket): predicted to {'hold' if h['HB2']['holds'] else 'FAIL'}; "
              + ", ".join(f"{t} {x:.2f}" for t, x in h["HB2"]["algorithm_factor_steady_socket"].items()) + ".",
              f"- **HA1** (plain/CV work ≥ 100; calibration): {'holds' if h['HA1']['holds'] else 'fails'}; "
              + ", ".join(f"{t} {x:.0f}" for t, x in h["HA1"]["work_ratio_plain_over_cv"].items()) + ".",
              f"- **HA2** (algorithm factor > platform gain; CPU CV < card plain): predicted to {'hold' if h['HA2']['holds'] else 'FAIL'}. "
              + "; ".join(f"{k}: {x['algorithm_factor']:.0f} vs {x['platform_gain']:.2f}" for k, x in h["HA2"]["per"].items()
                          if "algorithm_factor" in x and "socket" in k) + ".",
              f"- **HA3** (steady state, socket: card CV < best CPU CV): predicted to {'hold' if h['HA3']['holds'] else 'FAIL'}; "
              + ", ".join(f"{t} best CPU / card {x['ratio_cpu_over_card']:.2f}" for t, x in h["HA3"]["per_target"].items()) + ".", ""]
    L += ["H5 (model: energy within 20%, every winner and pairwise verdict, RMSE within 1.1 × target) is tested on variant *same* "
          "after the card runs, and reported separately from the main cases.", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
