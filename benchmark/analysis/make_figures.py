#!/usr/bin/env python3
"""
make_figures.py - journal figures from results/tables/*.csv

Every figure is written as PDF (vector) + PNG (300 dpi) to results/figures/.
Layout safety (no cropping, no overlapping text):
  * constrained layout + legends placed OUTSIDE the plotting area
  * after drawing, `fix_layout()` measures every tick label / annotation / legend,
    rotates or shrinks colliding tick labels, drops colliding value labels, and
    grows the figure if needed; `savefig(bbox_inches="tight")` then guarantees
    nothing is cut off. Anything that could not be fixed is listed in
    figures/LAYOUT_QC.txt.
"""
import argparse, os, sys, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

warnings.filterwarnings("ignore")

# validated categorical palette (fixed order) + neutrals
PAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, MUTED, GRID, NEUTRAL = "#0b0b0b", "#52514e", "#d9d8d4", "#b8b7b2"
HATCH = ["", "////", "\\\\\\\\", "....", "xxxx", "oo", "--", "++"]
ONE_COL, TWO_COL = 3.5, 7.16

plt.rcParams.update({
    "font.family": "serif", "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
    "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.5, "axes.axisbelow": True,
    "savefig.dpi": 300, "pdf.fonttype": 42, "ps.fonttype": 42,
    "figure.constrained_layout.use": True, "legend.frameon": False,
})

PRETTY = {"01_AES": "AES-256", "02_Convolution": "2D Conv", "03_MC_Heston": "MC Heston",
          "04_Portfolio": "Portfolio", "05_LiveStream_Single": "Live single", "06_LiveStream_Multi": "Live multi"}
BOUND = {"1_kernel": "Kernel only", "2_pipeline": "Pipeline (H2D+K+D2H)",
         "3_daemon_e2e": "Daemon E2E (no init)", "4_warm_e2e": "Warm E2E (bitstream cached)",
         "5_cold_e2e": "Cold E2E (bitstream swap)"}
QC = []

# ---------------------------------------------------------------------------
# layout guard
# ---------------------------------------------------------------------------
def _bboxes(texts, r):
    out = []
    for t in texts:
        if t.get_visible() and t.get_text().strip():
            try:
                out.append((t, t.get_window_extent(r)))
            except Exception:
                pass
    return out

def _ticklabels(axis):
    """Tick labels that are actually drawn (inside the view interval)."""
    lo, hi = sorted(axis.get_view_interval())
    out = []
    for tick in axis.get_major_ticks():
        loc = tick.get_loc()
        if lo - 1e-12 * abs(lo) <= loc <= hi + 1e-12 * abs(hi) and tick.label1.get_visible():
            out.append(tick.label1)
    return out

def _any_overlap(items, pad=-0.5):
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i][1], items[j][1]
            if a.x0 < b.x1 - pad and b.x0 < a.x1 - pad and a.y0 < b.y1 - pad and b.y0 < a.y1 - pad:
                return (items[i][0], items[j][0])
    return None

