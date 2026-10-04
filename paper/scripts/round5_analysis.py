#!/usr/bin/env python3
"""
round5_analysis.py - analysis of round5_measurements.sh (review round 5, Tier 1).

  A. sessions   headline rows measured in several sessions (days): per-session medians, bootstrap CIs and
                ranges, the spread BETWEEN sessions, and a two-level (session, run) bootstrap interval
  B. pcie       per-session PCIe link state, DMA bandwidth (xbutil validate) and the FPGA AES stream rate
  C. wall       energy at the wall (BMC power, ~1 Hz) in long windows; platform power not seen by RAPL
  D. mcperf     MC Heston slow-core test: static vs dynamic OpenMP schedule; per-core IPC, cache and TLB misses
  E. videoperf  decode-thread IPC and last-level-cache misses, CPU vs FPGA pipeline (240p, 1080p)
  F. overlap    FPGA hosts with more overlap: AES rate with 1-4 buffer sets per CU, convolution with smaller strips

Input : paper/inputs/results_round5/   (unpacked results_round5_<date>.tar.gz)
Output: paper/data/r5_*.csv and r5_summary.json
Every part is skipped with a message if its runs are not there.
"""
import glob, json, os, re, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlib import run_record  # noqa: E402

R5 = os.environ.get("R5_IN", os.path.join(HERE, "..", "inputs", "results_round5"))
OUT = os.environ.get("R5_OUT", os.path.join(HERE, "..", "data"))
RAW = os.path.join(R5, "raw")
N_BOOT, SEED = 4000, 1
SLOW = 1.10                      # slow process: pass time > 10% above the fastest process of its size
MIN_WALL_SAMPLES = 15            # wall energy only from windows with >= 15 BMC readings (the BMC reads slower under 96-thread load)
SUMMARY = {}
BND = {"s0_c24": "socket", "s0_t48": "socket", "s01_t96": "server"}
DOMS = {"socket": ["pkg0", "dram0"], "server": ["pkg0", "dram0", "pkg1", "dram1"]}


def boot_ratio(num, den, n=N_BOOT, seed=SEED):
    rng = np.random.default_rng(seed)
    num, den = np.asarray(num, float), np.asarray(den, float)
    s = [np.median(rng.choice(num, len(num))) / np.median(rng.choice(den, len(den))) for _ in range(n)]
    return float(np.median(num) / np.median(den)), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def boot_two_level(num_by_s, den_by_s, n=N_BOOT, seed=SEED):
    """resample sessions, then runs within each drawn session (both sides), ratio of pooled medians"""
    rng = np.random.default_rng(seed)
    ses = [s for s in num_by_s if s in den_by_s]
    out = []
    for _ in range(n):
        pick = rng.choice(ses, len(ses))
        a = np.concatenate([rng.choice(num_by_s[s], len(num_by_s[s])) for s in pick])
        b = np.concatenate([rng.choice(den_by_s[s], len(den_by_s[s])) for s in pick])
        out.append(np.median(a) / np.median(b))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def stdout_values(d, key):
    txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
    return [float(v) for v in re.findall(rf"^RESULT {key}=([-0-9.eE+]+)", txt, re.M)]


def read_wall(f):
    if not os.path.exists(f):
        return None
    w = pd.read_csv(f)
    w["wall_w"] = pd.to_numeric(w.wall_w, errors="coerce")
    return w.dropna().sort_values("t_unix").reset_index(drop=True)


def wall_energy(w, t0, t1):
    """energy (J) between the markers from instantaneous BMC readings: linear interpolation of the readings,
    integrated from t0 to t1 (trapezoid). Returns (energy, number of readings inside the window)."""
    if w is None or len(w) < 2:
        return np.nan, 0
    t, p = w.t_unix.values, w.wall_w.values
    inside = int(((t >= t0) & (t <= t1)).sum())
    if t0 < t[0] or t1 > t[-1]:
        return np.nan, inside
    tt = np.concatenate([[t0], t[(t > t0) & (t < t1)], [t1]])
    pp = np.interp(tt, t, p)
    return float(np.sum((pp[1:] + pp[:-1]) / 2 * np.diff(tt))), inside


