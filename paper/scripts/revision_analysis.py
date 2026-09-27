#!/usr/bin/env python3
"""
revision_analysis.py - extra analyses requested in the review (no new measurements needed).

Reads the same paper/inputs/ as paper_numbers.py and writes paper/data/rev_*.csv:

  rev_energy_ci.csv        energy ratios with 95% bootstrap CIs (same method as the speedup CIs),
                           mean-power estimator (as in the paper) and exact counter integration
  rev_power_breakdown.csv  mean power per domain (pkg0, dram0, pkg1, dram1, card) in the measured
                           window and in the idle period before launch, per workload/configuration
  rev_idle_card.csv        idle card power per bitstream and the "charge the idle card" uplift
  rev_slowstate.csv        MC steady-sweep CPU runs: pass time, slow/fast flag, pre-run idle power,
                           load average at launch -> tests the background-load hypothesis
  rev_membw.csv            CPU DRAM traffic of AES and convolution vs the installed memory channels
  rev_bound_model.csv      attainable FPGA rate = min(kernel peak, PCIe ceiling) vs measured CPU rate
  rev_v1v2_gains.csv       v1 -> v2 gains with the SAME protocol as the plotted v2 points
  rev_idle_sensitivity.csv energy ratio if the idle host socket drew +/-10/20 W
Run:  python3 revision_analysis.py
"""
import glob, json, os, re
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, "..", "inputs")
OUT = os.path.join(HERE, "..", "data")
DOM = ["pkg0_w", "dram0_w", "pkg1_w", "dram1_w", "card_w"]
N_BOOT, SEED = 4000, 1

# installed memory (dmidecode, 25 Sep 2026): DIMM 3 and 7 on CPU 1, DIMM 19 and 23 on CPU 2,
# 32 GB DDR4-3200 each, running at 2933 MT/s -> 2 of 8 channels per socket
CH_PER_SOCKET, MTS = 2, 2933e6
PEAK_BW_SOCKET = CH_PER_SOCKET * MTS * 8 / 1e9          # GB/s


def res(txt, k):
    m = re.search(rf"^RESULT {k}=(\S+)", txt, re.M)
    if not m:
        return np.nan
    try:
        return float(m.group(1))
    except ValueError:
        return m.group(1)


def boot_ratio(num, den, n=N_BOOT, seed=SEED):
    """95% bootstrap CI of median(num)/median(den), independent resampling (as paper_numbers.py)"""
    rng = np.random.default_rng(seed)
    num, den = np.asarray(num, float), np.asarray(den, float)
    s = [np.median(rng.choice(num, len(num))) / np.median(rng.choice(den, len(den))) for _ in range(n)]
    return np.median(num) / np.median(den), np.percentile(s, 2.5), np.percentile(s, 97.5)


def integrate(p, t0, t1, col):
    """exact energy (J) in [t0, t1]: row i is the mean power over (t_{i-1}, t_i]"""
    t = p.t_unix.values
    v = p[col].astype(float).values
    e = 0.0
    for i in range(1, len(t)):
        a, b = max(t[i - 1], t0), min(t[i], t1)
        if b > a and np.isfinite(v[i]):
            e += v[i] * (b - a)
    return e


