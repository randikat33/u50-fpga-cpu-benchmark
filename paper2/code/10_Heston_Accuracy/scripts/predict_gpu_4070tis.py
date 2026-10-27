#!/usr/bin/env python3
"""predict_gpu_4070tis.py - the RTX 4070 Ti SUPER prediction of paper 2 (addendum v0.9), made before any timed run of
a pricing kernel on that card.

The model is the V100 model of protocol v0.6 (scripts/predict_gpu.py) with the card's own constants, and with the two
power readings that the V100 measurements showed were needed (both fixed from V100 data only; the RTX card is the
first platform the model has not seen):
  1. GPU power, "pipe energy" rule (the pipe-weighted rule of the V100 analysis, written per instruction):
       P_gpu = P_idle + sum over pipes p in (fma, alu, xu) of e_p x (thread instructions per second on p),
     e_p = energy per thread instruction of pipe p from the microbenchmarks (fp32 -> fma; int -> alu after removing
     its IMAD share, which runs on the fma pipe; sfu -> xu). On the V100 this equals the exploratory "mix" variant
     within 2% (its benchmarks ran at 98-100% of the pipe peaks); on this card the fp32 benchmark reaches 82% of the
     nominal 128 per SM per clock, so the per-instruction form is the one that keeps its meaning.
  2. Host power with the blocking wait: during the kernel the host waits asleep and draws its idle power (V100 GPU
     server, steady state: socket 1 at 13.9 W against 13.7 W idle; the v0.6 rule took 25.1 W); during the fixed part
     of a run (t0) the GPU and the host draw the start-up benchmark's power (block).
Rate model unchanged: per SM and clock, thread-instruction limits per pipe, issue 128, dependent issue every L = 4
cycles, resident blocks from registers, eta = geometric mean of the three benchmarks' achieved / peak.

Card: 66 SMs, SM clock locked at results/rtx4070tis/step4b/results/locked_clock.txt (2340 MHz); compute capability
8.9 limits per SM per clock (CUDA C Programming Guide, arithmetic instruction throughput): fp32 128, int32 64,
conversions and special functions 16, fp64 2, issue 4 x 32 = 128; at most 1536 resident threads (6 blocks of 256)
and 65,536 registers per SM. Boundary: GPU (NVML energy counter) + CPU package (RAPL package-0; this desktop has no
DRAM domain); this one-socket machine has no other socket, so its "server" boundary is the same.

  python3 scripts/predict_gpu_4070tis.py                 the registered prediction -> results/predictions_gpu_4070tis/
  python3 scripts/predict_gpu_4070tis.py --platform v100 the same rules on the V100 against its measurements
                                                         (training fit, post hoc) -> results/predictions_gpu_4070tis/v100_fit.*
Inputs (no timed pricing run on the RTX card): its SASS counts and ptxas log (step 4A), its microbenchmarks at the
locked clock (step 4B), the registered settings, and the measured card, CPU and V100 energies (analysis_gpu.json)
used only to name winners, the Proposition 2 verdicts and the open question.
"""
import argparse, csv, hashlib, json, math, os, re, statistics, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "predictions_gpu_4070tis")
RTX = os.path.join(RES, "rtx4070tis")
L_LAT, TPB, REGS_SM = 4.0, 256, 65536
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
STEADY_PATH_STEPS, MAX_PAIRS = 1.0e11, 2 ** 34      # the steady-state runs of phase5_gpu.py
CALL = 1.3                                          # HR2: a pair is called only when its predicted margin is outside [1/1.3, 1.3]


def clock_rtx():
    txt = open(os.path.join(RTX, "step4b", "results", "locked_clock.txt")).read()
    return int(re.search(r"LOCKED_CLOCK_MHZ=(\d+)", txt).group(1))