def work(r, wl):
    """(work per pass, number of passes in the window, time per pass)"""
    if wl.startswith("mc"):
        return r["paths"] * r["steps"] / 1e6, r["runs"], r["t_compute_s"]
    if wl.startswith("aes"):
        rep = r["repeat"] if np.isfinite(r["repeat"]) else 1.0
        return r["bytes"] / 1e9, rep, r["t_compute_s"]
    if wl.startswith("conv"):
        return r["mpix"], r["repeat"], r["t_compute_s"]
    if wl.startswith("pf"):
        ts = stdout_values(r["_dir"], "t_compute_s")          # one value per portfolio run in the window
        return 1.0, float(len(ts)) if ts else 1.0, float(np.median(ts)) if ts else r["t_compute_s"]
    return 1.0, 1.0, r["t_compute_s"]


def load_group(group, wall_file):
    rows = []
    w = read_wall(wall_file)
    for d in sorted(glob.glob(os.path.join(RAW, group, "*", "*", "rep*"))):
        wl, cfg, rep = d.split(os.sep)[-3:]
        if wl == "idle":
            continue
        r = run_record(d)
        if not r:
            continue
        wpp, passes, tpp = work(r, wl)
        units = wpp * passes
        r.update(workload=wl, config=cfg, rep=rep, wpp=wpp, passes=passes, t_unit=tpp / wpp)
        card = 1.0 if cfg == "fpga" else 0.0
        for b, doms in DOMS.items():
            r[f"E_{b}"] = (sum(r[f"Eexact_{x}"] for x in doms) + card * r["Eexact_card"]) / units
        ew, nw = wall_energy(w, r["t0"], r["t1"])
        r["E_wall"] = ew / units if nw >= MIN_WALL_SAMPLES else np.nan
        r["wall_samples"] = nw
        # wall without the idle card in CPU runs (a CPU-only server has no card): subtract the card's DC energy
        r["E_wall_nocard"] = (ew - (0.0 if card else r["Eexact_card"])) / units if nw >= MIN_WALL_SAMPLES else np.nan
        rapl_all = sum(r[f"Eexact_{x}"] for x in ("pkg0", "dram0", "pkg1", "dram1", "card"))
        r["P_platform_W"] = (ew - rapl_all) / r["window_s"] if nw >= MIN_WALL_SAMPLES else np.nan
        r["P_wall_W"] = ew / r["window_s"] if nw >= MIN_WALL_SAMPLES else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


