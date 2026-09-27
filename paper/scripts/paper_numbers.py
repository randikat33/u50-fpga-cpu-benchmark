#!/usr/bin/env python3
"""
paper_numbers.py - every number used in the paper, computed from the raw campaign data.

Inputs  (paper/inputs/):
  runs_v2.csv                    one row per campaign run (1164 v2 runs)
  campaign_raw_small/v2/raw/...  meta.json + power.csv + stdout.log of every campaign run
  results_scaling/               CPU scaling (4 CPU configurations)
  results_energy/                long-window energy runs
  results_mc_steady/             MC size sweep, CPU at sustained clock (final MC times)
  ablation*, fpga_impl_v2.csv, v1_vs_v2.csv
Outputs (paper/data/*.csv) - read by make_paper_figures.py and quoted in the LaTeX text.
"""
import glob, json, os, re, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, "..", "inputs")
OUT = os.path.join(HERE, "..", "data")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_scaling import one_run  # noqa: E402

R = pd.read_csv(os.path.join(IN, "runs_v2.csv"))
R = R[(R.ok == 1) & (R.profiled != 1) & (R.cold != 1)]
med = lambda df, col: df[col].median()


def boot_ci(num, den, n=4000, seed=1):
    """95% bootstrap CI of median(num)/median(den) (independent resampling)"""
    rng = np.random.default_rng(seed)
    num, den = np.asarray(num, float), np.asarray(den, float)
    s = [np.median(rng.choice(num, len(num))) / np.median(rng.choice(den, len(den))) for _ in range(n)]
    return np.percentile(s, 2.5), np.percentile(s, 97.5)


def res(txt, k):
    m = re.search(rf"^RESULT {k}=(\S+)", txt, re.M)
    if not m:
        return np.nan
    try:
        return float(m.group(1))
    except ValueError:
        return m.group(1)


# ---------------------------------------------------------------- AES
rows = []
for grp in ("enc", "noio"):
    A = R[(R.project == "01_AES") & (R.group == grp)]
    for cfg, g in A.groupby("config"):
        mb = float(cfg.replace("MB", ""))
        for plat, h in g.groupby("plat"):
            t = h.t_compute_s.values
            rows.append(dict(group=grp, size_mb=mb, plat=plat, n=len(t), t_compute_s=np.median(t),
                             gbps=np.median(h.bytes / h.t_compute_s) / 1e9,
                             e2e_gbps=np.median(h.e2e_gbps) if "e2e_gbps" in h else np.nan))
aes = pd.DataFrame(rows).sort_values(["group", "size_mb", "plat"])
aes.to_csv(os.path.join(OUT, "aes.csv"), index=False)
sp = []
for (grp, mb), g in aes.groupby(["group", "size_mb"]):
    A = R[(R.project == "01_AES") & (R.group == grp) & (R.config == f"{int(mb)}MB")]
    f, c = A[A.plat == "fpga"].t_compute_s, A[A.plat == "cpu_vaes"].t_compute_s
    if len(f) and len(c):
        lo, hi = boot_ci(c, f)
        sp.append(dict(group=grp, size_mb=mb, speedup=c.median() / f.median(), ci_lo=lo, ci_hi=hi))
pd.DataFrame(sp).to_csv(os.path.join(OUT, "aes_speedup.csv"), index=False)

# ---------------------------------------------------------------- Convolution
C = R[(R.project == "02_Convolution") & (R.group == "synth")]
rows = []
for cfg, g in C.groupby("config"):
    var, size = cfg.split("_")
    w, h = map(int, size.split("x"))
    f = g[g.plat == "fpga"].t_compute_s
    for plat in ("cpu_avx512", "cpu_opencv"):
        c = g[g.plat == plat].t_compute_s
        if len(f) and len(c):
            lo, hi = boot_ci(c, f)
            rows.append(dict(variant=var, w=w, h=h, mpix=w * h / 1e6, cpu=plat, t_fpga=f.median(), t_cpu=c.median(),
                             fpga_mpix_s=w * h / 1e6 / f.median(), cpu_mpix_s=w * h / 1e6 / c.median(),
                             speedup=c.median() / f.median(), ci_lo=lo, ci_hi=hi, n=len(f)))
