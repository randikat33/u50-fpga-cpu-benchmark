#!/usr/bin/env python3
"""make_supp_tables.py - LaTeX tables for Supplementary File S2 generated from paper/data/.
Writes paper/tex/supp_ranges.tex: medians, 95% bootstrap CIs and the full min-max range of the
FPGA/CPU ratio over all pairs of runs, for every row of Table 6 of the paper."""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(HERE, "..", "tex"), exist_ok=True)   # the dataset archive has no tex/ folder
T = pd.read_csv(os.path.join(HERE, "..", "data", "rev_table4.csv"))
CFG = [("s0_c24", "1 socket"), ("s0_t48", "1 socket + SMT"), ("s01_t96", "whole server"),
       ("cpu (4 workers)", "1 socket")]
NAME = {"pf_131072": "Portfolio (short runs)", "mc_67108864x128": "MC Heston 67M$\\times$128 (short runs)",
        "aes_noio_1024MB": "AES-256-CTR 1\\,GiB (short runs)", "conv_rgb8_8K": "Convolution RGB8 8K",
        "mc_4194304x64": "MC Heston 4.2M$\\times$64 (long window)", "aes_noio": "AES-256-CTR (long window)",
        "live_single_1080p": "Live, 1 output (whole run)", "live_multi_all5": "Live, 5 outputs (whole run)"}


def f(v, lo, hi, a, b, d=2):
    if pd.isna(v):
        return "--"
    return f"{v:.{d}f} [{lo:.{d}f}, {hi:.{d}f}] ({a:.{d}f}--{b:.{d}f})"


rows = []
for _, r in T[T.panel.isin(["A", "B", "D"])].iterrows():
    lab = dict(CFG).get(r.cpu_config)
    if lab is None:
        continue
    name = NAME.get(r.workload, r.workload)
    if r.workload == "conv_rgb8_8K":
        name += " (short runs)" if r.panel == "A" else " (long window)"
    d = 3 if r.workload == "pf_131072" else 2
    rows.append(f"{name} & {lab} & {f(r.speedup, r.sp_lo, r.sp_hi, r.sp_min, r.sp_max, d)} & "
                f"{f(r.energy, r.e_lo, r.e_hi, r.e_min, r.e_max)} \\\\")
tex = r"""\begin{table}[ht]
\centering
\caption{Every row of Table~6 of the paper with its median ratio, 95\% percentile-bootstrap interval
(4,000 resamples, seed 1) in brackets and the full range over all pairs of runs in parentheses
[min(CPU)/max(FPGA), max(CPU)/min(FPGA)]. With five runs per side the bootstrap interval nearly
equals the full range; both describe variation within one measurement session.}
\label{tab:s-ranges}
\scriptsize
\begin{tabular}{@{}llll@{}}
\toprule
Workload & CPU & Speedup $S$ & Energy advantage $A$ \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
"""
open(os.path.join(HERE, "..", "tex", "supp_ranges.tex"), "w").write(tex)
print(f"wrote supp_ranges.tex ({len(rows)} rows)")


# ---------------------------------------------------------------------------------------------------------
# Round 7: every point of the break-even sweep (paper Section 6.8) -> paper/tex/supp_sweep.tex
# ---------------------------------------------------------------------------------------------------------
def sweep_table():
    f = os.path.join(HERE, "..", "data", "r7_sweep.csv")
    if not os.path.exists(f):
        return
    S = pd.read_csv(f)
    tv = {"CPU faster": "C", "tie": "T", "FPGA faster": "F"}
    ev = {"CPU less energy": "C", "FPGA less energy": "F"}
    out = []
    for b, cap in (("socket", "one socket (24 cores, per-socket boundary)"), ("server", "the whole server (96 threads)")):
        g = S[S.boundary == b].sort_values("active_lanes")
        rows = []
        for _, r in g.iterrows():
            rows.append(
                f"{int(r.active_lanes)} & {r.R_F_pred:.2f} & {r.R_F_meas:.3f} & {r.S_pred:.3f} & "
                f"{r.S_meas:.3f} \\ci{{{r.S_lo:.3f}}}{{{r.S_hi:.3f}}} & {r.A_pred:.3f} & "
                f"{r.A_meas:.3f} \\ci{{{r.A_lo:.3f}}}{{{r.A_hi:.3f}}} & {r.A_naive:.2f} & {r.P_card_pred:.2f} & "
                f"{r.P_card_meas:.2f} & {tv[r.time_verdict_pred]}/{tv[r.time_verdict_meas]}{'*' if r.near_time else ''} & "
                f"{ev[r.energy_verdict_pred]}/{ev[r.energy_verdict_meas]}{'*' if r.near_energy else ''} \\\\")
        out.append(r"""\begin{table}[H]
\centering
\caption{Break-even sweep against """ + cap + r""": prediction made before the design ran on the card, and
measurement (medians of 5 card and 5 CPU runs, 95\% bootstrap intervals). $R_F$ in G path-steps/s; CPU rate
in the prediction runs: """ + f"{g.R_C_pred_runs.iloc[0]:.2f}" + r""" (measurement runs: """ + f"{g.R_C_meas_runs.iloc[0]:.2f}" + r"""). Verdicts as
predicted/measured: C = CPU faster or CPU needs less energy, F = card, T = tie in time; an asterisk marks a
prediction flagged near break-even.}
\footnotesize
\setlength{\tabcolsep}{3pt}
\begin{adjustbox}{max width=\textwidth}\begin{tabular}{@{}rrrrrrrrrrll@{}}
\toprule
 & \multicolumn{2}{c}{$R_F$} & \multicolumn{2}{c}{$S$} & \multicolumn{3}{c}{$A$} & \multicolumn{2}{c}{$P_\text{card}$ (W)} & \multicolumn{2}{c}{Verdict} \\
\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-8}\cmidrule(lr){9-10}\cmidrule(lr){11-12}
$l$ & pred. & meas. & pred. & meas. & pred. & meas. & naïve & pred. & meas. & time & energy \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}\end{adjustbox}
\end{table}
""")
    open(os.path.join(HERE, "..", "tex", "supp_sweep.tex"), "w").write("\n".join(out))
    print("wrote tex/supp_sweep.tex")


