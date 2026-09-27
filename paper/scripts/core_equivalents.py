#!/usr/bin/env python3
"""
core_equivalents.py - how many CPU cores does the card replace, in time and in energy?

Input : paper/inputs/results_revision/raw/cores/<workload>/<config>/<rep>/   (revision_measurements.sh cores)
        config = fpga | s0_k<k>  (first k physical cores of socket 0, one thread per core)
Output: paper/data/rev_cores.csv          one row per workload x core count (medians, energy per unit)
        paper/data/rev_core_equiv.csv     one row per workload: time- and energy-equivalent core counts

Definitions (per-socket energy boundary: package 0 + DRAM 0, plus the card for the FPGA)
  R_C(k), E_C(k)  CPU rate and energy per unit of work with k cores (the rest of the socket idles)
  k_time          k at which R_C(k) = R_F (linear interpolation between measured k; beyond 24 cores
                  extrapolated with the marginal rate of the last measured step, and flagged)
  k_energy        smallest k at which E_C(k) <= E_F; 'none' if the CPU needs more energy at every k
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlib import load  # noqa: E402

IN = os.environ.get("CORES_IN", os.path.join(HERE, "..", "inputs", "results_revision", "raw", "cores"))
OUT = os.environ.get("CORES_OUT", os.path.join(HERE, "..", "data"))


def work(g, wl):
    """work per pass and number of passes, as in revision_analysis.py"""
    if wl.startswith("mc"):
        return g.paths * g.steps / 1e6, g.runs, "M path-steps"
    if wl.startswith("aes"):
        return g.bytes / 1e9, g.repeat.fillna(1), "GB"
    if wl.startswith("conv"):
        return g.mpix, g.repeat, "Mpix"
    return pd.Series(1.0, index=g.index), pd.Series(1.0, index=g.index), "run"


def main():
    X = load(os.path.join(IN, "*", "*", "rep*"), {"workload": -3, "config": -2, "rep": -1})
    if X.empty:
        print(f"no core-scaling runs found under {IN} - run 'revision_measurements.sh cores' first")
        return 1
    rows, eq = [], []
    for wl, g in X.groupby("workload"):
        g = g.copy()
        wpp, passes, unit = work(g, wl)
        g["rate"] = wpp / g.t_compute_s                                   # units per second (per pass)
        card = g.config.eq("fpga").astype(float)
        g["E_unit"] = (g.Eexact_pkg0 + g.Eexact_dram0 + card * g.Eexact_card) / (passes * wpp)
        f = g[g.config == "fpga"]
        if f.empty:
            continue
        RF, EF = f.rate.median(), f.E_unit.median()
        for cfg, c in g[g.config != "fpga"].groupby("config"):
            k = int(cfg.split("_k")[1])
            rows.append(dict(workload=wl, unit=unit, cores=k, n=len(c), rate=c.rate.median(),
                             E_unit=c.E_unit.median(), rate_fpga=RF, E_fpga=EF,
                             speedup_fpga=RF / c.rate.median(), energy_adv_fpga=c.E_unit.median() / EF,
                             min_samples=int(c.samples.min())))
        C = pd.DataFrame([r for r in rows if r["workload"] == wl]).sort_values("cores")
        k, R, E = C.cores.values.astype(float), C.rate.values, C.E_unit.values
        if RF <= R.max():
            i = int(np.argmax(R >= RF))
            kt = k[0] if i == 0 else np.interp(RF, R[i - 1:i + 1], k[i - 1:i + 1])
            kt_note = "interpolated" if i > 0 else f"CPU already faster with {int(k[0])} core(s)"
        else:
            slope = (R[-1] - R[-2]) / (k[-1] - k[-2])                      # marginal rate per added core
            kt = k[-1] + (RF - R[-1]) / slope if slope > 0 else np.inf
            kt_note = f"extrapolated beyond {int(k[-1])} cores"
        below = np.where(E <= EF)[0]
        if len(below) == 0:
            ke, ke_note = np.nan, "none: CPU needs more energy per unit at every core count"
        else:
            i = below[0]
            ke = k[0] if i == 0 else np.interp(EF, [E[i], E[i - 1]], [k[i], k[i - 1]])
            ke_note = "interpolated" if i > 0 else f"CPU already needs less energy with {int(k[0])} core(s)"
        eq.append(dict(workload=wl, unit=unit, rate_fpga=RF, E_fpga=EF, k_time=kt, k_time_note=kt_note,
                       k_energy=ke, k_energy_note=ke_note, E_cpu_min=E.min(), k_at_E_cpu_min=int(k[np.argmin(E)]),
                       energy_adv_at_best_k=E.min() / EF, parallel_eff_24=R[-1] / (k[-1] * R[0])))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "rev_cores.csv"), index=False)
    Q = pd.DataFrame(eq)
    Q.to_csv(os.path.join(OUT, "rev_core_equiv.csv"), index=False)
    pd.set_option("display.width", 200)
    print(Q.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
