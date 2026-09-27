#!/usr/bin/env python3
"""
analyze_v2.py - tables for the v2 (A-grade) campaign + the v1 -> v2 comparison.

  analyze_v2.py --results results/v2 [--v1 results/v1] [--idle results/idle]

Every v2 program prints `RESULT key=value` lines and `MARK start/end` around its measured
section, so one generic parser covers all six projects.

Boundaries (identical for FPGA and CPU):
  compute  : the program's t_compute_s (FPGA: H2D + kernel + D2H window; CPU: compute)
             live-stream projects: frames per second in the processing window
  e2e      : process launch -> start of the measured section + one measured section
             (= a warm, stand-alone job: device open + cached xclbin load + alloc + input
             read + compute); the cold group adds a real bitstream load
  energy   : socket-0 package + DRAM + Alveo card, integrated over the measured section,
             divided by the work done (bytes, Mpix, path-steps, frames)

Outputs (results/v2/tables):
  runs_v2.csv, summary_v2.csv, speedup_v2.csv, crossover_v2.csv, energy_v2.csv,
  cpu_impls_v2.csv, stages_v2.csv, accuracy_v2.csv, data_quality_v2.csv, v1_vs_v2.csv,
  REPORT_v2.md
"""
import argparse, glob, json, math, os, re, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import integrate, mean_in, load_power, load_xrt_power, t95, boot_ratio_ci, parse_xrt_summary  # noqa: E402

PROJECTS = ["01_AES", "02_Convolution", "03_MC_Heston", "04_Portfolio", "05_LiveStream_Single", "06_LiveStream_Multi"]
LIVE = ("05_LiveStream_Single", "06_LiveStream_Multi")
RENAME = {"ok": "self_ok", "platform": "res_platform", "project": "res_project", "error": "res_error"}


def parse_results(path):
    d = {}
    try:
        txt = open(path, "rb").read().decode("utf-8", "replace")
    except OSError:
        return d
    for line in txt.splitlines():
        if not line.startswith("RESULT "):
            continue
        k, _, v = line[7:].partition("=")
        k = RENAME.get(k.strip(), k.strip())
        v = v.strip()
        try:
            d[k] = float(v)
        except ValueError:
            d[k] = v
    return d


def work_units(r):
    """(units of work in ONE measured section, unit name)"""
    p = r["project"]
    g = lambda k: r.get(k, np.nan)
    if p == "01_AES":
        return g("bytes") / 1e9, "GB"
    if p == "02_Convolution":
        return g("mpix"), "Mpix"
    if p == "03_MC_Heston":
        return 2.0 * g("pairs") * g("steps") / 1e6 if g("pairs") == g("pairs") else g("paths_eff") * g("steps") / 1e6, "M path-steps"
    if p == "04_Portfolio":
        return g("path_steps") / 1e6, "M path-steps"
    return g("frames"), "frames"


def sections_in_window(r):
    """How many measured sections the MARK window contains."""
    for k in ("repeat", "runs"):
        v = r.get(k)
        if isinstance(v, float) and v == v and v >= 1:
            return v
    return 1.0