sweep_table()


# ---------------------------------------------------------------------------------------------------------
# Published results on other cards and their implied break-even speedup (S2.24) -> paper/tex/supp_lit.tex
#   source: paper/litreview/published_power.csv (values as printed in each paper; see its 'source' column)
#   S = FPGA rate / CPU rate,  S* = P_F / P_C,  A = S / S*  (energy ratio CPU / FPGA)
# ---------------------------------------------------------------------------------------------------------
def lit_table():
    src = os.path.join(HERE, "..", "litreview", "published_power.csv")
    if not os.path.exists(src):
        return
    L = pd.read_csv(src)
    der = L.power_derived == 1                      # powers not printed: mean power = energy / run time
    L.loc[der, "P_C"] = L.E_C_J[der] / (L.cpu_value[der] / 1e3)
    L.loc[der, "P_F"] = L.E_F_J[der] / (L.fpga_value[der] / 1e3)
    L["S"] = (L.fpga_value / L.cpu_value).where(L.higher_is_better == 1, L.cpu_value / L.fpga_value)
    L["Sstar"] = L.P_F / L.P_C
    L["A"] = L.S / L.Sstar
    L["case"] = ["faster, less energy" if s > 1 and a > 1 else "slower, less energy" if a > 1 else
                 "slower, more energy" if s < 1 else "faster, more energy" for s, a in zip(L.S, L.A)]
    L.to_csv(os.path.join(HERE, "..", "data", "lit_breakeven.csv"), index=False)

    # the same boundary on our own data: card power only, against the busy CPU
    M = pd.read_csv(os.path.join(HERE, "..", "data", "rev_te_model.csv"))
    M = M[M.workload.isin(["pf_131072", "mc_67108864x128", "aes_noio", "conv_rgb8_8K"]) & M.variant.isna()]
    own = {"n": len(M), "host_lo": M.Sstar_meas.min(), "host_hi": M.Sstar_meas.max(),
           "card_lo": (M.P_card_meas / M.P_C).min(), "card_hi": (M.P_card_meas / M.P_C).max(),
           "overstate_lo": (M.P_F_meas / M.P_card_meas).min(), "overstate_hi": (M.P_F_meas / M.P_card_meas).max()}
    import json
    summ = {"rows": len(L), "studies": int(L.bibkey.nunique()), "Sstar_min": L.Sstar.min(), "Sstar_max": L.Sstar.max(),
            "slower_less_energy": int((L.case == "slower, less energy").sum()),
            "slower_more_energy": int((L.case == "slower, more energy").sum()),
            "transfer_excluded": int((L.transfer == "excluded").sum()),
            "max_dev_from_printed_pct": float((100 * (L.A / L.printed_ratio - 1)).abs().max()), "own": own}
    summ = json.loads(json.dumps(summ, default=float))
    json.dump(summ, open(os.path.join(HERE, "..", "data", "lit_breakeven_summary.json"), "w"), indent=1)

    def w(v):
        return f"{v:.0f}" if v >= 100 else f"{v:.1f}"
    rows = []
    for _, r in L.iterrows():
        d = "$^\\dagger$" if r.power_derived == 1 else ""
        rows.append(f"{r.study}~\\cite{{{r.bibkey}}} & {r.card} & {r.cpu} & {r.workload} & {r.S:.2f} & "
                    f"{w(r.P_F)}{d} & {w(r.P_C)}{d} & {r.Sstar:.2f} & {r.A:.{1 if r.A >= 10 else 2}f} & "
                    f"{r.fpga_power_scope}; {r.cpu_power_scope} & {r.transfer} \\\\")
    out = r"""\begin{table}[H]
\centering
\caption{Published FPGA--CPU results on other cards that report power on both sides, with the break-even
speedup they imply. $S$: FPGA rate over CPU rate; $P_F$, $P_C$: reported power (W) on the FPGA and CPU
side; $S^*=P_F/P_C$; $A=S/S^*$: energy of the CPU over that of the FPGA. $S$, $S^*$ and $A$ are computed
by us from the values printed in the cited table of each paper. $^\dagger$\,Mean power computed from the
printed energy and run time.}
\label{tab:s-lit}
\scriptsize
\setlength{\tabcolsep}{3pt}
\begin{tabularx}{\textwidth}{@{}>{\raggedright\arraybackslash}p{2.25cm}>{\raggedright\arraybackslash}p{1.75cm}>{\raggedright\arraybackslash}p{2.35cm}>{\raggedright\arraybackslash}p{1.6cm}rrrrr>{\raggedright\arraybackslash}X>{\raggedright\arraybackslash}p{1.1cm}@{}}
\toprule
Study & Card & CPU & Workload & $S$ & $P_F$ & $P_C$ & $S^*$ & $A$ & Power counted & Transfer \\
\midrule
""" + "\n".join(rows).replace("2x ", "2\\x{} ") + r"""
\bottomrule
\end{tabularx}
\end{table}
"""
    open(os.path.join(HERE, "..", "tex", "supp_lit.tex"), "w").write(out)
    print("wrote tex/supp_lit.tex")
    print(L[["id", "S", "P_F", "P_C", "Sstar", "A", "printed_ratio", "case"]].round(3).to_string(index=False))
    print(summ)


lit_table()