# =============================================================================== A. sessions
def part_sessions():
    groups = sorted(os.path.basename(p) for p in glob.glob(os.path.join(RAW, "session_*")))
    if not groups:
        print("[sessions] no session runs yet"); return
    ALL = []
    for g in groups:
        s = g.replace("session_", "")
        df = load_group(g, os.path.join(R5, "wall", f"{g}.csv"))
        if len(df):
            df["session"] = s
            ALL.append(df)
    D = pd.concat(ALL, ignore_index=True)
    D.drop(columns=["_dir"]).to_csv(os.path.join(OUT, "r5_session_runs.csv"), index=False)
    per, summ = [], []
    for (wl, cfg), c in D[D.config != "fpga"].groupby(["workload", "config"]):
        b = BND.get(cfg, "server")
        fk = wl if wl != "aes_noio_1024MB" else "aes_noio_1024MB"
        f_all = D[(D.workload == fk) & (D.config == "fpga")]
        num_t, den_t, num_e, den_e = {}, {}, {}, {}
        for s, cs in c.groupby("session"):
            fs = f_all[f_all.session == s]
            if not len(fs):
                continue
            sp = boot_ratio(cs.t_unit, fs.t_unit)
            en = boot_ratio(cs[f"E_{b}"], fs[f"E_{b}"])
            per.append(dict(workload=wl, config=cfg, boundary=b, session=s, n_cpu=len(cs), n_fpga=len(fs),
                            speedup=sp[0], sp_lo=sp[1], sp_hi=sp[2],
                            sp_min=cs.t_unit.min() / fs.t_unit.max(), sp_max=cs.t_unit.max() / fs.t_unit.min(),
                            energy=en[0], e_lo=en[1], e_hi=en[2]))
            num_t[s], den_t[s] = cs.t_unit.values, fs.t_unit.values
            num_e[s], den_e[s] = cs[f"E_{b}"].values, fs[f"E_{b}"].values
        if not num_t:
            continue
        P = pd.DataFrame([p for p in per if p["workload"] == wl and p["config"] == cfg])
        pooled_t = np.median(np.concatenate(list(num_t.values()))) / np.median(np.concatenate(list(den_t.values())))
        pooled_e = np.median(np.concatenate(list(num_e.values()))) / np.median(np.concatenate(list(den_e.values())))
        lo_t, hi_t = boot_two_level(num_t, den_t) if len(num_t) > 1 else (np.nan, np.nan)
        lo_e, hi_e = boot_two_level(num_e, den_e) if len(num_e) > 1 else (np.nan, np.nan)
        summ.append(dict(workload=wl, config=cfg, boundary=b, sessions=len(num_t),
                         speedup=pooled_t, sp_2lvl_lo=lo_t, sp_2lvl_hi=hi_t,
                         sp_session_min=P.speedup.min(), sp_session_max=P.speedup.max(),
                         sp_between_session_pct=100 * (P.speedup.max() / P.speedup.min() - 1),
                         energy=pooled_e, e_2lvl_lo=lo_e, e_2lvl_hi=hi_e,
                         e_session_min=P.energy.min(), e_session_max=P.energy.max(),
                         e_between_session_pct=100 * (P.energy.max() / P.energy.min() - 1)))
    pd.DataFrame(per).to_csv(os.path.join(OUT, "r5_sessions.csv"), index=False)
    S = pd.DataFrame(summ)
    S.to_csv(os.path.join(OUT, "r5_sessions_summary.csv"), index=False)
    SUMMARY["sessions"] = dict(n_sessions=len(groups),
                               max_between_session_pct_time=float(S.sp_between_session_pct.max()) if len(S) else None,
                               max_between_session_pct_energy=float(S.e_between_session_pct.max()) if len(S) else None)
    print("[sessions]\n", S.round(3).to_string(index=False))
    # FPGA AES stream rate per session (for part B)
    a = D[(D.workload == "aes_noio") & (D.config == "fpga")]
    if len(a):
        SUMMARY["aes_stream_GBps_by_session"] = (a.groupby("session").apply(lambda g: float((g.wpp / g.t_compute_s).median()))
                                                  .round(2).to_dict())


# =============================================================================== B. pcie
def part_pcie():
    rows = []
    for d in sorted(glob.glob(os.path.join(R5, "sessions", "*"))):
        s = os.path.basename(d)
        for sub, when in ((d, "start"), (os.path.join(d, "end"), "end")):
            if not os.path.isdir(sub):
                continue
            link = open(os.path.join(sub, "pcie_link.txt")).read() if os.path.exists(os.path.join(sub, "pcie_link.txt")) else ""
            sta = re.findall(r"LnkSta:\s*Speed ([\d.]+GT/s)[^,]*, Width (x\d+)", link)
            v = open(os.path.join(sub, "xbutil_validate_dma.txt"), errors="replace").read() \
                if os.path.exists(os.path.join(sub, "xbutil_validate_dma.txt")) else ""
            wr = [float(x) for x in re.findall(r"write bandwidth\s*=\s*([\d.]+)\s*MB/s", v, re.I)]
            rd = [float(x) for x in re.findall(r"read bandwidth\s*=\s*([\d.]+)\s*MB/s", v, re.I)]
            rows.append(dict(session=s, when=when, link_speed=";".join(x[0] for x in sta), link_width=";".join(x[1] for x in sta),
                             dma_h2c_GBps_max=max(wr) / 1000 if wr else np.nan,
                             dma_c2h_GBps_max=max(rd) / 1000 if rd else np.nan))
    if not rows:
        print("[pcie] no session snapshots yet"); return
    P = pd.DataFrame(rows)
    P.to_csv(os.path.join(OUT, "r5_pcie.csv"), index=False)
    SUMMARY["pcie"] = P.to_dict("records")
    print("[pcie]\n", P.to_string(index=False))