def run_record(d):
    """everything we need from one run directory"""
    mf, pf, sf = (os.path.join(d, f) for f in ("meta.json", "power.csv", "stdout.log"))
    if not all(os.path.exists(f) and os.path.getsize(f) > 0 for f in (mf, pf, sf)):
        return None
    m = json.load(open(mf))
    t0, t1 = m.get("t_start_marker"), m.get("t_end_marker")
    if not (t0 and t1) or not m.get("ok", True):
        return None
    txt = open(sf, errors="replace").read()
    p = pd.read_csv(pf)
    for c in DOM:
        p[c] = pd.to_numeric(p[c], errors="coerce")
    win = p[(p.t_unix >= t0) & (p.t_unix <= t1)]
    if len(win) < 2:
        win = p.iloc[(p.t_unix - (t0 + t1) / 2).abs().argsort()[:2]]
    idle = p[p.t_unix < m["t_launch"] - 0.05]
    r = dict(window_s=t1 - t0, samples=int(((p.t_unix >= t0) & (p.t_unix <= t1)).sum()),
             t_compute_s=res(txt, "t_compute_s"), loadavg1=(m.get("loadavg") or [np.nan])[0],
             cpu_util_proc_pct=m.get("cpu_util_proc_pct"), cpu_temp_pre=m.get("cpu_temp_pre"),
             fpga_temp_pre=m.get("fpga_temp_pre"), t_launch=m["t_launch"], t0=t0, t1=t1,
             repeat=res(txt, "repeat"), runs=res(txt, "runs"), bytes=res(txt, "bytes"),
             mpix=res(txt, "mpix"), frames=res(txt, "frames"), paths=res(txt, "paths"),
             steps=res(txt, "steps"), path_steps=res(txt, "path_steps"), _dir=d)
    for c in DOM:
        r[f"P_{c[:-2]}"] = float(win[c].mean())                    # paper estimator (window mean)
        r[f"Pidle_{c[:-2]}"] = float(idle[c].median()) if len(idle) else np.nan
        r[f"Eexact_{c[:-2]}"] = integrate(p, t0, t1, c)             # counter integration at markers
    return r


def load(pattern, keys):
    rows = []
    for d in sorted(glob.glob(pattern)):
        parts = d.split(os.sep)
        r = run_record(d)
        if r:
            r.update({k: parts[i] for k, i in keys.items()})
            rows.append(r)
    return pd.DataFrame(rows)


S0 = ["pkg0", "dram0"]
S01 = ["pkg0", "dram0", "pkg1", "dram1"]


def add_energy(df, work_per_pass, passes):
    """E per unit: paper estimator (mean power x pass time / work) and exact integration / total work"""
    for name, doms in (("socket", S0), ("server", S01)):
        card = df.platform.eq("fpga").astype(float)
        P = sum(df[f"P_{d}"] for d in doms) + card * df["P_card"]
        Ex = sum(df[f"Eexact_{d}"] for d in doms) + card * df["Eexact_card"]
        df[f"E_{name}_est"] = P * df.t_compute_s / work_per_pass
        df[f"E_{name}_exact"] = Ex / (passes * work_per_pass)
    return df


# =============================================================================== long-window energy
rows = []
LW = load(os.path.join(IN, "results_energy", "raw", "*", "*", "rep*"), {"workload": -3, "platform": -2, "rep": -1})
for w, g in LW.groupby("workload"):
    g = g.copy()
    if w.startswith("mc"):
        wpp = g.paths * g.steps / 1e6                  # M path-steps per pass
        passes = g.runs
        unit = "J per M path-steps"
    elif w.startswith("aes"):
        wpp = g.bytes / 1e9                            # GB per pass
        passes = g.repeat.fillna(1)
        unit = "J per GB"
    else:
        wpp = g.mpix
        passes = g.repeat
        unit = "J per Mpix"
    g = add_energy(g, wpp, passes)
    g["unit"] = unit
    g["wpp"], g["passes"] = wpp, passes
    rows.append(g)
LW = pd.concat(rows)


def ratio_rows(df, src, key="workload"):
    out = []
    for w, g in df.groupby(key):
        f = g[g.platform == "fpga"]
        for cfg, c in g[g.platform != "fpga"].groupby("platform"):
            bnd = "socket" if cfg.startswith("s0_") else "server"
            for est in ("est", "exact"):
                r, lo, hi = boot_ratio(c[f"E_{bnd}_{est}"], f[f"E_{bnd}_{est}"])
                out.append(dict(source=src, workload=w, cpu_config=cfg, boundary=bnd, estimator=est,
                                energy_adv=r, ci_lo=lo, ci_hi=hi, n_cpu=len(c), n_fpga=len(f),
                                min_samples=int(min(c.samples.min(), f.samples.min()))))
            # also the per-socket boundary for 2-socket configs is not meaningful; skip
    return out


E_rows = ratio_rows(LW, "long-window runs")

