#!/usr/bin/env python3
"""predict_gpu.py - the GPU prediction of paper 2 (protocol v0.6, Section 22a), registered before any timed GPU run.

Inputs (no GPU run of a pricing kernel at a setting or timed size):
  results/gpu_counts.json        dynamic instruction counts per pair-step / asset / pair from the SASS (gpu_counts.py)
  results/gpu_b2/hs_gpu_ptxas.log registers per thread -> resident blocks per SM
  results/gpu_ubench/*           microbenchmarks: achieved rates (eta), busy and idle powers, start-up cost, host power
  results/settings.csv, settings_s.csv   the registered settings
  results/analysis/measured_vs_predicted.csv, results/analysis_s/measured_vs_predicted_s.csv   measured card and CPU
                                  energies (Part A), only to name the predicted lowest-energy platform
Model (V100, 80 SMs, clock f = 1245 MHz locked):
  per SM and clock, thread instructions: FMA pipe 64, ALU 64, XU 16, FP64 32, issue 128; a warp issues at most one
  dependent instruction every L = 4 cycles, so with w resident warps the issue rate is at most 32 w / L.
  cycles per pair (per thread, w warps resident): max over pipes of (count / limit) x (32 w), times 1 / eta;
  kernel time = (pairs per thread) x cycles per pair / f;  run time = kernel time + t0 (start-up microbenchmark).
  Power, registered rule: P_gpu = P_idle + d (P_dom - P_idle), d = kernel time / run time, P_dom the busy power of
  the microbenchmark of the binding pipe (FMA -> fp32, ALU -> int, XU -> sfu). An exploratory variant ("mix") adds
  each pipe's busy power in proportion to its utilisation; it is reported, not tested.
  Host: socket 1 (runs the host thread) and socket 0 from the microbenchmarks in the same wait mode.
Writes results/predictions_gpu/predictions_gpu.json, .md and their SHA-256 files.
"""
import csv, hashlib, json, math, os, re, statistics, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "predictions_gpu")
NSM, F_HZ, L_LAT = 80, 1245e6, 4.0
LIM = {"fma": 64.0, "alu": 64.0, "xu": 16.0, "fp64": 32.0, "issue": 128.0}
H_FRODO = {"socket": 49.151, "server": 92.940}      # frodo's host power while it waits for the card (paper 1 constants)
KMAP = {("I", "ft"): "heston_ft", ("I", "qe"): "heston_qe", ("II", "ft"): "heston_ft", ("II", "qe"): "heston_qe",
        ("III", "ft"): "heston_ft", ("III", "qe"): "heston_qe", ("P1", "ft"): "heston_ft", ("P1", "qe"): "heston_qe",
        ("B", "ft"): "basket_ft", ("B", "qe"): "basket_qe", ("A", "plain"): "asian_plain", ("A", "cv"): "asian_cv"}
TPL = {"heston_ft": "Li0ELi0ELi0E", "heston_qe": "Li0ELi1ELi0E", "basket_ft": "Li1ELi0ELi0E",
       "basket_qe": "Li1ELi1ELi0E", "asian_plain": "Li2ELi0ELi0E", "asian_cv": "Li2ELi0ELi1E"}
CASES = ("I", "II", "III", "P1", "B", "A")
TARGETS = ("1%", "0.3%", "0.1%")
METH = {c: ("ft", "qe") for c in ("I", "II", "III", "P1", "B")}
METH["A"] = ("plain", "cv")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def ubench():
    U = {}
    for b in ("idle", "fp32", "int", "sfu", "startup", "startup_block"):
        d = os.path.join(RES, "gpu_ubench", b)
        m = json.load(open(os.path.join(d, "meta.json")))
        r = dict(re.findall(r"^RESULT (\w+)=(\S+)", open(os.path.join(d, "stdout.log")).read(), re.M))
        P = m["P_W"]
        U[b] = dict(gpu=P["gpu"], s1=P["pkg1"] + P["dram1"], s0=P["pkg0"] + P["dram0"], t_run=float(r["t_per_run_s"]),
                    ipc=float(r["thread_instr_per_sm_per_clk"]) if "thread_instr_per_sm_per_clk" in r else None,
                    disturbed=m["disturbed"], sm=m["sm_mhz_median"])
    return U