def fix_layout(fig, name):
    grown = [0]
    for attempt in range(6):
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        changed = False
        for ax in fig.axes:
            # 1) x tick labels colliding -> rotate, then shrink
            xt = _bboxes(_ticklabels(ax.xaxis), r)
            if _any_overlap(xt):
                rot = xt[0][0].get_rotation()
                for t, _ in xt:
                    if rot < 30:
                        t.set_rotation(30); t.set_ha("right"); t.set_rotation_mode("anchor")
                    elif rot < 60:
                        t.set_rotation(90); t.set_ha("center"); t.set_va("top"); t.set_rotation_mode("default")
                    else:
                        t.set_fontsize(max(5, t.get_fontsize() - 0.5))
                changed = True
            yt = _bboxes(_ticklabels(ax.yaxis), r)
            if _any_overlap(yt):
                for t, _ in yt:
                    t.set_fontsize(max(5, t.get_fontsize() - 0.5))
                changed = True
            # 2) value labels colliding with each other -> hide the later one
            ann = _bboxes([t for t in ax.texts if t.get_gid() == "value"], r)
            hit = _any_overlap(ann)
            while hit:
                hit[1].set_visible(False)
                changed = True
                ann = _bboxes([t for t in ax.texts if t.get_gid() == "value"], r)
                hit = _any_overlap(ann)
            # 3) value labels that stick out of the axes -> hide
            axbb = ax.get_window_extent(r)
            for t, bb in _bboxes([t for t in ax.texts if t.get_gid() == "value"], r):
                if bb.y1 > axbb.y1 + 2 or bb.x1 > axbb.x1 + 30 or bb.x0 < axbb.x0 - 30:
                    t.set_visible(False); changed = True
            # 4) an in-axes legend covering data -> move it outside below
            leg = ax.get_legend()
            if leg is not None and leg.get_visible():
                lb = leg.get_window_extent(r)
                data_bbs = []
                for art in list(ax.patches) + list(ax.lines) + list(ax.collections):
                    try:
                        data_bbs.append(art.get_window_extent(r))
                    except Exception:
                        pass
                if any(lb.overlaps(b) and b.width < axbb.width * 0.95 for b in data_bbs):
                    handles, labels = ax.get_legend_handles_labels()
                    if not handles and hasattr(leg, "legend_handles"):
                        handles = leg.legend_handles
                        labels = [t.get_text() for t in leg.get_texts()]
                    leg.remove()
                    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.18),
                              ncol=min(4, len(labels)), frameon=False)
                    changed = True
        # 5) figure-level legend overlapping axes -> grow the figure
        for leg in fig.legends:
            if leg.get_window_extent(r).y1 <= fig.bbox.y0 + 1:
                continue      # old-matplotlib fallback legend sits below the canvas on purpose
            lb = leg.get_window_extent(r)
            for ax in fig.axes:
                if lb.overlaps(ax.get_tightbbox(r)) and grown[0] < 3:
                    grown[0] += 1
                    w, h = fig.get_size_inches()
                    fig.set_size_inches(w, h * 1.08)
                    changed = True
                    break
        # 6) drawn text outside the figure canvas -> enlarge the figure once (max +30 %)
        fb = fig.bbox
        texts = list(fig.texts)
        for leg in fig.legends:
            texts += leg.get_texts()
        for ax in fig.axes:
            if not ax.get_visible():
                continue
            texts += [ax.title, ax.xaxis.label, ax.yaxis.label] + list(ax.texts)
            texts += _ticklabels(ax.xaxis) + _ticklabels(ax.yaxis)
        over_x = over_y = 0.0
        for t, bb in _bboxes(texts, r):
            over_x = max(over_x, fb.x0 - bb.x0, bb.x1 - fb.x1)
            if bb.y1 > fb.y0 + 1:                  # ignore legends parked below the canvas
                over_y = max(over_y, fb.y0 - bb.y0, bb.y1 - fb.y1)
        if (over_x > 2 or over_y > 2) and grown[0] < 2:
            w, h = fig.get_size_inches()
            fig.set_size_inches(w * min(1 + 2 * max(over_x, 0) / fb.width, 1.15),
                                h * min(1 + 2 * max(over_y, 0) / fb.height, 1.15))
            grown[0] += 1
            changed = True
        if not changed:
            return
    QC.append(f"{name}: layout still had adjustments after 6 passes - please eyeball it")

def save(fig, out, name):
    fix_layout(fig, name)
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(out, f"{name}.{ext}"), bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    print("  wrote", name)

def legend_below(fig, handles, labels=None, ncol=4):
    if labels is None:
        labels = [h.get_label() for h in handles]
    try:   # matplotlib >= 3.7
        fig.legend(handles, labels, loc="outside lower center", ncol=min(ncol, len(handles)))
    except (ValueError, TypeError):   # older matplotlib: anchor below the figure, bbox_inches="tight" keeps it
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.0),
                   ncol=min(ncol, len(handles)))

def vlabel(ax, x, y, s, **kw):
    ax.text(x, y, s, ha="center", va="bottom", fontsize=5.5, color=MUTED, gid="value", **kw)

def read(T, name):
    p = os.path.join(T, name)
    if not os.path.exists(p) or os.path.getsize(p) < 5:
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()

# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def fig_speedup_ladder(T, out):
    sp = read(T, "speedups.csv")
    if sp.empty:
        return
    sp = sp[sp.cpu_baseline == "cpu"]
    picks = [("01_AES", "enc_2048MB"), ("01_AES", "dec_2048MB"), ("02_Convolution", "16bit"),
             ("02_Convolution", "8bit"), ("02_Convolution", "bw"), ("03_MC_Heston", "p131072_s32"),
             ("03_MC_Heston", "p4194304_s64"), ("04_Portfolio", "full"), ("05_LiveStream_Single", "1080p"),
             ("05_LiveStream_Single", "240p"), ("06_LiveStream_Multi", "all5")]
    picks = [p for p in picks if ((sp.project == p[0]) & (sp.config == p[1])).any()]
    if not picks:
        return
    bnds = [b for b in BOUND if b in set(sp.boundary)]
    fig, ax = plt.subplots(figsize=(TWO_COL, 3.0))
    w = 0.8 / len(bnds)
    for i, b in enumerate(bnds):
        xs, ys, lo, hi = [], [], [], []
        for k, (p, c) in enumerate(picks):
            r = sp[(sp.project == p) & (sp.config == c) & (sp.boundary == b)]
            if b == "5_cold_e2e" and r.empty:
                r = sp[(sp.project == p) & (sp.config == c) & (sp.boundary == "6_cold_launch_to_done")]
            if r.empty:
                continue
            r = r.iloc[0]
            xs.append(k + (i - (len(bnds) - 1) / 2) * w); ys.append(r.speedup)
            lo.append(r.speedup - r.ci_lo if r.ci_lo == r.ci_lo else 0)
            hi.append(r.ci_hi - r.speedup if r.ci_hi == r.ci_hi else 0)
        ax.bar(xs, ys, w * 0.92, color=PAL[i], hatch=HATCH[i], edgecolor="white", linewidth=0.4,
               label=BOUND[b], yerr=[lo, hi], error_kw=dict(lw=0.6, capsize=1.5, ecolor=INK))
    ax.axhline(1, color=INK, lw=0.8, ls="--", label="Parity (FPGA = CPU)")
    ax.set_yscale("log")
    ax.set_ylabel("Speedup over 24-core CPU (×, log)")
    ax.set_xticks(range(len(picks)))
    short = {"enc_2048MB": "enc 2 GB", "dec_2048MB": "dec 2 GB", "p131072_s32": "131k×32",
             "p4194304_s64": "4.2M×64", "full": "", "all5": "5 outputs"}
    ax.set_xticklabels([f"{PRETTY[p]} {short.get(c, c)}".strip() for p, c in picks])
    ax.text(1.0, 1.01, "values and 95 % CIs: tables/speedups.csv", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=6, color=MUTED)
    v = sp.speedup[np.isfinite(sp.speedup) & (sp.speedup > 0)]
    if len(v):
        ax.set_ylim(max(1e-3, v.min() * 0.5), v.max() * 3)
    legend_below(fig, ax.get_legend_handles_labels()[0], ncol=3)
    save(fig, out, "fig01_speedup_ladder")

def fig_aes_amortisation(T, out):
    sp = read(T, "speedups.csv")
    if sp.empty or not (sp.project == "01_AES").any():
        return
    a = sp[sp.project == "01_AES"].copy()
    a["mode"] = a.config.str.extract(r"^(enc|dec)")
    a["mb"] = a.config.str.extract(r"_(\d+)MB").astype(float)
    fig, axes = plt.subplots(1, 2, figsize=(TWO_COL, 2.6), sharey=True)
    handles = {}
    for ax, mode in zip(axes, ("enc", "dec")):
        d = a[a["mode"] == mode]
        for i, b in enumerate(list(BOUND) + ["4b_warm_e2e_ramdisk"]):
            r = d[(d.boundary == b) & (d.cpu_baseline == "cpu")].sort_values("mb")
            if r.empty:
                continue
            h = ax.errorbar(r.mb, r.speedup, yerr=[r.speedup - r.ci_lo.fillna(r.speedup), r.ci_hi.fillna(r.speedup) - r.speedup],
                            color=PAL[i], marker="osD^vP"[i], ms=4, lw=1.6, capsize=1.5,
                            label=BOUND.get(b, "Warm E2E, files on RAM disk"))
            handles[BOUND.get(b, "Warm E2E, files on RAM disk")] = h
        for j, ob in enumerate(("openssl_aesni", "openssl_noaesni")):
            r = d[(d.cpu_baseline == ob)].sort_values("mb")
            if not r.empty:
                lab = f"Warm E2E vs OpenSSL {'AES-NI' if ob == 'openssl_aesni' else 'no AES-NI'} (1 thread)"
                h, = ax.plot(r.mb, r.speedup, color=PAL[6 + j], ls=":", marker="x", ms=4, lw=1.2, label=lab)
                handles[lab] = h
        ax.axhline(1, color=INK, lw=0.8, ls="--")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_title("Encryption" if mode == "enc" else "Decryption")
        ax.set_xlabel("Input size (MB)")
    axes[0].set_ylabel("Speedup (×, log)")
    legend_below(fig, list(handles.values()), list(handles.keys()), ncol=3)
    save(fig, out, "fig02_aes_amortisation")