# ======================================================= portfolio and MC 67M: campaign FPGA + scaling CPU
SC = load(os.path.join(IN, "results_scaling", "raw", "*", "*", "rep*"), {"workload": -3, "platform": -2, "rep": -1})
CAMP = os.path.join(IN, "campaign_raw_small", "v2", "raw")
FPGA_OF = {"pf_131072": ("04_Portfolio", "main", "p2_131072"),
           "mc_67108864x128": ("03_MC_Heston", "main", "67108864x128"),
           "aes_noio_1024MB": ("01_AES", "noio", "1024MB"),
           "conv_rgb8_8K": ("02_Convolution", "synth", "rgb8_7680x4320"),
           "mc_4194304x64": ("03_MC_Heston", "main", "4194304x64")}
fp = []
for w, (proj, grp, cfg) in FPGA_OF.items():
    F = load(os.path.join(CAMP, proj, grp, cfg, "fpga", "rep*"), {"rep": -1})
    if len(F):
        F["workload"], F["platform"] = w, "fpga"
        fp.append(F)
SCF = pd.concat([SC] + fp, ignore_index=True)
SCF["passes"] = SCF.runs.fillna(SCF.repeat).fillna(1.0)
SCF = add_energy(SCF, 1.0, SCF.passes)     # J per pass; the same work per pass on both sides
for w in ("pf_131072", "mc_67108864x128"):
    E_rows += ratio_rows(SCF[SCF.workload == w], "campaign FPGA + CPU-scaling runs")
# time ratios from the same runs (consistency check for Table 4)
T_rows = []
for w, g in SCF.groupby("workload"):
    f = g[g.platform == "fpga"].t_compute_s
    for cfg, c in g[g.platform != "fpga"].groupby("platform"):
        r, lo, hi = boot_ratio(c.t_compute_s, f)
        T_rows.append(dict(source="campaign FPGA + CPU-scaling runs", workload=w, cpu_config=cfg,
                           speedup=r, ci_lo=lo, ci_hi=hi, n_cpu=len(c), n_fpga=len(f)))

# ======================================================= live pipelines (campaign, energy per frame)
for proj, grp, cfg, lab in (("05_LiveStream_Single", "pipe", "1080p", "live_single_1080p"),
                            ("06_LiveStream_Multi", "pipe", "all5", "live_multi_all5")):
    parts = []
    for plat in ("fpga", "cpu"):
        X = load(os.path.join(CAMP, proj, grp, cfg, plat, "rep*"), {"rep": -1})
        X["platform"] = plat
        parts.append(X)
    X = pd.concat(parts, ignore_index=True)
    X["workload"] = lab
    card = X.platform.eq("fpga").astype(float)
    for est in ("est", "exact"):
        if est == "est":
            X["E_socket_est"] = (X.P_pkg0 + X.P_dram0 + card * X.P_card) * X.window_s / X.frames
        else:
            X["E_socket_exact"] = (X.Eexact_pkg0 + X.Eexact_dram0 + card * X.Eexact_card) / X.frames
    f, c = X[X.platform == "fpga"], X[X.platform == "cpu"]
    for est in ("est", "exact"):
        r, lo, hi = boot_ratio(c[f"E_socket_{est}"], f[f"E_socket_{est}"])
        E_rows.append(dict(source="campaign", workload=lab, cpu_config="cpu (4 workers)", boundary="socket",
                           estimator=est, energy_adv=r, ci_lo=lo, ci_hi=hi, n_cpu=len(c), n_fpga=len(f),
                           min_samples=int(min(c.samples.min(), f.samples.min()))))
    SCF = pd.concat([SCF, X], ignore_index=True)

EC = pd.DataFrame(E_rows)
EC.to_csv(os.path.join(OUT, "rev_energy_ci.csv"), index=False)
pd.DataFrame(T_rows).to_csv(os.path.join(OUT, "rev_time_ci_scaling.csv"), index=False)

# =============================================================================== power breakdown
PB = []
for src, df in (("long-window", LW), ("scaling/campaign", SCF)):
    for (w, plat), g in df.groupby(["workload", "platform"]):
        d = dict(source=src, workload=w, platform=plat, n=len(g))
        for c in ("pkg0", "dram0", "pkg1", "dram1", "card"):
            d[f"{c}_W"] = g[f"P_{c}"].median()
            d[f"{c}_idle_W"] = g[f"Pidle_{c}"].median()
        PB.append(d)