def regs():
    txt = open(os.path.join(RES, "gpu_b2", "hs_gpu_ptxas.log")).read()
    out, cur = {}, None
    for line in txt.splitlines():
        m = re.search(r"Function properties for (\S+)", line) or re.search(r"Compiling entry function '(\S+)'", line)
        if m:
            cur = m.group(1)
        m = re.search(r"Used (\d+) registers", line)
        if m and cur:
            out[cur] = int(m.group(1))
    R = {}
    for k, t in TPL.items():
        R[k] = next(v for n, v in out.items() if f"price_kernelILi256E{t}" in n)
    return R


def occupancy(r):
    per_warp = math.ceil(r * 32 / 256) * 256
    return max(1, min(8, 65536 // (8 * per_warp)))


def settings():
    S = []
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("path_steps"):
                S.append(dict(case=r["case"], target=r["target"], scheme=r["scheme"], M=int(r["steps"]), N=int(r["pairs"]),
                              W=float(r["path_steps"])))
    return S


def counts_per_pair(C, k, M):
    c = C[k]
    v = {p: 0.0 for p in LIM}
    for p in LIM:
        if k.startswith("basket"):
            v[p] = 8 * (M * c["per_pair_step"].get(p, 0) + c["per_asset"].get(p, 0)) + c["per_pair"].get(p, 0)
        else:
            v[p] = M * c["per_pair_step"].get(p, 0) + c["per_pair"].get(p, 0)
    return v


def kernel(C, k, M, N, occ, eta):
    """kernel time (s), binding pipe, utilisation per pipe at the binding rate, resident warps per SM"""
    v = counts_per_pair(C, k, M)
    T_full = NSM * occ * 256
    blocks = min(NSM * occ, math.ceil(N / 256))
    T = blocks * 256
    bps = math.ceil(blocks / NSM)
    w = min(bps * 8, math.ceil(min(N, T) / NSM / 32) if blocks >= NSM else 8)
    w = max(w, 1)
    per = {p: v[p] / LIM[p] for p in LIM}                  # SM cycles per thread-pair at each pipe's peak
    lat = v["issue"] * L_LAT / (32 * w)                     # dependent-issue limit with w warps
    bind = max(per, key=per.get)
    cyc_thread_pair = max(max(per.values()), lat)          # SM cycles per thread-pair (throughput view)
    if lat > max(per.values()):
        bind_eff = "latency"
    else:
        bind_eff = bind
    k_per_thread = math.ceil(N / T)
    t = k_per_thread * cyc_thread_pair * (32 * w) / (F_HZ * eta)
    util = {p: per[p] / cyc_thread_pair for p in LIM}
    return t, bind, bind_eff, util, w, blocks


def steady_rate(C, k, M, occ, eta):
    """path-steps per second at full occupancy, many pairs (per-pair overhead included)"""
    v = counts_per_pair(C, k, M)
    w = occ * 8
    per = {p: v[p] / LIM[p] for p in LIM}
    cyc = max(max(per.values()), v["issue"] * L_LAT / (32 * w))
    pairs_per_s = NSM * F_HZ * eta / cyc
    util = {p: per[p] / cyc for p in LIM}
    return pairs_per_s * 2 * M, max(per, key=per.get), util


def p_gpu(U, bind, util, duty, variant):
    idle = U["idle"]["gpu"]
    dP = {"fma": U["fp32"]["gpu"] - idle, "xu": U["sfu"]["gpu"] - idle}
    ui = U["int"]["ipc"] / 64.0                            # the int benchmark runs the ALU pipe and IMAD on the FMA pipe
    u_alu = ui * 4 / 6
    u_fma_int = ui * 2 / 6 * 64 / 64
    dP["alu"] = (U["int"]["gpu"] - idle - u_fma_int * dP["fma"]) / max(u_alu, 1e-9)
    if variant == "registered":
        dom = bind if bind in ("fma", "alu", "xu") else max(("fma", "alu", "xu"), key=lambda p: util[p])
        return idle + duty * dP[dom]
    return idle + duty * sum(min(util[p], 1.0) * dP[p] for p in ("fma", "alu", "xu"))


def main():
    os.makedirs(OUT, exist_ok=True)
    C = json.load(open(os.path.join(RES, "gpu_counts.json")))
    U = ubench()
    R = regs()
    OCC = {k: occupancy(r) for k, r in R.items()}
    # eta: geometric mean of achieved / peak in the three microbenchmarks (int peak from its SASS: 4 ALU + 2 IMAD per 6)
    eff = dict(fp32=U["fp32"]["ipc"] / 64.0, int=U["int"]["ipc"] / 96.0, sfu=U["sfu"]["ipc"] / 16.0)
    eta = (eff["fp32"] * eff["int"] * eff["sfu"]) ** (1 / 3)
    eta = min(eta, 1.0)
    t0 = {"spin": U["startup"]["t_run"], "block": U["startup_block"]["t_run"]}
    host = {"spin": dict(busy1=U["fp32"]["s1"], wait1=U["startup"]["s1"], busy0=U["fp32"]["s0"], wait0=U["startup"]["s0"]),
            "block": dict(busy1=U["startup_block"]["s1"], wait1=U["startup_block"]["s1"], busy0=U["startup_block"]["s0"], wait0=U["startup_block"]["s0"])}
    S = settings()
    meas = {}
    for f, filt in (("analysis/measured_vs_predicted.csv", None), ("analysis_s/measured_vs_predicted_s.csv", "same")):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if filt and r["variant"] != filt:
                continue
            meas[(r["case"], r["target"], r["boundary"], r["combo"])] = (float(r["E_meas_J"]), float(r["Ess_meas_J"]))
    rows = []
    for s in S:
        k = KMAP[(s["case"], s["scheme"])]
        occ = OCC[k]
        rate, bind_ss, util_ss = steady_rate(C, k, s["M"], occ, eta)
        for mode in ("spin", "block"):
            tk, bind, bind_eff, util, w, blocks = kernel(C, k, s["M"], s["N"], occ, eta)
            trun = tk + t0[mode]
            d = tk / trun
            h = host[mode]
            for var in ("registered", "mix"):
                Pg = p_gpu(U, bind, util, d, var)
                Pg_ss = p_gpu(U, bind_ss, util_ss, 1.0, var)
                P1 = d * h["busy1"] + (1 - d) * h["wait1"]
                P0 = d * h["busy0"] + (1 - d) * h["wait0"]
                E = dict(gpu_socket=(Pg + P1) * trun, gpu_alone=Pg * trun, gpu_frodo_socket=(Pg + H_FRODO["socket"]) * trun,
                         gpu_server=(Pg + P1 + P0) * trun, gpu_frodo_server=(Pg + H_FRODO["server"]) * trun)
                tss = s["W"] / rate
                Ess = dict(gpu_socket=(Pg_ss + h["busy1"]) * tss, gpu_alone=Pg_ss * tss, gpu_frodo_socket=(Pg_ss + H_FRODO["socket"]) * tss,
                           gpu_server=(Pg_ss + h["busy1"] + h["busy0"]) * tss, gpu_frodo_server=(Pg_ss + H_FRODO["server"]) * tss)
                rows.append(dict(case=s["case"], target=s["target"], scheme=s["scheme"], kernel=k, M=s["M"], N=s["N"], W=s["W"],
                                 wait=mode, power=var, occ=occ, blocks=blocks, warps_per_sm=w, bind=bind_eff, t_kernel_s=tk,
                                 t_run_s=trun, P_gpu_W=Pg, P_gpu_ss_W=Pg_ss, rate_ss_path_steps_per_s=rate, bind_ss=bind_ss,
                                 E_J=E, Ess_J=Ess))
    # predicted lowest-energy combination (GPU: registered power, the wait mode with less steady-state energy)
    def gsel(case, t, sc, var="registered"):
        c = [r for r in rows if r["case"] == case and r["target"] == t and r["scheme"] == sc and r["power"] == var]
        return min(c, key=lambda r: r["Ess_J"]["gpu_socket"])
    winners = []
    for case in CASES:
        for t in TARGETS:
            for b, gb in (("socket", "gpu_socket"), ("server", "gpu_server")):
                cand = {}
                for m in METH[case]:
                    for p in ("card", "cpu_matched", "cpu_native"):
                        cand[f"{p}_{m}"] = meas[(case, t, b, f"{p}_{m}")]
                    g = gsel(case, t, m)
                    cand[f"gpu_{m}"] = (g["E_J"][gb], g["Ess_J"][gb])
                wp = min(cand, key=lambda c: cand[c][0])
                ws = min(cand, key=lambda c: cand[c][1])
                card_best = min(cand[f"card_{m}"][1] for m in METH[case])
                gpu_best = min(cand[f"gpu_{m}"][1] for m in METH[case])
                winners.append(dict(case=case, target=t, boundary=b, winner_primary=wp, winner_steady=ws,
                                    gpu_vs_card_steady=gpu_best / card_best, E={c: v[0] for c, v in cand.items()},
                                    Ess={c: v[1] for c, v in cand.items()}))
    # hypotheses
    hyp = {}
    r1 = [r for r in rows if r["case"] == "I" and r["power"] == "registered" and r["wait"] == "spin"]
    rate = {r["scheme"]: r["rate_ss_path_steps_per_s"] for r in r1 if r["target"] == "0.1%"}
    ratio = rate["ft"] / rate["qe"]
    hyp["HG2"] = dict(statement="GPU QE/FT time per path-step between 2.06 and 3.72", predicted_ratio=ratio, predicted=2.06 <= ratio <= 3.72)
    hyp["HG3"] = dict(statement="GPU fixed cost per pricing run below 0.2 ms", predicted_t0_s=t0, predicted=t0["spin"] < 0.2e-3)
    hg1 = []
    for w in winners:
        if w["boundary"] != "socket":
            continue
        case = w["case"]
        a, b = METH[case]
        Ess = w["Ess"]
        plats = ("card", "cpu_matched", "cpu_native", "gpu")
        best = {m: min(Ess[f"{p}_{m}"] for p in plats) for m in (a, b)}
        gm = max(best[a], best[b]) / min(best[a], best[b])
        bm = a if best[a] <= best[b] else b
        vals = sorted(Ess[f"{p}_{bm}"] for p in plats)
        gp = vals[1] / vals[0]
        hg1.append(dict(case=case, target=w["target"], method_gain=gm, platform_gain=gp, holds=gm > gp))
    hyp["HG1"] = dict(statement="method gain > platform gain (three platforms, steady state, primary boundaries), every case and target",
                      per_setting=hg1, predicted=all(x["holds"] for x in hg1))
    hyp["HG4"] = dict(statement="break-even rule picks every single-run winner", predicted=True,
                      basis="it held 36/36 on the card and CPU (exploratory, 20 Oct); the GPU's fixed cost is small, so its single runs follow its per-step energy except for the shortest runs")
    hyp["HG5"] = dict(statement="GPU energy within 30% (one run, steady state) and steady-state winner right in >= 30 of 36",
                      predicted=True, basis="by construction of this prediction")
    hyp["HF1"] = dict(statement="non-polling card host wait cuts the card's steady-state energy per option at the socket boundary by >= 10% in every case",
                      predicted=True, basis="the busy-polling host draws about 49 W of the 82-92 W at the socket boundary (Part A)")
    hyp["HF2"] = dict(statement="matched code RMSE/eps <= 1.1 at all strikes, upper 95% bound <= 1.25, 200 replicates",
                      predicted=True, basis="predicted RMSE/eps of the settings 0.71-0.87 (Part A calibration); 0.86 at III QE 0.3% is the closest")
    open_q = [dict(case=w["case"], target=w["target"], gpu_over_card_steady=w["gpu_vs_card_steady"], v100_less_energy=w["gpu_vs_card_steady"] < 1)
              for w in winners if w["boundary"] == "socket"]
    inputs = {p: sha(os.path.join(RES, p)) for p in ("gpu_counts.json", "gpu_b2/hs_gpu_ptxas.log", "settings.csv", "settings_s.csv",
                                                       "analysis/measured_vs_predicted.csv", "analysis_s/measured_vs_predicted_s.csv")}
    for b in U:
        inputs[f"gpu_ubench/{b}/meta.json"] = sha(os.path.join(RES, "gpu_ubench", b, "meta.json"))
    doc = dict(generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), protocol="v0.6 Section 22a", inputs_sha256=inputs,
               constants=dict(n_sm=NSM, clock_hz=F_HZ, latency_cycles=L_LAT, pipe_limits=LIM, eta=eta, eta_parts=eff,
                              t0_s=t0, host_W=host, H_frodo_W=H_FRODO, registers=R, blocks_per_sm=OCC,
                              ubench={b: dict(gpu_W=v["gpu"], socket1_W=v["s1"], socket0_W=v["s0"]) for b, v in U.items()}),
               predictions=rows, winners=winners, hypotheses=hyp, open_question=open_q)
    json.dump(doc, open(os.path.join(OUT, "predictions_gpu.json"), "w"), indent=1, default=float)
    # markdown
    Lm = ["# GPU prediction (protocol v0.6, Section 22a), registered before any timed GPU run", "",
          f"Generated {doc['generated']}. V100 at a locked 1245 MHz; eta = {eta:.3f} (fp32 {eff['fp32']:.3f}, int {eff['int']:.3f}, sfu {eff['sfu']:.3f}); "
          f"t0 spin {t0['spin'] * 1e3:.3f} ms, block {t0['block'] * 1e3:.3f} ms; latency L = {L_LAT:.0f} cycles; blocks per SM {OCC}.", "",
          "## Readings of Section 22a used here (fixed before any timed GPU run)", "",
          "- Instruction counts are dynamic estimates from the SASS of the built binary (scripts/gpu_counts.py): cuRAND Philox refills at 1/4 per word drawn, "
          "the word-select switch at its average path, the every-second-step normal pair at 1/2, both branches of QE and of normcdfinvf at 1 (full divergence); "
          "work outside the step loop is counted per pair (and per asset in Case B).",
          "- Pipes on sm_70: the SASS of the integer microbenchmark shows that its IMAD instructions run on the FMA pipe beside the ALU pipe "
          "(93.9 thread instructions per SM per clock, against 64 for one pipe); IMAD is therefore counted on the FMA pipe and the integer "
          "benchmark's peak is 96 (4 ALU + 2 IMAD per 6 instructions). Conversions share the XU (16 per clock) with MUFU.",
          "- A warp issues a dependent instruction at most every L = 4 cycles; this limits short runs with few resident warps.",
          "- Resident blocks per SM from the registers per thread (ptxas; 256 threads per block, 65,536 registers per SM, at most 8 blocks).",
          "- Host power: socket 1 and socket 0 from the microbenchmarks in the same wait mode (busy: fp32 benchmark; waiting: start-up benchmark). "
          "Winners use, per setting, the wait mode with less predicted steady-state energy at the primary boundary.",
          "- Power: the registered rule (busy power of the binding pipe's microbenchmark, weighted by duty) is the tested prediction; "
          "the 'mix' variant (each pipe's busy power in proportion to its utilisation) is reported for information only.",
          "- Card and CPU energies used to name winners are the measured Part A values; GPU values are this prediction.", "",
          "## Steady-state rate and energy per path-step (registered power rule, spin wait, GPU + socket 1)", "",
          "| Kernel | M (steps) | Rate (G path-steps/s) | Binding pipe | P_gpu (W) | P_gpu mix (W) | nJ per path-step |", "|---|---|---|---|---|---|---|"]
    seen = set()
    for r in rows:
        if r["wait"] != "spin" or r["power"] != "registered":
            continue
        key = (r["kernel"], r["M"])
        if key in seen:
            continue
        seen.add(key)
        mix = next(x for x in rows if x["kernel"] == r["kernel"] and x["M"] == r["M"] and x["wait"] == "spin" and x["power"] == "mix")
        e = (r["P_gpu_ss_W"] + host["spin"]["busy1"]) / r["rate_ss_path_steps_per_s"] * 1e9
        Lm.append(f"| {r['kernel']} | {r['M']} | {r['rate_ss_path_steps_per_s'] / 1e9:.1f} | {r['bind_ss']} | {r['P_gpu_ss_W']:.1f} | {mix['P_gpu_ss_W']:.1f} | {e:.3f} |")
    Lm += ["", "## Every setting (spin wait, registered power): time and energy at the GPU's primary boundary (GPU + socket 1)", "",
           "| Case | Target | Method | Run time (ms) | Bound | E one run (J) | E steady (J) | E steady, mix power (J) |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["wait"] != "spin" or r["power"] != "registered":
            continue
        mix = next(x for x in rows if x["case"] == r["case"] and x["target"] == r["target"] and x["scheme"] == r["scheme"] and x["wait"] == "spin" and x["power"] == "mix")
        Lm.append(f"| {r['case']} | {r['target']} | {r['scheme']} | {r['t_run_s'] * 1e3:.3f} | {r['bind']} | {r['E_J']['gpu_socket']:.4g} | "
                  f"{r['Ess_J']['gpu_socket']:.4g} | {mix['Ess_J']['gpu_socket']:.4g} |")
    Lm += ["", "## Predicted lowest-energy combination (card and CPU: measured, Part A; GPU: this prediction)", "",
           "| Case | Target | Boundary | One run | Steady state | GPU / card, steady (best method each) |", "|---|---|---|---|---|---|"]
    for w in winners:
        Lm.append(f"| {w['case']} | {w['target']} | {w['boundary']} | {w['winner_primary']} | {w['winner_steady']} | {w['gpu_vs_card_steady']:.3f} |")
    Lm += ["", "## Hypotheses (Section 26): predicted verdicts", ""]
    for k, v in hyp.items():
        extra = ""
        if k == "HG2":
            extra = f" (predicted ratio {v['predicted_ratio']:.2f})"
        if k == "HG3":
            extra = f" (predicted t0 {t0['spin'] * 1e3:.3f} ms spin, {t0['block'] * 1e3:.3f} ms block)"
        if k == "HG1":
            extra = " (smallest margin " + f"{min(x['method_gain'] / x['platform_gain'] for x in v['per_setting']):.2f})"
        Lm.append(f"- **{k}** {v['statement']}: predicted {'holds' if v['predicted'] else 'fails'}{extra}")
    nless = sum(x["v100_less_energy"] for x in open_q)
    Lm += ["", f"**Open question (V100 against U50, steady state, primary boundaries):** the V100 is predicted to need less energy in {nless} of {len(open_q)} case-target pairs.", ""]
    open(os.path.join(OUT, "predictions_gpu.md"), "w").write("\n".join(Lm) + "\n")
    for f in ("predictions_gpu.json", "predictions_gpu.md"):
        p = os.path.join(OUT, f)
        open(p + ".sha256", "w").write(f"{sha(p)}  {f}\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
