#!/usr/bin/env python3
"""
make_paper_figures.py - all data figures of the paper, from paper/data/*.csv.

Every figure: vector PDF + 300-dpi PNG in paper/figures/, and the plotted numbers in
paper/figures/data/<name>.csv (figdata.export) collected into figure_data.xlsx.
Layout rules: legends outside the plotting area, no text over data, log axes labelled with
plain numbers, value labels only where they cannot collide; every figure is checked by eye.
"""
import json, os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter, FixedLocator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import figdata  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..", "data")
OUT = os.path.join(HERE, "..", "figures")
os.makedirs(OUT, exist_ok=True)

# validated categorical palette (fixed order) + neutrals
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]
INK, MUTED, GRID = "#1a1a1a", "#555555", "#dcdcdc"
FULL, HALF = 6.3, 3.1
plt.rcParams.update({
    "font.family": "serif", "font.serif": ["DejaVu Serif"], "font.size": 8,
    "axes.titlesize": 8.5, "axes.labelsize": 8, "legend.fontsize": 7.2,
    "xtick.labelsize": 7.2, "ytick.labelsize": 7.2,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.5, "axes.axisbelow": True,
    "savefig.dpi": 300, "pdf.fonttype": 42, "ps.fonttype": 42,
    "figure.constrained_layout.use": True, "legend.frameon": False,
    "lines.linewidth": 1.4, "lines.markersize": 4.5,
})
CAPTIONS = {}


def plain(v, _pos=None):
    if v <= 0:
        return ""
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:g}"
    return f"{v:.2g}"


def logy(ax, ticks=None):
    ax.set_yscale("log")
    if ticks:
        ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(plain))
    ax.yaxis.set_minor_formatter(NullFormatter())


def logx(ax, ticks=None):
    ax.set_xscale("log")
    if ticks:
        ax.xaxis.set_major_locator(FixedLocator(ticks))
    ax.xaxis.set_major_formatter(FuncFormatter(plain))
    ax.xaxis.set_minor_formatter(NullFormatter())


def parity(ax, horizontal=True):
    (ax.axhline if horizontal else ax.axvline)(1.0, color=INK, lw=0.8, ls="--", zorder=1)


def save(fig, name, caption):
    CAPTIONS[name] = caption
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"), bbox_inches="tight", pad_inches=0.04)
    figdata.export(fig, OUT, name)
    plt.close(fig)
    print("  wrote", name)