# =============================================================================== C. wall
def part_wall():
    D = load_group("wall", os.path.join(R5, "wall", "wall.csv"))
    if not len(D):
        print("[wall] no wall runs yet"); return
    D.drop(columns=["_dir"]).to_csv(os.path.join(OUT, "r5_wall_runs.csv"), index=False)
    rows = []
    for wl in sorted(D.workload.unique()):
        f = D[(D.workload == wl) & (D.config == "fpga")]
        for cfg in ("s0_c24", "s01_t96"):
            c = D[(D.workload == wl) & (D.config == cfg)]
            if not (len(c) and len(f)):
                continue
            b = BND[cfg]
            r = dict(workload=wl, config=cfg, n_cpu=len(c), n_fpga=len(f),
                     wall_samples_min=int(min(c.wall_samples.min(), f.wall_samples.min())))
            r.update(zip(("speedup", "sp_lo", "sp_hi"), boot_ratio(c.t_unit, f.t_unit)))
            r.update(zip((f"A_{b}", f"A_{b}_lo", f"A_{b}_hi"), boot_ratio(c[f"E_{b}"], f[f"E_{b}"])))
            if c.E_wall.notna().all() and f.E_wall.notna().all():
                r.update(zip(("A_wall", "A_wall_lo", "A_wall_hi"), boot_ratio(c.E_wall, f.E_wall)))
                r.update(zip(("A_wall_nocard", "A_wall_nocard_lo", "A_wall_nocard_hi"),
                             boot_ratio(c.E_wall_nocard, f.E_wall_nocard)))
                r.update(P_wall_cpu_W=c.P_wall_W.median(), P_wall_fpga_W=f.P_wall_W.median(),
                         P_platform_cpu_W=c.P_platform_W.median(), P_platform_fpga_W=f.P_platform_W.median())
            rows.append(r)
    W = pd.DataFrame(rows)
    W.to_csv(os.path.join(OUT, "r5_wall.csv"), index=False)
    idle = glob.glob(os.path.join(RAW, "wall", "idle", "rep1"))
    if idle:
        rr = run_record(idle[0])
        if rr:
            w = read_wall(os.path.join(R5, "wall", "wall.csv"))
            ew, nw = wall_energy(w, rr["t0"], rr["t1"])
            SUMMARY["wall_idle_W"] = ew / rr["window_s"] if nw else None
            SUMMARY["rapl_card_idle_W"] = sum(rr[f"Eexact_{x}"] for x in ("pkg0", "dram0", "pkg1", "dram1", "card")) / rr["window_s"]
    SUMMARY["wall"] = W.round(3).to_dict("records")
    print("[wall]\n", W.round(3).to_string(index=False))


# =============================================================================== D. mcperf
def read_perf_percore(d):
    """perf stat -a -A -I 500 -x, : time,CPUn,count,unit,event,... -> DataFrame(t_rel, cpu, event, count)"""
    f = os.path.join(d, "perf_percore.csv")
    if not os.path.exists(f):
        return None
    rows = []
    for line in open(f, errors="replace"):
        if line.startswith("#") or not line.strip():
            continue
        fs = line.strip().split(",")
        if len(fs) < 5 or not fs[1].startswith("CPU"):
            continue
        try:
            rows.append((float(fs[0]), int(fs[1][3:]), fs[4], float(fs[2])))
        except ValueError:
            continue
    if not rows:
        return None
    p = pd.DataFrame(rows, columns=["t_rel", "cpu", "event", "count"])
    t0 = float(open(os.path.join(d, "perf_start.txt")).read().split()[0]) if os.path.exists(os.path.join(d, "perf_start.txt")) else 0.0
    p["t_unix"] = t0 + p.t_rel
    return p