pd.DataFrame(rows).sort_values(["variant", "mpix", "cpu"]).to_csv(os.path.join(OUT, "conv.csv"), index=False)

# ---------------------------------------------------------------- MC (steady-clock sweep = final)
rows = []
for f in glob.glob(os.path.join(IN, "results_mc_steady", "raw", "*", "*", "rep*", "stdout.log")):
    cfg, plat, rep = f.split(os.sep)[-4:-1]
    t = open(f).read()
    rows.append(dict(cfg=cfg, plat=plat, rep=rep, t=res(t, "t_compute_s"), tmin=res(t, "t_compute_min_s"),
                     price=res(t, "price"), h=res(t, "mom_hash")))
M = pd.DataFrame(rows)
out = []
for cfg, g in M.groupby("cfg"):
    p, s = map(int, cfg.split("x"))
    f = g[g.plat == "fpga"].t
    d = dict(cfg=cfg, paths=p, steps=s, msteps=p * s / 1e6, t_fpga=f.median(), fpga_cv=f.std() / f.mean() * 100,
             hash_identical=int(g.h.nunique() == 1), price=g.price.iloc[0])
    for k in ("s0_c24", "s01_t96"):
        c = g[g.plat == k].t
        lo, hi = boot_ci(c, f)
        d.update({f"t_{k}": c.median(), f"sp_{k}": c.median() / f.median(), f"lo_{k}": lo, f"hi_{k}": hi,
                  f"cv_{k}": c.std() / c.mean() * 100})
    d["fpga_msteps_s"] = p * s / 1e6 / d["t_fpga"]
    out.append(d)
mc = pd.DataFrame(out).sort_values("msteps")
mc.to_csv(os.path.join(OUT, "mc_steady.csv"), index=False)
# campaign MC (3 runs, no warm-up) for the methodology comparison
Mc = R[(R.project == "03_MC_Heston") & (R.group == "main")]
camp = (Mc.groupby(["config", "plat"]).t_compute_s.median().unstack())
camp["sp_campaign"] = camp.cpu_avx512 / camp.fpga
camp.reset_index().rename(columns={"config": "cfg"}).to_csv(os.path.join(OUT, "mc_campaign.csv"), index=False)
# mc_warm (2 warm-up passes) for the same comparison
rows = []
for f in glob.glob(os.path.join(IN, "results_mc_warm", "raw", "*", "*", "rep*", "stdout.log")):
    cfg, plat, rep = f.split(os.sep)[-4:-1]
    rows.append(dict(cfg=cfg, plat=plat, t=res(open(f).read(), "t_compute_s")))
W = pd.DataFrame(rows).groupby(["cfg", "plat"]).t.median().unstack()
W["sp_warm2"] = W.s0_c24 / W.fpga
W.reset_index().to_csv(os.path.join(OUT, "mc_warm2.csv"), index=False)

# ---------------------------------------------------------------- Portfolio
P = R[(R.project == "04_Portfolio") & (R.group == "main")]
rows = []
for cfg, g in P.groupby("config"):
    f, c = g[g.plat == "fpga"].t_compute_s, g[g.plat == "cpu_avx512"].t_compute_s
    s = g[g.plat == "cpu_scalar"].t_compute_s
    lo, hi = boot_ci(c, f)
    rows.append(dict(cfg=cfg, paths2=int(cfg.split("_")[1]), t_fpga=f.median(), t_cpu=c.median(),
                     t_scalar=s.median() if len(s) else np.nan, speedup=c.median() / f.median(), ci_lo=lo, ci_hi=hi,
                     fpga_msteps_s=np.median(g[g.plat == "fpga"].msteps_per_s),
                     cpu_msteps_s=np.median(g[g.plat == "cpu_avx512"].msteps_per_s),
                     price_identical=int(g.price_sum.nunique() == 1)))
pd.DataFrame(rows).sort_values("paths2").to_csv(os.path.join(OUT, "portfolio.csv"), index=False)

