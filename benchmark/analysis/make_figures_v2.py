#!/usr/bin/env python3
"""
make_figures_v2.py - journal figures for the v2 campaign (results/v2/tables -> results/v2/figures)

Same style and layout guard as make_figures.py (no cropping, no overlapping labels,
PDF + 300 dpi PNG, LAYOUT_QC.txt). Every figure has a CSV behind it in tables/.

  v2_fig01_speedup_overview   FPGA vs best CPU per project (compute and warm E2E)
  v2_fig02_crossover          speedup vs problem size (AES, Conv x3, MC, Portfolio)
  v2_fig03_aes_impls          AES throughput: FPGA vs VAES / AES-NI / OpenSSL / T-table
  v2_fig04_live               live stream: pipeline FPS and resize-stage FPS
  v2_fig05_stages             where the FPGA time goes (H2D / kernel / D2H) vs CPU compute
  v2_fig06_energy             energy per unit of work (system boundary)
  v2_fig07_v1_vs_v2           thesis (v1) vs A-grade (v2) speedups
  v2_fig08_cpu_util           CPU utilisation during the live pipelines
  v2_fig09_power              power timelines of representative runs
  v2_fig10_resources          FPGA utilisation and timing slack per project (from the Vivado reports)
"""
import argparse, glob, json, os, re, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_figures as mf  # noqa: E402
from make_figures import plt, PAL, INK, MUTED, GRID, NEUTRAL, HATCH, ONE_COL, TWO_COL, PRETTY, save, legend_below, read  # noqa
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

MARK = ["o", "s", "^", "D", "v", "P", "X", "*"]
QORDER = ["240p", "360p", "480p", "720p", "1080p"]


from matplotlib.ticker import FuncFormatter, NullFormatter, LogLocator  # noqa: E402


def _plain(v, _pos=None):
    if v <= 0:
        return ""
    if v >= 1:
        return f"{v:,.0f}" if v >= 10 else f"{v:g}"
    return f"{v:.3g}"


def logaxis(ax, which="x"):
    """log scale with plain labels and no minor-tick labels (no overlaps).
    Narrow ranges (< 2 decades) also label 2x and 5x so that every axis has several labels."""
    axis = ax.xaxis if which == "x" else ax.yaxis
    (ax.set_xscale if which == "x" else ax.set_yscale)("log")
    lo, hi = sorted(ax.get_xlim() if which == "x" else ax.get_ylim())
    narrow = lo > 0 and np.log10(hi / lo) < 2
    axis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0) if narrow else (1.0,), numticks=12))
    axis.set_major_formatter(FuncFormatter(_plain))
    axis.set_minor_formatter(NullFormatter())


def not_ref(plat):
    """CPU implementations that are references, not candidates for 'best CPU'"""
    return not str(plat).endswith("_1t")


def best_rows(SP, boundary, fpga="fpga"):
    if SP.empty:
        return SP
    return SP[SP.cpu.astype(str).str.startswith("BEST_CPU") & (SP.boundary == boundary) & (SP.fpga == fpga)]


def parity(ax):
    ax.axhline(1.0, color=INK, lw=0.8, ls="--", zorder=1)


def err(ax, x, r, color, **kw):
    lo = np.clip(r.speedup - r.ci_lo, 0, None) if r.ci_lo == r.ci_lo else 0
    hi = np.clip(r.ci_hi - r.speedup, 0, None) if r.ci_hi == r.ci_hi else 0
    ax.errorbar([x], [r.speedup], yerr=[[lo], [hi]], fmt="none", ecolor=color, elinewidth=0.8, capsize=2, **kw)


# ---------------------------------------------------------------------------
HEADLINE = {  # (group, config) shown in the overview
    "01_AES": ("enc", "2048MB"), "02_Convolution": ("image", "img_rgb16"), "03_MC_Heston": ("main", "4194304x64"),
    "04_Portfolio": ("main", "p2_131072"), "05_LiveStream_Single": ("pipe", "1080p"), "06_LiveStream_Multi": ("pipe", "all5"),
}