def platform(name):
    if name == "rtx4070tis":
        mhz = clock_rtx()
        return dict(name=name, label="RTX 4070 Ti SUPER", nsm=66, f_hz=mhz * 1e6, mhz=mhz, max_blocks=1536 // TPB,
                    lim={"fma": 128.0, "alu": 64.0, "xu": 16.0, "fp64": 2.0, "issue": 128.0},
                    counts=os.path.join(RTX, "step4a", "results", "gpu_counts.json"),
                    ptxas=os.path.join(RTX, "step4a", "build", "hs_gpu_ptxas.log"),
                    ubench=os.path.join(RTX, "step4b", "results", "gpu_ubench_4060", str(mhz)),
                    host=("pkg0", "dram0"), other=())
    return dict(name=name, label="V100", nsm=80, f_hz=1245e6, mhz=1245, max_blocks=2048 // TPB,
                lim={"fma": 64.0, "alu": 64.0, "xu": 16.0, "fp64": 32.0, "issue": 128.0},
                counts=os.path.join(RES, "gpu_counts.json"), ptxas=os.path.join(RES, "gpu_b2", "hs_gpu_ptxas.log"),
                ubench=os.path.join(RES, "gpu_ubench"), host=("pkg1", "dram1"), other=("pkg0", "dram0"))


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def ubench(P):
    U = {}
    for b in ("idle", "fp32", "int", "sfu", "startup", "startup_block"):
        d = os.path.join(P["ubench"], b)
        m = json.load(open(os.path.join(d, "meta.json")))
        r = dict(re.findall(r"^RESULT (\w+)=(\S+)", open(os.path.join(d, "stdout.log")).read(), re.M))
        W = m["P_W"]
        U[b] = dict(gpu=W["gpu"], host=sum(W.get(k, 0.0) for k in P["host"]), other=sum(W.get(k, 0.0) for k in P["other"]),
                    t_run=float(r["t_per_run_s"]), disturbed=m["disturbed"], sm=m["sm_mhz_median"],
                    ipc=float(r["thread_instr_per_sm_per_clk"]) if "thread_instr_per_sm_per_clk" in r else None)
    return U


def regs(P):
    txt = open(P["ptxas"]).read()
    out, cur = {}, None
    for line in txt.splitlines():
        m = re.search(r"Function properties for (\S+)", line) or re.search(r"Compiling entry function '(\S+)'", line)
        if m:
            cur = m.group(1)
        m = re.search(r"Used (\d+) registers", line)
        if m and cur:
            out[cur] = int(m.group(1))
    return {k: next(v for n, v in out.items() if f"price_kernelILi{TPB}E{t}" in n) for k, t in TPL.items()}


