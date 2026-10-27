#!/usr/bin/env python3
"""conc_engaged.py - engaged power and per-option dispatch costs (addendum v0.9, step 5b) from the duty-cycle runs.

Input: results/conc_duty2_summary.csv (scripts/conc_duty.py run 2, 27 Oct 2026, v0.9 agent with TCP_NODELAY); run 1
(conc_duty_summary.csv, agent without TCP_NODELAY) is reported only. Medians of 3 repeats per job.
Per device d (jobs d, d_f10, d_f50; f = measured busy fraction), on d's own server (frodo: card, fcpu; Supermicro:
gpu, scpu), power above that server's idle:
  P = f a + (1 - f) b    least squares; a = busy power, b = ENGAGED power (worker loaded, waiting)
  engaged_W[d] = max(b, 0)   (negative b: the CPU device's busy power falls at low duty; not a saving from waiting)
For the Supermicro's devices, the energy per option beyond the model's single-run input:
  overhead_J[d] (at the Supermicro) = median over d's jobs of [(P_smc - (1 - f) engaged_W[d]) / rate] - dE(class)
  coord_J[d]    (at frodo)          = median over d's jobs of [(P_frodo - P_frodo,idle) / rate]   (the coordinator)
(frodo's devices: alone energy per option within 8% of the model in step 5: no overhead.)
Same-server interactions and the pairs' C at full load are reported (descriptive).
Output: results/concurrency/engaged.json
"""
import csv, json, os, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import conc_duty as D

RES = S.RES
SRV = {"card": "P_frodo_W", "fcpu": "P_frodo_W", "gpu": "P_smc_W", "scpu": "P_smc_W"}


def load(name):
    rows = list(csv.DictReader(open(os.path.join(RES, name))))
    med = lambda j, k: st.median(float(r[k]) for r in rows if r["job"] == j and r.get(k) not in (None, ""))
    return rows, med


def fit(pts):
    sff = sum(f * f for f, _ in pts); sgg = sum((1 - f) ** 2 for f, _ in pts); sfg = sum(f * (1 - f) for f, _ in pts)
    sfy = sum(f * y for f, y in pts); sgy = sum((1 - f) * y for f, y in pts)
    det = sff * sgg - sfg * sfg
    return (sfy * sgg - sgy * sfg) / det, (sgy * sff - sfy * sfg) / det


def main():
    name = os.environ.get("DUTY_SUMMARY", "conc_duty2_summary.csv")
    rows, med = load(name)
    M = S.Model()
    out = dict(source=name, engaged_W={}, busy_W={}, overhead_J={}, coord_J={}, fit={}, interactions={}, pairs_full_load={},
               definition="own-server P - P_idle = f a + (1 - f) b; engaged_W = max(b, 0); overhead_J (Supermicro) and coord_J "
                          "(frodo) per remote option")
    for d in ("card", "fcpu", "gpu", "scpu"):
        js = (d, f"{d}_f10", f"{d}_f50")
        k = SRV[d]
        pts = [(med(j, f"{d}_busy_fraction"), med(j, k) - med("idle", k)) for j in js]
        a, b = fit(pts)
        eng = max(b, 0.0)
        out["engaged_W"][d], out["busy_W"][d] = eng, a
        c, t, m = D.CLASS[d]
        dE = M.C[(d, c, t, m)]["dE"]
        rec = dict(a_W=a, b_W=b, points=[dict(job=j, f=f, P_above_idle_W=y) for j, (f, y) in zip(js, pts)],
                   max_residual_W=max(abs(y - (f * a + (1 - f) * b)) for f, y in pts), model_dE_J=dE, cls=[c, t, m])
        if d in ("gpu", "scpu"):
            e_s = [((med(j, "P_smc_W") - med("idle", "P_smc_W")) - (1 - f) * eng) / med(j, f"{d}_rate_per_s") for j, (f, _) in zip(js, pts)]
            e_f = [(med(j, "P_frodo_W") - med("idle", "P_frodo_W")) / med(j, f"{d}_rate_per_s") for j in js]
            out["overhead_J"][d] = max(0.0, st.median(e_s) - dE)
            out["coord_J"][d] = max(0.0, st.median(e_f))
            rec.update(energy_per_option_smc_J=e_s, energy_per_option_frodo_J=e_f)
        out["fit"][d] = rec
    tot = lambda j: med(j, "P_frodo_W") + med(j, "P_smc_W")
    for j, (dd, other) in (("card_f10+fcpu", ("card_f10", "fcpu")), ("fcpu_f50+card", ("fcpu_f50", "card")),
                           ("scpu_f50+gpu", ("scpu_f50", "gpu")), ("gpu_f50+scpu", ("gpu_f50", "scpu"))):
        out["interactions"][j] = dict(extra_W=tot(j) - tot(dd) - tot(other) + tot("idle"))
    if any(r["job"] == "gpu+scpu" for r in rows):
        for k in ("P_frodo_W", "P_smc_W"):
            out["pairs_full_load"][f"gpu+scpu {k}"] = med("gpu+scpu", k) - med("gpu", k) - med("scpu", k) + med("idle", k)
    os.makedirs(os.path.join(RES, "concurrency"), exist_ok=True)
    json.dump(out, open(os.path.join(RES, "concurrency", "engaged.json"), "w"), indent=1)
    for d, x in out["fit"].items():
        line = f"{d:<5} busy {x['a_W']:6.1f} W  engaged {x['b_W']:6.1f} W -> {out['engaged_W'][d]:5.1f} W (residual {x['max_residual_W']:.1f} W)"
        if d in out["overhead_J"]:
            line += f"; per option: Supermicro {', '.join(f'{e * 1e3:.1f}' for e in x['energy_per_option_smc_J'])} mJ (model {x['model_dE_J'] * 1e3:.1f}) " \
                    f"-> overhead {out['overhead_J'][d] * 1e3:.1f} mJ; frodo {', '.join(f'{e * 1e3:.1f}' for e in x['energy_per_option_frodo_J'])} mJ -> coord {out['coord_J'][d] * 1e3:.1f} mJ"
        print(line)
    for j, x in {**out["interactions"], **out["pairs_full_load"]}.items():
        print(f"  {j:<22} extra {x['extra_W'] if isinstance(x, dict) else x:+.1f} W")


if __name__ == "__main__":
    main()