def fig_bitstream(T, out):
    bl = read(T, "bitstream_load.csv")
    if bl.empty:
        return
    bl = bl[bl.state.isin(["swap", "cached"])]
    projects = [p for p in PRETTY if p in set(bl.project)]
    fig, ax = plt.subplots(figsize=(ONE_COL, 2.4))
    rng = np.random.default_rng(0)
    for k, p in enumerate(projects):
        for j, (st, col) in enumerate((("swap", PAL[1]), ("cached", PAL[0]))):
            v = bl[(bl.project == p) & (bl.state == st)].load_s.dropna().values
            if len(v) == 0:
                continue
            x = k + (j - 0.5) * 0.36
            ax.bar(x, np.median(v), 0.34, color=col, edgecolor="white")
            ax.scatter(x + rng.uniform(-0.1, 0.1, len(v)), v, s=6, color=INK, zorder=3, linewidths=0)
    ax.set_yscale("log")
    ax.set_ylabel("Bitstream load / init (s, log)")
    ax.set_xticks(range(len(projects)))
    ax.set_xticklabels([PRETTY[p] for p in projects])
    legend_below(fig, [Patch(color=PAL[1], label="Different bitstream on card (swap)"),
                       Patch(color=PAL[0], label="Same bitstream already loaded")], ncol=1)
    save(fig, out, "fig03_bitstream_swap_vs_cached")

def fig_decomposition(T, out):
    runs = read(T, "runs.csv")
    if runs.empty:
        return
    f = runs[(runs.platform == "fpga") & (runs.ok == True) & (~runs.profiled)]
    rows = []
    def add(label, d, parts):
        if d.empty:
            return
        med = {k: np.nanmedian(np.nansum([d[c].astype(float).values for c in cols if c in d], axis=0))
               if any(c in d for c in cols) else 0 for k, cols in parts.items()}
        tot = np.nanmedian(d["wall_s"].astype(float)) if "wall_s" in d else np.nan
        if not tot or tot != tot:
            return
        known = sum(v for v in med.values() if v == v)
        med["Other host / process"] = max(0.0, tot - known)
        rows.append((label, {k: 100 * v / tot for k, v in med.items()}))
    aesP = {"Bitstream + context": ["init_s"], "Storage I/O": ["file_read_s", "file_write_s"],
            "PCIe transfer": ["h2d_s", "d2h_s"], "Kernel": ["kernel_s"]}
    for grp, tag in (("cold", "cold"), ("main", "warm")):
        add(f"AES enc 2 GB ({tag})", f[(f.project == "01_AES") & (f.group == grp) & (f.config == "enc_2048MB")], aesP)
    convP = {"Bitstream + context": ["fixed_setup_s"], "Storage I/O": ["image_load_s", "save_s"],
             "PCIe transfer": ["h2d_s", "d2h_s"], "Kernel": ["kernel_s"]}
    for grp, tag in (("cold", "cold"), ("main", "warm")):
        add(f"Conv 16-bit ({tag})", f[(f.project == "02_Convolution") & (f.group == grp) & (f.config == "16bit")], convP)
    mcP = {"Bitstream + context": ["program_s"], "PCIe transfer": ["h2d_s"], "Kernel": ["kernel_s", "warmup_s"]}
    for grp, tag in (("cold", "cold"), ("main", "warm")):
        add(f"MC Heston 131k×32 ({tag})", f[(f.project == "03_MC_Heston") & (f.group == grp) & (f.config == "p131072_s32")], mcP)
    pfP = {"Bitstream + context": ["program_s"], "Storage I/O": ["load_s"], "PCIe transfer": ["h2d_s"],
           "Kernel": ["stage1_s", "stage2_s"]}
    for grp, tag in (("cold", "cold"), ("main", "warm")):
        add(f"Portfolio ({tag})", f[(f.project == "04_Portfolio") & (f.group == grp)], pfP)
    if not rows:
        return
    comps = ["Bitstream + context", "Storage I/O", "PCIe transfer", "Kernel", "Other host / process"]
    cols = {c: col for c, col in zip(comps, [PAL[1], PAL[3], PAL[4], PAL[0], NEUTRAL])}
    fig, ax = plt.subplots(figsize=(TWO_COL, 0.42 * len(rows) + 0.9))
    for i, (label, sh) in enumerate(rows):
        left = 0
        for c in comps:
            v = sh.get(c, 0) or 0
            if v <= 0:
                continue
            ax.barh(i, v, left=left, color=cols[c], edgecolor="white", linewidth=1)
            if v >= 7:
                ax.text(left + v / 2, i, f"{v:.0f}%", ha="center", va="center", fontsize=6,
                        color="white" if c in ("Bitstream + context", "Kernel") else INK, gid="value")
            left += v
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows])
    ax.invert_yaxis(); ax.set_xlim(0, 100); ax.grid(axis="y", visible=False)
    ax.set_xlabel("Share of FPGA process wall time (%)  - median of repetitions")
    legend_below(fig, [Patch(color=cols[c], label=c) for c in comps], ncol=5)
    save(fig, out, "fig04_latency_decomposition")

