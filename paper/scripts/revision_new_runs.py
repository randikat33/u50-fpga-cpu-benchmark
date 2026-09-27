#!/usr/bin/env python3
"""
revision_new_runs.py - analysis of three revision steps of revision_measurements.sh:

  1. mc_telemetry  the slow CPU state: per-core clock (turbostat Bzy_MHz), busy share and other processes
                   on the benchmark's cores, slow vs. normal processes
  2. native        CPU built with fused multiply-add (-ffp-contract=fast) vs. the paper build:
                   speed gain, new FPGA speedup, price differences, FMA instruction counts
  3. video         both pipelines with 5 CPU worker/thread settings and a same-session FPGA reference:
                   fastest / most efficient CPU setting, new speedup and energy per frame; decode speed

Input : paper/inputs/results_revision/  (unpacked results_revision_<date>.tar.gz)
Output: paper/data/rev_telemetry_runs.csv, rev_telemetry_summary.csv, rev_native.csv,
        rev_video_tuning.csv, rev_video_decode.csv, rev_new_runs_summary.json
Every part is skipped with a message if its runs are not there yet.
"""
import glob, json, os, re, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlib import load  # noqa: E402

REV = os.environ.get("REV_IN", os.path.join(HERE, "..", "inputs", "results_revision"))
OUT = os.environ.get("REV_OUT", os.path.join(HERE, "..", "data"))
RAW, TEL = os.path.join(REV, "raw"), os.path.join(REV, "telemetry")
SLOW = 1.10            # a process is 'slow' if its pass time is > 10% above the fastest process of its size
# paper values (Table 5) used to restate the FPGA speedup against the FMA build
PAPER_SPEEDUP = {"67108864x128": 1.975, "4194304x64": 1.993, "pf_131072": 4.163}
SUMMARY = {}


def boot_ratio(num, den, n=4000, seed=1):
    rng = np.random.default_rng(seed)
    num, den = np.asarray(num, float), np.asarray(den, float)
    s = [np.median(rng.choice(num, len(num))) / np.median(rng.choice(den, len(den))) for _ in range(n)]
    return np.median(num) / np.median(den), np.percentile(s, 2.5), np.percentile(s, 97.5)


def cpu_list(spec):
    out = []
    for part in str(spec).split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return set(out)


# ------------------------------------------------------------------------------ turbostat / census
def read_turbostat(d):
    """per-CPU turbostat rows with the index of their 0.5 s interval (and a time stamp if recorded)"""
    f = os.path.join(d, "turbostat.txt")
    if not os.path.exists(f):
        return None
    rows, hdr, k = [], None, -1
    for line in open(f, errors="replace"):
        fs = line.rstrip("\n").split("\t")
        if "CPU" in fs and "Busy%" in fs:
            hdr = fs
            continue
        if hdr is None or len(fs) < 6:
            continue
        r = dict(zip(hdr, fs))
        if r.get("CPU") == "-":                     # summary row: starts a new interval
            k += 1
            continue
        try:
            rows.append(dict(iv=k, cpu=int(r["CPU"]), busy=float(r["Busy%"]), bzy_mhz=float(r["Bzy_MHz"]),
                             ipc=float(r.get("IPC", "nan") or "nan"), irq=float(r.get("IRQ", "nan") or "nan"),
                             t=float(r["Time_Of_Day_Seconds"]) if "Time_Of_Day_Seconds" in r else np.nan))
        except (KeyError, ValueError):
            continue
    return pd.DataFrame(rows)


def read_census(d):
    f = os.path.join(d, "census.txt")
    if not os.path.exists(f):
        return None
    rows, t = [], None
    for line in open(f, errors="replace"):
        if line.startswith("## "):
            t = float(line.split()[1])
            continue
        fs = line.split()
        if t is None or len(fs) < 6 or line.startswith("UNCORE"):
            continue
        try:
            rows.append(dict(t=t, pid=int(fs[0]), tid=int(fs[1]), psr=int(fs[2]), pcpu=float(fs[3]),
                             comm=" ".join(fs[5:])))
        except ValueError:
            continue
    return pd.DataFrame(rows)