# ---------------------------------------------------------------- LiveStream
L = R[R.project == "05_LiveStream_Single"]
rows = []
for q in ("240p", "360p", "480p", "720p", "1080p"):
    d = dict(quality=q)
    for plat in ("fpga", "cpu"):
        g = L[(L.group == "pipe") & (L.config == q) & (L.plat == plat)]
        d[f"fps_{plat}"] = g.fps.median()
        d[f"ttff_{plat}"] = g.t_first_frame_s.median()
        d[f"fps_steady_{plat}"] = np.median(g.frames / (g.t_window_s - g.t_first_frame_s))
    ro = L[(L.group == "ro") & (L.config == q)]
    d["ro_fpga"] = ro[ro.plat == "fpga"].fps.median()
    for k in ("cpu_w1c24", "cpu_w4c24", "cpu_w24c1"):
        d[f"ro_{k}"] = ro[ro.plat == k].fps.median()
    d["ro_cpu_best"] = max(d["ro_cpu_w1c24"], d["ro_cpu_w4c24"], d["ro_cpu_w24c1"])
    rows.append(d)
ls = pd.DataFrame(rows)
ls["speedup_window"] = ls.fps_fpga / ls.fps_cpu
ls["speedup_steady"] = ls.fps_steady_fpga / ls.fps_steady_cpu
ls["speedup_ro"] = ls.ro_fpga / ls.ro_cpu_best
ls.to_csv(os.path.join(OUT, "live_single.csv"), index=False)
ab = L[L.group.isin(["ablation", "ablation_pipe"])]
M6 = R[R.project == "06_LiveStream_Multi"]
# 06 two-CU ablation (measured after the campaign)
abl6 = {}
for grp in ("ablation", "ablation_pipe"):
    v = [res(open(f).read(), "fps") for f in glob.glob(os.path.join(IN, grp, "all5", "fpga_2cu", "rep*", "stdout.log"))]
    abl6[grp] = (np.median(v), np.min(v), np.max(v), len(v))
cu = pd.DataFrame([
    dict(design="05 single-output (1080p)", stage="resize only", cus="2 (selected)", fps=L[(L.group == "ro") & (L.config == "1080p") & (L.plat == "fpga")].fps.median()),
    dict(design="05 single-output (1080p)", stage="resize only", cus="4", fps=ab[(ab.group == "ablation")].fps.median()),
    dict(design="05 single-output (1080p)", stage="full pipeline", cus="2 (selected)", fps=L[(L.group == "pipe") & (L.config == "1080p") & (L.plat == "fpga")].fps.median()),
    dict(design="05 single-output (1080p)", stage="full pipeline", cus="4", fps=ab[(ab.group == "ablation_pipe")].fps.median()),
    dict(design="06 five-rung ladder", stage="resize only", cus="1 (selected)", fps=M6[(M6.group == "ro") & (M6.plat == "fpga_b1")].fps.median()),
    dict(design="06 five-rung ladder", stage="resize only", cus="2", fps=abl6["ablation"][0]),
    dict(design="06 five-rung ladder", stage="full pipeline", cus="1 (selected)", fps=M6[(M6.group == "pipe") & (M6.plat == "fpga")].fps.median()),
    dict(design="06 five-rung ladder", stage="full pipeline", cus="2", fps=abl6["ablation_pipe"][0]),
])
cu.to_csv(os.path.join(OUT, "cu_ablation.csv"), index=False)
lm = dict(pipe_fpga=M6[(M6.group == "pipe") & (M6.plat == "fpga")].fps.median(),
          pipe_cpu=M6[(M6.group == "pipe") & (M6.plat == "cpu")].fps.median(),
          **{f"ro_{k}": M6[(M6.group == "ro") & (M6.plat == k)].fps.median()
             for k in ("fpga_b1", "fpga_b2", "fpga_b4", "cpu_w1c24", "cpu_w4c24", "cpu_w24c1")},
          ro_fpga_2cu=abl6["ablation"][0], pipe_fpga_2cu=abl6["ablation_pipe"][0])
