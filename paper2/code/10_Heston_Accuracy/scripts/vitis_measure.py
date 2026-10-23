#!/usr/bin/env python3
"""vitis_measure.py - the AMD Vitis Libraries 2023.1 engines on the U50 (protocol v0.6 Section 23 item 5; descriptive),
measured like our card: common/run_measured.py (10 Hz RAPL and card rails, MARK window), host on CPU 0, memory node 0,
the registered XRT ini, steady-clock protocol (>= 3 s warm-up, >= 10 s window), quietness guard as phase 6, 3 repeats.

Settings: the QE settings of Cases I, II, III, P1 (MCEuropeanHestonEngine, which is QE in double precision) and the
plain settings of Case A (MCAsianAPEngine, arithmetic Asian under Black-Scholes), each at our steps and with
samples = 2 x our pairs. Case B is not run: the library's multi-asset engine fixes 5 assets at compile time, and B has 8.
Asian: the library's test uses timeSteps = fixings - 1; both 250 (the main runs) and 249 (an accuracy-only run, no
energy) are recorded, and the analysis reports which matches the reference.
Output: results/vitis/..., results/vitis_summary.csv (median of the repeats, with our card's phase-6 run at the setting).

Usage (frodo, project folder; the three hardware builds finished; `make vitis_host` or the g++ line in RUN_VITIS.md):
    python3 scripts/vitis_measure.py --lib ~/vitis_libraries_2023.1 --plan
    nohup python3 scripts/vitis_measure.py --lib ~/vitis_libraries_2023.1 > results/vitis.log 2>&1 &
"""
import argparse, csv, glob, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu_measure as CM
import cpu_measure_s as CMS
import phase5 as P5

ROOT, RES = CM.ROOT, CM.RES
OUT = os.path.join(RES, "vitis")
HOST = os.path.join(ROOT, "build", "vitis_host")
REPS, MAX_TRIES = 3, 6
HESTON = {"I": dict(T=10, r=0.0, v0=0.04, theta=0.04, kappa=0.5, xi=1.0, rho=-0.9),
          "II": dict(T=15, r=0.0, v0=0.04, theta=0.04, kappa=0.3, xi=0.9, rho=-0.5),
          "III": dict(T=5, r=0.0, v0=0.09, theta=0.09, kappa=1.0, xi=1.0, rho=-0.3),
          "P1": dict(T=1, r=0.01, v0=0.04, theta=0.04, kappa=1.5, xi=0.3, rho=-0.6)}
KMAIN, PUTS_MAIN = (70.0, 100.0, 140.0), (1, 0, 0)
ASIAN = dict(T=1.0, r=0.05, sigma=0.2, K=(90.0, 100.0, 110.0), puts=(0, 0, 0))
XCLBIN = {"heston": "MCEuropeanHestonEngine/build_dir.hw.xilinx_u50_gen3x16_xdma_5_202210_1/kernel_mc.xclbin",
          "asian": "MCAsianAPEngine/build_dir.hw.xilinx_u50_gen3x16_xdma_5_202210_1/kernel_MCAsianAP.xclbin"}


def settings():
    out = []
    for r in CM.settings():
        if r["scheme"] == "qe":
            out.append(dict(engine="heston", case=r["case"], target=r["target"], scheme="qe", M=r["M"], N=r["N"]))
    for r in CMS.settings():
        if r["case"] == "A" and r["scheme"] == "plain":
            out.append(dict(engine="asian", case="A", target=r["target"], scheme="plain", M=r["M"], N=r["N"]))
    return out


def args(s, xcl, steps=None):
    a = [HOST, "--xclbin", xcl, "--engine", s["engine"], "--S0", "100", "--steps", str(steps or s["M"]), "--samples", str(2 * s["N"])]
    if s["engine"] == "heston":
        c = HESTON[s["case"]]
        a += ["--r", str(c["r"]), "--T", str(c["T"]), "--K", ",".join(map(str, KMAIN)), "--puts", ",".join(map(str, PUTS_MAIN))]
        a += ["--v0", str(c["v0"]), "--theta", str(c["theta"]), "--kappa", str(c["kappa"]), "--xi", str(c["xi"]), "--rho", str(c["rho"])]
    else:
        a += ["--r", str(ASIAN["r"]), "--T", str(ASIAN["T"]), "--K", ",".join(map(str, ASIAN["K"])), "--puts", ",".join(map(str, ASIAN["puts"])),
              "--sigma", str(ASIAN["sigma"])]
    return a