def fig_overview(T, out):
    SP = read(T, "speedup_v2.csv")
    if SP.empty:
        return
    fig, ax = plt.subplots(figsize=(TWO_COL, 2.6))
    projects = [p for p in PRETTY if p in set(SP.project)]
    bounds = [("compute", "Compute (live: FPS of the processing window)"), ("e2e_warm", "Warm end-to-end job")]
    w = 0.36
    used = {}
    for j, (b, lab) in enumerate(bounds):
        B = best_rows(SP, b)
        for i, p in enumerate(projects):
            g, c = HEADLINE.get(p, (None, None))
            r = B[(B.project == p) & (B.group == g) & (B.config == c)]
            if r.empty:
                r = B[B.project == p].sort_values("config").tail(1)
            if r.empty:
                continue
            r = r.iloc[0]
            used.setdefault(p, f"{r.group}: {r.config}")
            x = i + (j - 0.5) * w
            ax.bar(x, r.speedup, w * 0.92, color=PAL[j], hatch=HATCH[j], edgecolor="white", linewidth=0.4, zorder=2)
            err(ax, x, r, INK)
            top = r.ci_hi if r.ci_hi == r.ci_hi and r.ci_hi > r.speedup else r.speedup
            mf.vlabel(ax, x, top * 1.05, f"{r.speedup:.2g}×")
    parity(ax)
    logaxis(ax, "y")
    ax.set_xticks(range(len(projects)))
    ax.set_xticklabels([f"{PRETTY[p]}\n{used.get(p, '')}" for p in projects])
    ax.set_ylabel("FPGA speedup over best CPU (×)")
    handles = [Patch(facecolor=PAL[j], hatch=HATCH[j], edgecolor="white", label=l) for j, (_, l) in enumerate(bounds)]
    handles.append(Line2D([], [], color=INK, ls="--", lw=0.8, label="parity (>1: FPGA faster)"))
    legend_below(fig, handles, ncol=3)
    save(fig, out, "v2_fig01_speedup_overview")


def fig_crossover(T, out):
    SP = read(T, "speedup_v2.csv")
    if SP.empty:
        return
    panels = [("01_AES", "enc", r"^(\d+)MB$", "file size [MB]"),
              ("02_Convolution", "synth", r"^(\w+?)_(\d+)x(\d+)$", "image size [Mpix]"),
              ("03_MC_Heston", "main", r"^(\d+)x(\d+)$", "path-steps [millions]"),
              ("04_Portfolio", "main", r"^p2_(\d+)$", "stage-2 paths per trade")]
    panels = [p for p in panels if not SP[(SP.project == p[0]) & (SP.group == p[1])].empty]
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(TWO_COL, 2.3), squeeze=False)
    bstyle = {"compute": (PAL[0], "-", "compute"), "e2e_warm": (PAL[1], ":", "warm E2E")}
    variants = ["gray8", "rgb8", "rgb16"]
    vmark = {"": "o", "gray8": "o", "rgb8": "s", "rgb16": "^"}
    for ax, (proj, grp, rx, xl) in zip(axes[0], panels):
        for b, (col, ls, _) in bstyle.items():
            B = best_rows(SP, b)
            B = B[(B.project == proj) & (B.group == grp)].copy()
            if B.empty:
                continue
            def xv(c):
                m = re.match(rx, c)
                if not m:
                    return np.nan
                if proj == "02_Convolution":
                    return int(m.group(2)) * int(m.group(3)) / 1e6
                if proj == "03_MC_Heston":
                    return int(m.group(1)) * int(m.group(2)) / 1e6
                return float(m.group(1))
            B["x"] = B.config.map(xv)
            B["series"] = B.config.map(lambda c: re.match(rx, c).group(1)
                                       if proj == "02_Convolution" and re.match(rx, c) else "")
            for s_, g in B.dropna(subset=["x"]).groupby("series"):
                g = g.groupby("x", as_index=False).median(numeric_only=True).sort_values("x")
                ax.plot(g.x, g.speedup, ls=ls, marker=vmark.get(s_, "o"), ms=4, lw=1.4, color=col, zorder=3)
                ax.fill_between(g.x, g.ci_lo, g.ci_hi, color=col, alpha=0.12, lw=0)
        parity(ax)
        logaxis(ax, "x"); logaxis(ax, "y")
        ax.set_xlabel(xl)
        ax.set_title(PRETTY[proj])
    axes[0][0].set_ylabel("FPGA speedup over best CPU (×)")
    handles = [Line2D([], [], color=c, ls=ls, lw=1.4, label=l) for c, ls, l in bstyle.values()]
    if any(p[0] == "02_Convolution" for p in panels):
        handles += [Line2D([], [], color=MUTED, marker=vmark[v], ls="", ms=4, label=f"Conv {v}") for v in variants]
    handles.append(Line2D([], [], color=INK, ls="--", lw=0.8, label="parity"))
    legend_below(fig, handles, ncol=6)
    save(fig, out, "v2_fig02_crossover")