def telemetry():
    """slow vs. normal MC processes: busy clock of the 24 benchmark cores while they compute,
    activity on their SMT siblings, other processes on socket 0 (census) and package power"""
    X = load(os.path.join(RAW, "mc_telemetry", "*", "*", "rep*"), {"size": -3, "config": -2, "rep": -1})
    if X.empty:
        print("[telemetry] no mc_telemetry runs yet")
        return
    X["pass_rel"] = X.t_compute_s / X.groupby("size").t_compute_s.transform("min")
    X["slow"] = X.pass_rel > SLOW
    out = []
    quiet = ("mc_cpu", "turbostat", "python3", "ps", "awk", "sleep", "bash", "cat", "numactl", "taskset")
    for _, r in X.iterrows():
        d = os.path.join(TEL, "mc_telemetry", r["size"], r["config"], r["rep"])
        cores = cpu_list(r.cores or "0-23")
        sibs = {c + 48 for c in cores}
        rec = dict(size=r["size"], rep=r["rep"], t_compute_s=r.t_compute_s, pass_rel=r.pass_rel, slow=r.slow,
                   P_pkg0=r.Eexact_pkg0 / r.window_s)
        ts = read_turbostat(d)
        if ts is not None and len(ts):
            c = ts[ts.cpu.isin(cores)].groupby("iv").agg(busy=("busy", "median"), mhz=("bzy_mhz", "median"),
                                                          mhz_min=("bzy_mhz", "min"))
            act = c[c.busy > 90]                    # intervals in which the benchmark cores compute
            sib = ts[ts.cpu.isin(sibs) & ts.iv.isin(act.index)].groupby("iv").busy.max()
            rec.update(active_intervals=len(act), bzy_mhz=act.mhz.median(), bzy_mhz_min_core=act.mhz_min.median(),
                       busy_pct=act.busy.median(), sibling_busy_max=sib.max() if len(sib) else np.nan,
                       sibling_busy_median=sib.median() if len(sib) else np.nan)
            # per-core view: the core with the lowest IPC while all cores compute (straggler candidate)
            full = c[c.busy > 95].index
            per = ts[ts.cpu.isin(cores) & ts.iv.isin(full)].groupby("cpu").agg(
                ipc=("ipc", "median"), mhz=("bzy_mhz", "median"), irq=("irq", "median"))
            if len(per):
                lo = per.ipc.idxmin()
                rec.update(low_ipc_cpu=int(lo), low_ipc=per.ipc[lo], ipc_median=per.ipc.median(),
                           low_ipc_cpu_mhz=per.mhz[lo], mhz_median_cores=per.mhz.median(),
                           low_ipc_cpu_irq=per.irq[lo], irq_median=per.irq.median(),
                           low_ipc_sibling_busy=ts[(ts.cpu == lo + 48) & ts.iv.isin(full)].busy.mean())
            hit = act[act.mhz >= 2450].index             # clock ramp: time until the busy clock reaches turbo
            rec["ramp_s"] = (hit.min() - act.index.min()) * 0.53 if len(hit) else np.nan
        cs = read_census(d)
        if cs is not None and len(cs):
            w = cs[(cs.t >= r.t0) & (cs.t <= r.t1) & cs.psr.isin(cores | sibs)]
            other = w[~w.comm.str.startswith(quiet)]
            rec.update(other_procs=";".join(sorted(set(other.comm)))[:200],
                       other_samples=len(other), census_samples=int(((cs.t >= r.t0) & (cs.t <= r.t1)).sum()))
        out.append(rec)
    T = pd.DataFrame(out)
    T.to_csv(os.path.join(OUT, "rev_telemetry_runs.csv"), index=False)
    cols = [c for c in ("pass_rel", "bzy_mhz", "bzy_mhz_min_core", "busy_pct", "sibling_busy_max", "P_pkg0",
                        "low_ipc", "ipc_median", "low_ipc_cpu_mhz", "mhz_median_cores", "low_ipc_sibling_busy",
                        "ramp_s") if c in T]
    S = T.groupby("slow")[cols].median()
    S.insert(0, "n", T.groupby("slow").size())
    S = S.reset_index()
    S.to_csv(os.path.join(OUT, "rev_telemetry_summary.csv"), index=False)
    n_slow = int(T.slow.sum())
    verdict = f"no slow process in {len(T)} runs"
    if n_slow and "bzy_mhz" in T:
        m = T.groupby("slow").bzy_mhz.median()
        ratio = m.get(True, np.nan) / m.get(False, np.nan)
        sib = T.groupby("slow").sibling_busy_max.median()
        lowipc = T.groupby("slow").apply(lambda g: (g.low_ipc / g.ipc_median).median())
        if lowipc.get(True, 1) < 0.9 and lowipc.get(False, 1) > 0.95:
            verdict = (f"straggler core: in every slow process one core ran at {lowipc[True]:.0%} of the others' IPC "
                       f"(normal processes {lowipc[False]:.0%}); the other cores wait for it at each pass")
        elif ratio < 0.95:
            verdict = (f"lower clock: slow processes computed at {m[True]:.0f} MHz, normal ones at "
                       f"{m[False]:.0f} MHz ({ratio:.0%})")
        elif sib.get(True, 0) > sib.get(False, 0) + 20:
            verdict = "straggler: clock unchanged, SMT siblings of the benchmark cores busy in slow runs"
        else:
            verdict = f"clock ({ratio:.0%} of normal) and census do not explain the slow state"
    SUMMARY["telemetry"] = dict(runs=len(T), slow=n_slow, verdict=verdict)
    print("[telemetry]", SUMMARY["telemetry"])
    print(S.round(3).to_string(index=False))


