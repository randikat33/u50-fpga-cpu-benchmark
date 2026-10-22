#!/usr/bin/env python3
"""predict_coexec.py - the co-execution prediction of addendum v0.7 (protocol v0.6, Section 25), from measured inputs
only, written before any co-execution run.

Two pairs of devices:
  frodo : the U50 card (host thread on CPU 0) + the CPU-native code on socket 0's other 23 cores (46 threads)
  smc   : the V100 (host thread on CPU 10) + the CPU-native code on socket 1's other 9 cores (18 threads)
Workloads: the 12 kernels (six cases x two methods) at the step of their 0.1% setting, each as
  "setting": the registered 0.1% setting (its pairs), and "large": pairs for about 4 s on the accelerator alone.
Linear model of one pricing run with a fraction f of the pairs on the CPU (the rest on the accelerator):
  t_a = (1-f) W / r_a + t0_a + t_ov,  t_c = f W / r_c + t_ov,  T = max(t_a, t_c)     (a part with no pairs takes 0)
  E_device = P_a t_a + P_a,idle (T - t_a)
  E_socket = P_both min(t_a, t_c) + P_h (t_a - min) + P_c (t_c - min)   (accelerator's host socket)
  primary boundary: frodo card + socket 0; smc GPU + socket 1. Server boundary: + the other socket at its measured power.
Policies: acc (f = 0); cpu (f = 1, only where the CPU alone takes <= 30 s); static_E (minimum energy, no deadline);
static_T (minimum makespan); static_M (minimum energy under the deadline halfway between the two); dynamic (work
stealing over K blocks, simulated with the same rates and per-block costs).
Inputs (medians): GPU - phase-5g sessions 1-2, block mode; Supermicro CPU - results/smc_cpu_summary.csv, s1_t18;
card - phase-6 session 1, --wait xrt, registered xrt.ini; frodo CPU - phase-6 session 1, s0_t48, scaled by 46/48.
Output: results/predictions_coexec/predictions_coexec.json, .md, .sha256
"""
import csv, hashlib, json, math, os, statistics as st

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "predictions_coexec")
KERNELS = [("I", "ft"), ("I", "qe"), ("II", "ft"), ("II", "qe"), ("III", "ft"), ("III", "qe"), ("P1", "ft"), ("P1", "qe"),
           ("B", "ft"), ("B", "qe"), ("A", "plain"), ("A", "cv")]
C = dict(t0_gpu=0.142e-3, t0_card=0.651e-3, t_ov=1.0e-3, gpu_idle_W=35.8, card_idle_W=21.5, frodo_s0_idle_W=39.5,
         frodo_cpu_scale=46 / 48, large_s=4.0, cpu_alone_max_s=30.0, maxpairs={"B": 2 ** 29}, maxpairs_default=2 ** 31,
         block_cost_factor=20, k_max=64)
INPUT_FILES = ["settings.csv", "settings_s.csv", "phase5g_summary_s1.csv", "phase5g_summary_s2.csv", "smc_cpu_summary.csv",
               "phase6_summary_s1.csv"]


def settings():
    out = {}
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("steps") and r["target"] == "0.1%":
                out[(r["case"], r["scheme"])] = dict(M=int(r["steps"]), N=int(r["pairs"]), eps=float(r["eps"]))
    return out


def gpu_inputs(case, sc, M):
    rows = {}
    for s in (1, 2):
        for r in csv.DictReader(open(os.path.join(RES, f"phase5g_summary_s{s}.csv"))):
            rows.setdefault(r["job"].split("/", 1)[1], []).append(r)
    k = f"block/ss_{case}_{sc}_M{M}"
    if k not in rows:
        k = f"block/set_{case}_{sc}_0.1pct"
    rs = rows[k]
    W = float(rs[0]["path_steps"])
    t = st.median(float(r["t_per_run_s"]) for r in rs)
    return dict(r=W / t, P_a=st.median(float(r["P_gpu_W"]) for r in rs), P_h=st.median(float(r["P_socket1_W"]) for r in rs),
                P_other=st.median(float(r["P_socket0_W"]) for r in rs), src=k)


