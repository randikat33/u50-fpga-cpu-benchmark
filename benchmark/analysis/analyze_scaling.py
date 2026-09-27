#!/usr/bin/env python3
"""
analyze_scaling.py - how does the FPGA compare with ONE socket and with the WHOLE server?

  analyze_scaling.py <results_scaling> [<results/v2>]

Inputs
  results_scaling/raw/<workload>/<config>/rep*/   CPU runs of cpu_scaling.sh
      configs: s0_c24 (socket 0, 24 cores = campaign baseline), s0_t48 (socket 0 + SMT),
               s01_c48 (both sockets, 48 cores), s01_t96 (both sockets + SMT)
  results/v2/raw/<project>/<group>/<config>/fpga/rep*/   the FPGA runs of the main campaign
      (optional; without it only the CPU scaling table is written)

Energy of one run = mean power over the MARK start..end window x t_compute (median per run),
the same formula for CPU and FPGA, so ratios compare equal work. Two boundaries:
  per-socket   CPU: pkg0 + dram0                 FPGA: pkg0 + dram0 + card
               (the campaign's conservative boundary: the card is not charged to the CPU)
  whole server CPU: pkg0+dram0+pkg1+dram1        FPGA: pkg0+dram0+pkg1+dram1 + card
               (socket 1 idles during FPGA runs but is still powered)

Outputs (in <results_scaling>)
  scaling_runs.csv       one row per CPU run
  scaling_summary.csv    median per workload x config (+ speedup vs s0_c24, CV)
  scaling_vs_fpga.csv    FPGA vs every CPU config: speedup and energy ratio, both boundaries
"""
import glob, json, os, re, sys
import numpy as np
import pandas as pd

ORDER = {"s0_c24": 0, "s0_t48": 1, "s01_c48": 2, "s01_t96": 3}
LABEL = {"s0_c24": "1 socket, 24 cores", "s0_t48": "1 socket, 48 threads (SMT)",
         "s01_c48": "2 sockets, 48 cores", "s01_t96": "2 sockets, 96 threads (SMT)"}
# scaling workload -> (campaign project, group, config) of the matching FPGA runs
FPGA_OF = {
    "mc_4194304x64":   ("03_MC_Heston", "main", "4194304x64"),
    "mc_67108864x128": ("03_MC_Heston", "main", "67108864x128"),
    "pf_131072":       ("04_Portfolio", "main", "p2_131072"),
    "aes_noio_1024MB": ("01_AES", "noio", "1024MB"),
    "conv_rgb8_8K":    ("02_Convolution", "synth", "rgb8_7680x4320"),
}
PRETTY = {"mc_4194304x64": "MC Heston 4.2M paths x 64 steps", "mc_67108864x128": "MC Heston 67M paths x 128 steps",
          "pf_131072": "Portfolio 131,072 stage-2 paths", "aes_noio_1024MB": "AES-256 1 GB (compute only)",
          "conv_rgb8_8K": "2D Conv RGB8 8K"}
S0 = ["pkg0_w", "dram0_w"]
ALL = ["pkg0_w", "dram0_w", "pkg1_w", "dram1_w"]


def res(txt, k):
    m = re.search(rf"^RESULT {k}=([^\s]+)", txt, re.M)
    if not m:
        return np.nan
    try:
        return float(m.group(1))
    except ValueError:
        return m.group(1)