def occupancy(P, r):
    per_warp = math.ceil(r * 32 / 256) * 256
    return max(1, min(P["max_blocks"], REGS_SM // ((TPB // 32) * per_warp)))


def settings():
    S = []
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("path_steps"):
                S.append(dict(case=r["case"], target=r["target"], scheme=r["scheme"], M=int(r["steps"]), N=int(r["pairs"]),
                              W=float(r["path_steps"])))
    return S


def steady_keys(S):
    """the steady-state runs of phase5_gpu.py: one per (case, method, steps) whose setting has < 1e11 path-steps"""
    keys = {}
    for s in S:
        if 2.0 * s["N"] * s["M"] < STEADY_PATH_STEPS:
            n = min(MAX_PAIRS, max(s["N"], int(STEADY_PATH_STEPS / (2 * s["M"]))))
            keys[(s["case"], s["scheme"], s["M"])] = (n + 255) // 256 * 256
    return keys


def per_pair(C, k, M, lim):
    c = C[k]
    v = {}
    for p in lim:
        if k.startswith("basket"):
            v[p] = 8 * (M * c["per_pair_step"].get(p, 0) + c["per_asset"].get(p, 0)) + c["per_pair"].get(p, 0)
        else:
            v[p] = M * c["per_pair_step"].get(p, 0) + c["per_pair"].get(p, 0)
    return v


def kernel(P, C, k, M, N, occ, eta):
    """kernel time (s), binding pipe, thread instructions per second per pipe, resident warps per SM, blocks"""
    lim, nsm = P["lim"], P["nsm"]
    v = per_pair(C, k, M, lim)
    blocks = min(nsm * occ, math.ceil(N / TPB))
    T = blocks * TPB
    bps = math.ceil(blocks / nsm)
    wpb = TPB // 32
    w = min(bps * wpb, math.ceil(min(N, T) / nsm / 32) if blocks >= nsm else wpb)
    w = max(w, 1)
    per = {p: v[p] / lim[p] for p in lim}                 # SM cycles per thread-pair at each pipe's peak
    lat = v["issue"] * L_LAT / (32 * w)                    # dependent-issue limit with w warps
    cyc = max(max(per.values()), lat)
    bind = "latency" if lat > max(per.values()) else max(per, key=per.get)
    k_per_thread = math.ceil(N / T)
    t = k_per_thread * cyc * (32 * w) / (P["f_hz"] * eta)
    pairs_done = T * k_per_thread                          # thread-pairs executed, padding included
    rate = {p: v[p] * pairs_done / t for p in ("fma", "alu", "xu")}
    return t, bind, rate, w, blocks


def steady(P, C, k, M, occ, eta):
    """path-steps per second at full occupancy, many pairs; binding pipe; thread instructions per second per pipe"""
    lim = P["lim"]
    v = per_pair(C, k, M, lim)
    w = occ * (TPB // 32)
    per = {p: v[p] / lim[p] for p in lim}
    cyc = max(max(per.values()), v["issue"] * L_LAT / (32 * w))
    pairs_per_s = P["nsm"] * P["f_hz"] * eta / cyc
    rate = {p: v[p] * pairs_per_s for p in ("fma", "alu", "xu")}
    bind = "latency" if v["issue"] * L_LAT / (32 * w) > max(per.values()) else max(per, key=per.get)
    return pairs_per_s * 2 * M, bind, rate


def pipe_energy(P, U):
    """energy per thread instruction (J) of the fma, alu and xu pipes from the microbenchmarks"""
    sm_clk = P["nsm"] * P["f_hz"]
    idle = U["idle"]["gpu"]
    r_fp32 = U["fp32"]["ipc"] * sm_clk
    r_int = U["int"]["ipc"] * sm_clk
    r_sfu = U["sfu"]["ipc"] * sm_clk
    e_fma = (U["fp32"]["gpu"] - idle) / r_fp32
    e_alu = (U["int"]["gpu"] - idle - e_fma * r_int * 2 / 6) / (r_int * 4 / 6)   # int benchmark: 4 ALU + 2 IMAD (fma pipe) per 6
    e_xu = (U["sfu"]["gpu"] - idle) / r_sfu
    return {"fma": e_fma, "alu": e_alu, "xu": e_xu}


def p_dyn(e, rate):
    return sum(e[p] * rate[p] for p in e)


def p_mix_v06(P, U, rate):
    """the exploratory v0.6 'mix' variant as coded for the V100 (utilisation against the nominal peak), for information"""
    lim, sm_clk, idle = P["lim"], P["nsm"] * P["f_hz"], U["idle"]["gpu"]
    dP = {"fma": U["fp32"]["gpu"] - idle, "xu": U["sfu"]["gpu"] - idle}
    ui = U["int"]["ipc"]
    u_alu, u_fma_int = ui * 4 / 6 / lim["alu"], ui * 2 / 6 / lim["fma"]
    dP["alu"] = (U["int"]["gpu"] - idle - u_fma_int * dP["fma"]) / u_alu
    util = {p: rate[p] / (lim[p] * sm_clk) for p in dP}
    eta_nom = 1.0
    return sum(min(util[p] / eta_nom, 1.0) * dP[p] for p in dP)


def eta_of(P, U):
    lim = P["lim"]
    int_peak = min(lim["alu"] * 6 / 4, lim["fma"] * 6 / 2, lim["issue"])
    eff = dict(fp32=U["fp32"]["ipc"] / min(lim["fma"], lim["issue"]), int=U["int"]["ipc"] / int_peak, sfu=U["sfu"]["ipc"] / lim["xu"])
    return min((eff["fp32"] * eff["int"] * eff["sfu"]) ** (1 / 3), 1.0), eff, int_peak


def predict(P):
    C = json.load(open(P["counts"]))
    U = ubench(P)
    R = regs(P)
    OCC = {k: occupancy(P, r) for k, r in R.items()}
    eta, eff, int_peak = eta_of(P, U)
    e = pipe_energy(P, U)
    t0 = U["startup_block"]["t_run"]
    H = dict(idle=U["idle"]["host"], fixed=U["startup_block"]["host"], other_idle=U["idle"]["other"], other_fixed=U["startup_block"]["other"])
    G = dict(idle=U["idle"]["gpu"], fixed=U["startup_block"]["gpu"])
    S = settings()
    rows, ss = [], {}
    for s in S:
        k = KMAP[(s["case"], s["scheme"])]
        occ = OCC[k]
        rate_ss, bind_ss, ir_ss = steady(P, C, k, s["M"], occ, eta)
        tk, bind, ir, w, blocks = kernel(P, C, k, s["M"], s["N"], occ, eta)
        trun = tk + t0
        d = tk / trun
        Pk = G["idle"] + p_dyn(e, ir)                       # GPU power while the kernel runs
        Pg = d * Pk + (1 - d) * G["fixed"]
        Ph = d * H["idle"] + (1 - d) * H["fixed"]
        Po = d * H["other_idle"] + (1 - d) * H["other_fixed"]
        Pss = G["idle"] + p_dyn(e, ir_ss)
        tss = s["W"] / rate_ss
        Pmix_ss = G["idle"] + p_mix_v06(P, U, ir_ss)
        E = dict(socket=(Pg + Ph) * trun, server=(Pg + Ph + Po) * trun, gpu_alone=Pg * trun,
                 frodo_socket=(Pg + H_FRODO["socket"]) * trun, frodo_server=(Pg + H_FRODO["server"]) * trun)
        Ess = dict(socket=(Pss + H["idle"]) * tss, server=(Pss + H["idle"] + H["other_idle"]) * tss, gpu_alone=Pss * tss,
                   frodo_socket=(Pss + H_FRODO["socket"]) * tss, frodo_server=(Pss + H_FRODO["server"]) * tss,
                   socket_mix_v06=(Pmix_ss + H["idle"]) * tss, socket_v06_host=(Pss + H["fixed"]) * tss)
        rows.append(dict(case=s["case"], target=s["target"], scheme=s["scheme"], kernel=k, M=s["M"], N=s["N"], W=s["W"],
                         occ=occ, blocks=blocks, warps_per_sm=w, bind=bind, t_kernel_s=tk, t_run_s=trun, duty=d,
                         P_gpu_W=Pg, P_host_W=Ph, P_gpu_ss_W=Pss, P_gpu_ss_mix_v06_W=Pmix_ss, rate_ss_path_steps_per_s=rate_ss,
                         bind_ss=bind_ss, e_ss_nJ_per_path_step=(Pss + H["idle"]) / rate_ss * 1e9, E_J=E, Ess_J=Ess))
        ss[(s["case"], s["scheme"], s["M"])] = (rate_ss, Pss, bind_ss, k)
    const = dict(platform=P["label"], n_sm=P["nsm"], clock_mhz=P["mhz"], latency_cycles=L_LAT, pipe_limits=P["lim"],
                 eta=eta, eta_parts=eff, int_benchmark_peak=int_peak, pipe_energy_pJ={p: v * 1e12 for p, v in e.items()},
                 t0_block_s=t0, gpu_W=G, host_W=H, registers=R, blocks_per_sm=OCC,
                 ubench={b: dict(gpu_W=v["gpu"], host_W=v["host"], ipc=v["ipc"], sm_mhz=v["sm"], disturbed=v["disturbed"]) for b, v in U.items()})
    return rows, ss, const


def measured():
    d = json.load(open(os.path.join(RES, "analysis_gpu", "analysis_gpu.json")))
    return {(w["case"], w["target"], w["boundary"]): w for w in d["winners"]}


def best(rows, case, t, m):
    return next(r for r in rows if r["case"] == case and r["target"] == t and r["scheme"] == m)


def prop2(WIN, rows, Wset):
    """Proposition 2 with platforms card, cpu_matched, cpu_native (measured, Part A) and the RTX card (predicted),
    socket boundary, e from the 0.1% setting of each case (as theory_check.py)"""
    plats = ("card", "cpu_matched", "cpu_native", "rtx")
    out = []
    for c in CASES:
        m1, m2 = METH[c]
        w01 = WIN[(c, "0.1%", "socket")]
        Ess01 = {(p, m): w01["Ess"][f"{p}_{m}"] for p in plats[:3] for m in (m1, m2)}
        for m in (m1, m2):
            Ess01[("rtx", m)] = best(rows, c, "0.1%", m)["Ess_J"]["socket"]
        e = {(p, m): Ess01[(p, m)] / Wset[(c, "0.1%", m)] for p in plats for m in (m1, m2)}
        bp = min(plats, key=lambda p: e[(p, m2)])
        rp = min((p for p in plats if p != bp), key=lambda p: e[(p, m2)])
        kappa = e[(bp, m2)] / e[(bp, m1)]
        G = e[(rp, m2)] / e[(bp, m2)]
        for t in TARGETS:
            M = Wset[(c, t, m1)] / Wset[(c, t, m2)]
            margin = M / (kappa * G)
            out.append(dict(case=c, target=t, M=M, kappa=kappa, G=G, threshold=kappa * G, margin=margin, best=bp, runner_up=rp,
                            predicted_holds=margin > 1, called=not (1 / CALL <= margin <= CALL)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="rtx4070tis", choices=("rtx4070tis", "v100"))
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    P = platform(a.platform)
    rows, ss, const = predict(P)
    WIN = measured()
    Wset = {(r["case"], r["target"], r["scheme"]): r["W"] for r in rows}
    if a.platform == "v100":
        return v100_fit(rows, const, WIN)
    # ---------------------------------------------------------------- winners with the RTX card (card, CPUs: measured)
    winners = []
    for case in CASES:
        for t in TARGETS:
            for b in ("socket", "server"):
                w = WIN[(case, t, b)]
                cand = {}
                for m in METH[case]:
                    for p in ("card", "cpu_matched", "cpu_native"):
                        cand[f"{p}_{m}"] = (w["E"][f"{p}_{m}"], w["Ess"][f"{p}_{m}"])
                    r = best(rows, case, t, m)
                    cand[f"rtx_{m}"] = (r["E_J"][b], r["Ess_J"][b])
                winners.append(dict(case=case, target=t, boundary=b, winner_one_run=min(cand, key=lambda c: cand[c][0]),
                                    winner_steady=min(cand, key=lambda c: cand[c][1]),
                                    E={c: v[0] for c, v in cand.items()}, Ess={c: v[1] for c, v in cand.items()}))
    # ---------------------------------------------------------------- open question: RTX against the V100 (measured)
    oq = []
    for case in CASES:
        for t in TARGETS:
            w = WIN[(case, t, "socket")]
            v100 = min(w["Ess"][f"gpu_{m}"] for m in METH[case])
            mv = min(METH[case], key=lambda m: w["Ess"][f"gpu_{m}"])
            rtx = min(best(rows, case, t, m)["Ess_J"]["socket"] for m in METH[case])
            mr = min(METH[case], key=lambda m: best(rows, case, t, m)["Ess_J"]["socket"])
            oq.append(dict(case=case, target=t, method_v100=mv, method_rtx=mr, Ess_v100_J=v100, Ess_rtx_J=rtx, rtx_over_v100=rtx / v100,
                           rtx_less=rtx < v100, called=not (1 / 1.05 <= rtx / v100 <= 1.05)))
    oq_mh = matched_host_oq(rows, WIN)
    for x, y in zip(oq, oq_mh):
        x.update(y)
    p2 = prop2(WIN, rows, Wset)
    # ---------------------------------------------------------------- hypotheses (predicted verdicts)
    hyp = {}
    hyp["HR1"] = dict(statement="steady-state energy per option at the primary boundary (GPU + CPU package), block wait, within 30% "
                                "of the prediction (pipe-energy rule) at >= 30 of the 36 settings", predicted=True,
                      basis="V100 with the same rules: 36 of 36 within 30% (training fit, v100_fit.md)")
    hyp["HR2"] = dict(statement="Proposition 2 with the predicted RTX energies gives the measured verdict (method gain above platform gain) "
                                "at every called pair (predicted margin outside [1/1.3, 1.3]); pairs inside the band are reported, not tested",
                      per_pair=p2, called=sum(r["called"] for r in p2), holds_predicted=sum(r["predicted_holds"] for r in p2))
    hyp["HR3"] = dict(statement="time per path-step at steady state within 30% of the prediction at >= 30 of the 36 settings", predicted=True,
                      basis="V100 with the same rate model: 36 of 36 within 30% (block, sessions 1-2), median measured/predicted 1.14 (v100_fit.md)")
    nless = sum(x["rtx_less"] for x in oq)
    inputs = {os.path.relpath(p, RES): sha(p) for p in (P["counts"], P["ptxas"], os.path.join(RES, "settings.csv"), os.path.join(RES, "settings_s.csv"),
                                                          os.path.join(RES, "analysis_gpu", "analysis_gpu.json"),
                                                          os.path.join(RTX, "step4b", "results", "locked_clock.txt"),
                                                          os.path.join(RTX, "step4a", "build", "hs_gpu.sha256"))}
    for b in ("idle", "fp32", "int", "sfu", "startup", "startup_block"):
        f = os.path.join(P["ubench"], b, "meta.json")
        inputs[os.path.relpath(f, RES)] = sha(f)
    doc = dict(generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), protocol="addendum v0.9, Section 9", inputs_sha256=inputs,
               constants=const, steady_keys=[dict(case=c, scheme=m, M=M, pairs=n, rate_path_steps_per_s=ss[(c, m, M)][0], P_gpu_W=ss[(c, m, M)][1],
                                                  bind=ss[(c, m, M)][2]) for (c, m, M), n in sorted(steady_keys(settings()).items())],
               predictions=rows, winners=winners, hypotheses=hyp, open_question=oq)
    json.dump(doc, open(os.path.join(OUT, "predictions_gpu_4070tis.json"), "w"), indent=1, default=float)
    write_md(doc, const, rows, winners, p2, oq, nless)
    for f in ("predictions_gpu_4070tis.json", "predictions_gpu_4070tis.md"):
        p = os.path.join(OUT, f)
        open(p + ".sha256", "w").write(f"{sha(p)}  {f}\n")
    print(open(os.path.join(OUT, "predictions_gpu_4070tis.md")).read())


def matched_host_oq(rows, WIN):
    """open question at a matched host: both GPUs with frodo's host socket (49.15 W) in place of their own host.
    V100: GPU energy and time per path-step from its measured block-wait runs (sessions 1-2, median; the steady-state
    run of the setting's kernel and time step, or the setting itself when it has >= 1e11 path-steps)"""
    M = {}
    for f in ("phase5g_summary_s1.csv", "phase5g_summary_s2.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["mode"] == "block":
                M.setdefault((r["kind"], r["case"], r["scheme"], r["target"] if r["kind"] == "set" else int(r["steps"])), []).append(r)
    out = []
    for case in CASES:
        for t in TARGETS:
            v = []
            for m in METH[case]:
                r0 = best(rows, case, t, m)
                g = M.get(("ss", case, m, r0["M"])) or M[("set", case, m, t)]
                e = statistics.median(float(x["E_gpu_per_run_J"]) / float(x["path_steps"]) for x in g)
                tau = statistics.median(float(x["t_per_run_s"]) / float(x["path_steps"]) for x in g)
                v.append(r0["W"] * (e + H_FRODO["socket"] * tau))
            rtx = min(best(rows, case, t, m)["Ess_J"]["frodo_socket"] for m in METH[case])
            out.append(dict(Ess_v100_frodo_socket_J=min(v), Ess_rtx_frodo_socket_J=rtx, rtx_over_v100_frodo_socket=rtx / min(v),
                            v100_frodo_socket_over_card=min(v) / min(WIN[(case, t, "socket")]["Ess"][f"card_{m}"] for m in METH[case])))
    return out


def write_md(doc, K, rows, winners, p2, oq, nless):
    L = ["# RTX 4070 Ti SUPER prediction (addendum v0.9), registered before any timed run of a pricing kernel on the card", "",
         f"Generated {doc['generated']}. {K['n_sm']} SMs at a locked {K['clock_mhz']} MHz; eta = {K['eta']:.3f} "
         f"(fp32 {K['eta_parts']['fp32']:.3f}, int {K['eta_parts']['int']:.3f}, sfu {K['eta_parts']['sfu']:.3f}); "
         f"t0 (block) {K['t0_block_s'] * 1e6:.1f} us; blocks per SM {K['blocks_per_sm']}.", "",
         f"Energy per thread instruction: fma {K['pipe_energy_pJ']['fma']:.2f} pJ, alu {K['pipe_energy_pJ']['alu']:.2f} pJ, xu {K['pipe_energy_pJ']['xu']:.2f} pJ. "
         f"GPU idle {K['gpu_W']['idle']:.1f} W, start-up (block) {K['gpu_W']['fixed']:.1f} W; CPU package idle {K['host_W']['idle']:.1f} W, "
         f"start-up (block) {K['host_W']['fixed']:.1f} W.", "",
         "## Steady state per kernel and time step (primary boundary: GPU + CPU package)", "",
         "| Kernel | M (steps) | Rate (G path-steps/s) | Binding | P_gpu (W) | P_gpu, v0.6 mix (W) | nJ per path-step |", "|---|---|---|---|---|---|---|"]
    seen = set()
    for r in rows:
        key = (r["kernel"], r["M"])
        if key in seen:
            continue
        seen.add(key)
        L.append(f"| {r['kernel']} | {r['M']} | {r['rate_ss_path_steps_per_s'] / 1e9:.1f} | {r['bind_ss']} | {r['P_gpu_ss_W']:.1f} | "
                 f"{r['P_gpu_ss_mix_v06_W']:.1f} | {r['e_ss_nJ_per_path_step']:.3f} |")
    L += ["", "## Every setting: time and energy at the primary boundary (block wait)", "",
          "| Case | Target | Method | Run time (ms) | Bound | P_gpu (W) | E one run (J) | E steady (J) | E steady, frodo's host (J) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['case']} | {r['target']} | {r['scheme']} | {r['t_run_s'] * 1e3:.3f} | {r['bind']} | {r['P_gpu_W']:.1f} | {r['E_J']['socket']:.4g} | "
                 f"{r['Ess_J']['socket']:.4g} | {r['Ess_J']['frodo_socket']:.4g} |")
    L += ["", "## Predicted lowest-energy combination (card and CPUs measured, Part A; RTX predicted)", "",
          "| Case | Target | Boundary | One run | Steady state |", "|---|---|---|---|---|"]
    for w in winners:
        L.append(f"| {w['case']} | {w['target']} | {w['boundary']} | {w['winner_one_run']} | {w['winner_steady']} |")
    L += ["", "## HR2: Proposition 2 (platforms card, CPU matched, CPU native, RTX; socket boundary)", "",
          "| Case | Target | M | kappa | G | kappa G | margin | predicted | called |", "|---|---|---|---|---|---|---|---|---|"]
    for r in p2:
        L.append(f"| {r['case']} | {r['target']} | {r['M']:.1f} | {r['kappa']:.2f} | {r['G']:.2f} | {r['threshold']:.1f} | {r['margin']:.2f} | "
                 f"{'method' if r['predicted_holds'] else 'platform'} | {'yes' if r['called'] else 'no'} |")
    L += ["", "## Open question: RTX 4070 Ti SUPER (predicted) against the V100 (measured), steady state, better method each", "",
          "| Case | Target | V100 (J) | RTX (J) | RTX / V100 | RTX / V100, both with frodo's host |", "|---|---|---|---|---|---|"]
    for x in oq:
        mh = f"{x['rtx_over_v100_frodo_socket']:.3f}" if x.get("rtx_over_v100_frodo_socket") else "-"
        L.append(f"| {x['case']} | {x['target']} | {x['Ess_v100_J']:.4g} | {x['Ess_rtx_J']:.4g} | {x['rtx_over_v100']:.3f} | {mh} |")
    L += ["", f"Predicted: the RTX card needs less energy than the V100 at {nless} of {len(oq)} case-target pairs (primary boundaries).", "",
          "## Hypotheses: predicted verdicts", ""]
    for k, v in doc["hypotheses"].items():
        extra = ""
        if k == "HR2":
            extra = f" ({v['called']} of 18 pairs called; method predicted to win at {v['holds_predicted']} of 18)"
        L.append(f"- **{k}** {v['statement']}{extra}")
    open(os.path.join(OUT, "predictions_gpu_4070tis.md"), "w").write("\n".join(L) + "\n")


def v100_fit(rows, K, WIN):
    """the v0.9 rules on the V100 against its measurements (sessions 1-2, block): training fit, post hoc"""
    L = ["# V100 with the v0.9 rules (pipe energy, host idle during the kernel): fit to its own measurements (post hoc)", "",
         f"eta {K['eta']:.3f}; pipe energy fma {K['pipe_energy_pJ']['fma']:.2f}, alu {K['pipe_energy_pJ']['alu']:.2f}, xu {K['pipe_energy_pJ']['xu']:.2f} pJ.", "",
         "| Case | Target | Method | steady meas/pred | one run meas/pred |", "|---|---|---|---|---|"]
    rs, r1 = [], []
    for r in rows:
        w = WIN[(r["case"], r["target"], "socket")]
        a = w["Ess"][f"gpu_{r['scheme']}"] / r["Ess_J"]["socket"]
        b = w["E"][f"gpu_{r['scheme']}"] / r["E_J"]["socket"]
        rs.append(a); r1.append(b)
        L.append(f"| {r['case']} | {r['target']} | {r['scheme']} | {a:.3f} | {b:.3f} |")
    M = {}
    for f in ("phase5g_summary_s1.csv", "phase5g_summary_s2.csv"):
        for x in csv.DictReader(open(os.path.join(RES, f))):
            if x["mode"] == "block":
                M.setdefault((x["kind"], x["case"], x["scheme"], x["target"] if x["kind"] == "set" else int(x["steps"])), []).append(x)
    rt = []
    for r in rows:
        g = M.get(("ss", r["case"], r["scheme"], r["M"])) or M[("set", r["case"], r["scheme"], r["target"])]
        rt.append(statistics.median(float(x["t_per_run_s"]) / float(x["path_steps"]) for x in g) * r["rate_ss_path_steps_per_s"])
    n_ss = sum(abs(x - 1) <= 0.3 for x in rs)
    n_1 = sum(abs(x - 1) <= 0.3 for x in r1)
    L += ["", f"Steady state within 30%: {n_ss} of 36 (median {statistics.median(rs):.3f}, range {min(rs):.3f}-{max(rs):.3f}); "
              f"one run: {n_1} of 36 (median {statistics.median(r1):.3f}, range {min(r1):.3f}-{max(r1):.3f}); "
              f"time per path-step at steady state: {sum(abs(x - 1) <= 0.3 for x in rt)} of 36 within 30% "
              f"(median {statistics.median(rt):.3f}, range {min(rt):.3f}-{max(rt):.3f})."]
    open(os.path.join(OUT, "v100_fit.md"), "w").write("\n".join(L) + "\n")
    json.dump(dict(constants=K, steady_ratio=rs, one_run_ratio=r1, time_ratio=rt, within30_steady=n_ss, within30_one_run=n_1),
              open(os.path.join(OUT, "v100_fit.json"), "w"), indent=1, default=float)
    print("\n".join(L[-1:]))


if __name__ == "__main__":
    main()