# =============================================================================
# Fig. 3 - headline: FPGA speedup and energy advantage vs one socket and vs whole server
# =============================================================================
def fig_headline():
    """values and 95% CIs from rev_table4.csv (revision_analysis.py): each row uses ONE protocol"""
    T = pd.read_csv(os.path.join(D, "rev_table4.csv"))
    pick = [("A", "pf_131072", "Portfolio valuation\n(short runs)"),
            ("A", "mc_67108864x128", "MC Heston 67M x 128\n(short runs)"),
            ("D", "live_single_1080p", "Live stream, 1 output\n(pipeline, 1080p)"),
            ("D", "live_multi_all5", "Live stream, 5 outputs\n(pipeline)"),
            ("B", "conv_rgb8_8K", "3x3 convolution RGB8 8K\n(long-window runs)"),
            ("B", "aes_noio", "AES-256-CTR, in memory\n(long-window runs)")]
    rows = []
    for panel, wl, lab in pick:
        g = T[(T.panel == panel) & (T.workload == wl)].set_index("cpu_config")
        one = g.iloc[0] if panel == "D" else g.loc["s0_c24"]
        srv = g.loc["s01_t96"] if "s01_t96" in g.index else None
        rows.append(dict(workload=lab, sp1=one.speedup, sp1_lo=one.sp_lo, sp1_hi=one.sp_hi,
                         sp2=srv.speedup if srv is not None else np.nan, sp2_lo=srv.sp_lo if srv is not None else np.nan,
                         sp2_hi=srv.sp_hi if srv is not None else np.nan,
                         e1=one.energy, e1_lo=one.e_lo, e1_hi=one.e_hi,
                         e2=srv.energy if srv is not None else np.nan, e2_lo=srv.e_lo if srv is not None else np.nan,
                         e2_hi=srv.e_hi if srv is not None else np.nan))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(D, "headline.csv"), index=False)
    fig, axes = plt.subplots(1, 2, figsize=(FULL, 3.1), sharey=True)
    y = np.arange(len(df))[::-1]
    for ax, (a, b), title in zip(axes, (("sp1", "sp2"), ("e1", "e2")),
                                 ("(a) Speedup (CPU time / FPGA time)", "(b) Energy advantage (CPU J / FPGA J)")):
        for yi, (_, r) in zip(y, df.iterrows()):
            if not np.isnan(r[b]):
                ax.plot([r[a], r[b]], [yi, yi], color=GRID, lw=2.2, zorder=1)
        for col, key, mk, lab in ((C[0], a, "o", "vs one socket (24 cores)"),
                                  (C[1], b, "s", "vs whole server (2 sockets, 96 threads)")):
            ok = ~df[key].isna()
            ax.errorbar(df[key][ok], y[ok.values], xerr=[(df[key] - df[key + "_lo"])[ok], (df[key + "_hi"] - df[key])[ok]],
                        fmt=mk, color=col, ms=5, elinewidth=0.9, capsize=2, zorder=3, label=lab)
        for yi, v in zip(y, df[a]):
            ax.annotate(f"{v:.2f}x", (v, yi), xytext=(8, 5), textcoords="offset points", ha="left",
                        va="center", fontsize=6.5, color=MUTED, zorder=4,
                        bbox=dict(boxstyle="square,pad=0.1", fc="white", ec="none"))
        parity(ax, horizontal=False)
        logx(ax, [0.1, 0.2, 0.5, 1, 2, 5, 10])
        ax.set_xlim(0.1, 16)
        ax.set_title(title, loc="left")
        ax.set_ylim(-0.6, len(df) - 0.35)
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(df.workload, fontsize=6.6)
    axes[0].set_xlabel("> 1: FPGA faster (log scale)")
    axes[1].set_xlabel("> 1: FPGA uses less energy (log scale)")
    h, l = axes[0].get_legend_handles_labels()
    h.append(Line2D([], [], color=INK, ls="--", lw=0.8))
    l.append("parity")
    fig.legend(h, l, loc="outside lower center", ncol=3)
    save(fig, "fig03_headline",
         "FPGA versus the optimised CPU baseline (medians of 5 runs per side; error bars: 95% bootstrap "
         "confidence intervals). Circles: one socket (24 cores), the socket the card is attached to "
         "(per-socket energy boundary). Squares: the whole dual-socket server with SMT (96 threads; "
         "whole-server boundary). Time and energy of each row come from the same runs (Table 5). "
         "The pipelines were measured against one socket only.")


# =============================================================================
# Fig. 4 - MC: speedup vs problem size under three measurement protocols
# =============================================================================
def fig_mc_protocols():
    M = pd.read_csv(os.path.join(D, "mc_steady.csv")).sort_values(["msteps", "steps"])
    Cp = pd.read_csv(os.path.join(D, "mc_campaign.csv")).set_index("cfg")
    W = pd.read_csv(os.path.join(D, "mc_warm2.csv")).set_index("cfg")
    M["camp"] = M.cfg.map(Cp.sp_campaign)
    M["warm2"] = M.cfg.map(W.sp_warm2)
    fig, ax = plt.subplots(figsize=(FULL, 2.8))
    x = np.arange(len(M))
    def short(n):    # decimal prefixes throughout the paper: 131,072 -> 131k, 4,194,304 -> 4.2M
        if n >= 10_000_000:
            return f"{n / 1e6:.0f}M"
        if n >= 1_000_000:
            return f"{n / 1e6:.1f}M"
        return f"{n / 1e3:.0f}k"
    ticklab = [f"{short(p)}x{st}\n{m:.0f} M" if m >= 10 else f"{short(p)}x{st}\n{m:.1f} M"
               for p, st, m in zip(M.paths, M.steps, M.msteps)]
    ax.plot(x, M.camp, "o:", color=C[3], label="3 timed passes, no warm-up (campaign protocol)")
    ax.plot(x, M.warm2, "^--", color=C[4], label="2 warm-up + 10 timed passes")
    ax.errorbar(x, M.sp_s0_c24, yerr=[M.sp_s0_c24 - M.lo_s0_c24, M.hi_s0_c24 - M.sp_s0_c24],
                fmt="s-", color=C[0], ecolor=C[0], elinewidth=0.8, capsize=2,
                label=">= 3 s sustained-load warm-up + 10 timed passes (final)")
    ax.errorbar(x, M.sp_s01_t96, yerr=[M.sp_s01_t96 - M.lo_s01_t96, M.hi_s01_t96 - M.sp_s01_t96],
                fmt="D-", color=C[1], ecolor=C[1], elinewidth=0.8, capsize=2,
                label="final protocol, CPU = whole server (96 threads)")
    parity(ax)
    logy(ax, [0.5, 0.7, 1, 1.5, 2, 3, 4])
    ax.set_ylim(0.5, 4.3)
    ax.set_xticks(x)
    ax.set_xticklabels(ticklab, fontsize=6.4)
    ax.set_xlim(-0.5, len(M) - 0.5)
    ax.set_xlabel("Problem size: paths x time steps (million path-steps)")
    ax.set_ylabel("FPGA speedup over CPU")
    ax.grid(axis="x", visible=False)
    fig.legend(loc="outside lower center", ncol=2)
    save(fig, "fig04_mc_protocols",
         "MC Heston speedup versus problem size under three CPU timing protocols (all 11 sizes, "
         "5 repetitions each; error bars: 95% bootstrap confidence intervals of the final protocol). "
         "Without a sustained-load warm-up the CPU is timed below its steady clock and the "
         "speedup at small sizes is overstated by up to 2.5x.")


