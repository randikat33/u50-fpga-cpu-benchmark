#!/usr/bin/env python3
"""
round6_analysis.py - out-of-sample test of the time-energy model (round6_new_kernels.sh).

Compares the predictions made BEFORE the card runs (results_round6/predictions/predictions.json, SHA-256
checked) with the measurements (raw/meas: card runs interleaved with fresh CPU runs of the same session).
Measured values use the paper's estimators: S = ratio of median rates, A = ratio of median energies per
unit of work at the boundary (socket: pkg0+dram0 [+card]; server: all four RAPL domains [+card]),
percentile bootstrap (4,000 resamples, seed 1) and the full min-max range over all pairs of runs.
Also reported: the CPU rate of the prediction runs vs the measurement runs (session drift), the card's
measured power vs P_card predicted, and whether each verdict (time: tie band 0.8-1.2; energy: A > 1) held.

Part 2 (mcnative): the paper's MC Heston card against the bit-exact AVX-512 CPU baseline and a CPU-native
baseline (Box-Muller + libmvec + fast-math); S and A for both, and the native/bit-exact CPU speed ratio.

Input : paper/inputs/results_round6/  (unpacked results_round6_<date>.tar.gz)    [R6_IN]
Output: paper/data/r6_model_test.csv, r6_mcnative.csv, r6_summary.json          [R6_OUT]
"""
import glob, hashlib, json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlib import run_record  # noqa: E402

R6 = os.environ.get("R6_IN", os.path.join(HERE, "..", "inputs", "results_round6"))
OUT = os.environ.get("R6_OUT", os.path.join(HERE, "..", "data"))
RAW = os.path.join(R6, "raw")
N_BOOT, SEED = 4000, 1
DOMS = {"socket": ["pkg0", "dram0"], "server": ["pkg0", "dram0", "pkg1", "dram1"]}


def boot_ratio(num, den, n=N_BOOT, seed=SEED):
    rng = np.random.default_rng(seed)
    num, den = np.asarray(num, float), np.asarray(den, float)
    s = [np.median(rng.choice(num, len(num))) / np.median(rng.choice(den, len(den))) for _ in range(n)]
    return float(np.median(num) / np.median(den)), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def work(wl, r):
    if wl.startswith("fir"):
        rep = r["repeat"] if np.isfinite(r["repeat"]) else 1.0
        return r["bytes"] / 2.0 * rep / 1e9
    runs = r["runs"] if np.isfinite(r["runs"]) else 1.0
    return r["paths"] * r["steps"] * runs / 1e9


def load(group, wl, cfg):
    """runs of one (group, workload, config) with rate, energy per unit at both boundaries, card and host power"""
    rows = []
    for d in sorted(glob.glob(os.path.join(RAW, group, wl, cfg, "rep*"))):
        r = run_record(d)
        if not r:
            continue
        u = work(wl, r)
        r["rate"] = u / r["window_s"]
        card = 1.0 if cfg == "fpga" else 0.0
        for b, doms in DOMS.items():
            r[f"E_{b}"] = (sum(r[f"Eexact_{x}"] for x in doms) + card * r["Eexact_card"]) / u
        r["P_card"] = r["Eexact_card"] / r["window_s"]
        r["H_socket"] = sum(r[f"Eexact_{x}"] for x in DOMS["socket"]) / r["window_s"]
        r["H_server"] = sum(r[f"Eexact_{x}"] for x in DOMS["server"]) / r["window_s"]
        rows.append(r)
    return pd.DataFrame(rows)


def side(x, lo=0.8, hi=1.2):
    return "FPGA faster" if x > hi else ("CPU faster" if x < lo else "tie (0.8-1.2)")