# ------------------------------------------------------------------------------ FMA build
def native():
    X = load(os.path.join(RAW, "native", "*", "*", "rep*"), {"work": -3, "config": -2, "rep": -1})
    if X.empty:
        print("[native] no native runs yet")
        return
    rows = []
    for w, g in X.groupby("work"):
        p, f = g[g.config.str.endswith("paper")], g[g.config.str.endswith("fma")]
        if p.empty or f.empty:
            continue
        gain, lo, hi = boot_ratio(p.t_compute_s, f.t_compute_s)          # > 1: FMA build faster
        r = dict(work=w, n_paper=len(p), n_fma=len(f), t_paper=p.t_compute_s.median(), t_fma=f.t_compute_s.median(),
                 fma_gain=gain, gain_lo=lo, gain_hi=hi)
        if w in PAPER_SPEEDUP:
            r["fpga_speedup_paper_build"] = PAPER_SPEEDUP[w]
            r["fpga_speedup_vs_fma"] = PAPER_SPEEDUP[w] / gain
        if p.price.notna().any():
            dp = abs(f.price.median() - p.price.median())
            r.update(price_paper=p.price.median(), price_fma=f.price.median(), price_diff=dp,
                     price_diff_in_stderr=dp / p.stderr.median())
        rows.append(r)
    N = pd.DataFrame(rows)
    cmp_f = os.path.join(REV, "native_pf_compare.csv")
    if os.path.exists(cmp_f):
        C = pd.read_csv(cmp_f)
        i = N.work == "pf_131072"
        N.loc[i, "pf_trades_different"] = C.n_different.median()
        N.loc[i, "price_diff_in_stderr_max"] = C.max_diff_in_stderr.max()
        N.loc[i, "price_diff_in_stderr"] = C.median_diff_in_stderr.median()
    ins = os.path.join(REV, "native_fma_instructions.txt")
    if os.path.exists(ins):
        SUMMARY["fma_instructions"] = [l.strip() for l in open(ins)]
    N.to_csv(os.path.join(OUT, "rev_native.csv"), index=False)
    SUMMARY["native"] = N.round(4).to_dict("records")
    print("[native]")
    print(N.round(3).to_string(index=False))