def fig_aes(T, out):
    S = read(T, "summary_v2.csv")
    if S.empty:
        return
    S = S[(S.project == "01_AES") & (S.metric == "throughput_gbps")]
    groups = [g for g in ("enc", "noio") if not S[S.group == g].empty]
    if not groups:
        return
    fig, axes = plt.subplots(1, len(groups), figsize=(TWO_COL if len(groups) > 1 else ONE_COL, 2.4), squeeze=False)
    order = ["fpga", "cpu_vaes", "cpu_aesni", "cpu_openssl", "cpu_ttable", "cpu_vaes_1t"]
    handles_extra = [Line2D([], [], color=MUTED, ls="-.", lw=0.8, label="PCIe Gen3 x16 roofline (≈12 GB/s)")]
    names = {"fpga": "FPGA (U50)", "cpu_vaes": "CPU VAES", "cpu_aesni": "CPU AES-NI", "cpu_openssl": "CPU OpenSSL",
             "cpu_ttable": "CPU T-table (no AES-NI)", "cpu_vaes_1t": "CPU VAES, 1 core"}
    handles = {}
    for ax, g in zip(axes[0], groups):
        G = S[S.group == g].copy()
        G["mb"] = G.config.str.extract(r"(\d+)MB").astype(float)
        for k, p in enumerate([p for p in order if p in set(G.plat)]):
            x = G[G.plat == p].sort_values("mb")
            ax.errorbar(x.mb, x["median"], yerr=x.ci95_half.fillna(0), marker=MARK[k], ms=4, lw=1.4, capsize=2,
                        color=PAL[k], label=names[p])
            handles[p] = Line2D([], [], color=PAL[k], marker=MARK[k], ms=4, lw=1.4, label=names[p])
        ax.axhline(12.0, color=MUTED, lw=0.8, ls="-.")
        logaxis(ax, "x"); logaxis(ax, "y")

        ax.set_xlabel("data size [MB]")
        ax.set_title("files on SSD" if g == "enc" else "compute only (no file I/O)")
    axes[0][0].set_ylabel("throughput [GB/s] (compute window)")
    legend_below(fig, [handles[p] for p in order if p in handles] + handles_extra, ncol=4)
    save(fig, out, "v2_fig03_aes_impls")


def fig_live(T, out):
    S = read(T, "summary_v2.csv")
    if S.empty:
        return
    S = S[S.metric == "fps"]
    panels = []
    if not S[(S.project == "05_LiveStream_Single") & (S.group == "pipe")].empty:
        panels.append(("05_LiveStream_Single", "pipe", "Single output: live pipeline"))
    if not S[(S.project == "05_LiveStream_Single") & (S.group == "ro")].empty:
        panels.append(("05_LiveStream_Single", "ro", "Single output: resize stage only"))
    if not S[(S.project == "06_LiveStream_Multi")].empty:
        panels.append(("06_LiveStream_Multi", None, "5-rung ladder"))
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(TWO_COL, 2.5), squeeze=False)
    handles = {}
    colors = {}
    def color_for(p):
        if p not in colors:
            colors[p] = len(colors)
        return PAL[colors[p] % len(PAL)], HATCH[colors[p] % len(HATCH)]
    for ax, (proj, grp, title) in zip(axes[0], panels):
        if grp:
            G = S[(S.project == proj) & (S.group == grp)]
            cats = [q for q in QORDER if q in set(G.config)]
            key = "config"
        else:
            G = S[(S.project == proj) & S.group.isin(["pipe", "ro"])].copy()
            G["cat"] = G.group.map({"pipe": "pipeline", "ro": "ladder only"})
            cats = [c for c in ("pipeline", "ladder only") if c in set(G.cat)]
            key = "cat"
        plats = sorted(set(G.plat), key=lambda p: (not p.startswith("fpga"), p))
        w = 0.8 / max(1, len(plats))
        for j, p in enumerate(plats):
            c, h = color_for(p)
            for i, cat in enumerate(cats):
                r = G[(G[key] == cat) & (G.plat == p)]
                if r.empty:
                    continue
                r = r.iloc[0]
                x = i + (j - (len(plats) - 1) / 2) * w
                ax.bar(x, r["median"], w * 0.9, color=c, hatch=h, edgecolor="white", linewidth=0.4, zorder=2)
                ax.errorbar([x], [r["median"]], yerr=[[0 if r.ci95_half != r.ci95_half else r.ci95_half]] * 2,
                            fmt="none", ecolor=INK, elinewidth=0.7, capsize=1.5)
            handles[p] = Patch(facecolor=c, hatch=h, edgecolor="white", label=p.replace("_", " "))
        ax.set_xticks(range(len(cats)))
        ax.set_xticklabels(cats)
        logaxis(ax, "y")
        ax.set_title(title)
    axes[0][0].set_ylabel("frames per second")
    legend_below(fig, list(handles.values()), ncol=4)
    save(fig, out, "v2_fig04_live")


