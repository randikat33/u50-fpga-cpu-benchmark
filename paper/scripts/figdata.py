#!/usr/bin/env python3
"""
figdata.py - write the numbers a figure actually shows, next to the figure.

make_figures.save() calls export() for every figure, so each <name>.pdf/.png gets a
<figures>/data/<name>.csv with one row per plotted point or bar:

  panel      axes title (or panel1, panel2 ... when untitled)
  series     legend label(s) of the mark (matched by colour / hatch / marker)
  kind       bar | line | point
  category   tick label under a bar (bar charts), else empty
  x, y       plotted position (bars: centre and height; stacked bars also y_bottom)
  err_lo, err_hi   ends of the error bar or the shaded band at that x, if any
  orientation      vertical | horizontal (horizontal charts: x = row position, y = value)
  x_scale, y_scale linear | log  (scale of the category/position axis and of the value axis)

Because the values are read back from the drawn figure, the CSV is exactly what the
reader sees. build_workbook() collects every CSV into one Excel file, one sheet per figure.
"""
import glob, os
import numpy as np
import pandas as pd
from matplotlib.colors import to_hex
from matplotlib.container import ErrorbarContainer
from matplotlib.collections import PolyCollection, PathCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle


def _hex(c):
    try:
        return to_hex(c, keep_alpha=False)
    except Exception:
        return None


def _legend_entries(fig):
    legs = list(fig.legends) + [ax.get_legend() for ax in fig.axes if ax.get_legend() is not None]
    out = []
    for leg in legs:
        handles = getattr(leg, "legend_handles", None) or getattr(leg, "legendHandles", [])
        for h, t in zip(handles, leg.get_texts()):
            lab = t.get_text()
            if isinstance(h, Patch) or hasattr(h, "get_facecolor") and not isinstance(h, Line2D):
                out.append(dict(label=lab, kind="patch", color=_hex(h.get_facecolor()),
                                hatch=(h.get_hatch() or "")))
            elif isinstance(h, Line2D):
                ls = h.get_linestyle()
                out.append(dict(label=lab, kind="line", color=_hex(h.get_color()),
                                marker=str(h.get_marker()), ls=ls,
                                visible_line=ls not in ("None", "none", "", " ")))
    return out


def _series_for_patch(entries, color, hatch):
    m = [e["label"] for e in entries if e["kind"] == "patch" and e["color"] == color and e["hatch"] == (hatch or "")]
    if not m:
        m = [e["label"] for e in entries if e["kind"] == "patch" and e["color"] == color]
    return " / ".join(dict.fromkeys(m))


def _series_for_line(entries, color, marker, use_marker=True):
    by_color = [e["label"] for e in entries if e["kind"] == "line" and e["visible_line"] and e["color"] == color]
    by_marker = [e["label"] for e in entries if use_marker and e["kind"] == "line" and not e["visible_line"]
                 and marker not in ("None", "", " ") and e["marker"] == marker]
    by_patch = [e["label"] for e in entries if e["kind"] == "patch" and e["color"] == color]
    return " / ".join(dict.fromkeys(by_color + by_marker + (by_patch if not by_color else [])))


def _is_ref_line(line, ax):
    # axhline / axvline live partly in axes coordinates: they are reference lines, not data
    return line.get_transform() != ax.transData


def _ticks(axis):
    out = {}
    for loc, lab in zip(axis.get_majorticklocs(), axis.get_majorticklabels()):
        t = lab.get_text().replace("\n", " | ").strip()
        if t:
            out[float(loc)] = t
    return out


def _numeric(labels):
    ok = 0
    for t in labels:
        try:
            float(t.replace(",", "").replace("×", "").replace("−", "-"))
            ok += 1
        except ValueError:
            pass
    return ok == len(labels)