PB = pd.DataFrame(PB)
PB.to_csv(os.path.join(OUT, "rev_power_breakdown.csv"), index=False)

# =============================================================================== idle card (pitfall 4)
IC = []
for w in ("pf_131072", "mc_67108864x128"):
    g = SCF[SCF.workload == w]
    f = g[g.platform == "fpga"]
    c = g[g.platform == "s0_c24"]
    p_idle_card = f.Pidle_card.median()             # card with this bitstream, before launch
    e_f = f.E_socket_exact.median()
    e_c = c.E_socket_exact.median()
    e_c_card = (c.E_socket_exact + p_idle_card * c.window_s / c.passes).median()
    IC.append(dict(workload=w, idle_card_W=p_idle_card, adv_without_card=e_c / e_f, adv_with_idle_card=e_c_card / e_f,
                   uplift_pct=(e_c_card / e_c - 1) * 100))
pd.DataFrame(IC).to_csv(os.path.join(OUT, "rev_idle_card.csv"), index=False)

# =============================================================================== idle-power sensitivity
SENS = []
for w in ("pf_131072", "mc_67108864x128"):
    g = SCF[SCF.workload == w]
    f, c = g[g.platform == "fpga"], g[g.platform == "s0_c24"]
    for dP in (-20, -10, 0, 10, 20):   # change of the host socket's idle power (both sides pay it)
        ef = (f.E_socket_exact + dP * f.window_s / f.passes).median()
        ec = c.E_socket_exact.median()      # busy CPU socket: its idle floor is already inside
        SENS.append(dict(workload=w, delta_idle_W=dP, energy_adv_socket=ec / ef))
for w in ("mc_4194304x64", "aes_noio", "conv_rgb8_8K"):
    g = LW[LW.workload == w]
    f, c = g[g.platform == "fpga"], g[g.platform == "s0_c24"]
    wpp_f = (f.paths * f.steps / 1e6) if w.startswith("mc") else (f.bytes / 1e9 if w.startswith("aes") else f.mpix)
    np_f = f.runs.fillna(f.repeat).fillna(1.0)
    for dP in (-20, -10, 0, 10, 20):
        ef = (f.E_socket_exact + dP * f.window_s / (np_f * wpp_f)).median()
        SENS.append(dict(workload=w, delta_idle_W=dP, energy_adv_socket=c.E_socket_exact.median() / ef))
pd.DataFrame(SENS).to_csv(os.path.join(OUT, "rev_idle_sensitivity.csv"), index=False)

# =============================================================================== slow CPU state (MC steady)
MS = load(os.path.join(IN, "results_mc_steady", "raw", "*", "s0_c24", "rep*"), {"cfg": -3, "rep": -1})
MS["msteps"] = MS.paths * MS.steps / 1e6
MS["ns_per_step_core"] = MS.t_compute_s / (MS.paths * MS.steps) * 1e9 * 24
ref = MS[MS.msteps >= 8].ns_per_step_core.min()
STEADY_NS = MS[MS.msteps >= 1000].ns_per_step_core.median()     # two largest sizes, 10 runs
MS["slow"] = MS.ns_per_step_core > 1.10 * STEADY_NS
MS["slow_vs_best"] = MS.ns_per_step_core / ref
MS[["cfg", "rep", "msteps", "t_compute_s", "ns_per_step_core", "slow", "Pidle_pkg0", "Pidle_pkg1",
    "Pidle_dram0", "loadavg1", "cpu_temp_pre", "P_pkg0", "t_launch"]].sort_values(["msteps", "rep"]) \
    .to_csv(os.path.join(OUT, "rev_slowstate.csv"), index=False)