def collect(RAW, warnings):
    rows, bw_rows, k_rows = [], [], []
    for mf in sorted(glob.glob(os.path.join(RAW, "*", "*", "*", "*", "*", "meta.json"))):
        rd = os.path.dirname(mf)
        project, group, config, plat, rep = os.path.relpath(rd, RAW).split(os.sep)
        try:
            meta = json.load(open(mf))
        except Exception as e:
            warnings.append(dict(level="error", where=rd, msg=f"bad meta.json: {e}"))
            continue
        r = dict(project=project, group=group, config=config, plat=plat, rep=rep,
                 platform="fpga" if plat.startswith("fpga") else "cpu",
                 profiled=rep.startswith("profiled"), cold=rep.startswith("cold"),
                 ok=bool(meta.get("ok")), exit_code=meta.get("exit_code"), timed_out=meta.get("timed_out"),
                 bitstream_state=meta.get("bitstream_state"), wall_s=meta.get("wall_s"),
                 t_launch=meta.get("t_launch"), t_exit=meta.get("t_exit"),
                 t_start=meta.get("t_start_marker"), t_end=meta.get("t_end_marker"),
                 window_s=meta.get("proc_s"), launch_to_end_s=meta.get("launch_to_end_s"),
                 cpu_util_proc_pct=meta.get("cpu_util_proc_pct"), cpu_util_run_pct=meta.get("cpu_util_run_pct"),
                 fpga_temp_pre=meta.get("fpga_temp_pre"), fpga_temp_post=meta.get("fpga_temp_post"),
                 cpu_temp_pre=meta.get("cpu_temp_pre"), cpu_temp_post=meta.get("cpu_temp_post"))
        r.update(parse_results(os.path.join(rd, "stdout.log")))
        if not r["ok"]:
            warnings.append(dict(level="error", where=f"{project}/{group}/{config}/{plat}/{rep}",
                                 msg=f"run failed rc={r['exit_code']} timeout={r['timed_out']}"))
        if r.get("self_ok") == 0:
            warnings.append(dict(level="error", where=f"{project}/{group}/{config}/{plat}/{rep}",
                                 msg="program reported ok=0 (self-check failed)"))
        r["verify_requested"] = "--verify" in (meta.get("cmd") or [])
        if r["verify_requested"] and isinstance(r.get("verified"), float) and r["verified"] == 0:
            warnings.append(dict(level="error", where=f"{project}/{group}/{config}/{plat}/{rep}", msg="--verify FAILED"))
        # boundaries
        tc = r.get("t_compute_s", np.nan)
        pre = (r["t_start"] - r["t_launch"]) if r["t_start"] and r["t_launch"] else np.nan
        r["startup_s"] = pre
        r["e2e_s"] = pre + tc if isinstance(tc, float) else np.nan
        units, unit = work_units(r)
        r["work_units"], r["unit"] = units, unit
        nsec = sections_in_window(r)
        r["sections"] = nsec
        # energy
        pw = load_power(rd)
        if pw is not None and r["t_start"] and r["t_end"]:
            t = pw.t_unix.values.astype(float)
            card = pw.card_w.values.astype(float)
            src = str(pw.card_src.dropna().iloc[0]) if "card_src" in pw and pw.card_src.notna().any() else "none"
            if np.isnan(card).all() and r["platform"] == "fpga":
                xp = load_xrt_power(rd, r["t_launch"])
                if xp is not None:
                    card = np.interp(t, xp.t_unix.values, xp.card_w.values, left=np.nan, right=np.nan)
                    src = "xrt_profile"
            r["card_src"] = src
            p0, p1 = r["t_start"], r["t_end"]
            idle_m = (t < r["t_launch"]) | (t > r["t_exit"])
            for col, name in (("pkg0_w", "pkg0"), ("dram0_w", "dram0")):
                v = pw[col].values.astype(float)
                r[f"E_{name}_J"] = integrate(t, v, p0, p1)
                r[f"P_{name}_w"] = mean_in(t, v, p0, p1)
                r[f"P_{name}_idle_w"] = float(np.nanmedian(v[idle_m])) if idle_m.any() and not np.isnan(v[idle_m]).all() else np.nan
            r["E_card_J"] = integrate(t, card, p0, p1)
            r["P_card_w"] = mean_in(t, card, p0, p1)
            r["P_card_idle_w"] = float(np.nanmedian(card[idle_m])) if idle_m.any() and not np.isnan(card[idle_m]).all() else np.nan
            fp = pw.fpga_temp_c.values.astype(float)
            r["fpga_temp_max"] = float(np.nanmax(fp)) if not np.isnan(fp).all() else np.nan
        rows.append(r)
        sfile = os.path.join(rd, "xrt", "summary.csv")
        if os.path.exists(sfile):
            try:
                x = parse_xrt_summary(sfile)
                tag = dict(project=project, group=group, config=config, plat=plat, rep=rep)
                bw_rows += [dict(tag, **q) for q in x["bw"]]
                k_rows += [dict(tag, **q) for q in x["kernels"]]
            except Exception as e:
                warnings.append(dict(level="warn", where=rd, msg=f"xrt summary: {e}"))
    return rows, bw_rows, k_rows


