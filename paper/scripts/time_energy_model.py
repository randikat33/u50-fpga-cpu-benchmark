#!/usr/bin/env python3
"""
time_energy_model.py - predict FPGA speedup and energy advantage before running the card,
and compare the prediction with the measurements.

Model (Section 6.4 of the paper)
  time    S  = R_F / R_C,  R_F = 1 / (b_in/B + b_out/B + 1/R_k)   (serial host schedule)
                              upper bound min(R_k, B / max(b_in, b_out)) (perfect overlap)
  power   P_F = H + P_card,  P_card = P0 + d (P_viv - P0),  d = R_F / R_k (kernel duty)
  energy  A  = S * P_C / P_F        (A > 1: the card needs less energy per unit of work)
  break-even speedup  S* = P_F / P_C  (the card saves energy iff S > S*)

Inputs and where they come from
  R_k, b_in, b_out  design and algorithm (rev_bound_model.csv, from paper_numbers/revision_analysis)
  B = 12.2 GB/s     one PCIe micro-benchmark on the platform
  P_viv             Vivado routed power estimate (vivado_power.csv; vectorless, default activity)
  P0 = 17.0 W       idle card with a small design loaded (platform constant, measured once)
  R_C, P_C          CPU runs only (no card needed)
  H                 host power while the card runs; two host classes, each value taken from the
                    OTHER workload of its class (leave-one-out): polling (portfolio <-> MC) and
                    streaming (AES <-> convolution). Pipelines: H = P_C(pipeline) + polling increment,
                    S = 1 because the offloaded stage is not on the critical path.
Outputs
  paper/data/rev_te_model.csv       one row per workload x boundary, predicted and measured
  paper/data/rev_te_summary.json    error summary quoted in the paper
"""
import json, os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..", "data")

B = 12.2          # GB/s per direction
P0 = 17.0         # W, idle card with a small design loaded

bound = pd.read_csv(os.path.join(D, "rev_bound_model.csv"))
pw = pd.read_csv(os.path.join(D, "rev_power_table.csv"))
t4 = pd.read_csv(os.path.join(D, "rev_table4.csv"))
viv = pd.read_csv(os.path.join(D, "vivado_power.csv")).set_index("design")
brk = pd.read_csv(os.path.join(D, "rev_power_breakdown.csv"))
dyn = pd.read_csv(os.path.join(D, "rev_dynamic_energy.csv")).set_index("workload")
FLOOR_SOCKET = float(dyn.floor_socket_W.iloc[0])      # pkg0 + dram0 idle floor

# workload -> (bound-model row, power source, table-4 panel, Vivado design, host class, measured FPGA rate)
el = pd.read_csv(os.path.join(D, "energy_long_summary.csv"))
def long_rate(wl, units_per_run):
    t = float(el[(el.workload == wl) & (el.platform == "fpga")].t_compute_s.iloc[0])
    return units_per_run / t

WL = {
    "pf_131072": dict(label="Portfolio", bound="Portfolio (1M trades)", src="short runs", panel="A",
                      viv="portfolio", cls="polling", mate="mc_67108864x128"),
    "mc_67108864x128": dict(label="MC Heston", bound="MC Heston (67M x 128)", src="short runs", panel="A",
                            viv="mc_heston", cls="polling", mate="pf_131072"),
    "aes_noio": dict(label="AES-256-CTR", bound="AES-256-CTR (1 GiB)", src="long-window runs", panel="B",
                     viv="aes", cls="streaming", mate="conv_rgb8_8K",
                     rf_meas=long_rate("aes_noio", 16 * 2**30 / 1e9)),          # GB/s, 16 GiB stream
    "conv_rgb8_8K": dict(label="3x3 convolution", bound="3x3 conv RGB8 8K (3 filters)", src="long-window runs",
                         panel="B", viv="conv", cls="streaming", mate="aes_noio",
                         rf_meas=long_rate("conv_rgb8_8K", 7680 * 4320 / 1e9)),  # Gpix/s
}


def power(src, wl, plat):
    r = pw[(pw.source == src) & (pw.workload == wl) & (pw.platform == plat)].iloc[0]
    return r


def boundary_power(r, b, card=True):
    p = r.pkg0 + r.dram0
    if b == "server":
        p += r.pkg1 + r.dram1
    return p + (r.card if card else 0.0)


