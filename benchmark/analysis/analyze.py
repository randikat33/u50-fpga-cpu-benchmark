#!/usr/bin/env python3
"""
analyze.py - turns results/raw/** into tidy tables for the thesis / journal paper.

Outputs (results/tables/):
  runs.csv               one row per run, every parsed metric + energy
  summary_stats.csv      median / mean / sd / CV / 95% CI per configuration
  speedups.csv           speedup at every measurement boundary (+ bootstrap CI)
  energy_ratios.csv      energy advantage at card-only / host-only / system boundary
  bitstream_load.csv     program time: bitstream swap vs cached
  xrt_bandwidth.csv      PCIe + kernel<->HBM transfer rates (profiled runs)
  xrt_kernels.csv        kernel execution + clock frequency (profiled runs)
  xrt_api.csv            XRT/OpenCL API times (profiled runs)
  accuracy.csv           correctness checks (AES md5, MC price, portfolio prices)
  data_quality.csv       warnings: failed runs, high CV, missing sensors ...
  REPORT.md              readable summary of everything above
"""
import argparse, csv, glob, json, math, os, re, struct, sys
from collections import defaultdict

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
       9: 2.262, 10: 2.228, 15: 2.131, 20: 2.086, 30: 2.042}

def t95(dof):
    if dof <= 0:
        return float("nan")
    keys = sorted(T95)
    for k in keys:
        if dof <= k:
            return T95[k]
    return 1.96

def num(pattern, text, cast=float, group=1, flags=re.M):
    m = re.search(pattern, text, flags)
    if not m:
        return None
    try:
        return cast(m.group(group))
    except (ValueError, TypeError):
        return None

def nz(x):
    return np.nan if x is None else x

def read_text(path):
    try:
        raw = open(path, "rb").read().decode("utf-8", "replace")
    except OSError:
        return ""
    return raw.replace("\r", "\n")

def integrate(t, p, t0, t1):
    """Trapezoidal energy (J) of power samples p(t) within [t0, t1]."""
    if t0 is None or t1 is None or t1 <= t0:
        return float("nan")
    m = ~np.isnan(p)
    t, p = t[m], p[m]
    if len(t) < 2:
        return float("nan")
    grid = np.concatenate(([t0], t[(t > t0) & (t < t1)], [t1]))
    vals = np.interp(grid, t, p)
    return float(np.trapz(vals, grid)) if hasattr(np, "trapz") else float(np.trapezoid(vals, grid))

def mean_in(t, p, t0, t1):
    m = (~np.isnan(p)) & (t >= t0) & (t <= t1)
    return float(np.mean(p[m])) if m.any() else float("nan")