# =============================================================================== memory traffic vs channels
aes = pd.read_csv(os.path.join(OUT, "aes.csv"))
conv = pd.read_csv(os.path.join(OUT, "conv.csv"))
MB = []
for plat in ("cpu_vaes", "cpu_aesni", "cpu_vaes_1t"):
    x = aes[(aes.group == "noio") & (aes.size_mb == 1024) & (aes.plat == plat)].iloc[0]
    for label, f in (("read + write", 2), ("read + write + write-allocate", 3)):
        MB.append(dict(kernel="AES-256-CTR 1 GiB", impl=plat, useful_GBps=x.gbps, traffic_model=label,
                       dram_GBps=x.gbps * f, pct_of_socket_peak=x.gbps * f / PEAK_BW_SOCKET * 100))
x = conv[(conv.variant == "rgb8") & (conv.w == 7680) & (conv.cpu == "cpu_avx512")].iloc[0]
px = x.w * x.h
for label, bpp in (("read + write", 3 + 9), ("read + write + write-allocate", 3 + 9 + 9)):
    bw = px * bpp / x.t_cpu / 1e9
    MB.append(dict(kernel="3x3 conv RGB8 8K", impl="cpu_avx512", useful_GBps=px * 3 / x.t_cpu / 1e9, traffic_model=label,
                   dram_GBps=bw, pct_of_socket_peak=bw / PEAK_BW_SOCKET * 100))
MBW = pd.DataFrame(MB)
MBW["socket_peak_GBps"] = PEAK_BW_SOCKET
MBW.to_csv(os.path.join(OUT, "rev_membw.csv"), index=False)

# =============================================================================== bound model
PCIE = 12.2       # GB/s per direction, measured single transfer (paper Section 6.3)
st = pd.read_csv(os.path.join(OUT, "stages.csv"))
mc = pd.read_csv(os.path.join(OUT, "mc_steady.csv"))
pf = pd.read_csv(os.path.join(OUT, "portfolio.csv"))
mpx_conv = x.w * x.h / 1e6
def ceil_overlap(h2d, d2h):          # transfers in both directions overlap with each other and the kernel
    return PCIE / max(h2d, d2h, 1e-30)
def ceil_serial(h2d, d2h, peak):       # copy in, compute, copy out, one after the other
    return 1.0 / (h2d / PCIE + d2h / PCIE + 1.0 / peak)
# bytes per unit of work; units: GB/s -> bytes per byte, Mpix/s -> bytes per pixel * 1e-3 (so GB/s / (B/px) = Gpix/s)
BM = [
    dict(workload="AES-256-CTR (1 GiB)", unit="GB/s", h2d=1.0, d2h=1.0, kernel_peak=2 * 19.2,
         fpga_measured=aes[(aes.group == "noio") & (aes.size_mb == 1024) & (aes.plat == "fpga")].gbps.iloc[0],
         cpu_measured=aes[(aes.group == "noio") & (aes.size_mb == 1024) & (aes.plat == "cpu_vaes")].gbps.iloc[0]),
    dict(workload="3x3 conv RGB8 8K (3 filters)", unit="Gpix/s", h2d=3.0, d2h=9.0, kernel_peak=0.300 * 64 / 3,
         fpga_measured=mpx_conv / x.t_fpga / 1e3, cpu_measured=mpx_conv / x.t_cpu / 1e3),
    dict(workload="MC Heston (67M x 128)", unit="G path-steps/s", h2d=0.0, d2h=0.0, kernel_peak=2 * 6 * 0.225,
         fpga_measured=mc.fpga_msteps_s.iloc[-1] / 1e3, cpu_measured=mc.msteps.iloc[-1] / mc.t_s0_c24.iloc[-1] / 1e3),
    dict(workload="Portfolio (1M trades)", unit="G path-steps/s", h2d=44.5e6 / 5.01e10, d2h=32.5e6 / 5.01e10,
         kernel_peak=4 * 8 * 0.25,
         fpga_measured=pf[pf.paths2 == 131072].fpga_msteps_s.iloc[0] / 1e3, cpu_measured=pf[pf.paths2 == 131072].cpu_msteps_s.iloc[0] / 1e3),
]
BM = pd.DataFrame(BM)
BM["pcie_ceiling_overlap"] = [ceil_overlap(a, b) for a, b in zip(BM.h2d, BM.d2h)]
BM["ceiling_serial"] = [ceil_serial(a, b, k) for a, b, k in zip(BM.h2d, BM.d2h, BM.kernel_peak)]
BM["attainable_overlap"] = BM[["kernel_peak", "pcie_ceiling_overlap"]].min(axis=1)
BM["bound"] = np.where(BM.pcie_ceiling_overlap < BM.kernel_peak, "PCIe", "kernel")
BM["max_speedup_overlap"] = BM.attainable_overlap / BM.cpu_measured
BM["max_speedup_serial"] = BM.ceiling_serial / BM.cpu_measured
BM["measured_speedup"] = BM.fpga_measured / BM.cpu_measured
BM["fpga_pct_of_serial"] = BM.fpga_measured / BM.ceiling_serial * 100
BM.to_csv(os.path.join(OUT, "rev_bound_model.csv"), index=False)