def smc_cpu_inputs(case, sc, M):
    r = next(x for x in csv.DictReader(open(os.path.join(RES, "smc_cpu_summary.csv"))) if x["group"] == f"s1_t18/ss_{case}_{sc}_M{M}")
    return dict(r=float(r["rate_path_steps_per_s"]), P_c=float(r["P_socket1_W"]), P_other=float(r["P_socket0_W"]), src=r["group"])


def frodo_rows():
    return list(csv.DictReader(open(os.path.join(RES, "phase6_summary_s1.csv"))))


def pick(rows, impl, wait, cfg, case, sc, M):
    for kind in ("ss", "set"):
        for r in rows:
            if (r["impl"], r["wait"], r["ini"], r["cfg"], r["kind"], r["case"], r["scheme"], r["steps"]) == (impl, wait, "registered", cfg, kind, case, sc, str(M)) \
                    and (kind == "ss" or r["target"] == "0.1%"):
                return r
    raise KeyError((impl, cfg, case, sc, M))


def card_inputs(rows, case, sc, M):
    r = pick(rows, "fpga", "xrt", "card", case, sc, M)
    W, t = float(r["path_steps"]), float(r["t_per_run_s"])
    return dict(r=W / t, P_a=float(r["P_card_W"]), P_h=float(r["P_host_socket_W"]),
                P_other=float(r["P_host_server_W"]) - float(r["P_host_socket_W"]), src=r["job"])


def frodo_cpu_inputs(rows, case, sc, M):
    r = pick(rows, "native", "", "s0_t48", case, sc, M)
    W, t = float(r["path_steps"]), float(r["t_per_run_s"])
    return dict(r=W / t * C["frodo_cpu_scale"], P_c=float(r["P_W"]), src=r["job"])


def model(W, f, a, c, mach):
    t0 = C["t0_gpu"] if mach == "smc" else C["t0_card"]
    ta = (1 - f) * W / a["r"] + t0 + C["t_ov"] if f < 1 else 0.0
    tc = f * W / c["r"] + C["t_ov"] if f > 0 else 0.0
    return energy(ta, tc, a, c, mach)


def energy(ta, tc, a, c, mach):
    T = max(ta, tc)
    idle = C["gpu_idle_W"] if mach == "smc" else C["card_idle_W"]
    tel = 0.0 if mach == "smc" else a["P_h"] - C["frodo_s0_idle_W"]      # card program's host overhead (telemetry)
    P_c = c["P_c"] + tel
    both = min(ta, tc)
    Ed = a["P_a"] * ta + idle * (T - ta)
    Es = P_c * both + a["P_h"] * (ta - both) + P_c * (tc - both)
    if ta == 0:                                                         # CPU alone: accelerator idle, host program alive
        Es = P_c * tc
    other = a["P_other"]
    return dict(T=T, ta=ta, tc=tc, E_primary=Ed + Es, E_server=Ed + Es + other * T)


def dynamic(W, M, N, a, c, mach):
    t0 = C["t0_gpu"] if mach == "smc" else C["t0_card"]
    B = max(256, math.ceil(C["block_cost_factor"] * t0 * a["r"] / (2 * M)))
    K = max(1, min(C["k_max"], math.ceil(N / B)))
    B = math.ceil(N / K)
    ba = 2 * M * B / a["r"] + t0 + C["t_ov"]
    bc = 2 * M * B / c["r"] + C["t_ov"]
    fa = fc = 0.0
    na = nc = 0
    for _ in range(K):                                                  # self-scheduling: the device that is free first takes the next block
        if fa <= fc:
            fa += ba; na += 1
        else:
            fc += bc; nc += 1
    e = energy(fa, fc, a, c, mach)
    e.update(K=K, block_pairs=B, blocks_acc=na, blocks_cpu=nc, f=nc / K)
    return e