def part_mcperf():
    base = os.path.join(RAW, "mcperf")
    if not os.path.isdir(base):
        print("[mcperf] no runs yet"); return
    rows = []
    for d in sorted(glob.glob(os.path.join(base, "*", "*", "rep*"))):
        size, sched, rep = d.split(os.sep)[-3:]
        r = run_record(d)
        if not r:
            continue
        rec = dict(size=size, schedule=sched.replace("s0_c24_", ""), rep=rep, t_pass=r["t_compute_s"])
        p = read_perf_percore(os.path.join(R5, "telemetry", "mcperf", size, sched, rep))
        if p is not None:
            w = p[(p.t_unix >= r["t0"]) & (p.t_unix <= r["t1"])]
            pc = w.pivot_table(index="cpu", columns="event", values="count", aggfunc="sum")
            if {"cycles", "instructions"} <= set(pc.columns):
                pc = pc[pc.cycles > 0.5 * pc.cycles.median()]            # the 24 busy cores
                ipc = pc.instructions / pc.cycles
                lo = ipc.idxmin()
                rec.update(n_cores=len(pc), ipc_median=float(ipc.median()), ipc_min=float(ipc.min()), slow_core=int(lo),
                           ipc_min_rel=float(ipc.min() / ipc.median()))
                for ev in ("LLC-load-misses", "cache-misses", "dTLB-load-misses", "l2_rqsts.miss",
                           "mem_load_retired.l3_miss", "mem_load_l3_miss_retired.remote_dram",
                           "mem_load_l3_miss_retired.local_dram"):
                    if ev in pc.columns:
                        mpki = 1000 * pc[ev] / pc.instructions
                        rec[f"{ev}_mpki_median"] = float(mpki.median())
                        rec[f"{ev}_mpki_slowcore"] = float(mpki[lo])
        rows.append(rec)
    M = pd.DataFrame(rows)
    if not len(M):
        print("[mcperf] no complete runs"); return
    M["fastest_of_size"] = M.groupby("size").t_pass.transform("min")
    M["slow"] = M.t_pass > SLOW * M.fastest_of_size
    M.to_csv(os.path.join(OUT, "r5_mcperf_runs.csv"), index=False)
    agg = dict(n=("rep", "size"), t_pass_median=("t_pass", "median"))
    if "ipc_min_rel" in M:
        agg["ipc_min_rel_median"] = ("ipc_min_rel", "median")
    S = M.groupby(["schedule", "slow"]).agg(**agg).reset_index()
    S.to_csv(os.path.join(OUT, "r5_mcperf_summary.csv"), index=False)
    by = M.groupby("schedule").agg(n=("rep", "size"), slow=("slow", "sum")).to_dict("index")
    t_rel = (M[M.schedule == "dynamic"].groupby("size").t_pass.median() /
             M[M.schedule == "static"].groupby("size").t_pass.median()).round(3).to_dict()
    SUMMARY["mcperf"] = dict(slow_by_schedule=by, dynamic_over_static_time_by_size=t_rel)
    ev_cols = [c for c in M.columns if c.endswith("_mpki_slowcore")]
    if ev_cols:
        sl = M[M.slow & (M.schedule == "static")]
        SUMMARY["mcperf"]["slow_core_vs_median_mpki"] = {
            c.replace("_mpki_slowcore", ""): float((sl[c] / sl[c.replace("slowcore", "median")]).median()) for c in ev_cols
        } if len(sl) else "no slow processes"
    print("[mcperf]\n", S.round(3).to_string(index=False))


# =============================================================================== E. videoperf
def read_perf_threads(f):
    """perf stat --per-thread -x, : comm-tid,count,unit,event,... -> {tid: {event: count}}"""
    th = {}
    for line in open(f, errors="replace"):
        if line.startswith("#") or not line.strip():
            continue
        fs = line.strip().split(",")
        if len(fs) < 4:
            continue
        try:
            th.setdefault(fs[0], {})[fs[3]] = float(fs[1])
        except ValueError:
            continue
    return th


