#!/usr/bin/env python3
"""make_figures.py - the figures of paper 2 from the two analyses (scripts/analyse.py, scripts/analyse_s.py).
  fig1_energy_to_target.(pdf|png)   energy to target per pricing run (primary) and in the steady state (secondary),
                                    socket boundary, every case, platform x scheme
  fig2_algorithm_vs_platform.(pdf|png)   RQ2: gain from the better method against gain from the better platform
  fig3_predicted_vs_measured.(pdf|png)   H5: measured against predicted energy, all combinations, both boundaries
Colour = platform (card, CPU matched, CPU native); line style and marker = method. Palette checked for colour-vision
deficiency (dataviz validator); identity never rests on colour alone (markers, line styles, labels).
Usage:  python3 scripts/make_figures.py   (writes results/figures/)
"""
import csv
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "figures")
PLAT_COL = {"card": "#2a78d6", "cpu_matched": "#eb6834", "cpu_native": "#1baf7a"}
PLAT_LAB = {"card": "Card (U50)", "cpu_matched": "CPU, card-matched code", "cpu_native": "CPU, native code"}
INK, INK2, GRID = "#2b2b29", "#6b6a64", "#e4e3de"
TARGETS = ("1%", "0.3%", "0.1%")
TX = {"1%": 1.0, "0.3%": 0.3, "0.1%": 0.1}
CASES = (("I", "Case I"), ("II", "Case II"), ("III", "Case III"), ("P1", "Case P1"), ("B", "Case B (basket)"), ("A", "Case A (Asian)"))
METHODS = {"I": ("ft", "qe"), "II": ("ft", "qe"), "III": ("ft", "qe"), "P1": ("ft", "qe"), "B": ("ft", "qe"), "A": ("plain", "cv")}
MLAB = {"ft": "Euler FT", "qe": "QE", "plain": "plain estimator", "cv": "control variate"}

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": INK2, "axes.labelcolor": INK,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.linewidth": 0.6, "xtick.major.width": 0.6,
                     "ytick.major.width": 0.6, "legend.frameon": False, "savefig.dpi": 300})


def load():
    rows = []
    for r in csv.DictReader(open(os.path.join(RES, "analysis", "measured_vs_predicted.csv"))):
        rows.append(r)
    for r in csv.DictReader(open(os.path.join(RES, "analysis_s", "measured_vs_predicted_s.csv"))):
        if r["variant"] == "same":
            rows.append(r)
    for r in rows:
        for k in ("E_meas_J", "E_pred_J", "Ess_meas_J", "Ess_pred_J"):
            r[k] = float(r[k])
    return rows


def style_ax(ax):
    ax.grid(True, which="major", color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def fig1(rows):
    fig, axes = plt.subplots(2, 6, figsize=(7.2, 4.0), sharex=True)
    for row, (field, title) in enumerate((("E_meas_J", "one run"), ("Ess_meas_J", "steady state"))):
        for col, (case, name) in enumerate(CASES):
            ax = axes[row, col]
            style_ax(ax)
            for plat in PLAT_COL:
                for i, m in enumerate(METHODS[case]):
                    ys = []
                    for t in TARGETS:
                        r = next(x for x in rows if x["case"] == case and x["target"] == t and x["boundary"] == "socket"
                                 and x["combo"] == f"{plat}_{m}")
                        ys.append(r[field])
                    ax.plot([TX[t] for t in TARGETS], ys, color=PLAT_COL[plat], linestyle="-" if i == 0 else "--",
                            marker="o" if i == 0 else "s", markersize=3.2, linewidth=1.1,
                            markerfacecolor=PLAT_COL[plat] if i == 0 else "white", markeredgewidth=0.9)
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_xlim(1.4, 0.07)
            ax.set_xticks([1, 0.3, 0.1])
            ax.set_xticklabels(["1%", "0.3%", "0.1%"])
            ax.minorticks_off()
            if row == 0:
                ax.set_title(name, fontsize=8, color=INK)
            if col == 0:
                ax.set_ylabel(f"Energy, {title} (J)", fontsize=7.5)
            if row == 1:
                ax.set_xlabel("RMSE target")
            ax.tick_params(labelsize=6.5)
    h = [Line2D([], [], color=c, linewidth=1.4, label=PLAT_LAB[p]) for p, c in PLAT_COL.items()]
    h += [Line2D([], [], color=INK2, linestyle="-", marker="o", markersize=3.2, label="first method: Euler FT (A: plain)"),
          Line2D([], [], color=INK2, linestyle="--", marker="s", markersize=3.2, markerfacecolor="white",
                 label="second method: QE (A: control variate)")]
    fig.legend(handles=h, loc="lower center", ncol=3, fontsize=7, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.09, 1, 1), w_pad=0.5, h_pad=1.0)
    save(fig, "fig1_energy_to_target")