def fig_bandwidth(T, out):
    bw = read(T, "xrt_bandwidth.csv")
    if bw.empty:
        return
    bw["util_pct"] = pd.to_numeric(bw.util_pct, errors="coerce")
    bw["rate_MBps"] = pd.to_numeric(bw.rate_MBps, errors="coerce")
    rows = []
    for p in PRETTY:
        d = bw[bw.project == p]
        if d.empty:
            continue
        for (link, direction), g in d.groupby(["link", "direction"]):
            i = g.util_pct.idxmax()
            rows.append((f"{PRETTY[p]} - {link} {direction}", g.util_pct.max(), g.loc[i, "rate_MBps"], link))
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(ONE_COL + 0.6, 0.22 * len(rows) + 0.9))
    y = np.arange(len(rows))
    ax.barh(y, [r[1] for r in rows], color=[PAL[0] if r[3] == "PCIe" else PAL[2] for r in rows], edgecolor="white")
    for yi, r in zip(y, rows):
        ax.text(min(r[1], 100) + 1, yi, f"{r[1]:.0f}% ({r[2] / 1000:.1f} GB/s)", va="center", fontsize=5.5, color=INK, gid="value")
    ax.set_yticks(y); ax.set_yticklabels([r[0] for r in rows])
    ax.invert_yaxis(); ax.grid(axis="y", visible=False)
    ax.set_xlim(0, 130); ax.axvline(80, color=INK, lw=0.8, ls="--")
    ax.set_xlabel("Peak utilisation of port maximum (%)")
    legend_below(fig, [Patch(color=PAL[0], label="PCIe Gen3×16 (host ↔ HBM)"),
                       Patch(color=PAL[2], label="Kernel AXI ↔ HBM")], ncol=1)
    save(fig, out, "fig05_pcie_hbm_utilisation")

