#!/usr/bin/env python3
"""
analyze_energy_long.py - energy per unit of work from the long-window runs (energy_long.sh)

  analyze_energy_long.py <results_energy>

Per run: mean power over the MARK window x t_compute (one pass) / work of one pass.
  MC   : J per M path-steps     Conv : J per Mpix     AES : J per GB
Boundaries as in analyze_scaling.py:
  per-socket    CPU pkg0+dram0            FPGA pkg0+dram0+card
  whole server  CPU all 4 RAPL rails      FPGA all 4 rails + card
Writes energy_runs.csv and energy_summary.csv (median of reps, FPGA advantage per config).
"""
import glob, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_scaling import one_run, res, LABEL, ORDER  # noqa: E402

UNIT = {"mc_4194304x64": "J per M path-steps", "conv_rgb8_8K": "J per Mpix", "aes_noio": "J per GB"}


def work(w, txt):
    if w.startswith("mc"):
        return res(txt, "paths") * res(txt, "steps") / 1e6
    if w.startswith("conv"):
        return res(txt, "mpix")
    return res(txt, "bytes") / 1e9


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results_energy"
    rows = []
    for d in glob.glob(os.path.join(root, "raw", "*", "*", "rep*")):
        w, plat, rep = d.split(os.sep)[-3:]
        r = one_run(d)
        if not r:
            continue
        txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
        u = work(w, txt)
        p = pd.read_csv(os.path.join(d, "power.csv"))
        m = json.load(open(os.path.join(d, "meta.json")))
        n_samp = int(((p.t_unix >= m["t_start_marker"]) & (p.t_unix <= m["t_end_marker"])).sum())
        fpga = plat == "fpga"
        P_sock = r["P_socket0_W"] + (r["P_card_W"] if fpga else 0)
        P_serv = r["P_server_W"] + (r["P_card_W"] if fpga else 0)
        rows.append(dict(workload=w, platform=plat, rep=rep, window_s=r["window_s"], power_samples=n_samp,
                         t_compute_s=r["t_compute_s"], work_per_pass=u, P_socket_W=P_sock, P_server_W=P_serv,
                         E_socket_per_unit=P_sock * r["t_compute_s"] / u,
                         E_server_per_unit=P_serv * r["t_compute_s"] / u, hash=r["hash"]))
    R = pd.DataFrame(rows)
    if R.empty:
        sys.exit("no runs under " + root)
    R.to_csv(os.path.join(root, "energy_runs.csv"), index=False)
    for w, g in R.groupby("workload"):
        hs = set(g.hash) - {""}
        print(f"{w:15s} min power samples per window: {g.power_samples.min():3d}   "
              f"outputs identical: {'yes' if len(hs) <= 1 else 'NO'}")
    S = R.groupby(["workload", "platform"]).agg(
        n=("rep", "size"), samples_min=("power_samples", "min"), t_compute_s=("t_compute_s", "median"),
        P_socket_W=("P_socket_W", "median"), P_server_W=("P_server_W", "median"),
        E_socket_per_unit=("E_socket_per_unit", "median"), E_server_per_unit=("E_server_per_unit", "median"),
        cv_E_socket_pct=("E_socket_per_unit", lambda x: x.std() / x.mean() * 100)).reset_index()
    S["unit"] = S.workload.map(UNIT)
    f = S[S.platform == "fpga"].set_index("workload")
    S["energy_adv_socket"] = S.apply(lambda x: x.E_socket_per_unit / f.E_socket_per_unit.get(x.workload, np.nan), axis=1)
    S["energy_adv_server"] = S.apply(lambda x: x.E_server_per_unit / f.E_server_per_unit.get(x.workload, np.nan), axis=1)
    S["platform_label"] = S.platform.map(lambda p: "FPGA (U50) + host" if p == "fpga" else LABEL.get(p, p))
    S = S.sort_values(["workload", "platform"], key=lambda s: s.map(lambda p: -1 if p == "fpga" else ORDER.get(p, 9))
                      if s.name == "platform" else s)
    S.to_csv(os.path.join(root, "energy_summary.csv"), index=False)
    pd.set_option("display.width", 220)
    print("\nEnergy per unit of work (median); advantage = CPU energy / FPGA energy (> 1 favours the FPGA)")
    print(S[["workload", "platform", "n", "samples_min", "t_compute_s", "P_socket_W", "P_server_W",
             "E_socket_per_unit", "E_server_per_unit", "cv_E_socket_pct", "energy_adv_socket",
             "energy_adv_server"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