def fig2(rows):
    pts = []
    for case, name in CASES:
        a, b = METHODS[case]
        for t in TARGETS:
            g = {x["combo"]: x["Ess_meas_J"] for x in rows if x["case"] == case and x["target"] == t and x["boundary"] == "socket"}
            best = lambda m, pl=("card", "cpu_matched", "cpu_native"): min(g[f"{p}_{m}"] for p in pl)
            alg = best(a) / best(b)
            pf = [best(m, ("cpu_matched", "cpu_native")) / g[f"card_{m}"] for m in (a, b)]
            plat = max(max(v, 1 / v) for v in pf)
            pts.append((case, t, plat, alg))
    fig, ax = plt.subplots(figsize=(3.6, 3.2))
    style_ax(ax)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(0.95, 2.6)
    ax.set_ylim(1, 1000)
    ax.plot([1, 1000], [1, 1000], color=INK2, linewidth=0.8, linestyle=":", zorder=1)
    cols = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
    mk = {"1%": "o", "0.3%": "s", "0.1%": "^"}
    # label anchor (target) and offset in points, chosen so labels clear points and each other
    lab = {"I": ("0.1%", (6, -3)), "II": ("0.1%", (6, -2)), "III": ("0.1%", (-16, -3)), "P1": ("0.1%", (-6, 6)),
           "B": ("1%", (5, 4)), "A": ("0.3%", (8, -3))}
    for (case, name), c in zip(CASES, cols):
        sel = sorted([p for p in pts if p[0] == case], key=lambda p: TARGETS.index(p[1]))
        ax.plot([p[2] for p in sel], [p[3] for p in sel], color=c, linewidth=0.7, alpha=0.6, zorder=2)
        for p in sel:
            ax.scatter(p[2], p[3], s=24, color=c, marker=mk[p[1]], edgecolor="white", linewidth=0.6, zorder=3)
        t, off = lab[case]
        p = next(q for q in sel if q[1] == t)
        ax.annotate(case, (p[2], p[3]), xytext=off, textcoords="offset points", fontsize=7, color=INK)
    # 'equal gain' label along the diagonal, inside the axes
    import numpy as np
    a = ax.transData.transform([(1.6, 1.6), (2.2, 2.2)])
    ang = np.degrees(np.arctan2(a[1, 1] - a[0, 1], a[1, 0] - a[0, 0]))
    ax.text(2.15, 1.72, "equal gain", color=INK2, fontsize=6, rotation=ang, rotation_mode="anchor", ha="center")
    ax.set_xticks([1, 1.5, 2, 2.5])
    ax.set_xticklabels(["1", "1.5", "2", "2.5"])
    ax.xaxis.set_minor_locator(plt.NullLocator())
    ax.set_xlabel("Gain from the better platform\n(steady state, socket)")
    ax.set_ylabel("Gain from the better method")
    h = [Line2D([], [], color=INK2, marker=m, linestyle="", markersize=4, label=f"target {t}") for t, m in mk.items()]
    ax.legend(handles=h, loc="upper right", fontsize=6.5)
    fig.tight_layout()
    save(fig, "fig2_algorithm_vs_platform")
    return pts


def fig3(rows):
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.1))
    for ax, (fm, fp, title) in zip(axes, (("E_meas_J", "E_pred_J", "One pricing run"), ("Ess_meas_J", "Ess_pred_J", "Steady state"))):
        style_ax(ax)
        lo, hi = 1e-4, 3e4
        ax.fill_between([lo, hi], [lo / 1.2, hi / 1.2], [lo * 1.2, hi * 1.2], color="#eceae3", linewidth=0)
        ax.plot([lo, hi], [lo, hi], color=INK2, linewidth=0.7)
        for plat, mk in (("cpu_native", "^"), ("cpu_matched", "s"), ("card", "o")):
            sel = [r for r in rows if r["combo"].startswith(plat)]
            ax.scatter([r[fp] for r in sel], [r[fm] for r in sel], s=10, marker=mk, color=PLAT_COL[plat],
                       edgecolor="white", linewidth=0.4, label=PLAT_LAB[plat], zorder=3)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_title(title, fontsize=8, color=INK)
        ax.set_xlabel("Predicted energy (J)")
        ax.set_ylabel("Measured energy (J)")
    axes[0].legend(loc="upper left", fontsize=6.5)
    axes[0].text(2e2, 3e-3, "band: ±20%", fontsize=6.5, color=INK2)
    fig.tight_layout()
    save(fig, "fig3_predicted_vs_measured")


def save(fig, name):
    os.makedirs(OUT, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote", os.path.join(OUT, name))


if __name__ == "__main__":
    rows = load()
    fig1(rows)
    pts = fig2(rows)
    fig3(rows)
    json.dump([dict(case=c, target=t, platform_gain=p, method_gain=a) for c, t, p, a in pts],
              open(os.path.join(OUT, "fig2_points.json"), "w"), indent=1)