pd.DataFrame([lm]).to_csv(os.path.join(OUT, "live_multi.csv"), index=False)

# ---------------------------------------------------------------- FPGA time decomposition
pick = [("AES-256 (1 GiB, in memory)", "01_AES", "noio", "1024MB"),
        ("3x3 conv. (RGB8, 7680x4320)", "02_Convolution", "synth", "rgb8_7680x4320"),
        ("MC Heston (4.2M x 64)", "03_MC_Heston", "main", "4194304x64"),
        ("Portfolio valuation (131,072)", "04_Portfolio", "main", "p2_131072")]
rows = []
for lab, p, g, c in pick:
    x = R[(R.project == p) & (R.group == g) & (R.config == c) & (R.plat == "fpga")]
    h2d, k, d2h = x.t_h2d_s.median(), x.t_kernel_s.median(), x.t_d2h_s.median()
    tot = h2d + k + d2h
    rows.append(dict(workload=lab, h2d=h2d / tot, kernel=k / tot, d2h=d2h / tot, t_compute=x.t_compute_s.median(),
                     serial_sum=tot, overlap=tot / x.t_compute_s.median()))
pd.DataFrame(rows).to_csv(os.path.join(OUT, "stages.csv"), index=False)

# ---------------------------------------------------------------- energy (final)
# long windows: MC 4.2M, AES, Conv (energy_long); Portfolio and MC 67M from campaign runs (long windows)
E = pd.read_csv(os.path.join(IN, "results_energy", "energy_summary.csv"))
rows = []
for w, lab in (("mc_4194304x64", "MC Heston 4.2M x 64"), ("aes_noio", "AES-256 in memory"), ("conv_rgb8_8K", "3x3 conv. RGB8 8K")):
    g = E[E.workload == w].set_index("platform")
    rows.append(dict(workload=lab, unit=g.unit.iloc[0], source="long-window runs",
                     E_fpga_socket=g.loc["fpga", "E_socket_per_unit"], E_cpu_socket=g.loc["s0_c24", "E_socket_per_unit"],
                     adv_socket=g.loc["s0_c24", "energy_adv_socket"],
                     E_fpga_server=g.loc["fpga", "E_server_per_unit"], E_cpu_server96=g.loc["s01_t96", "E_server_per_unit"],
                     adv_server96=g.loc["s01_t96", "energy_adv_server"],
                     P_fpga_sys=g.loc["fpga", "P_socket_W"], P_cpu_socket=g.loc["s0_c24", "P_socket_W"],
                     P_fpga_server=g.loc["fpga", "P_server_W"], P_cpu_server96=g.loc["s01_t96", "P_server_W"],
                     min_samples=int(g.samples_min.min())))
V = pd.read_csv(os.path.join(IN, "results_scaling", "scaling_vs_fpga.csv"))
for w, lab in (("pf_131072", "Portfolio valuation 131,072"), ("mc_67108864x128", "MC Heston 67M x 128")):
    a, b = V[(V.workload == w) & (V.cpu_config == "s0_c24")].iloc[0], V[(V.workload == w) & (V.cpu_config == "s01_t96")].iloc[0]
    rows.append(dict(workload=lab, unit="J per run", source="campaign FPGA + scaling CPU runs",
                     E_fpga_socket=a.E_fpga_socket_J, E_cpu_socket=a.E_cpu_socket_J, adv_socket=a.energy_adv_socket,
                     E_fpga_server=b.E_fpga_server_J, E_cpu_server96=b.E_cpu_server_J, adv_server96=b.energy_adv_server,
                     P_fpga_server=b.P_fpga_server_card_W, P_cpu_server96=b.P_cpu_server_W))