rows = []
for wl, m in WL.items():
    bm = bound[bound.workload == m["bound"]].iloc[0]
    Rk, bi, bo = bm.kernel_peak, bm.h2d, bm.d2h
    rf_pred = 1.0 / (bi / B + bo / B + 1.0 / Rk)
    rf_up = min(Rk, B / max(bi, bo)) if max(bi, bo) > 0 else Rk
    rf_meas = m.get("rf_meas", bm.fpga_measured)
    d = rf_pred / Rk
    pviv = float(viv.loc[m["viv"], "total_W"])
    pcard_pred = P0 + d * (pviv - P0)
    rF = power(m["src"], wl, "fpga")
    rMate = power(WL[m["mate"]]["src"], m["mate"], "fpga")
    for b, cfg in (("socket", "s0_c24"), ("server", "s01_t96")):
        t = t4[(t4.panel == m["panel"]) & (t4.workload == wl) & (t4.cpu_config == cfg)].iloc[0]
        s_meas = t.speedup
        a_meas = t.energy
        rC = power(m["src"], wl, cfg)
        P_C = boundary_power(rC, b, card=False)
        P_F_meas = boundary_power(rF, b)
        H_pred = boundary_power(rMate, b, card=False)          # leave-one-out host class
        P_F_pred = H_pred + pcard_pred
        s_pred = s_meas * rf_pred / rf_meas
        s_up = s_meas * rf_up / rf_meas
        rows.append(dict(workload=wl, label=m["label"], boundary=b, cpu_config=cfg, host_class=m["cls"],
                         R_k=Rk, R_F_pred=rf_pred, R_F_upper=rf_up, R_F_meas=rf_meas, duty=d,
                         P_vivado=pviv, P_card_pred=pcard_pred, P_card_meas=rF.card,
                         H_pred=H_pred, H_meas=P_F_meas - rF.card, P_C=P_C,
                         P_F_pred=P_F_pred, P_F_meas=P_F_meas,
                         S_pred=s_pred, S_upper=s_up, S_meas=s_meas,
                         Sstar_pred=P_F_pred / P_C, Sstar_meas=P_F_meas / P_C,
                         A_pred=s_pred * P_C / P_F_pred, A_meas=a_meas,
                         A_meas_check=s_meas * P_C / P_F_meas))

# pipelines: S = 1 (offloaded stage is off the critical path), card at idle, host = CPU pipeline + polling increment
poll_inc = np.mean([boundary_power(power("short runs", w, "fpga"), "socket", card=False) for w in
                    ("pf_131072", "mc_67108864x128")]) - FLOOR_SOCKET
for wl, lab, vd in (("live_single_1080p", "Live, 1 output", "live_single"),
                    ("live_multi_all5", "Live, 5 outputs", "live_multi")):
    g = brk[brk.workload == wl].set_index("platform")
    P_C = g.loc["cpu", "pkg0_W"] + g.loc["cpu", "dram0_W"]
    P_F_meas = g.loc["fpga", "pkg0_W"] + g.loc["fpga", "dram0_W"] + g.loc["fpga", "card_W"]
    t = t4[(t4.panel == "D") & (t4.workload == wl)].iloc[0]
    P_F_pred = P_C + poll_inc + P0
    rows.append(dict(workload=wl, label=lab, boundary="socket", cpu_config="cpu (4 workers)",
                     host_class="pipeline", R_k=np.nan, R_F_pred=np.nan, R_F_upper=np.nan, R_F_meas=np.nan,
                     duty=0.0, P_vivado=float(viv.loc[vd, "total_W"]), P_card_pred=P0,
                     P_card_meas=g.loc["fpga", "card_W"], H_pred=P_C + poll_inc,
                     H_meas=P_F_meas - g.loc["fpga", "card_W"], P_C=P_C, P_F_pred=P_F_pred, P_F_meas=P_F_meas,
                     S_pred=1.0, S_upper=np.nan, S_meas=t.speedup,
                     Sstar_pred=P_F_pred / P_C, Sstar_meas=P_F_meas / P_C,
                     A_pred=P_C / P_F_pred, A_meas=t.energy, A_meas_check=t.speedup * P_C / P_F_meas))

df = pd.DataFrame(rows)
df["S_err_pct"] = 100 * (df.S_pred / df.S_meas - 1)
df["A_err_pct"] = 100 * (df.A_pred / df.A_meas - 1)
df["Pcard_err_pct"] = 100 * (df.P_card_pred / df.P_card_meas - 1)
df["Pviv_raw_err_pct"] = 100 * (df.P_vivado / df.P_card_meas - 1)
df["time_verdict_ok"] = (df.S_pred > 1) == (df.S_meas > 1)
df["energy_verdict_ok"] = (df.A_pred > 1) == (df.A_meas > 1)
df.to_csv(os.path.join(D, "rev_te_model.csv"), index=False)

k = df[df.host_class != "pipeline"]
summ = dict(
    B_GBps=B, P0_W=P0, polling_increment_W=poll_inc,
    n_points=len(df), n_kernel_points=len(k),
    S_abs_err_pct_max_kernels=float(k.S_err_pct.abs().max()),
    S_abs_err_pct_max_compute=float(k[k.host_class == "polling"].S_err_pct.abs().max()),
    A_abs_err_pct_max_kernels=float(k.A_err_pct.abs().max()),
    A_abs_err_pct_median_kernels=float(k.A_err_pct.abs().median()),
    A_abs_err_pct_all=df.A_err_pct.abs().round(1).tolist(),
    Pcard_duty_err_pct=k.drop_duplicates("workload").set_index("workload").Pcard_err_pct.round(1).to_dict(),
    Pcard_raw_err_pct=k.drop_duplicates("workload").set_index("workload").Pviv_raw_err_pct.round(1).to_dict(),
    time_verdicts_ok=int(df.time_verdict_ok.sum()), energy_verdicts_ok=int(df.energy_verdict_ok.sum()),
    check_max_rel_diff_A=float((df.A_meas_check / df.A_meas - 1).abs().max()),
)
json.dump(summ, open(os.path.join(D, "rev_te_summary.json"), "w"), indent=1)

pd.set_option("display.width", 200)
print(df[["label", "boundary", "S_pred", "S_meas", "S_err_pct", "P_card_pred", "P_card_meas", "P_F_pred",
          "P_F_meas", "Sstar_pred", "Sstar_meas", "A_pred", "A_meas", "A_err_pct"]].round(3).to_string(index=False))
print(json.dumps(summ, indent=1))