# =============================================================================== v1 -> v2 gains, consistent protocol
V = pd.read_csv(os.path.join(OUT, "v1_vs_v2_raw.csv"))
lw = pd.read_csv(os.path.join(IN, "results_energy", "energy_summary.csv"))
t_lw_cpu = lw[(lw.workload == "mc_4194304x64") & (lw.platform == "s0_c24")].t_compute_s.iloc[0]
t_lw_fpga = lw[(lw.workload == "mc_4194304x64") & (lw.platform == "fpga")].t_compute_s.iloc[0]
G = []
for _, r in V.iterrows():
    d = dict(project=r.project, point=r.point, v1_speedup=r.v1_speedup_compute,
             fpga_gain=r.fpga_improvement, cpu_gain=r.cpu_improvement, v2_speedup=r.v2_speedup_compute)
    if r.project == "03_MC_Heston" and "524288" in r.point:
        m = mc[mc.cfg == "524288x32"].iloc[0]
        d.update(fpga_gain=r.v1_fpga_compute / m.t_fpga, cpu_gain=r.v1_cpu_compute / m.t_s0_c24, v2_speedup=m.sp_s0_c24,
                 note="v2 from the steady-clock sweep")
    if r.project == "03_MC_Heston" and "4194304" in r.point:
        d.update(fpga_gain=r.v1_fpga_compute / t_lw_fpga, cpu_gain=r.v1_cpu_compute / t_lw_cpu,
                 v2_speedup=t_lw_cpu / t_lw_fpga, note="v2 from the long-window runs")
    G.append(d)
pd.DataFrame(G).to_csv(os.path.join(OUT, "rev_v1v2_gains.csv"), index=False)

# =============================================================================== Table 4: one protocol per row
T4 = []


def put(panel, wl, label, cfg, bnd, sp, e, n_c, n_f, smin, note="", es=None):
    T4.append(dict(panel=panel, workload=wl, label=label, cpu_config=cfg, boundary=bnd,
                   speedup=sp[0], sp_lo=sp[1], sp_hi=sp[2],
                   energy=e[0] if e else np.nan, e_lo=e[1] if e else np.nan, e_hi=e[2] if e else np.nan,
                   energy_server=es[0] if es else np.nan, es_lo=es[1] if es else np.nan, es_hi=es[2] if es else np.nan,
                   n_cpu=n_c, n_fpga=n_f, min_samples=smin, note=note))


CFG3 = [("s0_c24", "socket"), ("s0_t48", "socket"), ("s01_c48", "server"), ("s01_t96", "server")]
# A: short runs (campaign FPGA runs + CPU-scaling runs), identical work per pass on both sides
for wl, label in (("pf_131072", "Portfolio, 131,072 stage-2 paths"), ("mc_67108864x128", "MC Heston, 67M x 128"),
                  ("aes_noio_1024MB", "AES-256-CTR, 1 GiB in memory"), ("conv_rgb8_8K", "3x3 convolution, RGB8 8K")):
    g = SCF[SCF.workload == wl]
    f = g[g.platform == "fpga"]
    for cfg, bnd in CFG3:
        c = g[g.platform == cfg]
        smin = int(min(c.samples.min(), f.samples.min()))
        e = boot_ratio(c[f"E_{bnd}_exact"], f[f"E_{bnd}_exact"]) if smin >= 20 else None
        es = boot_ratio(c["E_server_exact"], f["E_server_exact"]) if smin >= 20 else None
        put("A", wl, label, cfg, bnd, boot_ratio(c.t_compute_s, f.t_compute_s), e, len(c), len(f), smin, es=es)