def part_videoperf():
    files = sorted(glob.glob(os.path.join(R5, "perf", "video", "*.csv")))
    if not files:
        print("[videoperf] no perf files yet"); return
    rows = []
    for f in files:
        m = re.match(r"q(\d)_(fpga|cpu_w8c1)_(rep\d+)\.csv", os.path.basename(f))
        if not m:
            continue
        th = read_perf_threads(f)
        th = {k: v for k, v in th.items() if v.get("cycles", 0) > 0 and "instructions" in v}
        if not th:
            continue
        top = max(th, key=lambda k: th[k]["cycles"])                 # the busiest thread: the decoder
        t = th[top]
        rec = dict(quality=int(m.group(1)), platform=m.group(2), rep=m.group(3), thread=top,
                   ipc=t["instructions"] / t["cycles"], cycles=t["cycles"],
                   share_of_process_cycles=t["cycles"] / sum(v["cycles"] for v in th.values()))
        for ev in ("LLC-load-misses", "LLC-loads", "cache-misses", "mem_load_retired.l3_miss", "dTLB-load-misses"):
            if ev in t:
                rec[f"{ev}_mpki"] = 1000 * t[ev] / t["instructions"]
        if "LLC-load-misses" in t and t.get("LLC-loads"):
            rec["llc_miss_ratio"] = t["LLC-load-misses"] / t["LLC-loads"]
        r = run_record(os.path.join(RAW, "videoperf", f"q{m.group(1)}", m.group(2), m.group(3)))
        if r:
            rec["fps"] = r["fps"]
        rows.append(rec)
    V = pd.DataFrame(rows)
    V.to_csv(os.path.join(OUT, "r5_videoperf_runs.csv"), index=False)
    S = V.groupby(["quality", "platform"]).median(numeric_only=True).reset_index()
    S.to_csv(os.path.join(OUT, "r5_videoperf_summary.csv"), index=False)
    SUMMARY["videoperf"] = S.round(3).to_dict("records")
    print("[videoperf]\n", S.round(3).to_string(index=False))


# =============================================================================== F. overlap
def part_overlap():
    base = os.path.join(RAW, "overlap")
    if not os.path.isdir(base):
        print("[overlap] no runs yet"); return
    rows = []
    for d in sorted(glob.glob(os.path.join(base, "*", "fpga", "rep*"))):
        wl, _, rep = d.split(os.sep)[-3:]
        r = run_record(d)
        if not r:
            continue
        txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
        g = lambda k: (re.search(rf"^RESULT {k}=(\S+)", txt, re.M) or [None, None])[1]
        m = re.match(r"(aes_noio_1024MB|aes_noio|aes_check|conv_rgb8_8K)_(streams(\d)|strip(\d+)(_h2d2)?)", wl)
        if not m:
            continue
        kind = "aes_16GiB" if m.group(1) == "aes_noio" else ("aes_1GiB" if m.group(1) == "aes_noio_1024MB" else m.group(1))
        rec = dict(kind=kind, variant=m.group(2), rep=rep, t_compute_s=r["t_compute_s"],
                   verified=g("verified") or g("verify"), ok=g("ok"))
        if kind.startswith("aes"):
            rec["rate"] = r["bytes"] / 1e9 / r["t_compute_s"]; rec["unit"] = "GB/s"
        elif kind.startswith("conv"):
            rec["rate"] = r["mpix"] / 1e3 / r["t_compute_s"]; rec["unit"] = "Gpixel/s"
        rows.append(rec)
    O = pd.DataFrame(rows)
    if not len(O):
        print("[overlap] no complete runs"); return
    O.to_csv(os.path.join(OUT, "r5_overlap_runs.csv"), index=False)
    S = O[O.kind != "aes_check"].groupby(["kind", "variant"]).rate.agg(["median", "min", "max", "size"]).reset_index()
    # speedup against the paper's CPU rates (24 cores): AES 12.68 GB/s, convolution 1.79 Gpixel/s;
    # bounds of Table 7: AES overlapped 12.2 GB/s (0.96x), convolution overlapped 1.36 Gpixel/s (0.76x)
    cpu = {"aes_16GiB": 12.68, "aes_1GiB": 12.68, "conv_rgb8_8K": 1.79}
    S["speedup_vs_paper_cpu"] = [row["median"] / cpu[row.kind] for _, row in S.iterrows()]
    S.to_csv(os.path.join(OUT, "r5_overlap_summary.csv"), index=False)
    chk = O[O.kind == "aes_check"]
    SUMMARY["overlap"] = dict(best={k: g.sort_values("median").iloc[-1][["variant", "median", "speedup_vs_paper_cpu"]].to_dict()
                                    for k, g in S.groupby("kind")},
                              aes_checks_verified=chk.verified.tolist(), conv_verified=O[O.kind == "conv_rgb8_8K"].verified.dropna().unique().tolist())
    print("[overlap]\n", S.round(3).to_string(index=False))