def fig_stages(T, out):
    ST = read(T, "stages_v2.csv")
    if ST.empty:
        return
    picks = [("01_AES", "noio", "1024MB"), ("02_Convolution", "synth", "rgb16_7680x4320"),
             ("03_MC_Heston", "main", "4194304x64"), ("04_Portfolio", "main", "p2_131072")]
    rows = []
    for proj, g, c in picks:
        s = ST[(ST.project == proj) & (ST.group == g) & (ST.config == c)]
        if s.empty:
            continue
        f = s[s.plat == "fpga"]
        cp = s[~s.plat.str.startswith("fpga") & s.plat.map(not_ref)]
        if f.empty or cp.empty or "t_compute_s" not in s:
            continue
        f = f.iloc[0]
        best = cp.sort_values("t_compute_s").iloc[0]
        rows.append((f"{PRETTY[proj]}\n{c}", f, best))
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(TWO_COL, 2.4))
    comps = [("h2d", "H2D"), ("kernel", "kernel"), ("d2h", "D2H")]
    for i, (lab, f, best) in enumerate(rows):
        base = f.t_compute_s
        parts = [f.get(col, np.nan) for col, _ in comps]
        tot = np.nansum(parts)
        left = 0.0
        for k, v in enumerate(parts):
            if v != v or tot <= 0:
                continue
            w = v / tot   # share of the window (phases of concurrent CUs overlap in time)
            ax.barh(i + 0.2, w, 0.36, left=left, color=PAL[k], hatch=HATCH[k], edgecolor="white", linewidth=0.4)
            left += w
        ax.barh(i - 0.2, best.t_compute_s / base, 0.36, color=NEUTRAL, edgecolor="white", linewidth=0.4)
        ax.text(best.t_compute_s / base, i - 0.2, f"  {best.plat}", va="center", fontsize=6, color=MUTED)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlabel("time relative to the FPGA compute window (= 1.0, split by phase share)")
    ax.axvline(1.0, color=INK, lw=0.6, ls="--")
    ax.set_xlim(0, max(1.1, ax.get_xlim()[1]))
    handles = [Patch(facecolor=PAL[k], hatch=HATCH[k], edgecolor="white", label=f"FPGA {n}") for k, (_, n) in enumerate(comps)]
    handles.append(Patch(facecolor=NEUTRAL, label="best CPU (compute)"))
    legend_below(fig, handles, ncol=4)
    save(fig, out, "v2_fig05_stages")


def fig_energy(T, out):
    ER = read(T, "energy_v2.csv")
    if ER.empty:
        return
    E = ER[ER.boundary == "system"]
    rows = []
    for p, (g, c) in HEADLINE.items():
        x = E[(E.project == p) & (E.group == g) & (E.config == c) & (E.fpga == "fpga")]
        if x.empty:
            continue
        x = x[x.cpu.map(not_ref)]
        if x.empty:
            continue
        best = x.sort_values("cpu_J_per_unit").iloc[0]
        rows.append((p, best))
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(TWO_COL, 2.4))
    for i, (p, r) in enumerate(rows):
        ax.bar(i, r.fpga_advantage, 0.6, color=PAL[0] if r.fpga_advantage >= 1 else PAL[1],
               hatch="" if r.fpga_advantage >= 1 else "////", edgecolor="white", zorder=2)
        mf.vlabel(ax, i, r.fpga_advantage, f"{r.fpga_advantage:.2g}×")
    parity(ax)
    logaxis(ax, "y")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([f"{PRETTY[p]}\nvs {r.cpu.replace('cpu_', '')}" for p, r in rows])
    ax.set_ylabel("FPGA energy advantage (×)")
    handles = [Patch(facecolor=PAL[0], label="FPGA uses less energy"),
               Patch(facecolor=PAL[1], hatch="////", edgecolor="white", label="CPU uses less energy"),
               Line2D([], [], color=INK, ls="--", lw=0.8, label="parity: CPU J/unit ÷ FPGA J/unit = 1 (system boundary)")]
    legend_below(fig, handles, ncol=2)
    save(fig, out, "v2_fig06_energy")


