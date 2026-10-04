#!/usr/bin/env python3
"""
predict_round7.py - near-break-even test of the time-energy model: the MC Asian kernel rebuilt with
L lanes whose number of ACTIVE lanes l is chosen at run time. The card's rate grows with l, the CPU's
does not, so as l goes from 1 to L the verdicts cross over: energy per socket first, then time per socket
and energy per server. The prediction below is written and hashed BEFORE the sweep design ever runs on
the card.

Inputs (nothing measured on the card):
  model_constants.json   B, P0, host power of each class, tie band (frozen, as in round 6)
  --design design.json   clock, CUs, lanes L, II, P_viv of the routed sweep design (round7_sweep.sh design)
  --raw                  CPU-only runs results_round7/raw/pred/asian/<cpu_config>/<rep>
Model (paper Section 6.6, unchanged; the kernel moves no data, so R_F = R_k):
  R_k(l) = n_cu * l * 2 * f / II            G path-steps/s (antithetic pair = 2 path-steps per cycle)
  d(l)   = l / L                            share of the datapath that works (= R_F / R_k of the full design)
  P_card = P0 + d (P_viv - P0),  P_F = H(polling, boundary) + P_card
  S = R_F / R_C,  A = S * P_C / P_F,  S* = P_F / P_C
Verdicts: time  -> "FPGA faster" if S > 1.2, "CPU faster" if S < 1/1.2, else "tie";
          energy -> "FPGA less energy" if A > 1, else "CPU less energy".
A verdict is "near break-even" when the in-sample error band (+-20 %) around the prediction crosses a
threshold. Crossovers: the (continuous) number of active lanes at which S = 1/1.2, 1, 1.2 and A = 1.
Output: predictions.json, predictions.md and their SHA-256 (to be published before any card run).
"""
import argparse, glob, hashlib, json, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlib import run_record  # noqa: E402


def cpu_side(raw, cfg):
    recs = [run_record(d) for d in sorted(glob.glob(os.path.join(raw, "pred", "asian", cfg, "rep*")))]
    recs = [r for r in recs if r]
    if len(recs) < 3:
        sys.exit(f"need >= 3 valid CPU runs for asian/{cfg}, found {len(recs)}")
    R = np.array([r["paths"] * r["steps"] * (r["runs"] if np.isfinite(r["runs"]) else 1.0) / 1e9 / r["window_s"]
                  for r in recs])
    P = {dom: float(np.median([r[f"Eexact_{dom}"] / r["window_s"] for r in recs]))
         for dom in ("pkg0", "dram0", "pkg1", "dram1")}
    return dict(n=len(recs), R_C=float(np.median(R)), R_C_min=float(R.min()), R_C_max=float(R.max()), P=P,
                dirs=[os.path.relpath(r["_dir"], raw) for r in recs])