# ---------------------------------------------------------------------------
# power
# ---------------------------------------------------------------------------
def load_power(run_dir):
    f = os.path.join(run_dir, "power.csv")
    if not os.path.exists(f):
        return None
    try:
        df = pd.read_csv(f)
    except Exception:
        return None
    if df.empty:
        return None
    for c in ["pkg0_w", "dram0_w", "pkg1_w", "dram1_w", "card_w", "fpga_temp_c", "cpu0_temp_c"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = np.nan
    return df

def load_xrt_power(run_dir, t_launch):
    """XRT power_profile_*.csv -> (t_unix, card_w) using the rail currents/voltages."""
    files = glob.glob(os.path.join(run_dir, "xrt", "power_profile_*.csv"))
    if not files:
        return None
    try:
        df = pd.read_csv(files[0], skiprows=1, index_col=False)
        P = (df["12v_aux_curr"] * df["12v_aux_vol"] + df["12v_pex_curr"] * df["12v_pex_vol"]
             + df["3v3_pex_curr"] * df["3v3_pex_vol"]) / 1e6
        ts = df["timestamp"].astype(float)
        # XRT timestamps are ms since the profiler started (~process start)
        t = t_launch + (ts - ts.iloc[0]) / 1000.0 + ts.iloc[0] / 1000.0
        return pd.DataFrame({"t_unix": t.values, "card_w": P.values,
                             "fpga_temp_c": df.get("fpga_temp", pd.Series(np.nan, index=df.index)).values})
    except Exception:
        return None

# ---------------------------------------------------------------------------
# per-project stdout parsers -> dict of metrics (all times in seconds)
# ---------------------------------------------------------------------------
MS = 1e-3

def parse_aes(txt, platform, meta):
    d = {}
    if platform == "fpga":
        d["compute_units"] = num(r"Compute Units:\s*0*(\d+)", txt, int)
        d["bytes"] = num(r"Original size:\s*0*(\d+) bytes", txt, int)
        d["file_read_s"] = nz(num(r"File read:\s*([\d.]+) ms", txt)) * MS
        d["init_s"] = nz(num(r"FPGA initialization:\s*([\d.]+) ms", txt)) * MS
        d["pipeline_s"] = nz(num(r"Pipeline execution:\s*([\d.]+) ms", txt)) * MS
        d["h2d_s"] = nz(num(r"Host->Device \(H2D\):\s*([\d.]+) ms", txt)) * MS
        d["kernel_s"] = nz(num(r"Kernel execution:\s*([\d.]+) ms", txt)) * MS
        d["d2h_s"] = nz(num(r"Device->Host \(D2H\):\s*([\d.]+) ms", txt)) * MS
        d["file_write_s"] = nz(num(r"File write:\s*([\d.]+) ms", txt)) * MS
        d["app_total_s"] = nz(num(r"TOTAL TIME:\s*([\d.]+) ms", txt)) * MS
        d["compute_s"] = d["pipeline_s"]
    elif platform == "cpu":
        d["bytes"] = num(r"Original size:\s*0*(\d+) bytes", txt, int)
        d["file_read_s"] = nz(num(r"File read:\s*([\d.]+) ms", txt)) * MS
        d["init_s"] = nz(num(r"CPU initialization:\s*([\d.]+) ms", txt)) * MS
        d["compute_s"] = nz(num(r"Processing time:\s*([\d.]+) ms", txt)) * MS
        d["file_write_s"] = nz(num(r"File write:\s*([\d.]+) ms", txt)) * MS
        d["app_total_s"] = nz(num(r"TOTAL TIME:\s*([\d.]+) ms", txt)) * MS
    elif platform.startswith("openssl") and "speed" not in meta.get("_config", ""):
        m = re.search(r"enc_(\d+)MB", meta.get("_config", ""))
        d["bytes"] = int(m.group(1)) * 1048576 if m else None
        d["app_total_s"] = meta.get("wall_s")
    else:   # openssl speed
        m = re.findall(r"^\s*(aes-256-ctr|AES-256-CTR)\s+([\d.]+)k\s*$", txt, re.M | re.I)
        if m:
            d["speed_MBps"] = float(m[-1][1]) * 1000 / 1e6
    if d.get("bytes"):
        mb = d["bytes"] / 1048576
        d["mb"] = mb
        for k in ("app_total_s", "compute_s", "kernel_s"):
            if d.get(k) and d[k] == d[k] and d[k] > 0:
                d[k.replace("_s", "_MBps")] = mb / d[k]
    ver = os.path.join(meta["_dir"], "verify.txt")
    if os.path.exists(ver):
        d["verify"] = open(ver).read().strip()
    return d

def parse_conv(txt, platform, meta):
    d = {}
    g = lambda pat: nz(num(pat + r"[^:]*:\s*([\d.]+) ms", txt)) * MS
    if platform == "fpga":
        d["image_load_s"] = g(r"Image load")
        d["setup_s"] = g(r"Platform/device/queue setup")
        d["xclbin_read_s"] = g(r"XCLBIN file read")
        d["program_s"] = g(r"Program load/build")
        d["kernel_obj_s"] = g(r"Kernel object creation")
        d["buffer_s"] = g(r"Buffer creation")
        d["h2d_s"] = g(r"H2D span")
        d["kernel_s"] = g(r"Kernel span")
        d["d2h_s"] = g(r"D2H span")
        d["pipeline_s"] = g(r"Full pipeline span")
        d["save_s"] = g(r"Save outputs")
        d["app_total_s"] = g(r"TOTAL end-to-end")
        d["compute_units"] = num(r"(\d+)CU\]", txt, int)
        d["fixed_setup_s"] = np.nansum([d["setup_s"], d["xclbin_read_s"], d["program_s"],
                                        d["kernel_obj_s"], d["buffer_s"]])
    else:
        d["image_load_s"] = g(r"Image load \+ preprocess")
        d["compute_s"] = g(r"CPU convolution compute")
        d["save_s"] = g(r"Save outputs")
        d["app_total_s"] = g(r"TOTAL end-to-end")
    md5 = os.path.join(meta["_dir"], "outputs.md5")
    if os.path.exists(md5):
        d["n_outputs"] = sum(1 for _ in open(md5))
    return d

def parse_mc(txt, platform, meta):
    d = {}
    if platform == "fpga":
        d["program_s"] = nz(num(r"XCLBIN Load:\s*([\d.]+) ms", txt)) * MS
        d["h2d_s"] = nz(num(r"H2D Transfer:\s*([\d.]+) ms", txt)) * MS
        d["kernel_s"] = nz(num(r"Kernel Avg:\s*([\d.]+) ms", txt)) * MS
        d["kernel_median_s"] = nz(num(r"Kernel Median:\s*([\d.]+) ms", txt)) * MS
        d["warmup_s"] = nz(num(r"Warm-up:\s*([\d.]+) ms", txt)) * MS
        d["host_reported_total_s"] = nz(num(r"Total E2E:\s*([\d.]+) ms", txt)) * MS
        d["price"] = num(r"FPGA Price:\s*([\d.]+)", txt)
        d["price_se"] = num(r"FPGA Price:\s*[\d.]+\s*\+/-\s*([\d.]+)", txt)
        d["runs_inproc"] = num(r"Runs=(\d+)", txt, int)
        # single-job E2E = bitstream load + H2D + ONE kernel execution (corrected accounting)
        d["job_e2e_s"] = np.nansum([d["program_s"], d["h2d_s"], d["kernel_s"]])
        d["compute_s"] = d["kernel_s"]
    else:
        d["setup_s"] = nz(num(r"Setup:\s*([\d.]+) ms", txt)) * MS
        d["compute_s"] = nz(num(r"Compute:\s*([\d.]+) ms", txt)) * MS
        d["job_e2e_s"] = nz(num(r"Total:\s*([\d.]+) ms", txt)) * MS
        d["price"] = num(r"CPU Price:\s*([\d.]+)", txt)
        d["price_se"] = num(r"CPU Price:\s*[\d.]+\s*(?:\+/-|±)\s*([\d.]+)", txt)
    m = re.search(r"p(\d+)_s(\d+)", meta.get("_config", ""))
    if m:
        d["paths"], d["steps"] = int(m.group(1)), int(m.group(2))
        if d.get("compute_s"):
            d["pathsteps_per_s"] = d["paths"] * d["steps"] / d["compute_s"]
    return d

def parse_pf(txt, platform, meta):
    d = {}
    if platform == "fpga":
        d["trades"] = num(r"Trades=(\d+)", txt, int)
        d["load_s"] = nz(num(r"\[HOST\] Data load:\s*([\d.]+) ms", txt)) * MS
        d["program_s"] = nz(num(r"\[HOST\] XRT init:\s*([\d.]+) ms", txt)) * MS
        d["h2d_s"] = nz(num(r"\[HOST\] Market H2D:\s*([\d.]+) ms", txt)) * MS
        d["stage1_s"] = num(r"\[HOST\] Stage1:\s*([\d.]+) s", txt)
        d["stage1_trades_per_s"] = num(r"\[HOST\] Stage1:.*\|\s*([\d.]+) trades/s", txt)
        d["stage2_s"] = num(r"\[HOST\] Stage2:\s*([\d.]+) s", txt)
        d["stage2_trades_per_s"] = num(r"\[HOST\] Stage2:.*\|\s*([\d.]+) trades/s", txt)
        d["compute_s"] = np.nansum([d["stage1_s"], d["stage2_s"]])
    else:
        d["trades"] = num(r"Trades=(\d+)", txt, int)
        d["load_s"] = num(r"\[CPU\] Load time:\s*([\d.]+) s", txt)
        d["stage1_s"] = num(r"Stage1 compute\s*:\s*([\d.]+) s", txt)
        d["select_s"] = num(r"TOPK select\s*:\s*([\d.]+) s", txt)
        d["stage2_s"] = num(r"Stage2 compute\s*:\s*([\d.]+) s", txt)
        d["compute_s"] = num(r"TOTAL \(S1\+sel\+S2\):\s*([\d.]+) s", txt)
        if d.get("trades") and d.get("stage1_s"):
            d["stage1_trades_per_s"] = d["trades"] / d["stage1_s"]
        if d.get("stage2_s"):
            d["stage2_trades_per_s"] = 10000 / d["stage2_s"]
    d["job_e2e_s"] = meta.get("wall_s")
    return d

def program_time_from_ts(run_dir):
    """Time between XRT's "Loading: '<xclbin>'" line and the next output line."""
    f = os.path.join(run_dir, "stdout_ts.tsv")
    if not os.path.exists(f):
        return np.nan
    rows = [l.rstrip("\n").split("\t", 1) for l in open(f, errors="replace")][1:]
    for i, r in enumerate(rows):
        if len(r) == 2 and r[1].startswith("Loading:") and i + 1 < len(rows):
            return float(rows[i + 1][0]) - float(r[0])
    return np.nan

def parse_ls(txt, platform, meta):
    d = {}
    d["frames"] = num(r"Total frames:\s*(\d+)", txt, int)
    d["app_fps_int"] = num(r"Average FPS:\s*([\d.]+)", txt)
    d["precise_fps"] = num(r"Precise FPS:\s*([\d.]+)", txt)
    if platform == "fpga":
        d["kernel_ms"] = num(r"Avg Kernel:\s*([\d.]+) ms", txt)
        d["d2h_ms"] = num(r"Avg D2H:\s*([\d.]+) ms", txt)
        d["program_s"] = program_time_from_ts(meta["_dir"])
    else:
        d["kernel_ms"] = num(r"Avg CPU RESIZE:\s*([\d.]+) ms", txt)
        d["pack_ms"] = num(r"Avg CPU PACK \(raw->packed\):\s*([\d.]+) ms", txt)
        d["out_unpack_ms"] = num(r"Avg CPU OUT_UNPACK:\s*([\d.]+) ms", txt)
    d["mode"] = "direct" if "CPU_PIPELINE_MODE: direct" in txt else ("legacy" if platform.startswith("cpu") else "fpga")
    if d["frames"] and meta.get("proc_s"):
        d["fps"] = d["frames"] / meta["proc_s"]
    if d["frames"] and meta.get("launch_to_end_s"):
        d["fps_incl_startup"] = d["frames"] / meta["launch_to_end_s"]
    d["job_e2e_s"] = meta.get("launch_to_end_s")
    d["teardown_s"] = (meta["t_exit"] - meta["t_end_marker"]) if meta.get("t_end_marker") else np.nan
    return d

def parse_lm(txt, platform, meta):
    d = {}
    if platform == "fpga":
        d["frames"] = num(r"^(\d+) frames in \d+ seconds", txt, int)
        d["program_s"] = nz(num(r"Program:\s*([\d.]+) ms", txt)) * MS
        d["init_total_s"] = nz(num(r"=== FPGA Init ===[\s\S]*?Total:\s*([\d.]+) ms", txt)) * MS
        d["app_fps_int"] = num(r"Average FPS:\s*([\d.]+)", txt)
    else:
        d["frames"] = num(r"Processed\s+(\d+) frames", txt, int)
        d["cpu_all5_ms"] = num(r"Avg CPU time \(all 5 qualities\):\s*([\d.]+) ms", txt)
        d["app_fps_int"] = num(r"Average FPS:\s*([\d.]+)", txt)
    if d.get("frames") and meta.get("proc_s"):
        d["fps"] = d["frames"] / meta["proc_s"]
        d["output_fps_sum"] = 5 * d["fps"]
    d["job_e2e_s"] = meta.get("launch_to_end_s")
    d["teardown_s"] = (meta["t_exit"] - meta["t_end_marker"]) if meta.get("t_end_marker") else np.nan
    return d

PARSERS = {"01_AES": parse_aes, "02_Convolution": parse_conv, "03_MC_Heston": parse_mc,
           "04_Portfolio": parse_pf, "05_LiveStream_Single": parse_ls, "06_LiveStream_Multi": parse_lm}

# "unit of work" per project, used for J/unit and throughput
def work_units(project, d):
    if project == "01_AES":
        return d.get("mb"), "MB"
    if project in ("05_LiveStream_Single", "06_LiveStream_Multi"):
        return d.get("frames"), "frame"
    return 1.0, "job"

# ---------------------------------------------------------------------------
# XRT summary.csv (profiled runs)
# ---------------------------------------------------------------------------
def parse_xrt_summary(path):
    L = [l.rstrip("\r\n") for l in open(path, errors="replace")]
    out = {"bw": [], "kernels": [], "api": []}
    def section(title):
        try:
            i = L.index(title)
        except ValueError:
            return None, []
        hdr = L[i + 1].split(",")
        rows, j = [], i + 2
        while j < len(L) and L[j].strip():
            rows.append(L[j].split(","))
            j += 1
        return hdr, rows
    _, rows = section("Data Transfer: Host to Global Memory")
    for r in rows:
        if len(r) > 6:
            out["bw"].append(dict(link="PCIe", port="host", direction="H2D" if r[1] == "WRITE" else "D2H",
                                  n=r[2], rate_MBps=r[3], util_pct=r[4], avg_kb=r[5], total_ms=r[6], max_MBps=15754))
    _, rows = section("Data Transfer: Kernels to Global Memory")
    for r in rows:
        if len(r) > 9:
            out["bw"].append(dict(link="HBM", port=r[1], memory=r[3], direction=r[4], n=r[5],
                                  rate_MBps=r[6], util_pct=r[7], max_MBps=r[9]))
    hdr, rows = section("Compute Unit Utilization")
    for r in rows:
        if hdr and len(r) >= len(hdr) - 1:
            rec = dict(zip(hdr, r))
            out["kernels"].append(dict(cu=rec.get("Compute Unit"), kernel=rec.get("Kernel"),
                                       calls=rec.get("Number Of Calls"), total_ms=rec.get("Total Time (ms)"),
                                       avg_ms=rec.get("Average Time (ms)"), clock_mhz=rec.get("Clock Frequency (MHz)")))
    if not out["kernels"]:
        _, rows = section("Kernel Execution")
        for r in rows:
            if len(r) > 4:
                out["kernels"].append(dict(cu="", kernel=r[0], calls=r[1], total_ms=r[2], avg_ms=r[4], clock_mhz=""))
    for title in ("OpenCL API Calls", "Native XRT API Calls"):
        _, rows = section(title)
        for r in rows:
            if len(r) > 4:
                out["api"].append(dict(api=r[0], calls=r[1], total_ms=r[2], avg_ms=r[4], table=title))
    return out

# ---------------------------------------------------------------------------
def boot_ratio_ci(a, b, n=4000, seed=1):
    """95 % bootstrap CI of median(a)/median(b)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) == 0 or len(b) == 0:
        return (np.nan, np.nan, np.nan)
    r = np.median(a) / np.median(b)
    if len(a) < 2 and len(b) < 2:
        return (r, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    s = [np.median(rng.choice(a, len(a))) / np.median(rng.choice(b, len(b))) for _ in range(n)]
    return (r, float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    a = ap.parse_args()
    R = os.path.abspath(a.results)
    RAW = os.path.join(R, "raw")
    T = os.path.join(R, "tables")
    os.makedirs(T, exist_ok=True)
    warnings = []

    # ------------------------------------------------------------- runs
    rows, bw_rows, k_rows, api_rows, xpow = [], [], [], [], {}
    for mf in sorted(glob.glob(os.path.join(RAW, "*", "*", "*", "*", "*", "meta.json"))):
        rd = os.path.dirname(mf)
        rel = os.path.relpath(rd, RAW).split(os.sep)
        project, group, config, platform, rep = rel
        try:
            meta = json.load(open(mf))
        except Exception as e:
            warnings.append(dict(level="error", where="/".join(rel), msg=f"bad meta.json: {e}"))
            continue
        meta["_dir"], meta["_config"] = rd, config
        base = dict(project=project, group=group, config=config, platform=platform, rep=rep,
                    profiled=not re.match(r"^(rep|cold)\d+$", rep), ok=meta.get("ok"), exit_code=meta.get("exit_code"),
                    wall_s=meta.get("wall_s"), proc_s=meta.get("proc_s"),
                    launch_to_end_s=meta.get("launch_to_end_s"),
                    bitstream_state=meta.get("bitstream_state"), killed=meta.get("killed_after_end"),
                    timed_out=meta.get("timed_out"), fpga_temp_pre=meta.get("fpga_temp_pre"),
                    fpga_temp_post=meta.get("fpga_temp_post"), cpu_temp_pre=meta.get("cpu_temp_pre"),
                    cpu_temp_post=meta.get("cpu_temp_post"), t_launch=meta.get("t_launch"))
        if not meta.get("ok"):
            warnings.append(dict(level="error", where="/".join(rel),
                                 msg=f"run failed (rc={meta.get('exit_code')}, timeout={meta.get('timed_out')})"))
        txt = read_text(os.path.join(rd, "stdout.log"))
        d = {}
        if project in PARSERS:
            try:
                d = PARSERS[project](txt, platform, meta)
            except Exception as e:
                warnings.append(dict(level="error", where="/".join(rel), msg=f"parser error: {e}"))
        base.update(d)

        # ---- energy -------------------------------------------------------
        pw = load_power(rd)
        t0, t1 = meta.get("t_launch"), meta.get("t_exit")
        p0, p1 = meta.get("t_start_marker") or t0, meta.get("t_end_marker") or t1
        xp = load_xrt_power(rd, t0) if platform == "fpga" and t0 else None
        if xp is not None:
            xpow[(project, config, rep)] = xp
            base["card_w_xrt_mean"] = float(xp.card_w.mean())
        if pw is not None and t0:
            t = pw.t_unix.values.astype(float)
            card = pw.card_w.values.astype(float)
            card_src = str(pw.card_src.dropna().iloc[0]) if "card_src" in pw and pw.card_src.notna().any() else "none"
            if np.isnan(card).all() and xp is not None:
                card = np.interp(t, xp.t_unix.values, xp.card_w.values, left=np.nan, right=np.nan)
                card_src = "xrt_profile"
            base["card_src"] = card_src
            idle_m = (t < t0) | (t > t1)
            for col in ("pkg0_w", "dram0_w", "pkg1_w"):
                v = pw[col].values.astype(float)
                base[f"{col[:-2]}_idle_w"] = float(np.nanmedian(v[idle_m])) if idle_m.any() and not np.isnan(v[idle_m]).all() else np.nan
                base[f"E_{col[:-2]}_run_J"] = integrate(t, v, t0, t1)
                base[f"E_{col[:-2]}_proc_J"] = integrate(t, v, p0, p1)
                base[f"P_{col[:-2]}_run_w"] = mean_in(t, v, t0, t1)
            base["card_idle_w"] = float(np.nanmedian(card[idle_m])) if idle_m.any() and not np.isnan(card[idle_m]).all() else np.nan
            base["E_card_run_J"] = integrate(t, card, t0, t1)
            base["E_card_proc_J"] = integrate(t, card, p0, p1)
            base["P_card_run_w"] = mean_in(t, card, t0, t1)
            fpg = pw.fpga_temp_c.values.astype(float)
            base["fpga_temp_max"] = float(np.nanmax(fpg)) if not np.isnan(fpg).all() else np.nan
            cpt = pw.cpu0_temp_c.values.astype(float)
            base["cpu_temp_max"] = float(np.nanmax(cpt)) if not np.isnan(cpt).all() else np.nan
        rows.append(base)

        # ---- XRT tables (profiled runs) -------------------------------------
        sfile = os.path.join(rd, "xrt", "summary.csv")
        if os.path.exists(sfile):
            try:
                x = parse_xrt_summary(sfile)
                tag = dict(project=project, config=config, platform=platform, rep=rep)
                bw_rows += [dict(tag, **r) for r in x["bw"]]
                k_rows += [dict(tag, **r) for r in x["kernels"]]
                api_rows += [dict(tag, **r) for r in x["api"]]
            except Exception as e:
                warnings.append(dict(level="warn", where="/".join(rel), msg=f"xrt summary parse: {e}"))

    if not rows:
        print("no runs found under", RAW)
        return 1
    df = pd.DataFrame(rows)

    # card idle power for CPU runs when no card sensor exists: use XRT-derived idle
    if "card_src" in df and (df.card_src == "none").any():
        if xpow:
            idle_card = float(np.nanpercentile(np.concatenate([v.card_w.values for v in xpow.values()]), 5))
            m = df.card_src.eq("none")
            run_len = df.loc[m, "wall_s"].astype(float)
            df.loc[m, "E_card_run_J"] = idle_card * run_len
            proc = df.loc[m, "proc_s"].astype(float).fillna(run_len)
            df.loc[m, "E_card_proc_J"] = idle_card * proc
            df.loc[m, "card_src"] = "xrt_idle_estimate"
            warnings.append(dict(level="info", where="all", msg=f"no live card sensor: CPU-run card power estimated as XRT idle {idle_card:.2f} W"))

    # boundaries: which window is charged per project
    use_proc = df.project.isin(["05_LiveStream_Single", "06_LiveStream_Multi"])
    for comp in ("pkg0", "dram0", "card"):
        c_run, c_proc = f"E_{comp}_run_J", f"E_{comp}_proc_J"
        if c_run in df:
            df[f"E_{comp}_J"] = np.where(use_proc, df.get(c_proc), df.get(c_run))
    if "E_pkg0_J" in df:
        df["E_host_J"] = df["E_pkg0_J"] + df["E_dram0_J"].fillna(0)
        df["E_system_J"] = df["E_host_J"] + df["E_card_J"].fillna(0)
    units = df.apply(lambda r: work_units(r.project, r)[0], axis=1).astype(float)
    df["work_units"] = units
    df["unit"] = df.apply(lambda r: work_units(r.project, r)[1], axis=1)
    for c in ("E_host_J", "E_system_J", "E_card_J"):
        if c in df:
            df[c.replace("_J", "_per_unit_J")] = df[c] / units
    df.to_csv(os.path.join(T, "runs.csv"), index=False)

    timed = df[(~df.profiled) & (df.ok == True)]

    # ------------------------------------------------------------- summary stats
    metrics = [c for c in df.columns if df[c].dtype.kind in "fi" and c not in
               ("exit_code", "t_launch", "compute_units", "paths", "steps", "runs_inproc", "n_outputs")]
    srows = []
    for key, g in timed.groupby(["project", "group", "config", "platform"]):
        for m in metrics:
            v = g[m].astype(float).dropna().values
            if len(v) == 0:
                continue
            n, mean = len(v), float(np.mean(v))
            sd = float(np.std(v, ddof=1)) if n > 1 else np.nan
            srows.append(dict(zip(["project", "group", "config", "platform"], key), metric=m, n=n,
                              median=float(np.median(v)), mean=mean, sd=sd,
                              cv_pct=100 * sd / mean if mean and n > 1 else np.nan,
                              ci95_half=t95(n - 1) * sd / math.sqrt(n) if n > 1 else np.nan,
                              min=float(np.min(v)), max=float(np.max(v))))
    S = pd.DataFrame(srows)
    S.to_csv(os.path.join(T, "summary_stats.csv"), index=False)
    key_metric = {"01_AES": "app_total_s", "02_Convolution": "app_total_s", "03_MC_Heston": "compute_s",
                  "04_Portfolio": "job_e2e_s", "05_LiveStream_Single": "fps", "06_LiveStream_Multi": "fps"}
    for _, r in S.iterrows():
        if r.metric == key_metric.get(r.project) and r.cv_pct == r.cv_pct and r.cv_pct > 5:
            warnings.append(dict(level="warn", where=f"{r.project}/{r.group}/{r.config}/{r.platform}",
                                 msg=f"CV {r.cv_pct:.1f}% > 5% on {r.metric} (n={r.n})"))

    # ------------------------------------------------------------- speedups
    def vals(project, group, config, platform, col):
        g = timed[(timed.project == project) & (timed.group == group) & (timed.config == config)
                  & (timed.platform == platform)]
        return g[col].astype(float).values if col in g else np.array([])

    sp = []
    def add(project, config, boundary, num_, den_, note="", cpu="cpu"):
        r, lo, hi = boot_ratio_ci(num_, den_)
        if r == r:
            sp.append(dict(project=project, config=config, boundary=boundary, cpu_baseline=cpu,
                           speedup=r, ci_lo=lo, ci_hi=hi, n_cpu=len(num_), n_fpga=len(den_), note=note))

    for (project, config), g in timed[timed.group == "main"].groupby(["project", "config"]):
        V = lambda plat, col, grp="main": vals(project, grp, config, plat, col)
        if project in ("01_AES", "02_Convolution"):
            add(project, config, "1_kernel", V("cpu", "compute_s"), V("fpga", "kernel_s"))
            add(project, config, "2_pipeline", V("cpu", "compute_s"), V("fpga", "pipeline_s"))
            fixed = V("fpga", "init_s") if project == "01_AES" else V("fpga", "fixed_setup_s")
            tot = V("fpga", "app_total_s")
            if len(fixed) == len(tot):
                add(project, config, "3_daemon_e2e", V("cpu", "app_total_s"), tot - fixed,
                    "E2E minus one-time init (persistent process)")
            add(project, config, "4_warm_e2e", V("cpu", "app_total_s"), tot, "bitstream already on card")
            cold = vals(project, "cold", config, "fpga", "app_total_s")
            if len(cold):
                add(project, config, "5_cold_e2e", V("cpu", "app_total_s"), cold, "different bitstream loaded before")
            if project == "01_AES":
                tm = vals(project, "tmpfs", config, "fpga", "app_total_s")
                if len(tm):
                    add(project, config, "4b_warm_e2e_ramdisk", vals(project, "tmpfs", config, "cpu", "app_total_s"),
                        tm, "input/output on /dev/shm (storage removed)")
                for ob in ("openssl_aesni", "openssl_noaesni"):
                    o = vals(project, "openssl", config, ob, "app_total_s")
                    if len(o):
                        add(project, config, "4_warm_e2e", o, tot, "vs OpenSSL single-thread E2E", cpu=ob)
        elif project == "03_MC_Heston":
            add(project, config, "1_kernel", V("cpu", "compute_s"), V("fpga", "kernel_s"))
            add(project, config, "3_daemon_e2e", V("cpu", "job_e2e_s"), V("fpga", "h2d_s") + V("fpga", "kernel_s"))
            add(project, config, "4_warm_e2e", V("cpu", "job_e2e_s"), V("fpga", "job_e2e_s"),
                "load(cached)+H2D+1 kernel run")
            cold = vals(project, "cold", config, "fpga", "job_e2e_s")
            if len(cold):
                add(project, config, "5_cold_e2e", V("cpu", "job_e2e_s"), cold)
            add(project, config, "6_process_wall", V("cpu", "wall_s"), V("fpga", "wall_s"),
                "whole process incl. warm-up run (for reference only)")
        elif project == "04_Portfolio":
            add(project, config, "1_kernel", V("cpu", "compute_s"), V("fpga", "compute_s"), "stage1+stage2")
            add(project, config, "4_warm_e2e", V("cpu", "wall_s"), V("fpga", "wall_s"), "whole process")
            cold = vals(project, "cold", config, "fpga", "wall_s")
            if len(cold):
                add(project, config, "5_cold_e2e", V("cpu", "wall_s"), cold)
        elif project in ("05_LiveStream_Single", "06_LiveStream_Multi"):
            for cpu in sorted(set(g.platform) - {"fpga"}):
                # throughput speedup = fps_fpga / fps_cpu  (note the inverted order)
                add(project, config, "4_warm_e2e", V("fpga", "fps"), V(cpu, "fps"),
                    "FPS in processing window", cpu=cpu)
                add(project, config, "5_launch_to_done", V(cpu, "launch_to_end_s"), V("fpga", "launch_to_end_s"),
                    "process start -> last frame", cpu=cpu)
                if project == "05_LiveStream_Single":
                    add(project, config, "1_kernel", V(cpu, "kernel_ms"), V("fpga", "kernel_ms"),
                        "resize only", cpu=cpu)
                cold = vals(project, "cold", config, "fpga", "launch_to_end_s")
                if len(cold):
                    add(project, config, "6_cold_launch_to_done", V(cpu, "launch_to_end_s"), cold, cpu=cpu)
    SP = pd.DataFrame(sp)
    SP.to_csv(os.path.join(T, "speedups.csv"), index=False)

    # ------------------------------------------------------------- energy ratios
    er = []
    for (project, config), g in timed[timed.group == "main"].groupby(["project", "config"]):
        f = g[g.platform == "fpga"]
        if f.empty or "E_system_per_unit_J" not in g:
            continue
        for cpu in sorted(set(g.platform) - {"fpga"}):
            c = g[g.platform == cpu]
            if c.empty:
                continue
            def med(x, col):
                v = x[col].astype(float).dropna()
                return float(v.median()) if len(v) else np.nan
            cs, ch = med(c, "E_system_per_unit_J"), med(c, "E_host_per_unit_J")
            fs, fc = med(f, "E_system_per_unit_J"), med(f, "E_card_per_unit_J")
            er.append(dict(project=project, config=config, cpu_baseline=cpu, unit=f.unit.iloc[0],
                           cpu_J_per_unit_system=cs, cpu_J_per_unit_host_only=ch,
                           fpga_J_per_unit_system=fs, fpga_J_per_unit_card_only=fc,
                           ratio_system=cs / fs if fs else np.nan,
                           ratio_cpu_server_without_card=ch / fs if fs else np.nan,
                           ratio_card_only_legacy=ch / fc if fc else np.nan,
                           cpu_P_host_w=med(c, "P_pkg0_run_w") + np.nan_to_num(med(c, "P_dram0_run_w")),
                           fpga_P_host_w=med(f, "P_pkg0_run_w") + np.nan_to_num(med(f, "P_dram0_run_w")),
                           fpga_P_card_w=med(f, "P_card_run_w")))
    ER = pd.DataFrame(er)
    ER.to_csv(os.path.join(T, "energy_ratios.csv"), index=False)

    # ------------------------------------------------------------- bitstream load
    load_col = {"01_AES": "init_s", "02_Convolution": "program_s", "03_MC_Heston": "program_s",
                "04_Portfolio": "program_s", "05_LiveStream_Single": "program_s", "06_LiveStream_Multi": "program_s"}
    bl = []
    fp = df[(df.platform == "fpga") & (df.ok == True)]
    for _, r in fp.iterrows():
        col = load_col.get(r.project)
        if col and col in r and r[col] == r[col] and r[col] is not None:
            bl.append(dict(project=r.project, config=r.config, rep=r.rep, group=r.group,
                           state=r.bitstream_state, load_s=float(r[col]), metric=col))
    BL = pd.DataFrame(bl)
    BL.to_csv(os.path.join(T, "bitstream_load.csv"), index=False)

    # ------------------------------------------------------------- XRT tables
    pd.DataFrame(bw_rows).to_csv(os.path.join(T, "xrt_bandwidth.csv"), index=False)
    pd.DataFrame(k_rows).to_csv(os.path.join(T, "xrt_kernels.csv"), index=False)
    pd.DataFrame(api_rows).to_csv(os.path.join(T, "xrt_api.csv"), index=False)

    # ------------------------------------------------------------- accuracy
    acc = []
    a_ = df[(df.project == "01_AES") & df.get("verify", pd.Series(dtype=str)).notna()] if "verify" in df else pd.DataFrame()
    for _, r in a_.iterrows():
        acc.append(dict(project=r.project, check="decrypt == original (md5)", config=r.config,
                        platform=r.platform, rep=r.rep, result=r.verify))
    mc = timed[timed.project == "03_MC_Heston"]
    for cfg, g in mc.groupby("config"):
        pf_, pc_ = g[g.platform == "fpga"].price.dropna(), g[g.platform == "cpu"].price.dropna()
        sf, sc = g[g.platform == "fpga"].price_se.dropna(), g[g.platform == "cpu"].price_se.dropna()
        if len(pf_) and len(pc_):
            diff = pf_.median() - pc_.median()
            se = math.sqrt((sf.median() if len(sf) else 0) ** 2 + (sc.median() if len(sc) else 0) ** 2)
            acc.append(dict(project="03_MC_Heston", check="price FPGA vs CPU", config=cfg, platform="both", rep="median",
                            result=f"diff={100 * diff / pc_.median():+.3f}%  |diff|/SE={abs(diff) / se if se else float('nan'):.2f}"))
    pdirs = glob.glob(os.path.join(RAW, "04_Portfolio", "main", "full", "*", "rep1", "results"))
    fr = [p for p in pdirs if "/fpga/" in p]
    cr = [p for p in pdirs if "/cpu/" in p]
    def read_res(path):
        b = open(path, "rb").read()
        n = struct.unpack("<I", b[:4])[0]
        return np.frombuffer(b[4:4 + 8 * n], dtype="<f4").reshape(n, 2)
    try:
        if fr and cr:
            F = read_res(glob.glob(os.path.join(fr[0], "results_final.bin"))[0])
            C = read_res(glob.glob(os.path.join(cr[0], "results_final_cpu.bin"))[0])
            n = min(len(F), len(C))
            dp = np.abs(F[:n, 0] - C[:n, 0])
            se = np.sqrt(F[:n, 1] ** 2 + C[:n, 1] ** 2)
            ok = np.mean(dp <= 3 * np.maximum(se, 1e-9))
            acc.append(dict(project="04_Portfolio", check="per-trade price within 3 combined SE", config="full",
                            platform="both", rep="rep1", result=f"{100 * ok:.2f}% of {n} trades; median |dP|={np.median(dp):.4g}"))
    except Exception as e:
        warnings.append(dict(level="warn", where="04_Portfolio", msg=f"accuracy check failed: {e}"))
    pd.DataFrame(acc).to_csv(os.path.join(T, "accuracy.csv"), index=False)

    # ------------------------------------------------------------- data quality
    if "card_src" in df and df.card_src.isin(["none"]).all():
        warnings.append(dict(level="warn", where="all", msg="no FPGA card power sensor and no XRT power profile"))
    if "E_pkg0_J" in df and df.E_pkg0_J.isna().all():
        warnings.append(dict(level="error", where="all", msg="RAPL energy missing (permissions?)"))
    for _, r in df[df.platform == "fpga"].iterrows():
        if r.group == "main" and not r.profiled and r.bitstream_state == "swap":
            warnings.append(dict(level="info", where=f"{r.project}/{r.config}/{r.rep}",
                                 msg="timed run started after a bitstream swap"))
    Q = pd.DataFrame(warnings, columns=["level", "where", "msg"])
    Q.to_csv(os.path.join(T, "data_quality.csv"), index=False)

    write_report(R, df, S, SP, ER, BL, Q)
    print(f"analysis done: {len(df)} runs -> {T}")
    return 0

# ---------------------------------------------------------------------------
def md_table(d, cols, fmt=None):
    fmt = fmt or {}
    if d is None or d.empty:
        return "_no data_\n"
    out = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    for _, r in d.iterrows():
        cells = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                v = "" if v != v else (fmt.get(c, "{:.3g}").format(v))
            cells.append(str(v))
        out += "| " + " | ".join(cells) + " |\n"
    return out

def write_report(R, df, S, SP, ER, BL, Q):
    L = ["# Benchmark report\n", f"Runs: {len(df)} total, {int((df.ok == True).sum())} OK, "
         f"{int((df.ok != True).sum())} failed. Timed repetitions exclude profiled runs.\n"]
    sysf = os.path.join(R, "system", "lscpu.txt")
    if os.path.exists(sysf):
        txt = open(sysf).read()
        L.append("**Host:** " + " / ".join(x.strip() for x in re.findall(r"^(Model name:.*|Thread\(s\) per core:.*|NUMA node0 CPU\(s\):.*)$", txt, re.M)) + "\n")
    L.append("\n## Speedups by measurement boundary (median, 95 % bootstrap CI)\n")
    if not SP.empty:
        d = SP.copy()
        d["CI"] = d.apply(lambda r: f"[{r.ci_lo:.3g}, {r.ci_hi:.3g}]" if r.ci_lo == r.ci_lo else "", axis=1)
        L.append(md_table(d, ["project", "config", "cpu_baseline", "boundary", "speedup", "CI", "n_cpu", "n_fpga", "note"]))
    L.append("\n## Energy advantage (CPU J / FPGA J per unit of work)\n")
    L.append("`ratio_system` = both sides measured as socket-0 package + DRAM + Alveo card (same server). "
             "`ratio_cpu_server_without_card` = CPU side without the idle card. "
             "`ratio_card_only_legacy` = the boundary used in the thesis draft (card only) - do not use as headline.\n\n")
    L.append(md_table(ER, ["project", "config", "cpu_baseline", "unit", "cpu_J_per_unit_system", "fpga_J_per_unit_system",
                           "ratio_system", "ratio_cpu_server_without_card", "ratio_card_only_legacy",
                           "cpu_P_host_w", "fpga_P_host_w", "fpga_P_card_w"]))
    L.append("\n## Bitstream load time\n")
    if not BL.empty:
        b = BL.groupby(["project", "state"]).load_s.agg(["count", "median", "min", "max"]).reset_index()
        L.append(md_table(b, ["project", "state", "count", "median", "min", "max"]))
    L.append("\n## Key metrics per configuration (median, CV %)\n")
    keep = ["app_total_s", "compute_s", "kernel_s", "pipeline_s", "job_e2e_s", "fps", "wall_s", "proc_s",
            "E_system_per_unit_J", "speed_MBps"]
    if not S.empty:
        d = S[S.metric.isin(keep)]
        L.append(md_table(d, ["project", "group", "config", "platform", "metric", "n", "median", "cv_pct", "ci95_half"]))
    L.append("\n## Data-quality notes\n")
    L.append(md_table(Q, ["level", "where", "msg"]) if not Q.empty else "No warnings.\n")
    open(os.path.join(R, "REPORT.md"), "w").write("\n".join(L))

if __name__ == "__main__":
    sys.exit(main())