def fig_energy(T, out):
    er = read(T, "energy_ratios.csv")
    if er.empty:
        return
    er = er[er.cpu_baseline == "cpu"]
    picks = []
    for p in PRETTY:
        d = er[er.project == p]
        if d.empty:
            continue
        pref = {"01_AES": ["enc_2048MB", "dec_2048MB"], "02_Convolution": ["16bit"],
                "03_MC_Heston": ["p131072_s32", "p4194304_s64"], "05_LiveStream_Single": ["240p", "1080p"]}.get(p)
        cfgs = [c for c in (pref or list(d.config)) if c in set(d.config)] or list(d.config)[:2]
        picks += [(p, c) for c in cfgs]
    series = [("ratio_card_only_legacy", "Card only vs CPU package (thesis draft boundary)", PAL[1]),
              ("ratio_cpu_server_without_card", "System FPGA vs CPU server without card", PAL[2]),
              ("ratio_system", "System vs system (same server)", PAL[0])]
    fig, ax = plt.subplots(figsize=(TWO_COL, 2.8))
    w = 0.26
    for i, (col, lab, c) in enumerate(series):
        xs, ys = [], []
        for k, (p, cfg) in enumerate(picks):
            v = er[(er.project == p) & (er.config == cfg)][col]
            if len(v) and v.iloc[0] == v.iloc[0]:
                xs.append(k + (i - 1) * w); ys.append(v.iloc[0])
        ax.bar(xs, ys, w * 0.92, color=c, hatch=HATCH[i], edgecolor="white", label=lab)
        if col == "ratio_system":          # label only the headline boundary
            for x, y in zip(xs, ys):
                vlabel(ax, x, y * 1.15, f"{y:.2g}")
    ax.axhline(1, color=INK, lw=0.8, ls="--")
    ax.set_yscale("log")
    ax.set_ylabel("Energy advantage of FPGA\n(CPU J / FPGA J per unit, log)")
    ax.set_xticks(range(len(picks))); ax.set_xticklabels([f"{PRETTY[p]}\n{c}" for p, c in picks])
    vals_ = er[[s[0] for s in series]].values.astype(float)
    vals_ = vals_[np.isfinite(vals_) & (vals_ > 0)]
    if vals_.size == 0:
        plt.close(fig)
        QC.append("fig06_energy_boundary: skipped (no energy data - RAPL/card sensors unreadable?)")
        return
    ax.set_ylim(max(vals_.min() * 0.4, 1e-3), vals_.max() * 3)
    legend_below(fig, ax.get_legend_handles_labels()[0], ncol=3)
    save(fig, out, "fig06_energy_boundary")

def fig_mc_scaling(T, out):
    runs = read(T, "runs.csv")
    d = runs[(runs.project == "03_MC_Heston") & (runs.group == "main") & (~runs.profiled) & (runs.ok == True)] if not runs.empty else runs
    if d.empty:
        return
    d = d.assign(ps=d.paths * d.steps)
    fig, axes = plt.subplots(1, 2, figsize=(TWO_COL, 2.4))
    for i, plat in enumerate(("cpu", "fpga")):
        g = d[d.platform == plat].groupby("ps").compute_s.agg(["median", "min", "max"]).reset_index()
        axes[0].errorbar(g.ps, g["median"], yerr=[g["median"] - g["min"], g["max"] - g["median"]],
                         color=PAL[i], marker="os"[i], ms=4, lw=1.5, capsize=1.5,
                         label="CPU 24 cores" if plat == "cpu" else "FPGA 1 CU (kernel)")
    axes[0].set_xscale("log"); axes[0].set_yscale("log")
    axes[0].set_xlabel("paths × steps"); axes[0].set_ylabel("Compute time (s, log)")
    c = d[d.platform == "cpu"].groupby("ps").compute_s.median()
    f_ = d[d.platform == "fpga"].groupby("ps").compute_s.median()
    eq = (24 * c / f_).dropna()
    axes[1].plot(eq.index, eq.values, color=PAL[2], marker="D", ms=4, lw=1.5, label="Core-equivalents per FPGA CU")
    axes[1].set_xscale("log"); axes[1].set_xlabel("paths × steps")
    axes[1].set_ylabel("CPU cores matched by 1 CU")
    axes[1].set_ylim(0, max(eq.max() * 1.3, 1))
    h0, l0 = axes[0].get_legend_handles_labels(); h1, l1 = axes[1].get_legend_handles_labels()
    legend_below(fig, h0 + h1, l0 + l1, ncol=3)
    save(fig, out, "fig07_mc_scaling")

def fig_live(T, out):
    runs = read(T, "runs.csv")
    if runs.empty:
        return
    d = runs[(runs.project == "05_LiveStream_Single") & (runs.group == "main") & (~runs.profiled) & (runs.ok == True)]
    if d.empty:
        return
    qs = [q for q in ("240p", "360p", "480p", "720p", "1080p") if q in set(d.config)]
    plats = [p for p in ("fpga", "cpu", "cpu_direct") if p in set(d.platform)]
    lab = {"fpga": "FPGA (6 CU)", "cpu": "CPU, original pipeline (pack/unpack)", "cpu_direct": "CPU, direct pipeline"}
    fig, ax = plt.subplots(figsize=(ONE_COL + 0.8, 2.4))
    w = 0.8 / len(plats)
    for i, p in enumerate(plats):
        g = d[d.platform == p].groupby("config").fps.agg(["median", "min", "max"]).reindex(qs)
        x = np.arange(len(qs)) + (i - (len(plats) - 1) / 2) * w
        ax.bar(x, g["median"], w * 0.92, color=PAL[i], hatch=HATCH[i], edgecolor="white", label=lab[p],
               yerr=[g["median"] - g["min"], g["max"] - g["median"]], error_kw=dict(lw=0.6, capsize=1.5))
    src = runs[runs.project == "05_LiveStream_Single"]
    ax.set_xticks(range(len(qs))); ax.set_xticklabels(qs)
    ax.set_ylabel("Frames per second (processing window)")
    legend_below(fig, ax.get_legend_handles_labels()[0], ncol=2)
    save(fig, out, "fig08_live_single_fps")