# ------------------------------------------------------------------------------ video pipelines
def video():
    X = load(os.path.join(RAW, "video", "*", "*", "rep*"), {"pipe": -3, "config": -2, "rep": -1})
    if X.empty:
        print("[video] no video runs yet")
        return
    # the first FPGA run of each pipeline is excluded, as in the paper's campaign protocol (there the first
    # FPGA run was the profiled one): here it was 2.5-4x slower than the other four (start-up effect)
    first = (X.config == "fpga") & (X.rep == "rep1")
    SUMMARY["video_excluded_first_fpga_fps"] = X[first].set_index("pipe").fps.round(1).to_dict()
    X = X[~first].copy()
    fpga = X.config.eq("fpga").astype(float)
    X["E_frame_socket"] = (X.Eexact_pkg0 + X.Eexact_dram0 + fpga * X.Eexact_card) / X.frames
    X["t_frame"] = X.window_s / X.frames
    rows = []
    for pipe, g in X.groupby("pipe"):
        f = g[g.config == "fpga"]
        for cfg, c in g.groupby("config"):
            r = dict(pipe=pipe, config=cfg, n=len(c), fps=c.fps.median(), fps_window=(1 / c.t_frame).median(),
                     E_frame=c.E_frame_socket.median())
            if cfg != "fpga" and len(f):
                r["fpga_speedup"], r["sp_lo"], r["sp_hi"] = boot_ratio(c.t_frame, f.t_frame)
                r["fpga_energy_adv"], r["e_lo"], r["e_hi"] = boot_ratio(c.E_frame_socket, f.E_frame_socket)
            rows.append(r)
    V = pd.DataFrame(rows)
    V.to_csv(os.path.join(OUT, "rev_video_tuning.csv"), index=False)
    best = []
    for pipe, g in V[V.config != "fpga"].groupby("pipe"):
        fast, eff = g.loc[g.fps_window.idxmax()], g.loc[g.E_frame.idxmin()]
        paper = g[g.config == "cpu_w4c24"]
        best.append(dict(pipe=pipe, fastest_cpu=fast.config, speedup_vs_fastest=fast.get("fpga_speedup"),
                         most_efficient_cpu=eff.config, energy_adv_vs_most_efficient=eff.get("fpga_energy_adv"),
                         speedup_vs_paper_setting=paper.fpga_speedup.iloc[0] if len(paper) else np.nan))
    SUMMARY["video"] = best
    print("[video]")
    print(V.round(3).to_string(index=False))
    print(pd.DataFrame(best).round(3).to_string(index=False))
    # decode-only speed (ffmpeg -benchmark)
    df = os.path.join(REV, "video_decode", "ffmpeg_decode.txt")
    ff = os.path.join(REV, "video_decode", "frames.txt")
    if os.path.exists(df) and os.path.exists(ff):
        frames = float(open(ff).read().split()[0])
        dec = []
        for line in open(df):
            m = re.search(r"threads=(\d+) rep=(\d+).*rtime=([\d.]+)s", line)
            if m:
                dec.append(dict(threads=int(m.group(1)), rep=int(m.group(2)), fps=frames / float(m.group(3))))
        if not dec:
            print("[decode] no decode-only results (ffmpeg_decode.txt is empty)")
            return
        D = pd.DataFrame(dec).groupby("threads").fps.median().reset_index()
        D.to_csv(os.path.join(OUT, "rev_video_decode.csv"), index=False)
        SUMMARY["decode_fps"] = D.round(1).to_dict("records")
        print("[decode]", D.round(1).to_dict("records"))


# ------------------------------------------------------------------------------ 10 repetitions + idle
def reps10():
    X = load(os.path.join(RAW, "reps10", "*", "*", "rep*"), {"workload": -3, "config": -2, "rep": -1})
    if X.empty:
        print("[reps10] no runs yet")
        return
    X["passes"] = X.runs.fillna(1)
    fpga = X.config.eq("fpga").astype(float)
    X["E_socket"] = (X.Eexact_pkg0 + X.Eexact_dram0 + fpga * X.Eexact_card) / X.passes
    X["E_server"] = (X.Eexact_pkg0 + X.Eexact_dram0 + X.Eexact_pkg1 + X.Eexact_dram1 + fpga * X.Eexact_card) / X.passes
    rows = []
    for w, g in X.groupby("workload"):
        f = g[g.config == "fpga"]
        for cfg, c in g[g.config != "fpga"].groupby("config"):
            b = "server" if cfg == "s01_t96" else "socket"
            sp = boot_ratio(c.t_compute_s, f.t_compute_s)
            en = boot_ratio(c[f"E_{b}"], f[f"E_{b}"])
            rows.append(dict(workload=w, cpu_config=cfg, boundary=b, n_cpu=len(c), n_fpga=len(f),
                             speedup=sp[0], sp_lo=sp[1], sp_hi=sp[2], energy=en[0], e_lo=en[1], e_hi=en[2],
                             cv_t_cpu=c.t_compute_s.std() / c.t_compute_s.mean() * 100,
                             cv_t_fpga=f.t_compute_s.std() / f.t_compute_s.mean() * 100,
                             slow_cpu_runs=int((c.t_compute_s > 1.10 * c.t_compute_s.min()).sum())))
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT, "rev_reps10.csv"), index=False)
    SUMMARY["reps10"] = R.round(4).to_dict("records")
    print("[reps10]")
    print(R.round(3).to_string(index=False))