def export(fig, out, name):
    entries = _legend_entries(fig)
    rows = []
    for i, ax in enumerate(fig.axes):
        if not ax.get_visible():
            continue
        panel = ax.get_title() or (f"panel{i + 1}" if len(fig.axes) > 1 else "")
        xs, ys = ax.get_xscale(), ax.get_yscale()
        xt, yt = _ticks(ax.xaxis), _ticks(ax.yaxis)
        horizontal = bool(yt) and not _numeric(list(yt.values())) and _numeric(list(xt.values()) or ["0"])
        # horizontal charts are stored like vertical ones: x = row position, y = value
        base = dict(panel=panel, orientation="horizontal" if horizontal else "vertical",
                    x_scale=ys if horizontal else xs, y_scale=xs if horizontal else ys)
        pts = []
        # bars (vertical or horizontal, stacked or not)
        for p in ax.patches:
            if not isinstance(p, Rectangle) or not p.get_visible():
                continue
            w, h = p.get_width(), p.get_height()
            if w == 0 or h == 0:
                continue
            ser = _series_for_patch(entries, _hex(p.get_facecolor()), p.get_hatch())
            if horizontal:
                c = p.get_y() + h / 2
                cat = yt[min(yt, key=lambda k: abs(k - c))] if yt else ""
                pts.append(dict(base, series=ser, kind="bar", category=cat, x=c, y=w, y_bottom=p.get_x()))
            else:
                c = p.get_x() + w / 2
                cat = xt[min(xt, key=lambda k: abs(k - c))] if xt and not _numeric(list(xt.values())) else ""
                pts.append(dict(base, series=ser, kind="bar", category=cat, x=c, y=h, y_bottom=p.get_y()))
        # data lines (with or without markers). Marker-only legend entries (e.g. "Conv rgb8")
        # are used only in panels whose data lines actually use more than one marker.
        data_lines = [ln for ln in ax.lines if not _is_ref_line(ln, ax) and ln.get_visible()
                      and str(ln.get_marker()) not in ("_", "|")]
        use_marker = len({str(ln.get_marker()) for ln in data_lines} - {"None", "", " "}) > 1
        cat_axis = yt if horizontal else (xt if xt and not _numeric(list(xt.values())) else None)
        for ln in ax.lines:
            if _is_ref_line(ln, ax) or not ln.get_visible():
                continue
            x, y = np.asarray(ln.get_xdata(), float), np.asarray(ln.get_ydata(), float)
            if len(x) == 0:
                continue
            ls = ln.get_linestyle()
            has_line = ls not in ("None", "none", "", " ") and len(x) > 1
            lab = ln.get_label()
            ser = lab if lab and not lab.startswith("_") else _series_for_line(
                entries, _hex(ln.get_color()), str(ln.get_marker()), use_marker)
            # error-bar caps are marker-only lines drawn by errorbar(): skip them
            if str(ln.get_marker()) in ("_", "|") and not has_line:
                continue
            # unnamed connector lines in a category chart (dumbbells) carry no extra data
            if cat_axis and not ser:
                continue
            for a, b in zip(x, y):
                cat = ""
                if cat_axis:
                    pos = b if horizontal else a
                    cat = cat_axis[min(cat_axis, key=lambda k: abs(k - pos))]
                pts.append(dict(base, series=ser, kind="line" if has_line else "point", category=cat,
                                x=(b if horizontal else a), y=(a if horizontal else b),
                                _color=_hex(ln.get_color())))
        # scatter points
        for col in ax.collections:
            if isinstance(col, PathCollection) and col.get_visible():
                fc = col.get_facecolor()
                ser = _series_for_line(entries, _hex(fc[0]) if len(fc) else None, "o")
                for a, b in col.get_offsets():
                    pts.append(dict(base, series=ser, kind="point", category="", x=a, y=b))
        # error bars
        errs = []
        for c in ax.containers:
            if isinstance(c, ErrorbarContainer):
                for seg_col in c.lines[2]:
                    for seg in seg_col.get_segments():
                        (x0, y0), (x1, y1) = seg[0], seg[-1]
                        if not horizontal and abs(x0 - x1) < 1e-12:
                            errs.append((x0, min(y0, y1), max(y0, y1)))
                        elif horizontal and abs(y0 - y1) < 1e-12:   # xerr in a horizontal chart
                            errs.append((y0, min(x0, x1), max(x0, x1)))
        # shaded bands (fill_between): min/max of the polygon at each x
        bands = []
        for col in ax.collections:
            if isinstance(col, PolyCollection) and col.get_visible():
                fc = col.get_facecolor()
                color = _hex(fc[0]) if len(fc) else None
                for path in col.get_paths():
                    v = path.vertices
                    for xv in np.unique(np.round(v[:, 0], 12)):
                        yy = v[np.isclose(v[:, 0], xv), 1]
                        bands.append((xv, yy.min(), yy.max(), color))
        for p in pts:
            x, y = p["x"], p["y"]
            top = y + (p.get("y_bottom") or 0) if p["kind"] == "bar" else y
            m = [e for e in errs if np.isclose(e[0], x, rtol=1e-9, atol=1e-9) and e[1] - 1e-9 <= top <= e[2] + 1e-9]
            if not m:
                near = [b for b in bands if np.isclose(b[0], x, rtol=1e-9, atol=1e-9)
                        and b[1] - 1e-9 <= top <= b[2] + 1e-9]
                same = [b for b in near if b[3] is not None and b[3] == p.get("_color")]
                m = [(b[0], b[1], b[2]) for b in (same or near)]
            if m:
                p["err_lo"], p["err_hi"] = m[0][1], m[0][2]
            p.pop("_color", None)
        rows += pts
    cols = ["panel", "series", "kind", "category", "x", "y", "y_bottom", "err_lo", "err_hi", "orientation", "x_scale", "y_scale"]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty and df.y_bottom.fillna(0).abs().max() == 0:
        df = df.drop(columns="y_bottom")
    d = os.path.join(out, "data")
    os.makedirs(d, exist_ok=True)
    df.to_csv(os.path.join(d, f"{name}.csv"), index=False, float_format="%.6g")
    return df


def build_workbook(fig_dir, xlsx=None, captions=None):
    """one sheet per figure + an index sheet"""
    files = sorted(glob.glob(os.path.join(fig_dir, "data", "*.csv")))
    xlsx = xlsx or os.path.join(fig_dir, "figure_data.xlsx")
    idx = []
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        for f in files:
            name = os.path.splitext(os.path.basename(f))[0]
            sheet = name.replace("v2_", "")[:31]
            df = pd.read_csv(f)
            df.to_excel(w, sheet_name=sheet, index=False)
            ws = w.sheets[sheet]
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
            ws.freeze_panes = "A2"
            idx.append(dict(sheet=sheet, figure_files=f"{name}.pdf / {name}.png", rows=len(df),
                            caption=(captions or {}).get(name, "")))
        pd.DataFrame(idx).to_excel(w, sheet_name="index", index=False)
        ws = w.sheets["index"]
        for col, width in zip("ABCD", (26, 48, 8, 90)):
            ws.column_dimensions[col].width = width
        w.book.move_sheet("index", offset=-len(files))
    return xlsx