# B: long-window runs (energy protocol); time per unit of work = median pass time / work per pass
for wl, label in (("mc_4194304x64", "MC Heston, 4.2M x 64 (40 passes)"),
                  ("aes_noio", "AES-256-CTR (CPU 60 x 1 GiB; FPGA one 16 GiB stream)"),
                  ("conv_rgb8_8K", "3x3 convolution, RGB8 8K (200 passes)")):
    g = LW[LW.workload == wl]
    f = g[g.platform == "fpga"]
    for cfg, bnd in CFG3:
        c = g[g.platform == cfg]
        smin = int(min(c.samples.min(), f.samples.min()))
        put("B", wl, label, cfg, bnd, boot_ratio(c.t_compute_s / c.wpp, f.t_compute_s / f.wpp),
            boot_ratio(c[f"E_{bnd}_exact"], f[f"E_{bnd}_exact"]), len(c), len(f), smin,
            es=boot_ratio(c["E_server_exact"], f["E_server_exact"]))
# C: MC steady-clock sweep, sizes >= 8 M path-steps (time only; windows too short for energy)
big = mc[mc.msteps >= 8]
for cfg, col in (("s0_c24", "sp_s0_c24"), ("s01_t96", "sp_s01_t96")):
    put("C", "mc_sweep", "MC Heston, 10 sizes >= 8 M path-steps", cfg, "", (big[col].median(), big[col].min(), big[col].max()),
        None, 5, 5, 0, note="median and range over the 10 sizes")
# D: live pipelines (campaign; CPU pipeline with 4 workers); time per frame over the marked window
for wl in ("live_single_1080p", "live_multi_all5"):
    g = SCF[SCF.workload == wl]
    f, c = g[g.platform == "fpga"], g[g.platform == "cpu"]
    smin = int(min(c.samples.min(), f.samples.min()))
    put("D", wl, wl, "cpu (4 workers)", "socket", boot_ratio(c.window_s / c.frames, f.window_s / f.frames),
        boot_ratio(c.E_socket_exact, f.E_socket_exact), len(c), len(f), smin)
T4 = pd.DataFrame(T4)
T4.to_csv(os.path.join(OUT, "rev_table4.csv"), index=False)

# ---- mean power per domain in the measured window (counter integration / window length)
PT = []
for src, df, wls in (("short runs", SCF, ("pf_131072", "mc_67108864x128")),
                     ("long-window runs", LW, ("mc_4194304x64", "aes_noio", "conv_rgb8_8K"))):
    for wl in wls:
        for plat in ("fpga", "s0_c24", "s0_t48", "s01_t96"):
            g = df[(df.workload == wl) & (df.platform == plat)]
            d = dict(source=src, workload=wl, platform=plat, n=len(g))
            for c in ("pkg0", "dram0", "pkg1", "dram1", "card"):
                d[c] = (g[f"Eexact_{c}"] / g.window_s).median()
            PT.append(d)
PT = pd.DataFrame(PT)
PT.to_csv(os.path.join(OUT, "rev_power_table.csv"), index=False)

# ---- MC energy excluding the CPU clock ramp (steady part of the long-window runs)
def steady_energy(df, doms, skip=2.0):
    out = []
    for _, r in df.iterrows():
        d = r["_dir"]
        p = pd.read_csv(os.path.join(d, "power.csv"))
        t0 = r.t0 + (skip if r.platform != "fpga" else 0.0)
        e = sum(integrate(p, t0, r.t1, f"{x}_w") for x in doms)
        out.append(e / (r.t1 - t0) * r.t_compute_s / r.wpp)       # steady power x median pass time / work
    return np.array(out)