# =============================================================================
# Fig. 5 - CPU package power: firmware ramp and slow state
# =============================================================================
def fig_power_trace():
    A = pd.read_csv(os.path.join(D, "ramp_trace.csv"))
    B = pd.read_csv(os.path.join(D, "slowstate_trace.csv"))
    meta = json.load(open(os.path.join(D, "trace_meta.json")))
    fig, axes = plt.subplots(2, 1, figsize=(FULL, 3.3), sharex=False)
    ax = axes[0]
    ax.plot(A.t_s, A.pkg0_w, color=C[0], lw=1.2, label="socket 0 (running MC, 24 threads)")
    ax.plot(A.t_s, A.pkg1_w, color=C[2], lw=1.2, label="socket 1 (idle)")
    ax.axvspan(0, 1.55, color=C[3], alpha=0.15, lw=0)
    ax.text(0.78, 88, "first ~1.5 s:\nreduced clock", ha="center", va="center", fontsize=6.8, color=INK)
    ax.set_ylabel("Package power (W)")
    ax.set_title("(a) 40 back-to-back passes, 4.2M x 64: power steps up after ~1.5 s and stays flat", loc="left")
    ax.set_ylim(0, 200)
    ax.set_xlim(A.t_s.min(), A.t_s.max())
    ax.set_xlabel("Time from start of timed section (s)")
    ax.legend(loc="lower right", ncol=2)
    ax = axes[1]
    ax.plot(B.t_s, B.pkg0_w, color=C[0], lw=1.2)
    ax.axvspan(0, meta["slow_warmup_end"], color=GRID, alpha=0.6, lw=0)
    ax.text(meta["slow_warmup_end"] / 2, 25, ">= 3 s untimed warm-up", ha="center", fontsize=6.8, color=INK)
    ax.text((meta["slow_warmup_end"] + meta["slow_end"]) / 2, 25, "10 timed passes", ha="center", fontsize=6.8, color=INK)
    ax.set_ylim(0, 200)
    ax.set_xlim(B.t_s.min(), B.t_s.max())
    ax.set_ylabel("Package power (W)")
    ax.set_xlabel("Time from process start (s)")
    ax.set_title("(b) Same kernel, a process stuck in the slow state: power oscillates at ~140-176 W", loc="left")
    save(fig, "fig05_power_trace",
         "RAPL package power of socket 0 during the AVX-512 MC Heston kernel (4.2M paths x 64 steps, "
         "24 threads, 10 Hz sampling). (a) A run that reaches the sustained state: 0.196 s per pass. "
         "(b) A run that remains in an oscillating state for its whole life: 0.233 s per pass (+19%), "
         "despite a 3.8 s warm-up.")


