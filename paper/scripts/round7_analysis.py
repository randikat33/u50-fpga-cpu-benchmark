#!/usr/bin/env python3
"""
round7_analysis.py - review round 7 (round7_sweep.sh).

Part 1 (sweep): near-break-even test of the time-energy model. The MC Asian kernel with L = 16 lanes, of
which l are active (l from the sweep set). The predictions were made BEFORE the sweep design ran on the card
(results_round7/predictions/predictions.json, SHA-256 checked, creation time compared with the first card run).
Measured values use the paper's estimators: S = ratio of median rates, A = ratio of median energies per
unit of work at the boundary (socket: pkg0+dram0 [+card]; server: all four RAPL domains [+card]),
percentile bootstrap (4,000 resamples, seed 1) and the min-max range over all pairs of runs. CPU runs are the
fresh runs of the same session (raw/meas/asian/<cfg>), interleaved with the card runs.
  * verdicts: time with the paper's tie band (1/1.2 .. 1.2), energy A > 1; model and naive (75 W card)
  * crossovers: number of active lanes where S = 1/1.2, 1, 1.2 and A = 1, by log-linear interpolation
    of the measured medians between neighbouring sweep points (as fixed in the predictions file)
  * hypotheses H1-H3 as fixed in the predictions file
  * diagnostics: measured rate per active lane (slope and share of the nominal rate) and P_card(l) against
    the duty model P0 + (l/L)(P_viv - P0)

Part 2 (pfnative): the paper's portfolio card against the algorithm-matched AVX-512 CPU engine and the
CPU-native engine (Box-Muller + AVX-512 intrinsics + FMA); S and A for both, native/bit-exact CPU speed,
and the price-agreement check of the CPU-native engine (logs/pf_native_check.log).

Input : paper/inputs/results_round7/  (unpacked results_round7_<date>.tar.gz)        [R7_IN]
Output: paper/data/r7_sweep.csv, r7_crossovers.csv, r7_pfnative.csv, r7_summary.json   [R7_OUT]
"""
import glob, hashlib, json, os, re, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlib import run_record  # noqa: E402

R7 = os.environ.get("R7_IN", os.path.join(HERE, "..", "inputs", "results_round7"))
OUT = os.environ.get("R7_OUT", os.path.join(HERE, "..", "data"))
RAW = os.path.join(R7, "raw")
N_BOOT, SEED = 4000, 1
DOMS = {"socket": ["pkg0", "dram0"], "server": ["pkg0", "dram0", "pkg1", "dram1"]}


def boot_ratio(num, den, n=N_BOOT, seed=SEED):
    rng = np.random.default_rng(seed)
    num, den = np.asarray(num, float), np.asarray(den, float)
    s = [np.median(rng.choice(num, len(num))) / np.median(rng.choice(den, len(den))) for _ in range(n)]
    return float(np.median(num) / np.median(den)), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def work(wl, r):
    if wl.startswith("pf_"):
        return 1.0                                             # one valuation of the whole portfolio
    runs = r["runs"] if np.isfinite(r["runs"]) else 1.0
    return r["paths"] * r["steps"] * runs / 1e9                # G path-steps


def load(group, wl, cfg):
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
            r[f"H_{b}"] = sum(r[f"Eexact_{x}"] for x in doms) / r["window_s"]
        r["P_card"] = r["Eexact_card"] / r["window_s"]
        rows.append(r)
    return pd.DataFrame(rows)


def tside(x, lo, hi):
    return "FPGA faster" if x > hi else ("CPU faster" if x < lo else "tie")


def eside(a):
    return "FPGA less energy" if a > 1 else "CPU less energy"


def crossing(ls, ys, thr):
    """first l where the measured medians cross thr, log-linear interpolation; None if not inside the sweep"""
    ls, ys = np.asarray(ls, float), np.asarray(ys, float)
    o = np.argsort(ls)
    ls, ys = ls[o], ys[o]
    for i in range(len(ls) - 1):
        a, b = ys[i] - thr, ys[i + 1] - thr
        if a == 0:
            return float(ls[i])
        if a * b < 0:
            la, lb = np.log(ls[i]), np.log(ls[i + 1])
            ya, yb = np.log(ys[i]), np.log(ys[i + 1])
            return float(np.exp(la + (np.log(thr) - ya) * (lb - la) / (yb - ya)))
    return None