def fig_v1v2(T, out):
    V = read(T, "v1_vs_v2.csv")
    if V.empty or "v2_speedup_compute" not in V:
        return
    V = V.dropna(subset=["v1_speedup_compute", "v2_speedup_compute"], how="all")
    if V.empty:
        return
    fig, ax = plt.subplots(figsize=(TWO_COL, 0.35 * len(V) + 1.2))
    for i, (_, r) in enumerate(V.iterrows()):
        a, b = r.v1_speedup_compute, r.v2_speedup_compute
        if a == a and b == b:
            ax.plot([a, b], [i, i], color=GRID, lw=2, zorder=1)
        if a == a:
            ax.plot(a, i, "o", color=PAL[1], ms=6, zorder=2)
        if b == b:
            ax.plot(b, i, "s", color=PAL[0], ms=6, zorder=3)
    ax.axvline(1.0, color=INK, lw=0.8, ls="--")
    logaxis(ax, "x")
    ax.set_yticks(range(len(V)))
    ax.set_yticklabels([f"{PRETTY.get(r.project, r.project)}: {r.point}" for _, r in V.iterrows()])
    ax.invert_yaxis()
    ax.set_xlabel("FPGA speedup over the CPU baseline (×, compute; live: FPS ratio)")
    handles = [Line2D([], [], color=PAL[1], marker="o", ls="", ms=6, label="v1 (thesis code)"),
               Line2D([], [], color=PAL[0], marker="s", ls="", ms=6, label="v2 (A-grade FPGA vs A-grade CPU)")]
    legend_below(fig, handles, ncol=2)
    save(fig, out, "v2_fig07_v1_vs_v2")


def fig_cpu_util(T, out):
    ST = read(T, "stages_v2.csv")
    if ST.empty or "cpu_util_proc_pct" not in ST:
        return
    S = ST[ST.project.isin(["05_LiveStream_Single", "06_LiveStream_Multi"]) & (ST.group == "pipe")].dropna(subset=["cpu_util_proc_pct"])
    if S.empty:
        return
    S = S.copy()
    S["cat"] = S.project.map(lambda p: "multi" if p.startswith("06") else "single") + " " + S.config
    cats = sorted(set(S.cat), key=lambda c: (c.split()[0], QORDER.index(c.split()[1]) if c.split()[1] in QORDER else 9))
    fig, ax = plt.subplots(figsize=(TWO_COL, 2.3))
    for j, p in enumerate(["fpga", "cpu"]):
        for i, c in enumerate(cats):
            r = S[(S.cat == c) & (S.plat == p)]
            if r.empty:
                continue
            ax.bar(i + (j - 0.5) * 0.38, r.cpu_util_proc_pct.iloc[0], 0.36, color=PAL[j], hatch=HATCH[j], edgecolor="white", zorder=2)
    ax.set_xticks(range(len(cats)))
    ax.set_xticklabels(cats)
    ax.set_ylim(0, 100)
    ax.set_ylabel("utilisation of the 24 pinned cores [%]")
    legend_below(fig, [Patch(facecolor=PAL[0], label="FPGA run (host side)"),
                       Patch(facecolor=PAL[1], hatch=HATCH[1], edgecolor="white", label="CPU run")], ncol=2)
    save(fig, out, "v2_fig08_cpu_util")