def ci_row(v):
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    n = len(v)
    if n == 0:
        return None
    sd = float(np.std(v, ddof=1)) if n > 1 else np.nan
    mean = float(np.mean(v))
    return dict(n=n, median=float(np.median(v)), mean=mean, sd=sd,
                cv_pct=100 * sd / mean if n > 1 and mean else np.nan,
                ci95_half=t95(n - 1) * sd / math.sqrt(n) if n > 1 else np.nan,
                min=float(np.min(v)), max=float(np.max(v)))


def size_value(project, group, config):
    """numeric sweep variable for crossover analysis (None if not a sweep point)"""
    m = re.match(r"^(\d+)MB$", config)
    if project == "01_AES" and m:
        return float(m.group(1)), "file size [MB]"
    m = re.match(r"^\w+?_(\d+)x(\d+)$", config)
    if project == "02_Convolution" and group == "synth" and m:
        return int(m.group(1)) * int(m.group(2)) / 1e6, "image size [Mpix]"
    m = re.match(r"^(\d+)x(\d+)$", config)
    if project == "03_MC_Heston" and m:
        return int(m.group(1)) * int(m.group(2)) / 1e6, "path-steps [M]"
    m = re.match(r"^p2_(\d+)$", config)
    if project == "04_Portfolio" and m:
        return float(m.group(1)), "stage-2 paths"
    return None, None


def series_key(project, group, config):
    if project == "02_Convolution" and group == "synth":
        return f"{project}/{group}/{config.split('_')[0]}"
    return f"{project}/{group}"


