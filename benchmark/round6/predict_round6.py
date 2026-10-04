#!/usr/bin/env python3
"""
predict_round6.py - out-of-sample prediction of the card's speedup S and energy advantage A for the two
new kernels (07_FIR, 08_MC_Asian), BEFORE they run on the card.

Inputs (nothing measured on the card):
  model_constants.json            B, P0, host power of each class (frozen from the paper's data)
  design facts (--design)         kernel clock and CUs/lanes from the xclbin, II from HLS, P_viv from the
                                  Vivado routed power report (written by round6_new_kernels.sh design)
  CPU-only runs (--raw)           results_round6/raw/pred/<workload>/<cpu_config>/<rep>  (run_measured.py)
Model (same equations as paper/scripts/time_energy_model.py):
  R_F = 1 / (b_in/B + b_out/B + 1/R_k)            serial host schedule
  d   = R_F / R_k,  P_card = P0 + d (P_viv - P0)
  P_F = H(class, boundary) + P_card
  S   = R_F / R_C,  A = S * P_C / P_F,  S* = P_F / P_C
  naive: S = R_k / R_C, card at 75 W
Output: predictions.json and predictions.md (+ their SHA-256), to be committed before any card run.
"""
import argparse, glob, hashlib, json, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlib import run_record  # noqa: E402

WL = {
    "fir": dict(label="int16 FIR (32 taps)", cls="streaming", unit="Gsample/s", b_in=2.0, b_out=2.0),
    "asian": dict(label="MC Asian option (GBM)", cls="polling", unit="G path-steps/s", b_in=0.0, b_out=0.0),
}


def work(wl, r):
    """units of work inside the measured window (Gsample or G path-steps)"""
    if wl == "fir":
        rep = r["repeat"] if np.isfinite(r["repeat"]) else 1.0
        return r["bytes"] / 2.0 * rep / 1e9
    runs = r["runs"] if np.isfinite(r["runs"]) else 1.0
    return r["paths"] * r["steps"] * runs / 1e9


def cpu_side(raw, wl, cfg):
    recs = [run_record(d) for d in sorted(glob.glob(os.path.join(raw, "pred", wl, cfg, "rep*")))]
    recs = [r for r in recs if r]
    if len(recs) < 3:
        sys.exit(f"need >= 3 valid CPU runs for {wl}/{cfg}, found {len(recs)}")
    R = np.array([work(wl, r) / r["window_s"] for r in recs])
    P = {}
    for dom in ("pkg0", "dram0", "pkg1", "dram1"):
        P[dom] = float(np.median([r[f"Eexact_{dom}"] / r["window_s"] for r in recs]))
    return dict(n=len(recs), R_C=float(np.median(R)), R_C_min=float(R.min()), R_C_max=float(R.max()), P=P,
                dirs=[os.path.relpath(r["_dir"], raw) for r in recs])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="results_round6/raw")
    ap.add_argument("--design", required=True, help="results_round6/design.json")
    ap.add_argument("--out", required=True, help="output directory")
    a = ap.parse_args()
    K = json.load(open(os.path.join(HERE, "model_constants.json")))
    D = json.load(open(a.design))
    B, P0 = K["B_GBps"], K["P0_W"]
    out = dict(created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), model_constants=K, design=D,
               predictions=[])
    for wl, m in WL.items():
        d = D[wl]
        if wl == "fir":
            R_k = d["n_cu"] * 16 * d["f_mhz"] * 1e6 / d.get("ii", 1) / 1e9          # Gsample/s
        else:
            R_k = d["n_cu"] * d["lanes"] * 2 * d["f_mhz"] * 1e6 / d.get("ii", 1) / 1e9   # G path-steps/s
        bi, bo = m["b_in"], m["b_out"]
        R_F = 1.0 / (bi / B + bo / B + 1.0 / R_k)
        R_up = min(R_k, B / max(bi, bo)) if max(bi, bo) > 0 else R_k
        duty = R_F / R_k
        P_card = P0 + duty * (d["P_viv_W"] - P0)
        for b, cfg in K["boundary_cpu_config"].items():
            c = cpu_side(a.raw, wl, cfg)
            P_C = c["P"]["pkg0"] + c["P"]["dram0"] + ((c["P"]["pkg1"] + c["P"]["dram1"]) if b == "server" else 0.0)
            H = K["H_W"][m["cls"]][b]
            P_F = H + P_card
            S = R_F / c["R_C"]
            A = S * P_C / P_F
            S_naive = R_k / c["R_C"]
            A_naive = S_naive * P_C / (H + K["P_card_naive_W"])
            lo, hi = K["tie_band_S"]
            out["predictions"].append(dict(
                workload=wl, label=m["label"], host_class=m["cls"], boundary=b, cpu_config=cfg, unit=m["unit"],
                R_k=R_k, R_F_pred=R_F, R_F_upper=R_up, duty=duty, P_viv_W=d["P_viv_W"], P_card_pred_W=P_card,
                H_pred_W=H, P_F_pred_W=P_F, R_C=c["R_C"], R_C_range=[c["R_C_min"], c["R_C_max"]], P_C_W=P_C,
                cpu_runs=c["n"], cpu_run_dirs=c["dirs"],
                S_pred=S, S_upper=R_up / c["R_C"], Sstar_pred=P_F / P_C, A_pred=A,
                time_verdict="FPGA faster" if S > hi else ("CPU faster" if S < lo else "tie (0.8-1.2)"),
                energy_verdict="FPGA less energy" if A > 1 else "CPU less energy",
                S_naive=S_naive, A_naive=A_naive))
    os.makedirs(a.out, exist_ok=True)
    jf = os.path.join(a.out, "predictions.json")
    json.dump(out, open(jf, "w"), indent=1)
    lines = ["# Round 6: predictions made before the new kernels ran on the card", "",
             f"Created (UTC): {out['created_utc']}", "",
             "| Workload | Boundary | R_k | R_F pred | R_C (CPU) | S pred | S* pred | A pred | Time verdict | Energy verdict | naive S / A |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in out["predictions"]:
        lines.append(f"| {p['label']} | {p['boundary']} | {p['R_k']:.3f} | {p['R_F_pred']:.3f} | {p['R_C']:.3f} | "
                     f"{p['S_pred']:.2f} | {p['Sstar_pred']:.2f} | {p['A_pred']:.2f} | {p['time_verdict']} | "
                     f"{p['energy_verdict']} | {p['S_naive']:.2f} / {p['A_naive']:.2f} |")
    lines += ["", f"Units: {WL['fir']['unit']} (FIR), {WL['asian']['unit']} (MC Asian).",
              "Design facts: " + json.dumps(D), ""]
    mf = os.path.join(a.out, "predictions.md")
    open(mf, "w").write("\n".join(lines))
    for f in (jf, mf):
        h = hashlib.sha256(open(f, "rb").read()).hexdigest()
        open(f + ".sha256", "w").write(f"{h}  {os.path.basename(f)}\n")
        print(f"SHA-256 {h}  {f}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