# LiveStream pipelines: energy per frame, conservative boundary (CPU socket without card)
for p, g_, c_, lab in (("05_LiveStream_Single", "pipe", "1080p", "Live single 1080p pipeline"),
                       ("06_LiveStream_Multi", "pipe", "all5", "Live multi 5-rung pipeline")):
    x = R[(R.project == p) & (R.group == g_) & (R.config == c_)]
    f, c = x[x.plat == "fpga"], x[x.plat == "cpu"]
    ef = ((f.E_pkg0_J + f.E_dram0_J + f.E_card_J) / f.frames).median()
    ec = ((c.E_pkg0_J + c.E_dram0_J) / c.frames).median()
    rows.append(dict(workload=lab, unit="J per frame", source="campaign", E_fpga_socket=ef, E_cpu_socket=ec, adv_socket=ec / ef))
pd.DataFrame(rows).to_csv(os.path.join(OUT, "energy_final.csv"), index=False)

# ---------------------------------------------------------------- scaling (time) incl. MC steady
S = pd.read_csv(os.path.join(IN, "results_scaling", "scaling_summary.csv"))
S.to_csv(os.path.join(OUT, "cpu_scaling.csv"), index=False)
V.to_csv(os.path.join(OUT, "scaling_vs_fpga.csv"), index=False)
Es = E.copy()
Es.to_csv(os.path.join(OUT, "energy_long_summary.csv"), index=False)

# ---------------------------------------------------------------- CPU clock-ramp power trace
d = os.path.join(IN, "results_energy", "raw", "mc_4194304x64", "s0_c24", "rep2")
m = json.load(open(os.path.join(d, "meta.json")))
p = pd.read_csv(os.path.join(d, "power.csv"))
p["t_s"] = p.t_unix - m["t_start_marker"]
p[(p.t_s > -1.0) & (p.t_s < m["t_end_marker"] - m["t_start_marker"] + 1.0)][["t_s", "pkg0_w", "pkg1_w", "dram0_w"]] \
    .to_csv(os.path.join(OUT, "ramp_trace.csv"), index=False)
d2 = os.path.join(IN, "results_mc_steady", "raw", "4194304x64", "s0_c24", "rep1")
m2 = json.load(open(os.path.join(d2, "meta.json")))
p2 = pd.read_csv(os.path.join(d2, "power.csv"))
p2["t_s"] = p2.t_unix - m2["t_launch"]
p2[(p2.t_s > -1.0) & (p2.t_s < m2["t_end_marker"] - m2["t_launch"] + 1.0)][["t_s", "pkg0_w"]] \
    .to_csv(os.path.join(OUT, "slowstate_trace.csv"), index=False)
json.dump(dict(fast_start=m["t_start_marker"], slow_warmup_end=m2["t_start_marker"] - m2["t_launch"],
               slow_end=m2["t_end_marker"] - m2["t_launch"]), open(os.path.join(OUT, "trace_meta.json"), "w"))
# per-rep CPU ns/path-step for the bimodality statement
rows = []
for src, root in (("steady", "results_mc_steady"), ("warm2", "results_mc_warm")):
    for f in glob.glob(os.path.join(IN, root, "raw", "*", "s0_c24", "rep*", "stdout.log")):
        cfg = f.split(os.sep)[-4]
        pth, st = map(int, cfg.split("x"))
        t = open(f).read()
        rows.append(dict(src=src, cfg=cfg, msteps=pth * st / 1e6, ns=res(t, "t_compute_s") / (pth * st) * 1e9 * 24))
pd.DataFrame(rows).to_csv(os.path.join(OUT, "mc_ns_per_step.csv"), index=False)

# ---------------------------------------------------------------- resources / timing
I = pd.read_csv(os.path.join(IN, "fpga_impl_v2.csv"))
I = I[~I.project.str.startswith("xclbin")]
cols = ["project", "kernel_clock_mhz", "kernel_WNS_ns"] + [f"kernels_total.{k}_pct" for k in ("LUT", "REG", "BRAM", "URAM", "DSP")]
I[cols].to_csv(os.path.join(OUT, "resources.csv"), index=False)

# ---------------------------------------------------------------- v1 vs v2
V1 = pd.read_csv(os.path.join(IN, "v1_vs_v2.csv"))
V1.to_csv(os.path.join(OUT, "v1_vs_v2_raw.csv"), index=False)
print("numbers ->", OUT)
for f in sorted(os.listdir(OUT)):
    print("  ", f)
