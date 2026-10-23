#!/usr/bin/env python3
"""analyse_phase6.py - analysis of the frodo sessions of Part B (protocol v0.6 Section 23, addendum v0.6.1).

  HF1     : card steady-state energy per option at the socket boundary, --wait callback / --wait xrt, per setting
            (value = median of the two session medians); holds if <= 0.90 at all 36 settings
  SMT     : the registered 24/48 choice per (case, scheme, steps) from session 1 (s0_t48 against s0_c24), and whether
            session 2 agrees; the CPU's socket results with the chosen thread count
  repro   : session 1 against session 2, and phase 6 against Part A (card, --wait xrt)
  tel     : the exploratory telemetry check (host socket and server power, 20 ms / 200 ms / off)
  clocks, wall : measured busy clock of socket 0 under load; wall power
Writes results/analysis_phase6/phase6.md, phase6.json and cpu_socket_better.csv (the CPU-native and matched socket
results with the registered thread choice, per setting: E per run, steady-state E, time, steady time per path-step),
which scripts/analyse_gpu.py uses for the re-evaluation of HG1 and HG4 (--cpu48).
"""
import csv, json, math, os, statistics as st

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "analysis_phase6")


def load(s):
    return {(r["impl"], r["wait"], r["ini"], r["cfg"], r["kind"], r["case"], r["scheme"], r["steps"], r["target"]): r
            for r in csv.DictReader(open(os.path.join(RES, f"phase6_summary_s{s}.csv")))}


def settings():
    out = []
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("steps"):
                out.append(dict(case=r["case"], scheme=r["scheme"], target=r["target"], M=r["steps"], N=int(r["pairs"]), W=float(r["path_steps"])))
    return out


def e(r, b="socket"):
    return float(r[f"E_{b}_per_run_J"]) / float(r["path_steps"])


def ss_row(S, impl, wait, ini, cfg, case, sc, M, target):
    k = (impl, wait, ini, cfg, "ss", case, sc, M, "")
    return S[k] if k in S else S[(impl, wait, ini, cfg, "set", case, sc, M, target)]