def part_model_test(summ):
    pdir = os.path.join(R6, "predictions")
    pj = os.path.join(pdir, "predictions.json")
    if not os.path.exists(pj):
        print(f"model test skipped: no predictions in {pdir}")
        return
    sha_ok = None
    shaf = pj + ".sha256"
    if os.path.exists(shaf):
        sha_ok = open(shaf).read().split()[0] == hashlib.sha256(open(pj, "rb").read()).hexdigest()
    P = json.load(open(pj))
    first_card = min((run_record(d) or {}).get("t_launch") or 9e18
                     for d in glob.glob(os.path.join(RAW, "meas", "*", "fpga", "rep*"))) \
        if glob.glob(os.path.join(RAW, "meas", "*", "fpga", "rep*")) else None
    rows = []
    for p in P["predictions"]:
        wl, b, cfg = p["workload"], p["boundary"], p["cpu_config"]
        F, C = load("meas", wl, "fpga"), load("meas", wl, cfg)
        Cp = load("pred", wl, cfg)
        if F.empty or C.empty:
            print(f"skip {wl}/{b}: no measured runs")
            continue
        S, S_lo, S_hi = boot_ratio(F.rate, C.rate)
        A, A_lo, A_hi = boot_ratio(C[f"E_{b}"], F[f"E_{b}"])
        S_min, S_max = F.rate.min() / C.rate.max(), F.rate.max() / C.rate.min()
        A_min, A_max = C[f"E_{b}"].min() / F[f"E_{b}"].max(), C[f"E_{b}"].max() / F[f"E_{b}"].min()
        R_F, R_C = float(F.rate.median()), float(C.rate.median())
        P_F = float((F[f"E_{b}"] * F.rate).median())               # W at the boundary, card included
        P_C = float((C[f"E_{b}"] * C.rate).median())
        rows.append(dict(
            workload=wl, label=p["label"], boundary=b, host_class=p["host_class"], n_fpga=len(F), n_cpu=len(C),
            R_k=p["R_k"], R_F_pred=p["R_F_pred"], R_F_meas=R_F, R_F_err_pct=100 * (p["R_F_pred"] / R_F - 1),
            R_C_pred_runs=p["R_C"], R_C_meas_runs=R_C, R_C_drift_pct=100 * (R_C / p["R_C"] - 1),
            P_card_pred=p["P_card_pred_W"], P_card_meas=float(F.P_card.median()),
            H_pred=p["H_pred_W"], H_meas=float(F[f"H_{b}"].median()),
            P_F_pred=p["P_F_pred_W"], P_F_meas=P_F, P_C_pred_runs=p["P_C_W"], P_C_meas_runs=P_C,
            S_pred=p["S_pred"], S_meas=S, S_lo=S_lo, S_hi=S_hi, S_min=S_min, S_max=S_max,
            S_err_pct=100 * (p["S_pred"] / S - 1),
            A_pred=p["A_pred"], A_meas=A, A_lo=A_lo, A_hi=A_hi, A_min=A_min, A_max=A_max,
            A_err_pct=100 * (p["A_pred"] / A - 1),
            Sstar_pred=p["Sstar_pred"], Sstar_meas=P_F / P_C if P_C else np.nan,
            time_verdict_pred=p["time_verdict"], time_verdict_meas=side(S),
            energy_verdict_pred=p["energy_verdict"], energy_verdict_meas="FPGA less energy" if A > 1 else "CPU less energy",
            S_naive=p["S_naive"], A_naive=p["A_naive"]))
    df = pd.DataFrame(rows)
    if df.empty:
        print("model test skipped: no card runs of the new kernels yet")
        return
    df["time_ok"] = df.time_verdict_pred == df.time_verdict_meas
    df["energy_ok"] = df.energy_verdict_pred == df.energy_verdict_meas
    df["time_ok_naive"] = [side(n) == m for n, m in zip(df.S_naive, df.time_verdict_meas)]
    df["energy_ok_naive"] = (df.A_naive > 1) == (df.A_meas > 1)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "r6_model_test.csv"), index=False)
    summ.update(predictions_sha256_ok=sha_ok, predictions_created_utc=P["created_utc"],
                first_card_run_unix=first_card,
                prediction_before_card=(first_card is not None and
                                        pd.Timestamp(P["created_utc"]).timestamp() < first_card),
                design=P["design"], n_rows=len(df),
                time_verdicts_ok=int(df.time_ok.sum()), energy_verdicts_ok=int(df.energy_ok.sum()),
                naive_time_verdicts_ok=int(df.time_ok_naive.sum()), naive_energy_verdicts_ok=int(df.energy_ok_naive.sum()),
                S_abs_err_pct=df.set_index(df.workload + "/" + df.boundary).S_err_pct.round(1).to_dict(),
                A_abs_err_pct=df.set_index(df.workload + "/" + df.boundary).A_err_pct.round(1).to_dict(),
                R_C_drift_pct=df.set_index(df.workload + "/" + df.boundary).R_C_drift_pct.round(1).to_dict())
    pd.set_option("display.width", 220)
    print(df[["label", "boundary", "S_pred", "S_meas", "S_lo", "S_hi", "S_err_pct", "A_pred", "A_meas", "A_lo", "A_hi",
              "A_err_pct", "P_card_pred", "P_card_meas", "R_C_drift_pct", "time_ok", "energy_ok"]].round(3).to_string(index=False))


def part_mcnative(summ):
    base = "mc_67108864x128"
    F = load("mcnative", base, "fpga")
    if F.empty:
        print("mcnative skipped: no runs")
        return
    rows = []
    for b, cfg in (("socket", "s0_c24"), ("server", "s01_t96")):
        for cpu, wl in (("bit-exact AVX-512", base), ("CPU-native", base + "_native")):
            C = load("mcnative", wl, cfg)
            if C.empty:
                continue
            S, S_lo, S_hi = boot_ratio(F.rate, C.rate)
            A, A_lo, A_hi = boot_ratio(C[f"E_{b}"], F[f"E_{b}"])
            rows.append(dict(boundary=b, cpu_config=cfg, cpu_baseline=cpu, n_fpga=len(F), n_cpu=len(C),
                             R_F=float(F.rate.median()), R_C=float(C.rate.median()),
                             S=S, S_lo=S_lo, S_hi=S_hi, S_min=F.rate.min() / C.rate.max(), S_max=F.rate.max() / C.rate.min(),
                             A=A, A_lo=A_lo, A_hi=A_hi,
                             A_min=C[f"E_{b}"].min() / F[f"E_{b}"].max(), A_max=C[f"E_{b}"].max() / F[f"E_{b}"].min(),
                             price=float(np.nanmedian(C.price)), stderr=float(np.nanmedian(C.stderr))))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "r6_mcnative.csv"), index=False)
    ratio = {}
    for cfg in ("s0_c24", "s01_t96"):
        a = load("mcnative", base + "_native", cfg)
        r = load("mcnative", base, cfg)
        if not a.empty and not r.empty:
            ratio[cfg] = boot_ratio(a.rate, r.rate)
    summ["mcnative"] = dict(native_over_bitexact_cpu_rate=ratio, fpga_price=float(np.nanmedian(F.price)))
    pd.set_option("display.width", 220)
    print(df[["boundary", "cpu_baseline", "R_C", "S", "S_lo", "S_hi", "A", "A_lo", "A_hi", "price"]].round(3).to_string(index=False))


def main():
    summ = {}
    os.makedirs(OUT, exist_ok=True)
    part_model_test(summ)
    part_mcnative(summ)
    json.dump(summ, open(os.path.join(OUT, "r6_summary.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in summ.items() if k != "design"}, indent=1, default=str))


if __name__ == "__main__":
    main()