def tverdict(S, lo, hi):
    return "FPGA faster" if S > hi else ("CPU faster" if S < lo else "tie")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--lanes", required=True, help="active-lane counts of the sweep, e.g. 1,2,...,16")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    K = json.load(open(os.path.join(HERE, "model_constants.json")))
    D = json.load(open(a.design))
    d = D["sweep"]
    P0, eb = K["P0_W"], K["in_sample_error_band"]
    lo, hi = K["tie_band_S"]
    L, f, ncu, ii = d["lanes"], d["f_mhz"] * 1e6, d["n_cu"], d.get("ii", 1)
    per_lane = ncu * 2 * f / ii / 1e9                     # G path-steps/s per active lane
    lanes = [int(x) for x in a.lanes.split(",")]
    if max(lanes) > L:
        sys.exit(f"sweep asks for {max(lanes)} active lanes but the design has {L}")
    out = dict(created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), model_constants=K, design=D,
               per_lane_G=per_lane, lanes=lanes, predictions=[], crossovers={}, scoring=dict(
                   time="measured median S classified with the same tie band; a prediction is right when the classes agree",
                   energy="measured median A classified as A > 1; a prediction is right when the classes agree",
                   crossover="measured crossover = log-linear interpolation of the measured medians between sweep points",
                   near="near break-even = the +-20 % in-sample error band around the prediction crosses a threshold"),
               hypotheses=dict(
                   H1="every verdict NOT flagged near break-even is right (time and energy, both boundaries)",
                   H2="every measured crossover (S = 1/1.2, 1, 1.2 and A = 1 where measured inside the sweep) lies within "
                      "+-20 % of the predicted number of active lanes",
                   H3="the model's energy verdicts are right at least as often as the naive (75 W card) estimate's",
                   note="verdicts flagged near break-even are reported, not counted as a pass/fail test: inside the error "
                        "band the model does not claim to know the sign"))
    for b, cfg in K["boundary_cpu_config"].items():
        c = cpu_side(a.raw, cfg)
        P_C = c["P"]["pkg0"] + c["P"]["dram0"] + ((c["P"]["pkg1"] + c["P"]["dram1"]) if b == "server" else 0.0)
        H = K["H_W"]["polling"][b]
        for l in lanes:
            R_F = per_lane * l
            duty = l / L
            P_card = P0 + duty * (d["P_viv_W"] - P0)
            P_F = H + P_card
            S = R_F / c["R_C"]
            A = S * P_C / P_F
            A_naive = S * P_C / (H + K["P_card_naive_W"])
            near_t = any(S * (1 - eb) < t < S * (1 + eb) for t in (lo, hi))
            near_e = A * (1 - eb) < 1 < A * (1 + eb)
            out["predictions"].append(dict(
                boundary=b, cpu_config=cfg, active_lanes=l, R_F_pred=R_F, duty=duty, P_card_pred_W=P_card, H_pred_W=H,
                P_F_pred_W=P_F, R_C=c["R_C"], R_C_range=[c["R_C_min"], c["R_C_max"]], P_C_W=P_C, cpu_runs=c["n"],
                S_pred=S, Sstar_pred=P_F / P_C, A_pred=A, A_naive=A_naive,
                time_verdict=tverdict(S, lo, hi), energy_verdict="FPGA less energy" if A > 1 else "CPU less energy",
                energy_verdict_naive="FPGA less energy" if A_naive > 1 else "CPU less energy",
                near_break_even_time=near_t, near_break_even_energy=near_e))
        # continuous crossovers in l (S is linear in l; A = 1 solves l * (per_lane*P_C/R_C - (P_viv-P0)/L) = H + P0)
        k = per_lane / c["R_C"]
        den = k * P_C - (d["P_viv_W"] - P0) / L
        out["crossovers"][b] = dict(
            lanes_S_lo=lo / k, lanes_S_1=1.0 / k, lanes_S_hi=hi / k,
            lanes_A_1=(H + P0) / den if den > 0 else None, R_C=c["R_C"], P_C_W=P_C, cpu_run_dirs=c["dirs"])
    os.makedirs(a.out, exist_ok=True)
    jf = os.path.join(a.out, "predictions.json")
    json.dump(out, open(jf, "w"), indent=1)
    rows = ["# Round 7: near-break-even predictions (MC Asian, active-lane sweep), made before any card run", "",
            f"Created (UTC): {out['created_utc']}", "",
            f"Design: {L} lanes, {d['f_mhz']:.0f} MHz, {ncu} CU, II {ii}, P_viv {d['P_viv_W']:.2f} W; "
            f"{per_lane:.3f} G path-steps/s per active lane.", "",
            "| Boundary | Active lanes | R_F pred | R_C | S pred | S* pred | A pred | Time verdict | Energy verdict | near (time/energy) |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for p in out["predictions"]:
        rows.append(f"| {p['boundary']} | {p['active_lanes']} | {p['R_F_pred']:.3f} | {p['R_C']:.3f} | {p['S_pred']:.3f} | "
                    f"{p['Sstar_pred']:.3f} | {p['A_pred']:.3f} | {p['time_verdict']} | {p['energy_verdict']} | "
                    f"{'yes' if p['near_break_even_time'] else 'no'} / {'yes' if p['near_break_even_energy'] else 'no'} |")
    rows += ["", "Predicted crossovers (number of active lanes):", "",
             "| Boundary | S = 1/1.2 | S = 1 | S = 1.2 | A = 1 |", "|---|---|---|---|---|"]
    for b, x in out["crossovers"].items():
        a1 = f"{x['lanes_A_1']:.2f}" if x["lanes_A_1"] else "none"
        rows.append(f"| {b} | {x['lanes_S_lo']:.2f} | {x['lanes_S_1']:.2f} | {x['lanes_S_hi']:.2f} | {a1} |")
    rows += ["", "Scoring rules (fixed now):", ""] + [f"- {k}: {v}" for k, v in out["scoring"].items()]
    rows += ["", "Hypotheses (fixed now):", ""] + [f"- {k}: {v}" for k, v in out["hypotheses"].items()] + [""]
    mf = os.path.join(a.out, "predictions.md")
    open(mf, "w").write("\n".join(rows))
    for fpath in (jf, mf):
        h = hashlib.sha256(open(fpath, "rb").read()).hexdigest()
        open(fpath + ".sha256", "w").write(f"{h}  {os.path.basename(fpath)}\n")
        print(f"SHA-256 {h}  {fpath}")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