def fig_power_timelines(R, T, out):
    runs = read(T, "runs.csv")
    if runs.empty:
        return
    projects = [p for p in PRETTY if p in set(runs.project)]
    n = len(projects)
    cols = 2 if n > 1 else 1
    rows_ = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows_, cols, figsize=(TWO_COL, 1.7 * rows_ + 0.5), squeeze=False)
    handles = {}
    for ax, p in zip(axes.flat, projects):
        d = runs[(runs.project == p) & (runs.group == "main") & (runs.rep == "rep1") & (runs.ok == True)]
        if d.empty:
            ax.set_visible(False); continue
        cfg = d.config.iloc[-1]
        for i, plat in enumerate(("cpu", "fpga")):
            r = d[(d.config == cfg) & (d.platform == plat)]
            if r.empty:
                continue
            rd = os.path.join(R, "raw", p, "main", cfg, plat, "rep1", "power.csv")
            if not os.path.exists(rd):
                continue
            pw = pd.read_csv(rd)
            t = pw.t_unix - r.t_launch.iloc[0]
            host = pw.pkg0_w.fillna(0) + pw.dram0_w.fillna(0)
            sysw = host + pw.card_w.fillna(0)
            lbl = f"{'CPU' if plat == 'cpu' else 'FPGA'} run: system (pkg0+DRAM0+card)"
            h, = ax.plot(t, sysw, color=PAL[i], lw=1.0, label=lbl)
            handles[lbl] = h
            if plat == "fpga":
                h2, = ax.plot(t, pw.card_w, color=PAL[2], lw=1.0, ls="--", label="FPGA run: card only")
                handles["FPGA run: card only"] = h2
        ax.set_title(f"{PRETTY[p]} ({cfg}, rep1)")
        ax.set_xlabel("time since launch (s)"); ax.set_ylabel("W")
    for ax in list(axes.flat)[n:]:
        ax.set_visible(False)
    if handles:
        legend_below(fig, list(handles.values()), list(handles.keys()), ncol=3)
    save(fig, out, "fig09_power_timelines")

def fig_drift(T, out):
    runs = read(T, "runs.csv")
    if runs.empty or "fpga_temp_max" not in runs:
        return
    d = runs[(runs.ok == True) & (~runs.profiled) & (runs.platform == "fpga")].sort_values("t_launch")
    if d.empty:
        return
    fig, ax = plt.subplots(figsize=(TWO_COL, 2.2))
    for i, p in enumerate([p for p in PRETTY if p in set(d.project)]):
        g = d[d.project == p]
        ax.plot((g.t_launch - d.t_launch.min()) / 3600, g.fpga_temp_max, "o", ms=2.5, color=PAL[i], label=PRETTY[p])
    ax.set_xlabel("hours since first run"); ax.set_ylabel("FPGA die max (°C)")
    legend_below(fig, ax.get_legend_handles_labels()[0], ncol=6)
    save(fig, out, "fig10_thermal_drift")