def ref(case):
    import hqlib as HQ, hslib as HS
    return HQ.CASES[case]["ref"] if case in HESTON else [HS.ref(case, k) for k in range(3)]


def card_phase6():
    """our card's phase-6 runs at each setting (--wait xrt, both sessions): median t and energies per run"""
    import phase6 as P6
    out = {}
    for f in ("phase6_summary_s1.csv", "phase6_summary_s2.csv"):
        p = os.path.join(RES, f)
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            if r["kind"] == "set" and r["impl"] == "fpga" and r["wait"] == "xrt":
                out.setdefault((r["case"], r["target"], r["scheme"]), []).append(r)
    return {k: dict(t=statistics.median(float(x["t_per_run_s"]) for x in v), E_socket=statistics.median(float(x["E_socket_per_run_J"]) for x in v),
                    E_server=statistics.median(float(x["E_server_per_run_J"]) for x in v)) for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", required=True, help="the Vitis Libraries 2023.1 worktree")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    T = os.path.join(os.path.expanduser(a.lib), "quantitative_finance", "L2", "tests")
    xcl = {e: os.path.join(T, p) for e, p in XCLBIN.items()}
    ss = settings()
    jobs = [dict(s, rep=k, name=f"{s['case']}_{s['scheme']}_{s['target'].replace('%', 'pct')}/rep{k}") for k in range(1, REPS + 1) for s in ss]
    if a.summary:
        return summary(ss)
    left = [j for j in jobs if not os.path.exists(os.path.join(OUT, j["name"], "DONE"))]
    if a.plan:
        print(f"{len(left)} of {len(jobs)} jobs left ({len(ss)} settings x {REPS}); host {'ok' if os.path.exists(HOST) else 'MISSING'}")
        for e, p in xcl.items():
            print(f"  {e}: {p} {'ok' if os.path.exists(p) else 'MISSING'}")
        return
    for e, p in xcl.items():
        if not os.path.exists(p):
            sys.exit(f"{e} xclbin not found: {p}")
    if not os.path.exists(HOST):
        sys.exit("build/vitis_host not found")
    os.makedirs(OUT, exist_ok=True)
    ev = open(os.path.join(RES, "vitis_events.log"), "a")

    def log(s):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + s
        print(line, flush=True); ev.write(line + "\n"); ev.flush()

    others = CM.Others()
    ini = os.path.join(a.common, "xrt_timing.ini")
    cfg0 = CM.configs()["s0_c24"]["cores"]
    # accuracy-only runs of the Asian engine with 249 steps (the library test's fixings - 1), no energy
    acc = os.path.join(OUT, "asian_steps249.json")
    if not os.path.exists(acc):
        rows = []
        for s in [x for x in ss if x["engine"] == "asian"]:
            p = subprocess.run(args(s, xcl["asian"], steps=249) + ["--warmup-s", "0", "--window-s", "0"], capture_output=True, text=True,
                               env=dict(os.environ, XRT_INI_PATH=ini))
            res = dict(l[7:].split("=", 1) for l in p.stdout.splitlines() if l.startswith("RESULT ") and "=" in l)
            rows.append(dict(target=s["target"], prices=[float(res.get(f"price{k}", "nan")) for k in range(3)], rc=p.returncode))
            log(f"asian 249 steps {s['target']}: {rows[-1]['prices']}")
        json.dump(rows, open(acc, "w"), indent=1)
    # accuracy-only check of the Heston engine's time-step bias: P1 at its 0.1% pairs, 8 to 128 steps (first check on
    # the card, 23 Oct: at 8 steps the K=100 price was 8.0140 against the reference 8.0956, about -10 eps)
    hc = os.path.join(OUT, "heston_steps_P1.json")
    if not os.path.exists(hc):
        s1 = next(x for x in ss if x["engine"] == "heston" and x["case"] == "P1" and x["target"] == "0.1%")
        rows = []
        for m in (8, 16, 32, 64, 128):
            p = subprocess.run(args(s1, xcl["heston"], steps=m) + ["--warmup-s", "0", "--window-s", "0"], capture_output=True, text=True,
                               env=dict(os.environ, XRT_INI_PATH=ini))
            res = dict(l[7:].split("=", 1) for l in p.stdout.splitlines() if l.startswith("RESULT ") and "=" in l)
            rows.append(dict(steps=m, prices=[float(res.get(f"price{k}", "nan")) for k in range(3)], rc=p.returncode))
            log(f"heston P1 {m} steps: {rows[-1]['prices']}")
        json.dump(rows, open(hc, "w"), indent=1)
    log(f"Vitis engines: {len(left)} of {len(jobs)} jobs left")
    for j in left:
        d = os.path.join(OUT, j["name"])
        ok = False
        for t in range(1, MAX_TRIES + 1):
            if os.path.isdir(d):
                shutil.rmtree(d)
            CM.wait_quiet(others, log)
            t0 = time.time()
            p = subprocess.run([sys.executable, os.path.join(a.common, "run_measured.py"), "--out", d, "--timeout", "3600", "--label", j["name"],
                                "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--cores", cfg0, "--node", "0", "--xrt-ini", ini, "--"] +
                               args(j, xcl[j["engine"]]), capture_output=True, text=True, env=dict(os.environ, OMP_NUM_THREADS="1"))
            t1 = time.time()
            mean, peak = others.stats(t0, t1)
            if os.path.exists(os.path.join(d, "DONE")) and mean <= CM.BUSY_MEAN and peak <= CM.BUSY_PEAK:
                r = P5.record(d)
                log(f"{j['name']}: {r['runs']} runs, {r['t_per_run_s']:.4f} s/run, card {r['P_card_W']:.1f} W, socket {r['P_socket_W']:.0f} W, ok {r['ok']}")
                ok = True
                break
            log(f"{j['name']}: try {t} set aside (rc {p.returncode}, others {mean:.2f}/{peak:.1f}) {p.stderr.strip()[-300:]}")
            if os.path.exists(os.path.join(d, "DONE")):
                os.remove(os.path.join(d, "DONE"))
            time.sleep(10)
        if not ok:
            log(f"{j['name']}: given up")
    others.stop = True
    summary(ss)
    done = sum(1 for j in jobs if os.path.exists(os.path.join(OUT, j["name"], "DONE")))
    log(f"VITIS {'FINISHED' if done == len(jobs) else 'INCOMPLETE'}: {done} of {len(jobs)} jobs")


def summary(ss):
    card = card_phase6()
    rows = []
    for s in ss:
        rs = []
        for k in range(1, REPS + 1):
            d = os.path.join(OUT, f"{s['case']}_{s['scheme']}_{s['target'].replace('%', 'pct')}/rep{k}")
            if os.path.exists(os.path.join(d, "DONE")):
                rs.append(P5.record(d))
        if not rs:
            continue
        med = lambda key: statistics.median(x[key] for x in rs)
        rf = ref(s["case"])
        eps = (CM.settings() if s["engine"] == "heston" else CMS.settings())
        e = next(float(x["eps"]) for x in eps if (x["case"], x["target"], x["scheme"]) == (s["case"], s["target"], s["scheme"]))
        row = dict(engine=s["engine"], case=s["case"], target=s["target"], steps=s["M"], samples=2 * s["N"], reps=len(rs),
                   t_per_run_s=med("t_per_run_s"), E_socket_J=statistics.median((x["E_card_J"] + x["E_cpu_socket_J"]) / x["runs"] for x in rs),
                   E_server_J=statistics.median((x["E_card_J"] + x["E_cpu_server_J"]) / x["runs"] for x in rs),
                   P_card_W=med("P_card_W"), P_socket_W=med("P_socket_W"))
        for k in range(3):
            row[f"err_over_eps{k}"] = (rs[0][f"price{k}"] - rf[k]) / e
        c = card.get((s["case"], s["target"], s["scheme"]))
        if c:
            row.update(our_card_t_s=c["t"], our_card_E_socket_J=c["E_socket"], our_card_E_server_J=c["E_server"],
                       t_ratio_lib_over_ours=row["t_per_run_s"] / c["t"], E_ratio_lib_over_ours=row["E_socket_J"] / c["E_socket"])
        rows.append(row)
    if rows:
        f = os.path.join(RES, "vitis_summary.csv")
        keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} settings -> {f}")


if __name__ == "__main__":
    main()