def part_wall_consistency():
    """Do the two sensor chains agree? Wall power (the server's input power, read from its power supplies by the
    management controller) against the sum of the rail sensors used in the paper (RAPL packages and DRAM, card
    rails), for every run with enough wall readings: the wall-power runs and replication sessions 2 and 3."""
    fs = [os.path.join(OUT, f) for f in ("r5_wall_runs.csv", "r5_session_runs.csv")]
    if not all(os.path.exists(f) for f in fs):
        print("[wall consistency] skipped: run part_wall and part_sessions first"); return
    a = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    a = a[a.P_wall_W.notna()].copy()
    a["P_rapl"] = (a.Eexact_pkg0 + a.Eexact_dram0 + a.Eexact_pkg1 + a.Eexact_dram1) / a.window_s
    a["P_card"] = a.Eexact_card / a.window_s
    y = a.P_wall_W.values
    X1 = np.c_[np.ones(len(a)), a.P_rapl + a.P_card]
    X2 = np.c_[np.ones(len(a)), a.P_rapl, a.P_card]
    b1 = np.linalg.lstsq(X1, y, rcond=None)[0]
    b2 = np.linalg.lstsq(X2, y, rcond=None)[0]
    r1 = y - X1 @ b1
    rng = np.random.default_rng(SEED)
    B1, B2 = [], []
    for _ in range(N_BOOT):
        i = rng.integers(0, len(a), len(a))
        B1.append(np.linalg.lstsq(X1[i], y[i], rcond=None)[0])
        B2.append(np.linalg.lstsq(X2[i], y[i], rcond=None)[0])
    B1, B2 = np.array(B1), np.array(B2)
    ci = lambda v: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
    a["resid_W"] = r1
    a["P_card_implied"] = (a.P_wall_W - b1[0]) / b1[1] - a.P_rapl      # card power implied by the wall reading
    G = a.groupby(["workload", "config"]).agg(n=("resid_W", "size"), P_rapl=("P_rapl", "median"),
                                              P_card=("P_card", "median"), P_wall=("P_wall_W", "median"),
                                              resid_W=("resid_W", "median"),
                                              P_card_implied=("P_card_implied", "median")).reset_index()
    G.to_csv(os.path.join(OUT, "r5_wall_consistency.csv"), index=False)
    f = G[G.config == "fpga"]
    SUMMARY["wall_consistency"] = dict(
        n_runs=int(len(a)), wall_range_W=[float(y.min()), float(y.max())],
        one_slope=dict(intercept_W=float(b1[0]), slope=float(b1[1]), intercept_ci=ci(B1[:, 0]), slope_ci=ci(B1[:, 1]),
                       r2=float(1 - r1.var() / y.var()), resid_sd_W=float(r1.std(ddof=2)),
                       resid_sd_pct_of_mean=float(100 * r1.std(ddof=2) / y.mean()), max_abs_resid_W=float(np.abs(r1).max())),
        two_slopes=dict(intercept_W=float(b2[0]), rapl=float(b2[1]), card=float(b2[2]), rapl_ci=ci(B2[:, 1]),
                        card_ci=ci(B2[:, 2]), card_over_rapl=float(b2[2] / b2[1]), card_over_rapl_ci=ci(B2[:, 2] / B2[:, 1])),
        card_implied_over_sensor_fpga_runs={r.workload: float(r.P_card_implied / r.P_card) for r in f.itertuples()})
    print("[wall consistency]", json.dumps(SUMMARY["wall_consistency"], indent=1))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    if not os.path.isdir(R5):
        sys.exit(f"no round-5 results at {R5} (unpack results_round5_<date>.tar.gz into paper/inputs/)")
    for part in (part_sessions, part_pcie, part_wall, part_wall_consistency, part_mcperf, part_videoperf, part_overlap):
        part()
    json.dump(SUMMARY, open(os.path.join(OUT, "r5_summary.json"), "w"), indent=1, default=str)
    print("wrote r5_*.csv and r5_summary.json")