def plan(mach, a, c, W, M, N):
    grid = [i / 10000 for i in range(10001)]
    res = {f: model(W, f, a, c, mach) for f in grid}
    fE = min(grid, key=lambda f: (res[f]["E_primary"], f))
    fT = min(grid, key=lambda f: (res[f]["T"], f))
    D = (res[fE]["T"] + res[fT]["T"]) / 2
    fM = min((f for f in grid if res[f]["T"] <= D * (1 + 1e-9)), key=lambda f: (res[f]["E_primary"], f))
    pol = {"acc": dict(f=0.0, **res[0.0])}
    if res[1.0]["T"] <= C["cpu_alone_max_s"]:
        pol["cpu"] = dict(f=1.0, **res[1.0])
    for name, f in (("static_E", fE), ("static_M", fM), ("static_T", fT)):
        pol[name] = dict(f=f, **res[f])
    pol["dynamic"] = dynamic(W, M, N, a, c, mach)
    return pol


def main():
    os.makedirs(OUT, exist_ok=True)
    S = settings()
    fr = frodo_rows()
    pred = []
    for mach in ("smc", "frodo"):
        for case, sc in KERNELS:
            s = S[(case, sc)]
            M = s["M"]
            if mach == "smc":
                a, c = gpu_inputs(case, sc, M), smc_cpu_inputs(case, sc, M)
            else:
                a, c = card_inputs(fr, case, sc, M), frodo_cpu_inputs(fr, case, sc, M)
            maxp = C["maxpairs"].get(case, C["maxpairs_default"])
            NL = min(maxp, int(C["large_s"] * a["r"] / (2 * M)) // 256 * 256)
            for wl, N in (("setting", s["N"]), ("large", NL)):
                W = 2.0 * N * M
                pol = plan(mach, a, c, W, M, N)
                for name, p in pol.items():
                    pred.append(dict(machine=mach, case=case, scheme=sc, steps=M, workload=wl, pairs=N, path_steps=W, policy=name,
                                     f_cpu=p["f"], pairs_cpu=int(round(p["f"] * N)) if name != "dynamic" else None,
                                     K=p.get("K"), block_pairs=p.get("block_pairs"), T_s=p["T"], t_acc_s=p["ta"], t_cpu_s=p["tc"],
                                     E_primary_J=p["E_primary"], E_server_J=p["E_server"]))
            pred[-1]["inputs"] = dict(acc=a, cpu=c)
    # hypotheses (large workloads)
    def g(m, wl, case, sc, pol):
        return next(p for p in pred if (p["machine"], p["workload"], p["case"], p["scheme"], p["policy"]) == (m, wl, case, sc, pol))
    hc2 = []
    for case, sc in KERNELS:
        A, T = g("smc", "large", case, sc, "acc"), g("smc", "large", case, sc, "static_T")
        hc2.append(dict(case=case, scheme=sc, f_E=g("smc", "large", case, sc, "static_E")["f_cpu"], makespan_gain=1 - T["T_s"] / A["T_s"],
                        energy_ratio_T_over_acc=T["E_primary_J"] / A["E_primary_J"]))
    hc3 = []
    for case, sc in KERNELS:
        A, T = g("frodo", "large", case, sc, "acc"), g("frodo", "large", case, sc, "static_T")
        er = T["E_primary_J"] / A["E_primary_J"]
        hc3.append(dict(case=case, scheme=sc, makespan_ratio=T["T_s"] / A["T_s"], energy_ratio=er,
                        call="lower" if er <= 0.95 else ("higher" if er >= 1.05 else "no call")))
    hyp = dict(
        HC1=dict(statement="static co-execution (static_E, static_M, static_T) on the large workloads: measured makespan and energy per run "
                           "at the primary boundary within 15% of this prediction, every kernel, both machines", predicted=True),
        HC2=dict(statement="Supermicro: the energy-optimal static split leaves the CPU idle (f_E = 0) for every kernel, and the time-optimal "
                           "split raises the GPU+socket energy per run (large workloads, measured static_T > measured acc)",
                 predicted=all(x["f_E"] == 0 for x in hc2) and all(x["energy_ratio_T_over_acc"] > 1 for x in hc2), per_kernel=hc2),
        HC3=dict(statement="frodo, large workloads: the time-optimal static split shortens the makespan for every kernel; its card+socket "
                           "energy per run is lower than the card alone's for the kernels called 'lower' here (predicted ratio <= 0.95) and "
                           "higher for those called 'higher' (>= 1.05); kernels in between get no call and are reported only",
                 lower=[f"{x['case']} {x['scheme']}" for x in hc3 if x["call"] == "lower"],
                 higher=[f"{x['case']} {x['scheme']}" for x in hc3 if x["call"] == "higher"],
                 no_call=[f"{x['case']} {x['scheme']}" for x in hc3 if x["call"] == "no call"], per_kernel=hc3),
        HC4=dict(statement="dynamic self-scheduling over K equal blocks (large workloads): its measured makespan is within 10% of measured "
                           "static_T on frodo for every kernel, and more than 10% above it on the Supermicro for every kernel (there a CPU "
                           "block takes longer than the GPU's whole share, so the run waits for the CPU's last block)",
                 per_kernel=[dict(machine=m, case=cs, scheme=sc, dyn_over_static_T=g(m, "large", cs, sc, "dynamic")["T_s"] / g(m, "large", cs, sc, "static_T")["T_s"])
                             for m in ("smc", "frodo") for cs, sc in KERNELS]))
    hyp["HC4"]["predicted"] = all((abs(x["dyn_over_static_T"] - 1) <= 0.10) == (x["machine"] == "frodo") for x in hyp["HC4"]["per_kernel"])
    ins = {f: hashlib.sha256(open(os.path.join(RES, f), "rb").read()).hexdigest() for f in INPUT_FILES}
    out = dict(protocol="v0.7 (co-execution)", constants=C, inputs_sha256=ins, predictions=pred, hypotheses=hyp)
    jf = os.path.join(OUT, "predictions_coexec.json")
    json.dump(out, open(jf, "w"), indent=1, default=float)
    L = ["# Co-execution prediction (addendum v0.7)", "", "Primary boundary: frodo card + socket 0; Supermicro GPU + socket 1. Large workloads (about 4 s on the accelerator alone).", "",
         "| Machine | Kernel | acc T (s) | acc E (J) | static_E f | static_T f | static_T T/acc | static_T E/acc | static_M f | dynamic K, f |", "|---|---|---|---|---|---|---|---|---|---|"]
    for m in ("smc", "frodo"):
        for case, sc in KERNELS:
            A, E, T, Mm, D = (g(m, "large", case, sc, p) for p in ("acc", "static_E", "static_T", "static_M", "dynamic"))
            L.append(f"| {m} | {case} {sc} | {A['T_s']:.3f} | {A['E_primary_J']:.1f} | {E['f_cpu']:.3f} | {T['f_cpu']:.3f} | {T['T_s'] / A['T_s']:.3f} | "
                     f"{T['E_primary_J'] / A['E_primary_J']:.3f} | {Mm['f_cpu']:.3f} | {D['K']}, {D['f_cpu']:.3f} |")
    L += ["", f"- HC2 predicted: {hyp['HC2']['predicted']}", f"- HC4 predicted: {hyp['HC4']['predicted']} (dynamic/static_T makespan "
          f"{min(x['dyn_over_static_T'] for x in hyp['HC4']['per_kernel']):.3f}-{max(x['dyn_over_static_T'] for x in hyp['HC4']['per_kernel']):.3f})", f"- HC3 lower: {', '.join(hyp['HC3']['lower'])}; higher: {', '.join(hyp['HC3']['higher'])}; no call: {', '.join(hyp['HC3']['no_call'])}"]
    mf = os.path.join(OUT, "predictions_coexec.md")
    open(mf, "w").write("\n".join(L) + "\n")
    for f in (jf, mf):
        open(f + ".sha256", "w").write(hashlib.sha256(open(f, "rb").read()).hexdigest() + "  " + os.path.basename(f) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