def crossover(xs, ys):
    """x where y crosses 1 (log-log interpolation); None if no crossing"""
    pts = sorted((x, y) for x, y in zip(xs, ys) if x > 0 and y > 0)
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if (y0 - 1) * (y1 - 1) < 0:
            lx0, lx1, ly0, ly1 = map(math.log, (x0, x1, y0, y1))
            out.append(math.exp(lx0 + (0 - ly0) * (lx1 - lx0) / (ly1 - ly0)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--v1")
    ap.add_argument("--idle")
    a = ap.parse_args()
    R = os.path.abspath(a.results)
    RAW, T = os.path.join(R, "raw"), os.path.join(R, "tables")
    os.makedirs(T, exist_ok=True)
    warnings = []
    rows, bw_rows, k_rows = collect(RAW, warnings)
    if not rows:
        print("no v2 runs found under", RAW)
        return 1
    df = pd.DataFrame(rows)

    # card power for CPU runs when the card has no live sensor: idle estimate from FPGA runs
    if "card_src" in df and df.card_src.eq("none").any():
        idle = df.loc[df.platform.eq("fpga"), "P_card_idle_w"].astype(float)
        if idle.notna().any():
            est = float(np.nanmedian(idle))
            m = df.card_src.eq("none")
            df.loc[m, "E_card_J"] = est * df.loc[m, "window_s"].astype(float)
            df.loc[m, "card_src"] = "idle_estimate"
            warnings.append(dict(level="info", where="all", msg=f"CPU-run card power = idle estimate {est:.1f} W"))
    for c in ("E_pkg0_J", "E_dram0_J", "E_card_J"):
        if c not in df:
            df[c] = np.nan
    per = df["work_units"].astype(float) * df["sections"].astype(float)
    df["E_host_J"] = df.E_pkg0_J + df.E_dram0_J.fillna(0)
    df["E_system_J"] = df.E_host_J + df.E_card_J.fillna(0)
    df["E_system_per_unit_J"] = df.E_system_J / per
    df["E_host_per_unit_J"] = df.E_host_J / per
    # a CPU-only server would not have the card at all
    df["E_server_nocard_per_unit_J"] = np.where(df.platform.eq("cpu"), df.E_host_per_unit_J, df.E_system_per_unit_J)
    df.to_csv(os.path.join(T, "runs_v2.csv"), index=False)

    timed = df[(~df.profiled) & (~df.cold) & df.ok]
    cold = df[df.cold & df.ok]

    # ------------------------------------------------------------------ summary
    metrics = [c for c in df.columns if df[c].dtype.kind in "fi" and c not in
               ("exit_code", "t_launch", "t_exit", "t_start", "t_end")]
    srows = []
    for key, g in pd.concat([timed, cold]).groupby(["project", "group", "config", "plat"]):
        for m in metrics:
            c = ci_row(g[m].astype(float).values)
            if c:
                srows.append(dict(zip(["project", "group", "config", "plat"], key), metric=m, **c))
    S = pd.DataFrame(srows)
    S.to_csv(os.path.join(T, "summary_v2.csv"), index=False)
    key = S[(S.metric.eq("fps") & S.project.isin(LIVE)) | (S.metric.eq("t_compute_s") & ~S.project.isin(LIVE))]
    for _, r in key.iterrows():
        if r.cv_pct == r.cv_pct and r.cv_pct > 5:
            warnings.append(dict(level="warn", where=f"{r.project}/{r.group}/{r.config}/{r.plat}",
                                 msg=f"CV {r.cv_pct:.1f}% > 5% on {r.metric} (n={r.n})"))

    # ------------------------------------------------------------------ speedups
    def vals(g, plat, col):
        x = g[g.plat == plat]
        return x[col].astype(float).dropna().values if col in x else np.array([])

    sp = []
    for (project, group, config), g in timed.groupby(["project", "group", "config"]):
        fplats = sorted(p for p in g.plat.unique() if p.startswith("fpga"))
        cplats = sorted(p for p in g.plat.unique() if not p.startswith("fpga"))
        cand = [p for p in cplats if not p.endswith("_1t")]   # 1-thread runs are references, never "best CPU"
        live = project in LIVE
        bounds = [("compute", "fps", True)] if live else [("compute", "t_compute_s", False), ("e2e_warm", "e2e_s", False)]
        if live and group in ("pipe", "ablation_pipe"):
            bounds.append(("launch_to_done", "launch_to_end_s", False))
        for fp in fplats:
            for bname, col, higher in bounds:
                best = None
                for cp in cplats:
                    c, f = vals(g, cp, col), vals(g, fp, col)
                    if not len(c) or not len(f):
                        continue
                    r_, lo, hi = boot_ratio_ci(f, c) if higher else boot_ratio_ci(c, f)
                    row = dict(project=project, group=group, config=config, fpga=fp, cpu=cp, boundary=bname,
                               metric=col, cpu_median=float(np.median(c)), fpga_median=float(np.median(f)),
                               speedup=r_, ci_lo=lo, ci_hi=hi, n_cpu=len(c), n_fpga=len(f))
                    sp.append(row)
                    better = (row["cpu_median"] > best["cpu_median"]) if (best and higher) else \
                             (row["cpu_median"] < best["cpu_median"]) if best else True
                    if better and cp in cand:
                        best = row
                if best:
                    sp.append(dict(best, cpu="BEST_CPU(" + best["cpu"] + ")"))
            # cold start: cold FPGA runs of this config vs the warm CPU runs
            cg = cold[(cold.project == project) & (cold.config == config) & (cold.plat == fp)]
            if len(cg) and not live:
                for cp in cplats:
                    c = vals(g, cp, "e2e_s")
                    f = cg["e2e_s"].astype(float).dropna().values
                    if len(c) and len(f):
                        r_, lo, hi = boot_ratio_ci(c, f)
                        sp.append(dict(project=project, group=group, config=config, fpga=fp + "_cold", cpu=cp,
                                       boundary="e2e_cold", metric="e2e_s", cpu_median=float(np.median(c)),
                                       fpga_median=float(np.median(f)), speedup=r_, ci_lo=lo, ci_hi=hi,
                                       n_cpu=len(c), n_fpga=len(f)))
    SP = pd.DataFrame(sp)
    SP.to_csv(os.path.join(T, "speedup_v2.csv"), index=False)

    # ------------------------------------------------------------------ energy
    er = []
    for (project, group, config), g in timed.groupby(["project", "group", "config"]):
        for fp in [p for p in g.plat.unique() if p.startswith("fpga")]:
            f = vals(g, fp, "E_system_per_unit_J")
            for cp in [p for p in g.plat.unique() if not p.startswith("fpga")]:
                for bname, col in (("system", "E_system_per_unit_J"), ("cpu_server_without_card", "E_server_nocard_per_unit_J")):
                    c = vals(g, cp, col)
                    if len(c) and len(f):
                        r_, lo, hi = boot_ratio_ci(c, f)
                        er.append(dict(project=project, group=group, config=config, fpga=fp, cpu=cp, boundary=bname,
                                       unit=g.unit.iloc[0], cpu_J_per_unit=float(np.median(c)),
                                       fpga_J_per_unit=float(np.median(f)), fpga_advantage=r_, ci_lo=lo, ci_hi=hi))
    ER = pd.DataFrame(er)
    ER.to_csv(os.path.join(T, "energy_v2.csv"), index=False)

    # ------------------------------------------------------------------ crossover
    cx = []
    if len(SP):
        best = SP[SP.cpu.str.startswith("BEST_CPU")]
        for bname in ("compute", "e2e_warm"):
            b = best[best.boundary == bname]
            ser = {}
            for _, r in b.iterrows():
                x, xl = size_value(r.project, r.group, r.config)
                if x is None or r.fpga != "fpga":
                    continue
                ser.setdefault((series_key(r.project, r.group, r.config), xl), []).append((x, r.speedup))
            for (key, xl), pts in ser.items():
                pts.sort()
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                cr = crossover(xs, ys)
                cx.append(dict(series=key, boundary=bname, x_label=xl, points=len(pts),
                               speedup_min=min(ys), speedup_max=max(ys),
                               speedup_smallest=ys[0], speedup_largest=ys[-1],
                               crossover_x=";".join(f"{c:.4g}" for c in cr) if cr else "none in tested range",
                               verdict=("FPGA faster at all sizes" if min(ys) > 1 else
                                        "CPU faster at all sizes" if max(ys) < 1 else "platform changes with size")))
    pd.DataFrame(cx).to_csv(os.path.join(T, "crossover_v2.csv"), index=False)

    # ------------------------------------------------------------------ CPU implementations (fairness)
    ci = []
    for (project, group, config), g in timed[timed.platform == "cpu"].groupby(["project", "group", "config"]):
        col = "fps" if project in LIVE else "t_compute_s"
        meds = {p: float(np.median(x[col].astype(float).dropna())) for p, x in g.groupby("plat") if col in x and x[col].notna().any()}
        if not meds:
            continue
        best = max(meds.values()) if col == "fps" else min(meds.values())
        for p, v in meds.items():
            ci.append(dict(project=project, group=group, config=config, cpu=p, metric=col, median=v,
                           relative_to_best=(v / best if col == "fps" else best / v),
                           isa=str(g[g.plat == p].get("isa", pd.Series(["?"])).iloc[0]),
                           cv_ipp=g[g.plat == p].get("cv_ipp", pd.Series([np.nan])).iloc[0]))
    pd.DataFrame(ci).to_csv(os.path.join(T, "cpu_impls_v2.csv"), index=False)

    # ------------------------------------------------------------------ FPGA stage breakdown
    st = []
    stage_cols = [("t_xclbin_s", "xclbin"), ("t_alloc_s", "alloc"), ("t_read_s", "read"), ("t_h2d_s", "h2d"),
                  ("t_kernel_s", "kernel"), ("t_d2h_s", "d2h"), ("t_write_s", "write"),
                  ("h2d_ms_mean", "h2d_ms/frame"), ("kernel_ms_mean", "kernel_ms/frame"), ("d2h_ms_mean", "d2h_ms/frame"),
                  ("decode_ms_mean", "decode_ms/frame"), ("sink_ms_mean", "sink_ms/frame"),
                  ("h2d_ms_per_frame", "h2d_ms/frame"), ("kernel_ms_per_frame", "kernel_ms/frame"),
                  ("d2h_ms_per_frame", "d2h_ms/frame"), ("ladder_ms_per_frame", "ladder_ms/frame"),
                  ("sink_ms_per_frame", "sink_ms/frame"), ("resize_ms_mean", "resize_ms/frame")]
    for (project, group, config, plat), g in timed.groupby(["project", "group", "config", "plat"]):
        row = dict(project=project, group=group, config=config, plat=plat)
        for col, name in stage_cols:
            if col in g and g[col].notna().any():
                row[name] = float(np.median(g[col].astype(float).dropna()))
        for col in ("t_compute_s", "e2e_s", "startup_s", "fps", "cpu_util_proc_pct"):
            if col in g and g[col].notna().any():
                row[col] = float(np.median(g[col].astype(float).dropna()))
        st.append(row)
    pd.DataFrame(st).to_csv(os.path.join(T, "stages_v2.csv"), index=False)

    # ------------------------------------------------------------------ accuracy / equivalence
    acc = []
    for (project, group, config), g in df[df.ok].groupby(["project", "group", "config"]):
        for col in [c for c in g.columns if c.startswith(("out_fnv1a64", "cks_", "mom_hash", "checksum", "price"))]:
            by = {p: sorted(set(str(v) for v in x[col].dropna())) for p, x in g.groupby("plat")}
            by = {p: v for p, v in by.items() if v}
            if not by:
                continue
            allv = sorted(set(sum(by.values(), [])))
            acc.append(dict(project=project, group=group, config=config, field=col,
                            identical_across_platforms=len(allv) == 1,
                            values=" | ".join(f"{p}:{','.join(v)}" for p, v in sorted(by.items()))[:500]))
        for col in ("verified", "verify_max_absdiff", "verify_min_psnr_db", "verify_mismatch", "verify_bit_identical", "kat_ok"):
            if col in g and g[col].notna().any():
                for p, x in g.groupby("plat"):
                    if x[col].notna().any():
                        acc.append(dict(project=project, group=group, config=config, field=f"{col}[{p}]",
                                        identical_across_platforms=np.nan, values=",".join(str(v) for v in x[col].dropna())))
    A = pd.DataFrame(acc)
    A.to_csv(os.path.join(T, "accuracy_v2.csv"), index=False)
    if len(A) and "field" in A:
        # checksums that SHOULD match between FPGA and CPU (bit-exact designs)
        exact = A[A.field.isin(["mom_hash", "checksum", "cks_sharpen", "cks_edge", "cks_blur", "out_fnv1a64"])
                  & A.project.isin(["01_AES", "02_Convolution", "03_MC_Heston", "04_Portfolio"])]
        for _, r in exact.iterrows():
            if r.identical_across_platforms is False:
                warnings.append(dict(level="error", where=f"{r.project}/{r.group}/{r.config}",
                                     msg=f"{r.field} differs between platforms: {r['values'][:120]}"))

    # ------------------------------------------------------------------ XRT tables
    if bw_rows:
        pd.DataFrame(bw_rows).to_csv(os.path.join(T, "xrt_bandwidth_v2.csv"), index=False)
    if k_rows:
        pd.DataFrame(k_rows).to_csv(os.path.join(T, "xrt_kernels_v2.csv"), index=False)

    # ------------------------------------------------------------------ v1 vs v2
    VV = v1_vs_v2(a.v1, S, warnings) if a.v1 else pd.DataFrame()
    VV.to_csv(os.path.join(T, "v1_vs_v2.csv"), index=False)

    # ------------------------------------------------------------------ data quality
    Q = pd.DataFrame(warnings, columns=["level", "where", "msg"])
    Q.to_csv(os.path.join(T, "data_quality_v2.csv"), index=False)
    write_report(R, df, SP, ER, pd.DataFrame(cx), VV, Q)
    print(f"analyze_v2: {len(df)} runs, {len(SP)} speedup rows, {len(Q)} notes -> {T}")
    return 0


# ---------------------------------------------------------------------------
V1MAP = [
    # project, v1 (group, config), v2 (group, config), point label, kind
    ("01_AES", ("main", "enc_100MB"), ("enc", "100MB"), "enc 100 MB", "time"),
    ("01_AES", ("main", "enc_2048MB"), ("enc", "2048MB"), "enc 2048 MB", "time"),
    ("02_Convolution", ("main", "16bit"), ("image", "img_rgb16"), "8K RGB16", "time"),
    ("02_Convolution", ("main", "8bit"), ("image", "img_rgb8"), "8K RGB8", "time"),
    ("02_Convolution", ("main", "bw"), ("image", "img_gray8"), "8K gray8", "time"),
    ("03_MC_Heston", ("main", "p524288_s32"), ("main", "524288x32"), "524288 x 32", "time"),
    ("03_MC_Heston", ("main", "p4194304_s64"), ("main", "4194304x64"), "4194304 x 64", "time"),
    ("04_Portfolio", ("main", "full"), ("main", "p2_131072"), "thesis workload", "time"),
    ("05_LiveStream_Single", ("main", "1080p"), ("pipe", "1080p"), "1080p pipeline", "fps"),
    ("05_LiveStream_Single", ("main", "240p"), ("pipe", "240p"), "240p pipeline", "fps"),
    ("06_LiveStream_Multi", ("main", "all5"), ("pipe", "all5"), "5-rung pipeline", "fps"),
]


def v1_vs_v2(v1dir, S2, warnings):
    f = os.path.join(v1dir, "tables", "summary_stats.csv")
    if not os.path.exists(f):
        warnings.append(dict(level="info", where="v1_vs_v2", msg="no v1 summary (results/v1) - comparison skipped"))
        return pd.DataFrame()
    S1 = pd.read_csv(f)

    def med1(project, grp, cfg, plat, metrics):
        for m in metrics:
            x = S1[(S1.project == project) & (S1.group == grp) & (S1.config == cfg) & (S1.platform == plat) & (S1.metric == m)]
            if len(x):
                return float(x["median"].iloc[0]), m
        return np.nan, ""

    def med2(project, grp, cfg, plat, metric):
        x = S2[(S2.project == project) & (S2.group == grp) & (S2.config == cfg) & (S2.plat == plat) & (S2.metric == metric)]
        return float(x["median"].iloc[0]) if len(x) else np.nan

    out = []
    for project, (g1, c1), (g2, c2), label, kind in V1MAP:
        cpus2 = sorted(set(S2[(S2.project == project) & (S2.group == g2) & (S2.config == c2)].plat) - {"fpga"})
        cpus2 = [c for c in cpus2 if not c.startswith("fpga")]
        if kind == "time":
            f1, m1 = med1(project, g1, c1, "fpga", ["compute_s", "pipeline_s", "kernel_s"])
            c1v, mc1 = med1(project, g1, c1, "cpu", ["compute_s"])
            e1f, _ = med1(project, g1, c1, "fpga", ["app_total_s", "job_e2e_s", "wall_s"])
            e1c, _ = med1(project, g1, c1, "cpu", ["app_total_s", "job_e2e_s", "wall_s"])
            f2 = med2(project, g2, c2, "fpga", "t_compute_s")
            e2f = med2(project, g2, c2, "fpga", "e2e_s")
            c2s = {c: med2(project, g2, c2, c, "t_compute_s") for c in cpus2}
            e2s = {c: med2(project, g2, c2, c, "e2e_s") for c in cpus2}
            c2s = {k: v for k, v in c2s.items() if v == v}
            bestc = min(c2s, key=c2s.get) if c2s else None
            row = dict(project=project, point=label, kind="time [s] (lower is better)",
                       v1_fpga_compute=f1, v1_fpga_metric=m1, v2_fpga_compute=f2, fpga_improvement=f1 / f2 if f2 else np.nan,
                       v1_cpu_compute=c1v, v2_best_cpu=bestc, v2_cpu_compute=c2s.get(bestc, np.nan),
                       cpu_improvement=c1v / c2s[bestc] if bestc else np.nan,
                       v1_speedup_compute=c1v / f1 if f1 else np.nan,
                       v2_speedup_compute=c2s[bestc] / f2 if bestc and f2 else np.nan,
                       v1_speedup_e2e=e1c / e1f if e1f else np.nan,
                       v2_speedup_e2e=(e2s.get(bestc, np.nan) / e2f) if bestc and e2f else np.nan)
            for c, v in c2s.items():
                row[f"v2_cpu_{c}"] = v
        else:
            f1, _ = med1(project, g1, c1, "fpga", ["fps"])
            c1v, _ = med1(project, g1, c1, "cpu", ["fps"])
            d1, _ = med1(project, g1, c1, "cpu_direct", ["fps"])
            f2 = med2(project, g2, c2, "fpga", "fps")
            c2v = med2(project, g2, c2, "cpu", "fps")
            row = dict(project=project, point=label, kind="frames/s (higher is better)",
                       v1_fpga_compute=f1, v2_fpga_compute=f2, fpga_improvement=f2 / f1 if f1 else np.nan,
                       v1_cpu_compute=c1v, v1_cpu_direct=d1, v2_best_cpu="cpu", v2_cpu_compute=c2v,
                       cpu_improvement=c2v / c1v if c1v else np.nan,
                       v1_speedup_compute=f1 / c1v if c1v else np.nan,
                       v1_speedup_vs_fair_cpu=f1 / d1 if d1 else np.nan,
                       v2_speedup_compute=f2 / c2v if c2v else np.nan)
        out.append(row)
    return pd.DataFrame(out)


def md(d, cols, n=40):
    if d is None or not len(d):
        return "_(no data)_\n"
    d = d[[c for c in cols if c in d]].head(n)
    s = "| " + " | ".join(d.columns) + " |\n|" + "---|" * len(d.columns) + "\n"
    for _, r in d.iterrows():
        s += "| " + " | ".join(f"{v:.4g}" if isinstance(v, float) else str(v) for v in r.values) + " |\n"
    return s


def write_report(R, df, SP, ER, CX, VV, Q):
    L = ["# v2 benchmark report (A-grade FPGA vs A-grade CPU)\n",
         f"Runs: {len(df)} (failed: {int((~df.ok).sum())}). Boundaries: compute = program's measured section; "
         "e2e = launch -> measured section (warm, one job); energy = socket0 + DRAM + card over the measured section.\n"]
    err = Q[Q.level == "error"] if len(Q) else Q
    L.append(f"\n**Data quality:** {len(err)} error(s), {len(Q) - len(err)} note(s); full list at the end.\n")
    if len(err):
        L.append(md(err, ["level", "where", "msg"], 60))
    if len(SP):
        b = SP[SP.cpu.str.startswith("BEST_CPU") & SP.fpga.eq("fpga")]
        L.append("\n## Speedup of the FPGA over the best CPU implementation (>1 = FPGA faster)\n")
        L.append(md(b.sort_values(["project", "boundary", "group", "config"]),
                    ["project", "group", "config", "boundary", "cpu", "cpu_median", "fpga_median", "speedup", "ci_lo", "ci_hi"], 200))
    L.append("\n## Crossover analysis\n")
    L.append(md(CX, ["series", "boundary", "x_label", "points", "speedup_smallest", "speedup_largest", "crossover_x", "verdict"]))
    if len(ER):
        L.append("\n## Energy per unit of work (FPGA advantage >1 = FPGA uses less energy)\n")
        L.append(md(ER[ER.boundary == "system"], ["project", "group", "config", "fpga", "cpu", "unit",
                                                   "cpu_J_per_unit", "fpga_J_per_unit", "fpga_advantage"], 200))
    T = os.path.join(R, "tables")
    for fn, title, cols in (("fpga_hls_top_v2.csv", "FPGA kernels: HLS estimates",
                             ["project", "top_module", "target_ns", "estimated_ns", "fmax_est_mhz", "loops", "loops_II_gt_1",
                              "timing_issues", "LUT", "FF", "DSP", "BRAM", "URAM"]),
                            ("fpga_impl_v2.csv", "FPGA implementation (Vivado, routed)",
                             ["project", "WNS_ns", "WNS_kernel_clocks_min", "WNS_hbm_aclk", "kernels_total.LUT_pct",
                              "kernels_total.REG_pct", "kernels_total.BRAM_pct", "kernels_total.URAM_pct", "kernels_total.DSP_pct"]),
                            ("fpga_tuning_v2.csv", "Automatic design selection (tune_v2.sh: every variant built and tested)",
                             ["project", "variant", "built", "ok", "metric", "score", "requested_clock_mhz", "achieved_kernel_clock_mhz",
                              "WNS_kernel_clocks_min", "util_LUT_pct", "util_DSP_pct", "util_BRAM_pct", "decision"])):
        f = os.path.join(T, fn)
        if os.path.exists(f) and os.path.getsize(f) > 5:
            try:
                L.append(f"\n## {title}\n")
                L.append(md(pd.read_csv(f), cols))
            except Exception:
                pass
    L.append("\n## v1 (thesis) vs v2 (A-grade)\n")
    L.append(md(VV, ["project", "point", "kind", "v1_fpga_compute", "v2_fpga_compute", "fpga_improvement",
                     "v1_cpu_compute", "v2_cpu_compute", "cpu_improvement", "v1_speedup_compute", "v2_speedup_compute"]))
    L.append("\n## Data quality (all notes)\n")
    L.append(md(Q, ["level", "where", "msg"], 300))
    open(os.path.join(R, "REPORT_v2.md"), "w").write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