def part_sweep(summ):
    pj = os.path.join(R7, "predictions", "predictions.json")
    if not os.path.exists(pj):
        print(f"sweep skipped: no predictions in {os.path.dirname(pj)}")
        return
    sha_ok = {}
    for f in ("predictions.json", "predictions.md"):
        fp = os.path.join(R7, "predictions", f)
        if os.path.exists(fp + ".sha256") and os.path.exists(fp):
            sha_ok[f] = open(fp + ".sha256").read().split()[0] == hashlib.sha256(open(fp, "rb").read()).hexdigest()
    P = json.load(open(pj))
    K = P["model_constants"]
    lo, hi = K["tie_band_S"]
    eb = K["in_sample_error_band"]
    d = P["design"]["sweep"]
    L = d["lanes"]
    card_dirs = glob.glob(os.path.join(RAW, "meas", "asian_l*", "fpga", "rep*"))
    first_card = min(((run_record(x) or {}).get("t_launch") or 9e18 for x in card_dirs), default=None)
    rows = []
    for p in P["predictions"]:
        b, cfg, l = p["boundary"], p["cpu_config"], p["active_lanes"]
        F, C = load("meas", f"asian_l{l}", "fpga"), load("meas", "asian", cfg)
        if F.empty or C.empty:
            print(f"skip {b}/l={l}: no measured runs")
            continue
        S, S_lo, S_hi = boot_ratio(F.rate, C.rate)
        A, A_lo, A_hi = boot_ratio(C[f"E_{b}"], F[f"E_{b}"])
        R_F, R_C = float(F.rate.median()), float(C.rate.median())
        P_F = float((F[f"E_{b}"] * F.rate).median())
        P_C = float((C[f"E_{b}"] * C.rate).median())
        rows.append(dict(
            boundary=b, cpu_config=cfg, active_lanes=l, n_fpga=len(F), n_cpu=len(C),
            R_F_pred=p["R_F_pred"], R_F_meas=R_F, R_F_err_pct=100 * (p["R_F_pred"] / R_F - 1),
            R_C_pred_runs=p["R_C"], R_C_meas_runs=R_C, R_C_drift_pct=100 * (R_C / p["R_C"] - 1),
            P_card_pred=p["P_card_pred_W"], P_card_meas=float(F.P_card.median()),
            H_pred=p["H_pred_W"], H_meas=float(F[f"H_{b}"].median()),
            P_F_pred=p["P_F_pred_W"], P_F_meas=P_F, P_C_pred_runs=p["P_C_W"], P_C_meas_runs=P_C,
            S_pred=p["S_pred"], S_meas=S, S_lo=S_lo, S_hi=S_hi,
            S_min=F.rate.min() / C.rate.max(), S_max=F.rate.max() / C.rate.min(), S_err_pct=100 * (p["S_pred"] / S - 1),
            A_pred=p["A_pred"], A_meas=A, A_lo=A_lo, A_hi=A_hi,
            A_min=C[f"E_{b}"].min() / F[f"E_{b}"].max(), A_max=C[f"E_{b}"].max() / F[f"E_{b}"].min(),
            A_err_pct=100 * (p["A_pred"] / A - 1), A_naive=p["A_naive"],
            Sstar_pred=p["Sstar_pred"], Sstar_meas=P_F / P_C if P_C else np.nan,
            near_time=p["near_break_even_time"], near_energy=p["near_break_even_energy"],
            time_verdict_pred=p["time_verdict"], time_verdict_meas=tside(S, lo, hi),
            time_verdict_ci=("FPGA faster" if S_lo > hi else "CPU faster" if S_hi < lo else
                             "tie" if (S_lo >= lo and S_hi <= hi) else "unresolved"),
            energy_verdict_pred=p["energy_verdict"], energy_verdict_meas=eside(A),
            energy_verdict_ci=("FPGA less energy" if A_lo > 1 else "CPU less energy" if A_hi < 1 else "unresolved"),
            energy_verdict_naive=p["energy_verdict_naive"]))
    df = pd.DataFrame(rows)
    if df.empty:
        print("sweep skipped: no card runs yet")
        return
    df["time_ok"] = df.time_verdict_pred == df.time_verdict_meas
    df["energy_ok"] = df.energy_verdict_pred == df.energy_verdict_meas
    df["energy_ok_naive"] = df.energy_verdict_naive == df.energy_verdict_meas
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "r7_sweep.csv"), index=False)

    # crossovers
    xr = []
    for b, g in df.groupby("boundary"):
        px = P["crossovers"][b]
        for name, col, thr, key in (("S = 1/1.2", "S_meas", lo, "lanes_S_lo"), ("S = 1", "S_meas", 1.0, "lanes_S_1"),
                                    ("S = 1.2", "S_meas", hi, "lanes_S_hi"), ("A = 1", "A_meas", 1.0, "lanes_A_1")):
            pred = px.get(key)
            meas = crossing(g.active_lanes, g[col], thr)
            inside = pred is not None and g.active_lanes.min() <= pred <= g.active_lanes.max()
            xr.append(dict(boundary=b, crossover=name, lanes_pred=pred, pred_inside_sweep=inside, lanes_meas=meas,
                           err_pct=(100 * (pred / meas - 1)) if (pred and meas) else np.nan,
                           within_20pct=(abs(pred / meas - 1) <= eb) if (pred and meas) else None))
    X = pd.DataFrame(xr)
    X.to_csv(os.path.join(OUT, "r7_crossovers.csv"), index=False)

    # hypotheses
    far_t, far_e = df[~df.near_time], df[~df.near_energy]
    meas_x = X[X.lanes_meas.notna() & X.lanes_pred.notna()]
    missing_x = X[X.pred_inside_sweep & X.lanes_meas.isna()]
    H = dict(
        H1=dict(far_time=f"{int(far_t.time_ok.sum())}/{len(far_t)}", far_energy=f"{int(far_e.energy_ok.sum())}/{len(far_e)}",
                passed=bool(far_t.time_ok.all() and far_e.energy_ok.all())),
        H2=dict(measured=f"{int(meas_x.within_20pct.sum())}/{len(meas_x)} within 20 %",
                predicted_inside_but_not_measured=missing_x.crossover.tolist(),
                passed=bool(len(meas_x) > 0 and meas_x.within_20pct.all() and missing_x.empty)),
        H3=dict(model=f"{int(df.energy_ok.sum())}/{len(df)}", naive=f"{int(df.energy_ok_naive.sum())}/{len(df)}",
                passed=bool(df.energy_ok.sum() >= df.energy_ok_naive.sum())),
        near=dict(time=f"{int(df[df.near_time].time_ok.sum())}/{int(df.near_time.sum())} right",
                  energy=f"{int(df[df.near_energy].energy_ok.sum())}/{int(df.near_energy.sum())} right"))

    # diagnostics: rate per active lane, P_card(l) vs the duty model
    cardrows = df.drop_duplicates("active_lanes").sort_values("active_lanes")
    slope = float(np.polyfit(cardrows.active_lanes, cardrows.R_F_meas, 1)[0]) if len(cardrows) > 1 else np.nan
    pc = np.polyfit(cardrows.active_lanes, cardrows.P_card_meas, 1) if len(cardrows) > 1 else [np.nan, np.nan]
    summ["sweep"] = dict(
        predictions_sha256_ok=sha_ok, predictions_created_utc=P["created_utc"], first_card_run_unix=first_card,
        prediction_before_card=(first_card is not None and pd.Timestamp(P["created_utc"]).timestamp() < first_card),
        design={k: d.get(k) for k in ("f_mhz", "n_cu", "lanes", "ii", "P_viv_W", "wns_ns")}, n_rows=len(df),
        time_verdicts_ok=f"{int(df.time_ok.sum())}/{len(df)}", energy_verdicts_ok=f"{int(df.energy_ok.sum())}/{len(df)}",
        naive_energy_verdicts_ok=f"{int(df.energy_ok_naive.sum())}/{len(df)}",
        S_err_pct_median_abs=float(df.S_err_pct.abs().median()), S_err_pct_max_abs=float(df.S_err_pct.abs().max()),
        A_err_pct_median_abs=float(df.A_err_pct.abs().median()), A_err_pct_max_abs=float(df.A_err_pct.abs().max()),
        R_C_drift_pct=df.drop_duplicates("boundary").set_index("boundary").R_C_drift_pct.round(2).to_dict(),
        rate_per_lane_meas=slope, rate_per_lane_pred=P["per_lane_G"],
        rate_share_of_nominal=float(slope / P["per_lane_G"]) if np.isfinite(slope) else None,
        P_card_fit=dict(intercept_W=float(pc[1]), per_lane_W=float(pc[0]),
                        model_intercept_W=K["P0_W"], model_per_lane_W=(d["P_viv_W"] - K["P0_W"]) / L),
        P_card_err_pct_max_abs=float((100 * (cardrows.P_card_pred / cardrows.P_card_meas - 1)).abs().max()),
        hypotheses=H)
    pd.set_option("display.width", 250)
    print(df[["boundary", "active_lanes", "S_pred", "S_meas", "S_lo", "S_hi", "S_err_pct", "A_pred", "A_meas", "A_lo",
              "A_hi", "A_err_pct", "P_card_pred", "P_card_meas", "near_time", "near_energy", "time_ok", "energy_ok",
              "energy_ok_naive"]].round(3).to_string(index=False))
    print(X.round(2).to_string(index=False))