# =============================================================================
# Fig. 6 - FPGA speedup vs each CPU configuration (time) + CPU scaling
# =============================================================================
def fig_scaling():
    """speedups: short runs (panel A); whole-server energy: short runs for the finance kernels and
    long-window runs for AES/convolution (short windows hold 0-1 power samples)"""
    T = pd.read_csv(os.path.join(D, "rev_table4.csv"))
    order = [("pf_131072", "Portfolio\n131k paths"), ("mc_67108864x128", "MC Heston\n67M x 128"),
             ("conv_rgb8_8K", "3x3 conv.\nRGB8 8K"), ("aes_noio_1024MB", "AES-256\n1 GiB")]
    energy_src = {"pf_131072": ("A", "pf_131072"), "mc_67108864x128": ("A", "mc_67108864x128"),
                  "conv_rgb8_8K": ("B", "conv_rgb8_8K"), "aes_noio_1024MB": ("B", "aes_noio")}

    def row(panel, wl, c):
        return T[(T.panel == panel) & (T.workload == wl) & (T.cpu_config == c)].iloc[0]
    cfgs = [("s0_c24", "1 socket, 24 cores"), ("s0_t48", "1 socket, 48 threads (SMT)"),
            ("s01_c48", "2 sockets, 48 cores"), ("s01_t96", "2 sockets, 96 threads (SMT)")]
    fig, axes = plt.subplots(1, 2, figsize=(FULL, 2.9))
    w = 0.19
    for ax, kind, title, ylab in ((axes[0], "sp", "(a) FPGA speedup (short runs)", "CPU time / FPGA time"),
                                  (axes[1], "es", "(b) FPGA energy advantage (whole server)", "CPU J / FPGA J")):
        for j, (c, lab) in enumerate(cfgs):
            vals, lo, hi = [], [], []
            for wl, _ in order:
                r = row("A", wl, c) if kind == "sp" else row(*energy_src[wl], c)
                v, l_, h_ = ((r.speedup, r.sp_lo, r.sp_hi) if kind == "sp" else (r.energy_server, r.es_lo, r.es_hi))
                vals.append(v); lo.append(v - l_); hi.append(h_ - v)
            ax.bar(np.arange(len(order)) + (j - 1.5) * w, vals, w * 0.92, color=C[j], label=lab, zorder=2,
                   yerr=[lo, hi], error_kw=dict(elinewidth=0.7, capsize=1.5, ecolor=INK))
        parity(ax)
        logy(ax, [0.1, 0.2, 0.5, 1, 2, 5, 10])
        ax.set_ylim(0.1, 10)
        ax.set_xticks(range(len(order)))
        ax.set_xticklabels([l for _, l in order])
        ax.set_title(title, loc="left")
        ax.set_ylabel(ylab)
        ax.grid(axis="x", visible=False)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="outside lower center", ncol=4)
    save(fig, "fig06_cpu_scaling",
         "FPGA versus four CPU configurations of the same server (medians of 5 runs; error bars: 95% "
         "bootstrap CIs). Values below 1 favour the CPU. (a) Speedups from the short runs. (b) Energy "
         "with the whole server charged to both sides (both packages and DRAM domains, plus the card for "
         "the FPGA); portfolio and MC from the same short runs as (a), AES and convolution from the "
         "long-window runs, whose AES FPGA run streams 16 GiB (Section 5.5).")