g = LW[LW.workload == "mc_4194304x64"]
STEADY_MC = {}
for cfg, doms in (("s0_c24", S0), ("s01_t96", S01)):
    ec = steady_energy(g[g.platform == cfg], doms)
    ef = steady_energy(g[g.platform == "fpga"], doms + ["card"])
    STEADY_MC[cfg] = boot_ratio(ec, ef)
json.dump({k: list(map(float, v)) for k, v in STEADY_MC.items()}, open(os.path.join(OUT, "rev_mc_steady_energy.json"), "w"), indent=1)

# ---- dynamic (above-idle) energy: settled idle floors
FLOOR = {c: np.nanpercentile(pd.concat([SCF[f"Pidle_{c}"], LW[f"Pidle_{c}"]]), 5) for c in ("pkg0", "dram0", "pkg1", "dram1")}
DYN = []
for wl in ("pf_131072", "mc_67108864x128"):
    g = SCF[SCF.workload == wl]
    f, c = g[g.platform == "fpga"], g[g.platform == "s0_c24"]
    card_idle = f.Pidle_card.median()
    fl = FLOOR["pkg0"] + FLOOR["dram0"]
    ec = c.E_socket_exact - fl * c.window_s / c.passes
    ef = f.E_socket_exact - (fl + card_idle) * f.window_s / f.passes
    DYN.append(dict(workload=wl, floor_socket_W=fl, card_idle_W=card_idle,
                    P_cpu_socket_W=(c.E_socket_exact * c.passes / c.window_s).median(),
                    P_fpga_socket_W=(f.E_socket_exact * f.passes / f.window_s).median(),
                    total_adv=(c.E_socket_exact.median() / f.E_socket_exact.median()),
                    dynamic_adv=ec.median() / ef.median()))
DYN = pd.DataFrame(DYN)
DYN.to_csv(os.path.join(OUT, "rev_dynamic_energy.csv"), index=False)

# ---- slow-state summary
SS = MS.groupby("slow").agg(n=("rep", "size"), P_pkg0=("P_pkg0", "median"), Pidle_pkg0=("Pidle_pkg0", "median"),
                            Pidle_pkg1=("Pidle_pkg1", "median"), temp=("cpu_temp_pre", "median"),
                            load=("loadavg1", "median"), slow_vs_steady=("ns_per_step_core", "median"))
SS["slow_vs_steady"] = SS.slow_vs_steady / STEADY_NS
SS.to_csv(os.path.join(OUT, "rev_slowstate_summary.csv"))

# =============================================================================== run counts
R = pd.read_csv(os.path.join(IN, "runs_v2.csv"))
cnt = dict(campaign_total=len(R), campaign_ok=int((R.ok == 1).sum()),
           campaign_profiled=int((R.profiled == 1).sum()), campaign_cold=int((R.cold == 1).sum()),
           campaign_timed_ok=int(((R.ok == 1) & (R.profiled != 1) & (R.cold != 1)).sum()),
           long_window=len(LW), scaling_cpu=len(SC), mc_steady_cpu=len(MS))
json.dump(cnt, open(os.path.join(OUT, "rev_run_counts.json"), "w"), indent=1)

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)
print("== energy ratios with CIs\n", EC.round(3).to_string(index=False))
print("\n== time (scaling runs)\n", pd.DataFrame(T_rows).round(3).to_string(index=False))
print("\n== idle card\n", pd.DataFrame(IC).round(3).to_string(index=False))
print("\n== memory traffic\n", MBW.round(2).to_string(index=False))
print("\n== bound model\n", BM.round(3).to_string(index=False))
print("\n== v1->v2\n", pd.DataFrame(G).round(3).to_string(index=False))
print("\n== run counts", cnt)
print("\n== TABLE 4\n", T4.drop(columns=["label"]).round(3).to_string(index=False))
print("\n== dynamic\n", DYN.round(3).to_string(index=False))
print("\n== slow state (steady ns/step/core = %.2f)\n" % STEADY_NS, SS.round(3).to_string())
print("\n== MC long-window energy without the first 2 s (CPU ramp)", STEADY_MC)
print("\n== power table\n", PT.round(1).to_string(index=False))
print("\n== idle floors", {k: round(v, 2) for k, v in FLOOR.items()})