def idle():
    X = load(os.path.join(RAW, "idle", "rep*"), {"rep": -1})
    if X.empty:
        return
    r = X.iloc[0]
    P = {d: r[f"Eexact_{d}"] / r.window_s for d in ("pkg0", "dram0", "pkg1", "dram1", "card")}
    SUMMARY["idle_60s_W"] = {k: round(v, 2) for k, v in P.items()}
    print("[idle 60 s]", SUMMARY["idle_60s_W"])


# ------------------------------------------------------------------------------ STREAM and full truncation
def stream():
    f = os.path.join(REV, "stream", "bw_results.txt")
    rows = []
    if os.path.exists(f):
        for line in open(f):
            m = re.search(r"config=(\S+) rep=(\d+) threads=(\d+) copy_GBps=([\d.]+) triad_GBps=([\d.]+) xor_GBps=([\d.]+)", line)
            if m:
                rows.append(dict(config=m.group(1), rep=int(m.group(2)), threads=int(m.group(3)),
                                 copy=float(m.group(4)), triad=float(m.group(5)), xor=float(m.group(6))))
    if not rows:
        print("[stream] no valid results")
        return
    S = pd.DataFrame(rows).groupby(["config", "threads"])[["copy", "triad", "xor"]].agg(["median", "min", "max"])
    S.columns = ["_".join(c) for c in S.columns]
    S = S.reset_index()
    S.to_csv(os.path.join(OUT, "rev_stream.csv"), index=False)
    SUMMARY["stream_GBps"] = S.round(2).to_dict("records")
    print("[stream]")
    print(S.round(2).to_string(index=False))


def pf_ft():
    X = load(os.path.join(RAW, "pf_ft", "*", "*", "rep*"), {"work": -3, "config": -2, "rep": -1})
    if X.empty:
        print("[pf_ft] no runs yet")
        return
    X["E_socket"] = X.Eexact_pkg0 + X.Eexact_dram0 + X.config.eq("fpga") * X.Eexact_card
    f, c = X[X.config == "fpga"], X[X.config != "fpga"]
    sp, en = boot_ratio(c.t_compute_s, f.t_compute_s), boot_ratio(c.E_socket, f.E_socket)
    chk = os.path.join(REV, "pf_ft_check.txt")
    ident = open(chk).read().count("identical") if os.path.exists(chk) else None
    r = dict(n_fpga=len(f), n_cpu=len(c), t_fpga=f.t_compute_s.median(), t_cpu=c.t_compute_s.median(),
             speedup=sp[0], sp_lo=sp[1], sp_hi=sp[2], energy=en[0], e_lo=en[1], e_hi=en[2], identical_reps=ident)
    pd.DataFrame([r]).to_csv(os.path.join(OUT, "rev_pf_ft.csv"), index=False)
    SUMMARY["pf_ft"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
    print("[pf_ft]", SUMMARY["pf_ft"])


if __name__ == "__main__":
    if not os.path.isdir(REV):
        print(f"{REV} not found - unpack results_revision_<date>.tar.gz into paper/inputs/ first")
        sys.exit(1)
    for part in (telemetry, native, video, reps10, idle, stream, pf_ft):
        part()
    json.dump(SUMMARY, open(os.path.join(OUT, "rev_new_runs_summary.json"), "w"), indent=1, default=str)