def fig_openssl(T, out):
    s = read(T, "summary_stats.csv")
    if s.empty:
        return
    a = s[(s.project == "01_AES")]
    bars = []
    def get(group, config, plat, metric, label):
        r = a[(a.group == group) & (a.config == config) & (a.platform == plat) & (a.metric == metric)]
        if not r.empty:
            bars.append((label, r["median"].iloc[0], r["min"].iloc[0], r["max"].iloc[0]))
    get("main", "enc_2048MB", "fpga", "kernel_MBps", "FPGA kernel (3 CU)")
    get("main", "enc_2048MB", "fpga", "compute_MBps", "FPGA pipeline (H2D+K+D2H)")
    get("main", "enc_2048MB", "fpga", "app_total_MBps", "FPGA E2E (warm)")
    get("main", "enc_2048MB", "cpu", "compute_MBps", "CPU portable C, 24 thr")
    get("main", "enc_2048MB", "cpu", "app_total_MBps", "CPU portable C E2E")
    get("openssl", "speed", "aesni_24proc", "speed_MBps", "OpenSSL AES-NI, 24 proc")
    get("openssl", "speed", "aesni_1proc", "speed_MBps", "OpenSSL AES-NI, 1 proc")
    get("openssl", "speed", "noaesni_24proc", "speed_MBps", "OpenSSL no AES-NI, 24 proc")
    get("openssl", "enc_2048MB", "openssl_aesni", "app_total_MBps", "OpenSSL AES-NI E2E (1 thr)")
    if not bars:
        return
    fig, ax = plt.subplots(figsize=(ONE_COL + 0.8, 0.24 * len(bars) + 0.8))
    y = np.arange(len(bars))
    colors = [PAL[0] if b[0].startswith("FPGA") else (PAL[1] if b[0].startswith("CPU") else PAL[2]) for b in bars]
    ax.barh(y, [b[1] for b in bars], color=colors, edgecolor="white",
            xerr=[[b[1] - b[2] for b in bars], [b[3] - b[1] for b in bars]], error_kw=dict(lw=0.6, capsize=1.5))
    for yi, b in zip(y, bars):
        ax.text(b[1] * 1.15, yi, f"{b[1]:,.0f}", va="center", fontsize=5.5, gid="value")
    ax.set_xscale("log"); ax.set_yticks(y); ax.set_yticklabels([b[0] for b in bars])
    ax.invert_yaxis(); ax.grid(axis="y", visible=False)
    ax.set_xlim(right=max(b[3] for b in bars) * 6)
    ax.set_xlabel("AES-256-CTR throughput (MB/s, log)")
    legend_below(fig, [Patch(color=PAL[0], label="FPGA"), Patch(color=PAL[1], label="Thesis CPU code"),
                       Patch(color=PAL[2], label="OpenSSL")], ncol=3)
    save(fig, out, "fig11_aes_throughput_baselines")

def fig_clock(T, out):
    k = read(T, "xrt_kernels.csv")
    if k.empty:
        return
    k["clock_mhz"] = pd.to_numeric(k.clock_mhz, errors="coerce")
    g = k.dropna(subset=["clock_mhz"]).groupby("project").clock_mhz.min()
    if g.empty:
        return
    fig, ax = plt.subplots(figsize=(ONE_COL, 2.0))
    x = np.arange(len(g))
    ax.bar(x, g.values, color=[PAL[0] if v >= 300 else PAL[1] for v in g.values], edgecolor="white")
    for xi, v in zip(x, g.values):
        vlabel(ax, xi, v + 4, f"{v:.0f}")
    ax.axhline(300, color=INK, lw=0.8, ls="--")
    ax.set_xticks(x); ax.set_xticklabels([PRETTY.get(p, p) for p in g.index])
    ax.set_ylabel("Kernel clock (MHz)"); ax.set_ylim(0, 340)
    legend_below(fig, [Patch(color=PAL[0], label="300 MHz target met"), Patch(color=PAL[1], label="scaled down by Vitis")], ncol=2)
    save(fig, out, "fig12_achieved_clock")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    a = ap.parse_args()
    R = os.path.abspath(a.results)
    T, out = os.path.join(R, "tables"), os.path.join(R, "figures")
    os.makedirs(out, exist_ok=True)
    for fn in (fig_speedup_ladder, fig_aes_amortisation, fig_bitstream, fig_decomposition, fig_bandwidth,
               fig_energy, fig_mc_scaling, fig_live, fig_drift, fig_openssl, fig_clock):
        try:
            fn(T, out)
        except Exception as e:
            QC.append(f"{fn.__name__}: FAILED ({e})")
            plt.close("all")
    try:
        fig_power_timelines(R, T, out)
    except Exception as e:
        QC.append(f"fig_power_timelines: FAILED ({e})")
    open(os.path.join(out, "LAYOUT_QC.txt"), "w").write("\n".join(QC) + ("\n" if QC else "all figures passed the layout check\n"))
    print("figures ->", out, "| QC:", "; ".join(QC) or "ok")

if __name__ == "__main__":
    sys.exit(main())