def fig_power(R, out):
    RAW = os.path.join(R, "raw")
    picks = [("03_MC_Heston", "main", "4194304x64"), ("04_Portfolio", "main", "p2_131072"), ("01_AES", "noio", "4096MB")]
    panels = []
    for proj, g, c in picks:
        d = os.path.join(RAW, proj, g, c)
        if not os.path.isdir(d):
            continue
        runs = {}
        for plat in sorted(os.listdir(d)):
            rd = os.path.join(d, plat, "rep1")
            if os.path.exists(os.path.join(rd, "power.csv")) and os.path.exists(os.path.join(rd, "meta.json")):
                runs[plat] = rd
        f = runs.get("fpga")
        cpus = [p for p in runs if not p.startswith("fpga")]
        if f and cpus:
            panels.append((proj, c, [("fpga", f), (cpus[0], runs[cpus[0]])]))
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(TWO_COL, 2.3), squeeze=False)
    handles = {}
    for ax, (proj, c, runs) in zip(axes[0], panels):
        for k, (plat, rd) in enumerate(runs):
            m = json.load(open(os.path.join(rd, "meta.json")))
            p = pd.read_csv(os.path.join(rd, "power.csv"))
            t = p.t_unix - m["t_launch"]
            tot = p.pkg0_w.fillna(0) + p.dram0_w.fillna(0) + p.card_w.fillna(0)
            ax.plot(t, tot, color=PAL[k], lw=1.2, label=f"{plat} (system)")
            handles[f"{k}"] = Line2D([], [], color=PAL[k], lw=1.2, label=("FPGA run" if k == 0 else "CPU run") + " – system power")
            if m.get("t_start_marker") and m.get("t_end_marker"):
                ax.axvspan(m["t_start_marker"] - m["t_launch"], m["t_end_marker"] - m["t_launch"], color=PAL[k], alpha=0.08, lw=0)
        ax.set_title(f"{PRETTY[proj]} {c}")
        ax.set_xlabel("time since launch [s]")
    axes[0][0].set_ylabel("socket0 + DRAM + card [W]")
    handles["span"] = Patch(facecolor=NEUTRAL, alpha=0.4, label="measured section")
    legend_below(fig, list(handles.values()), ncol=3)
    save(fig, out, "v2_fig09_power")


def fig_resources(T, out):
    I = read(T, "fpga_impl_v2.csv")
    if I.empty or "kernels_total.LUT_pct" not in I:
        return
    I = I[I.project.isin(PRETTY)].copy()
    if I.empty:
        return
    res = [("LUT", "LUT"), ("REG", "FF"), ("BRAM", "BRAM"), ("URAM", "URAM"), ("DSP", "DSP")]
    fig, ax = plt.subplots(figsize=(TWO_COL, 2.4))
    w = 0.8 / len(res)
    for k, (col, lab) in enumerate(res):
        c = f"kernels_total.{col}_pct"
        if c not in I:
            continue
        for i, (_, r) in enumerate(I.iterrows()):
            v = r[c]
            if v == v:
                ax.bar(i + (k - (len(res) - 1) / 2) * w, v, w * 0.9, color=PAL[k], hatch=HATCH[k], edgecolor="white", zorder=2)
    ax.axhline(70, color=MUTED, lw=0.8, ls="-.")
    ax.set_ylim(0, 100)
    ax.set_xticks(range(len(I)))
    labels = []
    for _, r in I.iterrows():
        wns = r.get("WNS_kernel_clocks_min", r.get("WNS_ns", float("nan")))
        labels.append(f"{PRETTY[r.project]}\nWNS {wns:+.2f} ns" if wns == wns else PRETTY[r.project])
    ax.set_xticklabels(labels)
    ax.set_ylabel("share of the U50 user budget [%]")
    handles = [Patch(facecolor=PAL[k], hatch=HATCH[k], edgecolor="white", label=l) for k, (_, l) in enumerate(res)]
    handles.append(Line2D([], [], color=MUTED, ls="-.", lw=0.8, label="70% planning limit"))
    legend_below(fig, handles, ncol=6)
    save(fig, out, "v2_fig10_resources")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    a = ap.parse_args()
    R = os.path.abspath(a.results)
    T, out = os.path.join(R, "tables"), os.path.join(R, "figures")
    os.makedirs(out, exist_ok=True)
    for fn in (fig_overview, fig_crossover, fig_aes, fig_live, fig_stages, fig_energy, fig_v1v2, fig_cpu_util, fig_resources):
        try:
            fn(T, out)
        except Exception as e:
            mf.QC.append(f"{fn.__name__}: FAILED ({e})")
            plt.close("all")
    try:
        fig_power(R, out)
    except Exception as e:
        mf.QC.append(f"fig_power: FAILED ({e})")
    open(os.path.join(out, "LAYOUT_QC.txt"), "w").write("\n".join(mf.QC) + ("\n" if mf.QC else "all figures passed the layout check\n"))
    print("v2 figures ->", out, "| QC:", "; ".join(mf.QC) or "ok")


if __name__ == "__main__":
    sys.exit(main())