# =============================================================================
# Fig. 7 - memory-bound kernels: AES implementations and convolution sweep
# =============================================================================
def fig_bandwidth_kernels():
    A = pd.read_csv(os.path.join(D, "aes.csv"))
    Cv = pd.read_csv(os.path.join(D, "conv.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(FULL, 3.4))
    ax = axes[0]
    impls = [("fpga", "FPGA (U50)", C[0], "o"), ("cpu_vaes", "CPU VAES, 24 threads", C[1], "s"),
             ("cpu_aesni", "CPU AES-NI, 24 threads", C[2], "^"), ("cpu_vaes_1t", "CPU VAES, 1 thread", C[3], "D"),
             ("cpu_ttable", "CPU T-table (no AES instr.), 24 thr.", C[4], "v")]
    a = A[A.group == "noio"]
    for p, lab, col, mk in impls:
        s = a[a.plat == p].sort_values("size_mb")
        ax.plot(s.size_mb / 1024, s.gbps, mk + "-", color=col, label=lab)
    ax.axhline(12.2, color=INK, lw=0.8, ls="--", zorder=1)
    ax.axhline(5.26, color=INK, lw=0.8, ls=":", zorder=1)
    ax.text(0.21, 13.55, "dashed: PCIe bound, phases overlapped (12.2)", fontsize=6.0, color=INK, va="bottom")
    ax.text(0.21, 5.5, "dotted: bound with serial phases (5.3)", fontsize=6.0, color=INK, va="bottom")
    logx(ax, [0.25, 1, 4])
    ax.set_xlim(0.2, 5)
    ax.set_ylim(0, 15)
    ax.set_xlabel("Data size (GiB, in memory)")
    ax.set_ylabel("Throughput (GB/s)")
    ax.set_title("(a) AES-256-CTR throughput", loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=1, fontsize=6.4)
    ax.set_ylim(0, 15)
    ax = axes[1]
    for (v, lab, col, mk) in (("gray8", "Gray 8-bit", C[0], "o"), ("rgb8", "RGB 8-bit", C[1], "s"),
                              ("rgb16", "RGB 16-bit", C[2], "^")):
        s = Cv[(Cv.variant == v) & (Cv.cpu == "cpu_avx512")].sort_values("mpix")
        ax.errorbar(s.mpix, s.speedup, yerr=[s.speedup - s.ci_lo, s.ci_hi - s.speedup], fmt=mk + "-", color=col,
                    ecolor=col, elinewidth=0.8, capsize=2, label=f"{lab} vs AVX-512")
        s2 = Cv[(Cv.variant == v) & (Cv.cpu == "cpu_opencv")].sort_values("mpix")
        ax.plot(s2.mpix, s2.speedup, mk + ":", color=col, mfc="white", label=f"{lab} vs OpenCV")
    parity(ax)
    logx(ax, [2, 8, 33, 134])
    logy(ax, [0.05, 0.1, 0.2, 0.5, 1, 2])
    ax.set_ylim(0.05, 2.5)
    ax.set_xlabel("Image size (Mpixel; log scale)")
    ax.set_ylabel("FPGA speedup over CPU")
    ax.set_title("(b) 3x3 convolution (3 filters)", loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=6.4)
    save(fig, "fig07_bandwidth_kernels",
         "Transfer-bound kernels. (a) AES-256-CTR on in-memory data, with the PCIe bounds of Table 8: "
         "one core with VAES matches or beats the whole card from 1 GiB upwards. (b) 3x3 convolution speedup versus image size against a hand-vectorised "
         "AVX-512 kernel (solid, 95% CIs) and against OpenCV (dotted). Missing points: sizes whose "
         "pinned working set exceeds the host-buffer limit (Appendix A).")


# =============================================================================
# Fig. 8 - where the FPGA time goes
# =============================================================================
def fig_stages():
    S = pd.read_csv(os.path.join(D, "stages.csv"))
    fig, ax = plt.subplots(figsize=(FULL, 1.9))
    y = np.arange(len(S))[::-1]
    left = np.zeros(len(S))
    for col, lab, colr in (("h2d", "host-to-card transfer", C[0]), ("kernel", "kernel", C[1]),
                           ("d2h", "card-to-host transfer", C[2])):
        ax.barh(y, S[col] * 100, left=left, color=colr, label=lab, height=0.62, zorder=2)
        for yi, l0, v in zip(y, left, S[col] * 100):
            if v >= 8:
                ax.text(l0 + v / 2, yi, f"{v:.0f}%", ha="center", va="center", fontsize=6.8, color="white")
        left += S[col] * 100
    ax.set_yticks(y)
    ax.set_yticklabels(S.workload)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of accumulated FPGA phase time (%)")
    ax.grid(axis="y", visible=False)
    fig.legend(loc="outside lower center", ncol=3)
    save(fig, "fig08_fpga_phases",
         "Decomposition of the FPGA compute window into PCIe transfers and kernel execution (medians "
         "of 5 runs). AES and convolution spend 80-88% of their accelerator time moving data over "
         "PCIe; the finance kernels are compute-bound on the card.")


# =============================================================================
# Fig. 9 - live stream pipelines
# =============================================================================
def fig_live():
    L = pd.read_csv(os.path.join(D, "live_single.csv"))
    LM = pd.read_csv(os.path.join(D, "live_multi.csv")).iloc[0]
    fig, axes = plt.subplots(1, 2, figsize=(FULL, 2.7), gridspec_kw={"width_ratios": [1.35, 1]})
    ax = axes[0]
    x = np.arange(len(L))
    w = 0.2
    ax.bar(x - 1.5 * w, L.fps_steady_fpga, w * 0.92, color=C[0], label="FPGA, steady state", zorder=2)
    ax.bar(x - 0.5 * w, L.fps_steady_cpu, w * 0.92, color=C[1], label="CPU, steady state", zorder=2)
    ax.bar(x + 0.5 * w, L.fps_fpga, w * 0.92, color=C[0], alpha=0.45, label="FPGA, whole run", zorder=2)
    ax.bar(x + 1.5 * w, L.fps_cpu, w * 0.92, color=C[1], alpha=0.45, label="CPU, whole run", zorder=2)
    ax.set_xticks(x)
    ax.set_xticklabels(L.quality)
    ax.set_ylabel("Frames per second")
    ax.set_xlabel("Output resolution (4K H.264 input -> resize -> x264)")
    ax.set_title("(a) Single-output live pipeline", loc="left")
    ax.set_ylim(0, 150)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right", ncol=2, fontsize=6.4)
    ax = axes[1]
    labels = ["FPGA\n1 CU", "FPGA\n2 CUs", "CPU\n(24 cores)", "CPU\nbest cfg"]
    lad = [LM.ro_fpga_b1, LM.ro_fpga_2cu, LM.ro_cpu_w24c1, max(LM.ro_cpu_w24c1, LM.ro_cpu_w4c24, LM.ro_cpu_w1c24)]
    pipe = [LM.pipe_fpga, LM.pipe_fpga_2cu, LM.pipe_cpu, LM.pipe_cpu]
    xx = np.arange(3)
    ax.bar(xx - 0.2, [LM.ro_fpga_b1, LM.ro_fpga_2cu, lad[2]], 0.36, color=C[2], label="resize stage only", zorder=2)
    ax.bar(xx + 0.2, [LM.pipe_fpga, LM.pipe_fpga_2cu, LM.pipe_cpu], 0.36, color=C[3], label="full pipeline", zorder=2)
    for xi, v in zip(xx - 0.2, [LM.ro_fpga_b1, LM.ro_fpga_2cu, lad[2]]):
        ax.text(xi, v * 1.08, f"{v:.0f}", ha="center", va="bottom", fontsize=6.5, color=MUTED)
    for xi, v in zip(xx + 0.2, [LM.pipe_fpga, LM.pipe_fpga_2cu, LM.pipe_cpu]):
        ax.text(xi, v * 1.08, f"{v:.1f}", ha="center", va="bottom", fontsize=6.5, color=MUTED)
    ax.set_xticks(xx)
    ax.set_xticklabels(labels[:3])
    logy(ax, [10, 20, 50, 100, 200, 500, 1000])
    ax.set_ylim(10, 1200)
    ax.set_ylabel("Frames per second (log scale)")
    ax.set_title("(b) Five-output ladder (4K -> 5 resolutions)", loc="left")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", fontsize=6.4)
    save(fig, "fig09_live",
         "Live-streaming pipelines (decode, resize, encode). (a) Single output: frame rate over the "
         "steady state (after the ~2.4 s start-up) and over the whole 363-frame run. (b) Five-output "
         "ladder: a second compute unit speeds up the resize stage by 52% but leaves the end-to-end "
         "rate unchanged, because software decode and encode bound the pipeline.")


# =============================================================================
# Fig. 10 - baseline quality flips the verdict (thesis v1 vs optimised v2)
# =============================================================================
def fig_v1v2():
    V = pd.read_csv(os.path.join(D, "v1_vs_v2_raw.csv"))
    M = pd.read_csv(os.path.join(D, "mc_steady.csv")).set_index("cfg")
    E = pd.read_csv(os.path.join(D, "energy_long_summary.csv"))
    rows = []
    lab = {"enc 100 MB": "AES-256, 100 MB file", "enc 2048 MB": "AES-256, 2 GB file",
           "8K RGB16": "Conv., 8K RGB16", "8K RGB8": "Conv., 8K RGB8", "8K gray8": "Conv., 8K gray",
           "524288 x 32": "MC Heston, 524k x 32", "4194304 x 64": "MC Heston, 4.2M x 64",
           "thesis workload": "Portfolio valuation", "1080p pipeline": "Live 1-out, 1080p",
           "240p pipeline": "Live 1-out, 240p", "5-rung pipeline": "Live 5-out ladder"}
    V = V[~V.project.str.startswith("0" + "5") & ~V.project.str.startswith("06")]   # pipelines: not comparable
    V = V[~V.project.str.startswith("04")]   # v1 portfolio kernel did not compute the intended function
    for _, r in V.iterrows():
        v2 = r.v2_speedup_compute
        if r.point == "524288 x 32":
            v2 = M.loc["524288x32", "sp_s0_c24"]
        if r.point == "4194304 x 64":   # long-window 40-pass measurement (sustained clock)
            f = E[(E.workload == "mc_4194304x64") & (E.platform == "fpga")].t_compute_s.iloc[0]
            c = E[(E.workload == "mc_4194304x64") & (E.platform == "s0_c24")].t_compute_s.iloc[0]
            v2 = c / f
        rows.append((lab.get(r.point, r.point), r.v1_speedup_compute, v2))
    df = pd.DataFrame(rows, columns=["point", "v1", "v2"])
    df.to_csv(os.path.join(D, "v1_v2_final.csv"), index=False)
    fig, ax = plt.subplots(figsize=(FULL, 2.6))
    y = np.arange(len(df))[::-1]
    for yi, (_, r) in zip(y, df.iterrows()):
        ax.annotate("", xy=(r.v2, yi), xytext=(r.v1, yi),
                    arrowprops=dict(arrowstyle="->", color=MUTED, lw=0.9, shrinkA=4, shrinkB=4))
    ax.scatter(df.v1, y, s=28, color=C[1], zorder=3, label="thesis implementation (v1): original FPGA design vs original CPU code")
    ax.scatter(df.v2, y, s=28, color=C[0], marker="s", zorder=3, label="optimised on both sides (v2): tuned FPGA design vs best CPU code")
    parity(ax, horizontal=False)
    logx(ax, [0.2, 0.5, 1, 2, 5])
    ax.set_xlim(0.15, 8)
    ax.set_yticks(y)
    ax.set_yticklabels(df.point)
    ax.set_xlabel("FPGA speedup over the CPU baseline (log scale; > 1: FPGA faster)")
    ax.grid(axis="y", visible=False)
    fig.legend(loc="outside lower center", ncol=1)
    save(fig, "fig10_v1_v2",
         "The same workloads before (v1, the original thesis code) and after optimising both the FPGA "
         "design and the CPU baseline (v2). Arrows show the change. The verdict flips for AES "
         "(FPGA 3.6-4.5x faster becomes 0.3-0.5x) and for MC Heston (0.22-0.27x becomes 2.0x). The "
         "portfolio is omitted because the v1 kernel did not compute its intended function.")


# =============================================================================
# Fig. 11 - time-energy map: speedup vs power ratio, predicted (open) and measured (filled)
# =============================================================================
def fig_te_map():
    T = pd.read_csv(os.path.join(D, "rev_te_model.csv"))
    order = ["Portfolio", "MC Heston", "AES-256-CTR", "3x3 convolution", "Live, 1 output", "Live, 5 outputs"]
    col = dict(zip(order, C))
    fig, axes = plt.subplots(1, 2, figsize=(FULL, 3.2), sharey=True)
    xs = np.array([0.1, 10])
    for ax, b, title in zip(axes, ("socket", "server"),
                            ("(a) One socket (per-socket boundary)", "(b) Whole server, 96 threads")):
        for A, ls in ((0.5, ":"), (1, "--"), (2, ":"), (4, ":"), (8, ":")):
            ax.plot(xs, xs / A, color=INK if A == 1 else MUTED, lw=0.8 if A == 1 else 0.6, ls=ls, zorder=1)
        ax.axvline(1.0, color=INK, lw=0.6, zorder=1)
        g = T[T.boundary == b]
        for _, r in g.iterrows():
            c = col[r.label]
            ax.annotate("", xy=(r.S_meas, r.Sstar_meas), xytext=(r.S_pred, r.Sstar_pred),
                        arrowprops=dict(arrowstyle="-", color=c, lw=0.8, shrinkA=2, shrinkB=2), zorder=2)
            ax.scatter(r.S_pred, r.Sstar_pred, s=30, facecolors="white", edgecolors=c, lw=1.1, zorder=3)
            ax.scatter(r.S_meas, r.Sstar_meas, s=30, color=c, zorder=4)
        logx(ax, [0.2, 0.5, 1, 2, 5])
        logy(ax, [0.3, 0.5, 1, 1.5])
        ax.set_xlim(0.15, 7)
        ax.set_ylim(0.3, 1.8)
        ax.set_title(title, loc="left")
        ax.set_xlabel("Speedup $S$ (> 1: card faster)")
        ax.fill_between(xs, [0.01, 0.01], xs, color="#eeeeee", lw=0, zorder=0)
        for A in (0.5, 1, 2, 4, 8):
            if A * 1.7 < 6.0:
                xl, yl, ha, va = A * 1.62, 1.7, "right", "top"
            else:
                xl, yl, ha, va = 6.7, 6.7 / A * 0.94, "right", "top"
            ax.text(xl, yl, f"A = {A:g}", fontsize=6, color=INK if A == 1 else MUTED, ha=ha, va=va, zorder=5,
                    bbox=dict(boxstyle="square,pad=0.08", fc="white", ec="none"))
    axes[0].set_ylabel("Power ratio $P_F/P_C$ = break-even $S^*$")
    h = [Line2D([], [], color=col[k], marker="o", ls="", ms=5) for k in order]
    l = list(order)
    h += [Line2D([], [], color=MUTED, marker="o", ls="", ms=5, mfc="white"),
          Line2D([], [], color=MUTED, marker="o", ls="", ms=5)]
    h += [Patch(color="#eeeeee")]
    l += ["predicted (workload never run on the card)", "measured", "card needs less energy (A > 1)"]
    idx = [0, 3, 6, 1, 4, 7, 2, 5, 8]          # column-major fill -> rows of three
    fig.legend([h[i] for i in idx], [l[i] for i in idx], loc="outside lower center", ncol=3)
    save(fig, "fig11_time_energy_map",
         "Time-energy map. Each point is a workload: speedup S on the x-axis, the ratio of FPGA-side to "
         "CPU-side power on the y-axis. Energy advantage A = S / (P_F/P_C), so lines of equal A are "
         "diagonals; below the dashed line (A = 1) the card needs less energy, whether or not it is faster. "
         "Open circles: model prediction from the design, the Vivado power estimate, a PCIe micro-benchmark "
         "and CPU-only runs; filled circles: measurement; lines join the two. Pipelines were measured "
         "against one socket only.")


# =============================================================================
# Fig. 12 - core-equivalents (only when the 'cores' measurements are present)
# =============================================================================
def fig_core_equivalents(path=None):
    path = path or os.path.join(D, "rev_cores.csv")
    if not os.path.exists(path):
        print("  (skipped fig12_core_equivalents: run revision_measurements.sh cores, then core_equivalents.py)")
        return
    T = pd.read_csv(path)
    lab = {"pf_131072": "Portfolio", "mc_4194304x64": "MC Heston, 4.2M x 64", "aes_noio": "AES-256-CTR",
           "conv_rgb8_8K": "3x3 convolution, RGB8 8K"}
    col = dict(zip(lab.values(), C))
    fig, axes = plt.subplots(1, 2, figsize=(FULL, 2.9))
    for ax, key, title, yl in ((axes[0], "speedup_fpga", "(a) Time (> 1: card faster)", "FPGA speedup"),
                               (axes[1], "energy_adv_fpga", "(b) Energy (> 1: card needs less)",
                                "FPGA energy advantage")):
        for wl in [w for w in lab if w in set(T.workload)]:
            g = T[T.workload == wl].sort_values("cores")
            name = lab.get(wl, wl)
            ax.plot(g.cores, g[key], marker="o", ms=3.5, color=col.get(name, MUTED), label=name)
        parity(ax)
        ax.set_xscale("log", base=2)
        ax.xaxis.set_major_locator(FixedLocator([1, 2, 4, 8, 16, 24]))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
        logy(ax, [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100])
        ax.set_xlabel("CPU cores k on socket 0")
        ax.set_ylabel(yl)
        ax.set_title(title, loc="left")
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="outside lower center", ncol=4)
    save(fig, "fig12_core_equivalents",
         "The card against socket 0 with k physical cores, one thread per core (medians of 3 runs; "
         "portfolio at 1 and 2 cores: one run; per-socket energy boundary; FPGA reference runs from the same "
         "session). Where a curve crosses 1, k cores match the card.")


def main():
    for fn in (fig_headline, fig_mc_protocols, fig_power_trace, fig_scaling, fig_bandwidth_kernels,
               fig_stages, fig_live, fig_v1v2, fig_te_map, fig_core_equivalents):
        fn()
    figdata.build_workbook(OUT, os.path.join(OUT, "figure_data.xlsx"), CAPTIONS)
    json.dump(CAPTIONS, open(os.path.join(OUT, "captions.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
