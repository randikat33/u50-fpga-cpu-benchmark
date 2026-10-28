#!/usr/bin/env python3
"""recount_fp64.py - addendum v0.10, Part B: the RTX rate model with float<->double conversions counted at their
measured rate. A registered analysis of existing measurements (the RTX sessions of v0.9), so a consistency check, not a
blind test; the blind part of Part B is the microbenchmark's outcome (gpu/fp64_ubench.cu, HF2-HF3 of the addendum).

The registered model (predict_gpu_4070tis.py, gpu_counts.py) put every F2F instruction on the conversion/special-function
pipe (16 per SM per clock). Here an F2F whose source or destination is 64-bit (F2F.F64.F32, F2F.F32.F64, ...) is
counted instead
  shared:    on the fp64 pipe, with the fp64 limit set to the microbenchmark's measured DFMA rate (default), or
  separate:  on its own pipe at the microbenchmark's measured F2F rate.
Everything else (counts, occupancy, eta, pipe energies, settings) is the registered v0.9 RTX model.

  cuobjdump -sass build/hs_gpu > build/hs_gpu.sass                              (on the RTX desktop)
  python3 scripts/recount_fp64.py --sass build/hs_gpu.sass --fp64-rate 2.0 --cvt-rate 2.0
  python3 scripts/recount_fp64.py --sass build/hs_gpu.sass --fp64-rate X --cvt-rate Y   (X, Y from fp64_ubench)
Output: results/analysis_rtx_v10/recount_fp64.{md,json}
"""
import argparse, json, os, re, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpu_counts as GC
import predict_gpu_4070tis as PG

ROOT = PG.ROOT
RES = PG.RES
OUT = os.path.join(RES, "analysis_rtx_v10")
ORIG_PIPE = GC.pipe


def pipe_cvt(target):
    def f(op):
        o = op.split(".")
        if o[0] == "F2F" and any(x.endswith("64") for x in o[1:]):
            return target
        return ORIG_PIPE(op)
    return f


def counts(sass, tpb=256):
    F = GC.parse(sass)
    out = {}
    for name, ins in F.items():
        m = re.search(r"price_kernelILi(\d+)ELi(\d+)ELi(\d+)ELi(\d+)E", name)
        if not m or int(m.group(1)) != tpb:
            continue
        key = GC.KERNELS[(int(m.group(2)), int(m.group(3)), int(m.group(4)))]
        loops = [(s, e) for s, e in GC.back_edges(ins) if e - s > 0x10]
        idx = {ad: i for i, (ad, _) in enumerate(ins)}
        L = [(idx[s], idx[e]) for s, e in loops]
        step = ins[L[0][0]:L[0][1] + 1]
        vs, _ = GC.step_loop(step)
        res = dict(per_pair_step=dict(vs))
        if key.startswith("basket"):
            asset, pair = ins[L[1][0]:L[1][1] + 1], ins[L[2][0]:L[2][1] + 1]
            res.update(per_asset=dict(GC.vec([t for _, t in asset]) - GC.vec([t for _, t in step])),
                       per_pair=dict(GC.vec([t for _, t in pair]) - GC.vec([t for _, t in asset])))
        else:
            pair = ins[L[1][0]:L[1][1] + 1]
            res.update(per_pair=dict(GC.vec([t for _, t in pair]) - GC.vec([t for _, t in step])))
        out[key] = res
    return out