def main():
    os.makedirs(OUT, exist_ok=True)
    S1, S2 = load(1), load(2)
    sets = settings()
    res, L = {}, ["# frodo sessions of Part B (phase 6): HF1, SMT, reproducibility, telemetry, clocks", ""]
    # HF1
    hf = []
    for x in sets:
        rat = []
        for S in (S1, S2):
            cb = ss_row(S, "fpga", "callback", "registered", "card", x["case"], x["scheme"], x["M"], x["target"])
            xr = ss_row(S, "fpga", "xrt", "registered", "card", x["case"], x["scheme"], x["M"], x["target"])
            rat.append((e(cb), e(xr)))
        v = st.median(a for a, _ in rat) / st.median(b for _, b in rat)
        hf.append(dict(case=x["case"], scheme=x["scheme"], target=x["target"], ratio=v))
    res["HF1"] = dict(per_setting=hf, median=st.median(h["ratio"] for h in hf), min=min(h["ratio"] for h in hf), max=max(h["ratio"] for h in hf),
                      holds=all(h["ratio"] <= 0.90 for h in hf))
    L += [f"## HF1 (non-polling host wait)", "",
          f"callback / xrt steady energy per option, socket boundary: median {res['HF1']['median']:.4f}, range {res['HF1']['min']:.4f}-{res['HF1']['max']:.4f}; "
          f"<= 0.90 at {sum(h['ratio'] <= 0.9 for h in hf)} of {len(hf)} settings. **HF1 {'holds' if res['HF1']['holds'] else 'fails'}.**", ""]
    # SMT
    keys = sorted({(k[5], k[6], k[7]) for k in S1 if k[3] == "s0_c24"})
    smt, agree = [], 0
    for impl in ("avx512", "native"):
        for case, sc, M in keys:
            tgt = next(x["target"] for x in sets if (x["case"], x["scheme"], x["M"]) == (case, sc, M))
            r = []
            for S in (S1, S2):
                r24 = ss_row(S, impl, "", "registered", "s0_c24", case, sc, M, tgt)
                r48 = ss_row(S, impl, "", "registered", "s0_t48", case, sc, M, tgt)
                r.append(e(r48) / e(r24))
            choose48 = r[0] < 1
            agree += (r[1] < 1) == choose48
            smt.append(dict(impl=impl, case=case, scheme=sc, steps=M, ratio_s1=r[0], ratio_s2=r[1], threads=48 if choose48 else 24))
    res["SMT"] = dict(per_key=smt, agree_s2=agree, n=len(smt))
    L += ["## SMT (24 vs 48 threads, registered choice from session 1)", ""]
    for impl in ("avx512", "native"):
        rr = [x["ratio_s1"] for x in smt if x["impl"] == impl]
        L.append(f"- {impl}: 48 threads chosen at {sum(x['threads'] == 48 for x in smt if x['impl'] == impl)} of {len(rr)} keys; e48/e24 session 1 median {st.median(rr):.3f} "
                 f"({min(rr):.3f}-{max(rr):.3f})")
    L += [f"- session 2 agrees with the choice at {agree} of {len(smt)} keys", ""]
    # CPU socket results with the registered choice (median of the two sessions; 48-thread values from phase 6)
    rows = []
    for impl, combo in (("avx512", "cpu_matched"), ("native", "cpu_native")):
        for x in sets:
            ch = next(s["threads"] for s in smt if (s["impl"], s["case"], s["scheme"], s["steps"]) == (impl, x["case"], x["scheme"], x["M"]))
            if ch != 48:
                continue
            Es, Ess, ts, taus = [], [], [], []
            for S in (S1, S2):
                st_ = S[(impl, "", "registered", "s0_t48", "set", x["case"], x["scheme"], x["M"], x["target"])]
                ssr = ss_row(S, impl, "", "registered", "s0_t48", x["case"], x["scheme"], x["M"], x["target"])
                Es.append(float(st_["E_socket_per_run_J"])); ts.append(float(st_["t_per_run_s"]))
                Ess.append(e(ssr) * x["W"]); taus.append(float(ssr["t_per_run_s"]) / float(ssr["path_steps"]))
            rows.append(dict(case=x["case"], target=x["target"], scheme=x["scheme"], combo=f"{combo}_{x['scheme']}", threads=48,
                             E_J=st.median(Es), Ess_J=st.median(Ess), t_s=st.median(ts), tau_s=st.median(taus)))
    with open(os.path.join(OUT, "cpu_socket_better.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    # reproducibility
    d = [abs(e(S1[k]) / e(S2[k]) - 1) for k in S1 if k in S2 and float(S2[k]["path_steps"]) > 0 and float(S2[k][f"E_socket_per_run_J"]) > 0]
    res["repro"] = dict(median=st.median(d), max=max(d), over5=sum(x > 0.05 for x in d), n=len(d))
    L += ["## Reproducibility", "", f"- session 1 vs 2, energy per path-step at the socket boundary, all 316 groups: median |diff| {res['repro']['median'] * 100:.2f}%, "
          f"max {res['repro']['max'] * 100:.1f}%, {res['repro']['over5']} groups over 5%"]
    A = {}
    for f in ("phase5_summary_s1.csv", "phase5_summary_s2.csv", "phase5s_summary_s1.csv", "phase5s_summary_s2.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["impl"] == "fpga":
                A.setdefault((r["kind"], r["case"], r["scheme"], r["steps"], r["target"]), []).append(e(r))
    rp = []
    for k, v in A.items():
        kk = ("fpga", "xrt", "registered", "card") + k
        if kk in S1 and kk in S2:
            rp.append((k, st.median([e(S1[kk]), e(S2[kk])]) / st.median(v)))
    ss_r = [x for k, x in rp if k[0] == "ss" or float(next(s["W"] for s in sets if (s["case"], s["scheme"], s["M"]) == (k[1], k[2], k[3]))) >= 1e10]
    res["vs_partA"] = dict(steady_median=st.median(ss_r), steady_min=min(ss_r), steady_max=max(ss_r), all_max=max(x for _, x in rp))
    L.append(f"- card (--wait xrt) phase 6 vs Part A, steady groups: median {res['vs_partA']['steady_median']:.4f} ({res['vs_partA']['steady_min']:.4f}-"
             f"{res['vs_partA']['steady_max']:.4f}); short single runs up to {res['vs_partA']['all_max']:.2f}x (see the host timing check)")
    L.append("")
    # telemetry
    tel = []
    for case, sc, M in (("I", "ft", "1280"), ("P1", "qe", "1")):
        for ini in ("registered", "tel200", "teloff"):
            v = [S[("fpga", "xrt", ini, "card", "ss", case, sc, M, "")] for S in (S1, S2)]
            tel.append(dict(case=case, scheme=sc, steps=M, ini=ini, P_card=st.median(float(r["P_card_W"]) for r in v),
                            P_host_socket=st.median(float(r["P_host_socket_W"]) for r in v), P_host_server=st.median(float(r["P_host_server_W"]) for r in v),
                            t_run=st.median(float(r["t_per_run_s"]) for r in v)))
    res["telemetry"] = tel
    L += ["## Telemetry check (exploratory; card --wait xrt, steady state; median of the sessions)", "", "| Kernel | xrt.ini | card W | host socket W | server W | ms/run |", "|---|---|---|---|---|---|"]
    for t in tel:
        L.append(f"| {t['case']} {t['scheme']} M{t['steps']} | {t['ini']} | {t['P_card']:.1f} | {t['P_host_socket']:.1f} | {t['P_host_server']:.1f} | {t['t_run'] * 1e3:.1f} |")
    L.append("")
    # clocks and wall
    L += ["## Clocks and wall power", ""]
    for s in (1, 2):
        rows_c = list(csv.DictReader(open(os.path.join(RES, "phase6", f"s{s}_clocks.csv"))))
        busy = [float(r["busy_mhz"]) for r in rows_c if r["socket"] == "0" and float(r["busy_frac_mean"]) > 0.5]
        w = [float(r["wall_w"]) for r in csv.DictReader(open(os.path.join(RES, "phase6", f"s{s}_wall.csv")))]
        res[f"clocks_s{s}"] = dict(busy_mhz_median=st.median(busy), min=min(busy), max=max(busy), n=len(busy))
        res[f"wall_s{s}"] = dict(median=st.median(w), min=min(w), max=max(w), n=len(w))
        L.append(f"- session {s}: socket 0 busy clock under load (busy > 50%) median {st.median(busy):.0f} MHz ({min(busy):.0f}-{max(busy):.0f}, {len(busy)} intervals); "
                 f"wall power median {st.median(w):.0f} W ({min(w):.0f}-{max(w):.0f})")
    json.dump(res, open(os.path.join(OUT, "phase6.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, "phase6.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