def one_run(d):
    """power + timing of one run directory (CPU or FPGA)"""
    mf, pf, sf = (os.path.join(d, f) for f in ("meta.json", "power.csv", "stdout.log"))
    if not (os.path.exists(mf) and os.path.exists(pf) and os.path.exists(sf)):
        return None
    if os.path.getsize(pf) == 0 or os.path.getsize(mf) == 0:
        return None   # file lost in transfer
    m = json.load(open(mf))
    t0, t1 = m.get("t_start_marker"), m.get("t_end_marker")
    if not (t0 and t1) or not m.get("ok", True):
        return None
    txt = open(sf, errors="replace").read()
    p = pd.read_csv(pf)
    s = p[(p.t_unix >= t0) & (p.t_unix <= t1)]
    if len(s) < 2:   # window shorter than two samples: take the two nearest samples
        s = p.iloc[(p.t_unix - (t0 + t1) / 2).abs().argsort()[:2]]
    P = {c: float(s[c].astype(float).mean()) if c in s else np.nan for c in ALL + ["card_w"]}
    tc = res(txt, "t_compute_s")
    return dict(t_compute_s=tc, window_s=t1 - t0, threads=res(txt, "threads"), ok=res(txt, "ok"),
                P_socket0_W=sum(P[c] for c in S0), P_socket1_W=P["pkg1_w"] + P["dram1_w"],
                P_server_W=sum(P[c] for c in ALL), P_card_W=P["card_w"],
                hash=next((res(txt, k) for k in ("mom_hash", "checksum", "out_fnv1a64", "cks_sharpen")
                           if isinstance(res(txt, k), str)), ""))


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results_scaling"
    camp = sys.argv[2] if len(sys.argv) > 2 else None
    rows = []
    for d in glob.glob(os.path.join(root, "raw", "*", "*", "rep*")):
        w, c, rep = d.split(os.sep)[-3:]
        r = one_run(d)
        if r:
            rows.append(dict(workload=w, config=c, rep=rep, **r))
    R = pd.DataFrame(rows)
    if R.empty:
        sys.exit("no runs found under " + root)
    R["E_socket0_J"] = R.P_socket0_W * R.t_compute_s
    R["E_server_J"] = R.P_server_W * R.t_compute_s
    R.to_csv(os.path.join(root, "scaling_runs.csv"), index=False)

    # results must not depend on the thread count (bit-identical outputs)
    for w, g in R.groupby("workload"):
        hs = set(g.hash) - {""}
        print(f"{w:18s} output hash identical across all configs: {'yes' if len(hs) <= 1 else 'NO ' + str(hs)}")

    T = R.groupby(["workload", "config"]).agg(
        n=("rep", "size"), t_compute_s=("t_compute_s", "median"),
        cv_pct=("t_compute_s", lambda x: x.std() / x.mean() * 100),
        P_socket0_W=("P_socket0_W", "median"), P_socket1_W=("P_socket1_W", "median"),
        P_server_W=("P_server_W", "median"), E_socket0_J=("E_socket0_J", "median"),
        E_server_J=("E_server_J", "median")).reset_index()
    base = T[T.config == "s0_c24"].set_index("workload").t_compute_s
    T["speedup_vs_s0_c24"] = base.reindex(T.workload).values / T.t_compute_s
    T["config_label"] = T.config.map(LABEL)
    T = T.sort_values(["workload", "config"], key=lambda s: s.map(ORDER) if s.name == "config" else s)
    T.to_csv(os.path.join(root, "scaling_summary.csv"), index=False)
    pd.set_option("display.width", 220)
    print("\nCPU scaling (median of 5 reps)")
    print(T.drop(columns="config_label").round(4).to_string(index=False))

    if not camp:
        return
    out = []
    for w, (proj, grp, cfg) in FPGA_OF.items():
        F = [one_run(d) for d in glob.glob(os.path.join(camp, "raw", proj, grp, cfg, "fpga", "rep*"))]
        F = pd.DataFrame([f for f in F if f])
        if F.empty:
            print(f"  (no FPGA runs for {w} in {camp})")
            continue
        tf = F.t_compute_s.median()
        e_sock = (F.P_socket0_W + F.P_card_W).mul(F.t_compute_s).median()
        e_serv = (F.P_server_W + F.P_card_W).mul(F.t_compute_s).median()
        for _, t in T[T.workload == w].iterrows():
            out.append(dict(workload=w, workload_label=PRETTY.get(w, w), cpu_config=t.config,
                            cpu_config_label=LABEL[t.config], n_fpga=len(F),
                            t_fpga_s=tf, t_cpu_s=t.t_compute_s, fpga_speedup=t.t_compute_s / tf,
                            E_fpga_socket_J=e_sock, E_cpu_socket_J=t.E_socket0_J,
                            energy_adv_socket=t.E_socket0_J / e_sock,
                            E_fpga_server_J=e_serv, E_cpu_server_J=t.E_server_J,
                            energy_adv_server=t.E_server_J / e_serv,
                            P_fpga_server_card_W=float((F.P_server_W + F.P_card_W).median()),
                            P_cpu_server_W=t.P_server_W))
    V = pd.DataFrame(out)
    V.to_csv(os.path.join(root, "scaling_vs_fpga.csv"), index=False)
    print("\nFPGA vs CPU configurations (speedup > 1 and energy advantage > 1 favour the FPGA)")
    print(V[["workload", "cpu_config", "fpga_speedup", "energy_adv_socket", "energy_adv_server",
             "P_fpga_server_card_W", "P_cpu_server_W"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