def rates(P, C, lim_extra, CE=None, e_extra=None):
    """steady-state rate, binding pipe and energy per path-step of every setting. CE: counts with 64-bit conversions on
    their own key (cvt64), used for the energy of the fp64-pipe instructions; e_extra: J per thread instruction of the
    fp64 pipe (DFMA/DADD) and of 64-bit conversions, from the microbenchmark (none in the registered rule)"""
    P = dict(P, lim=dict(P["lim"], **lim_extra))
    U = PG.ubench(P)
    R = PG.regs(P)
    occ = {k: PG.occupancy(P, r) for k, r in R.items()}
    eta = PG.eta_of(P, U)[0]
    e = PG.pipe_energy(P, U)
    out = {}
    for s in PG.settings():
        k = PG.KMAP[(s["case"], s["scheme"])]
        rate, bind, ir = PG.steady(P, C, k, s["M"], occ[k], eta)
        dyn = PG.p_dyn(e, ir)
        if e_extra and CE is not None:
            lim = dict(P["lim"], cvt64=P["lim"].get("cvt64", 1.0))
            v = PG.per_pair(CE, k, s["M"], lim)
            pairs_per_s = rate / (2 * s["M"])
            dyn += sum(e_extra[q] * v.get(q, 0.0) * pairs_per_s for q in e_extra)
        e_ps = (U["idle"]["gpu"] + dyn + U["idle"]["host"]) / rate      # J per path-step, GPU + host package (pipe-energy rule)
        out[(s["case"], s["target"], s["scheme"])] = (rate, bind, e_ps)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sass", required=True)
    ap.add_argument("--platform", default="rtx4070tis", choices=("rtx4070tis", "v100"))
    ap.add_argument("--fp64-rate", type=float, default=None, help="measured DFMA thread ops per SM per clock (default: the registered limit)")
    ap.add_argument("--cvt-rate", type=float, default=None, help="measured F2F (64-bit) thread ops per SM per clock (separate variant)")
    ap.add_argument("--e-fp64-pj", type=float, default=0.0, help="measured energy per DFMA/DADD thread op above idle (pJ)")
    ap.add_argument("--e-cvt-pj", type=float, default=0.0, help="measured energy per 64-bit F2F thread op above idle (pJ), the FADD of the loop removed")
    a = ap.parse_args()
    P = PG.platform(a.platform)
    fp64 = a.fp64_rate or P["lim"]["fp64"]
    cvt = a.cvt_rate or fp64
    GC.pipe = ORIG_PIPE
    C0 = counts(a.sass)
    GC.pipe = pipe_cvt("fp64")
    Cs = counts(a.sass)
    GC.pipe = pipe_cvt("cvt64")
    Cp = counts(a.sass)
    GC.pipe = ORIG_PIPE
    n_cvt = {k: (v["per_pair_step"].get("cvt64", 0), v["per_pair"].get("cvt64", 0), v.get("per_asset", {}).get("cvt64", 0)) for k, v in Cp.items()}
    R0 = rates(P, C0, {})
    ee = {"fp64": a.e_fp64_pj * 1e-12, "cvt64": a.e_cvt_pj * 1e-12} if (a.e_fp64_pj or a.e_cvt_pj) else None
    Rs = rates(P, Cs, {"fp64": fp64}, Cp, ee)
    Rp = rates(P, Cp, {"fp64": fp64, "cvt64": cvt}, Cp, ee)
    meas = None
    if a.platform == "rtx4070tis":
        A = json.load(open(os.path.join(RES, "analysis_rtx_v09", "rtx_v09.json")))
        meas = {(x["case"], x["target"], x["scheme"]): x for x in A["per_setting"]}
    rows = []
    for key in sorted(R0):
        r = dict(case=key[0], target=key[1], scheme=key[2], rate_registered=R0[key][0], bind_registered=R0[key][1],
                 rate_shared=Rs[key][0], bind_shared=Rs[key][1], rate_separate=Rp[key][0], bind_separate=Rp[key][1])
        if meas:
            x = meas[key]
            tau = x["t_ratio"] / R0[key][0]        # measured time per path-step (t_ratio = measured time x registered rate)
            e_meas = x["Ess_meas_J"] / [s for s in PG.settings() if (s["case"], s["target"], s["scheme"]) == key][0]["W"]
            r.update(t_ratio_registered=tau * R0[key][0], t_ratio_shared=tau * Rs[key][0], t_ratio_separate=tau * Rp[key][0],
                     e_ratio_registered=e_meas / R0[key][2], e_ratio_shared=e_meas / Rs[key][2], e_ratio_separate=e_meas / Rp[key][2],
                     fp64_bound_registered=x["bind_ss"] == "fp64")
        rows.append(r)
    os.makedirs(OUT, exist_ok=True)
    doc = dict(platform=a.platform, sass=os.path.abspath(a.sass), fp64_rate=fp64, cvt_rate=cvt, e_fp64_pJ=a.e_fp64_pj, e_cvt_pJ=a.e_cvt_pj,
               conversions_64bit=n_cvt, rows=rows)
    json.dump(doc, open(os.path.join(OUT, f"recount_fp64_{a.platform}.json"), "w"), indent=1)
    L = [f"# Rate model with 64-bit conversions recounted ({a.platform}; fp64 limit {fp64:.2f}, conversion limit {cvt:.2f} per SM per clock; "
         f"energy per op: fp64 {a.e_fp64_pj:.1f} pJ, 64-bit conversion {a.e_cvt_pj:.1f} pJ)", "",
         "64-bit F2F per pair-step / per pair (/ per asset): " + "; ".join(f"{k} {v[0]:.2f} / {v[1]:.0f}" + (f" / {v[2]:.0f}" if v[2] else "") for k, v in n_cvt.items()), ""]
    if meas:
        def summ(lab, sel, q="t"):
            out = []
            for var in ("registered", "shared", "separate"):
                t = [r[f"{q}_ratio_{var}"] for r in rows if sel(r)]
                out.append(f"{var}: median {statistics.median(t):.3f}, within 15% {sum(abs(x - 1) <= 0.15 for x in t)} of {len(t)}, "
                           f"within 30% {sum(abs(x - 1) <= 0.30 for x in t)} of {len(t)}")
            return f"- **{lab}:** " + "; ".join(out)
        L += [summ("fp64-bound settings (registered model)", lambda r: r["fp64_bound_registered"]),
              summ("other settings", lambda r: not r["fp64_bound_registered"]),
              summ("all 36", lambda r: True), "", "Energy per option, measured / predicted (pipe-energy rule at the recounted rate):",
              summ("fp64-bound settings", lambda r: r["fp64_bound_registered"], "e"), summ("all 36", lambda r: True, "e"), "",
              "| Case | Target | Method | binds (reg.) | binds (shared) | t meas/pred: registered | shared | separate | E meas/pred: registered | shared |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| {r['case']} | {r['target']} | {r['scheme']} | {r['bind_registered']} | {r['bind_shared']} | {r['t_ratio_registered']:.3f} | "
                     f"{r['t_ratio_shared']:.3f} | {r['t_ratio_separate']:.3f} | {r['e_ratio_registered']:.3f} | {r['e_ratio_shared']:.3f} |")
    else:
        L += ["| Case | Target | Method | rate registered (G/s) | rate shared | binds |", "|---|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| {r['case']} | {r['target']} | {r['scheme']} | {r['rate_registered'] / 1e9:.1f} | {r['rate_shared'] / 1e9:.1f} | {r['bind_shared']} |")
    open(os.path.join(OUT, f"recount_fp64_{a.platform}.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