def part_pfnative(summ):
    F = load("pfnative", "pf_131072", "fpga")
    if F.empty:
        print("pfnative skipped: no runs")
        return
    rows = []
    for b, cfg in (("socket", "s0_c24"), ("server", "s01_t96")):
        for cpu, wl in (("algorithm-matched AVX-512", "pf_131072"), ("CPU-native", "pf_131072_native")):
            C = load("pfnative", wl, cfg)
            if C.empty:
                continue
            S, S_lo, S_hi = boot_ratio(F.rate, C.rate)
            A, A_lo, A_hi = boot_ratio(C[f"E_{b}"], F[f"E_{b}"])
            rows.append(dict(boundary=b, cpu_config=cfg, cpu_baseline=cpu, n_fpga=len(F), n_cpu=len(C),
                             t_F_s=float(F.window_s.median()), t_C_s=float(C.window_s.median()),
                             S=S, S_lo=S_lo, S_hi=S_hi, S_min=F.rate.min() / C.rate.max(), S_max=F.rate.max() / C.rate.min(),
                             A=A, A_lo=A_lo, A_hi=A_hi,
                             A_min=C[f"E_{b}"].min() / F[f"E_{b}"].max(), A_max=C[f"E_{b}"].max() / F[f"E_{b}"].min(),
                             time_verdict=tside(S, 1 / 1.2, 1.2), energy_verdict=eside(A)))
    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "r7_pfnative.csv"), index=False)
    ratio = {}
    for cfg in ("s0_c24", "s01_t96"):
        a, r = load("pfnative", "pf_131072_native", cfg), load("pfnative", "pf_131072", cfg)
        if not a.empty and not r.empty:
            ratio[cfg] = boot_ratio(a.rate, r.rate)
    chk = os.path.join(R7, "logs", "pf_native_check.log")
    check = open(chk).read().strip().splitlines() if os.path.exists(chk) else []
    summ["pfnative"] = dict(native_over_matched_cpu_speed=ratio, check=check,
                            check_passed=any("pf_native_check: PASS" in x for x in check))
    pd.set_option("display.width", 250)
    print(df[["boundary", "cpu_baseline", "t_F_s", "t_C_s", "S", "S_lo", "S_hi", "A", "A_lo", "A_hi", "time_verdict",
              "energy_verdict"]].round(3).to_string(index=False))


def main():
    summ = {}
    os.makedirs(OUT, exist_ok=True)
    part_sweep(summ)
    part_pfnative(summ)
    json.dump(summ, open(os.path.join(OUT, "r7_summary.json"), "w"), indent=1, default=str)
    print(json.dumps(summ, indent=1, default=str))


if __name__ == "__main__":
    main()
