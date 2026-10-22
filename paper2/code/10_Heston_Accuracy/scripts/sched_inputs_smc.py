#!/usr/bin/env python3
"""sched_inputs_smc.py - Supermicro input runs for addendum v0.8 (scheduling), measured before v0.8 is registered:
  1. idle   : 3 windows of 30 s with nothing running (RAPL of both sockets, NVML of the GPU at the locked 1245 MHz)
  2. cpu    : the Supermicro's CPU device (socket 0, 20 threads, CPU-native code) at every one of the 36 settings,
              single pricing runs under the steady-clock protocol (3 s warm-up, 10 s window), 3 repeats
  3. switch : the V100's option-class switch in serve mode (build/hs_coexec, command C): time of the switch and of the
              first run after it, against the next runs, over all 12 kernel classes, 5 rounds (no energy)
Output: results/sched_inputs_smc/..., results/sched_inputs_smc_summary.json. About 45 minutes.
Usage (from the project folder; clock locked at 1245 MHz, RAPL readable): python3 scripts/sched_inputs_smc.py
"""
import csv, json, os, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase5_gpu as G
import smc_cpu as SC
import coexec_run as CR

ROOT, RES = G.ROOT, G.RES
SC.OUT = OUT = os.path.join(RES, "sched_inputs_smc")
REPS = 3


def idle(ev):
    out = []
    for i in range(1, REPS + 1):
        d = os.path.join(OUT, f"idle/rep{i}")
        prog = "import time; print('MARK start %.6f' % time.time(), flush=True); time.sleep(30); print('MARK end %.6f' % time.time(), flush=True); print('RESULT runs=1'); print('RESULT path_steps=1'); print('RESULT ok=1')"
        while G.other_cores() > 0.5:
            time.sleep(30)
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gpu_run.py"), "--no-pin", "--out", d, "--", sys.executable, "-c", prog],
                       capture_output=True, text=True)
        m = json.load(open(os.path.join(d, "meta.json")))
        out.append(dict(m["P_W"], disturbed=m["disturbed"]))
        G.log(ev, f"idle rep{i}: {json.dumps({k: round(v, 2) for k, v in m['P_W'].items()})}, disturbed {m['disturbed']}")
    return out


def cpu(ev):
    cfgs = SC.configs()
    rows = []
    for rep in range(1, REPS + 1):
        for r in G.settings():
            j = dict(case=r["case"], scheme=r["scheme"], M=r["M"], N=r["N"], cfg="s0_t20", rep=rep, seed=20262200 + rep,
                     name=f"cpu/set_{r['case']}_{r['scheme']}_{r['target'].replace('%', 'pct')}/rep{rep}")
            d = os.path.join(OUT, j["name"])
            if not os.path.exists(os.path.join(d, "DONE")):
                SC.run(j, cfgs, ev)
            if os.path.exists(os.path.join(d, "DONE")):
                x = G.record(d)
                rows.append(dict(case=r["case"], scheme=r["scheme"], target=r["target"], rep=rep, t_per_run_s=x["t_per_run_s"],
                                 E_socket0_J=x["E_socket0_J"], E_socket1_J=x["E_socket1_J"], E_gpu_J=x["E_gpu_J"], P_socket0_W=x["P_socket0_W"]))
    return rows


def switch(ev):
    ks = [("I", "ft", 2048), ("I", "qe", 320), ("II", "ft", 30720), ("II", "qe", 240), ("III", "ft", 2560), ("III", "qe", 40),
          ("P1", "ft", 64), ("P1", "qe", 8), ("B", "ft", 64), ("B", "qe", 8), ("A", "plain", 250), ("A", "cv", 250)]
    p = subprocess.Popen(["numactl", "--physcpubind=10", "--membind=1", os.path.join(ROOT, "build", "hs_coexec"), "--case", "I", "--scheme", "ft",
                          "--steps", "64", "--seed", "1", "--sync", "block"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    assert p.stdout.readline().strip() == "READY"

    def cmd(s):
        t0 = time.time(); p.stdin.write(s + "\n"); p.stdin.flush(); line = p.stdout.readline().strip(); return time.time() - t0, line
    out = []
    for rnd in range(5):
        for case, sc, M in ks:
            n = max(256, int(1e9 / (2 * M)) // 256 * 256)                 # about 1e9 path-steps: a few ms on the V100
            ts, _ = cmd(f"C {case} {sc} {M} 7")
            t1, _ = cmd(f"R 1 0 {n}")
            t2, _ = cmd(f"R 2 {n} {2 * n}")
            t3, _ = cmd(f"R 3 {2 * n} {3 * n}")
            out.append(dict(case=case, scheme=sc, round=rnd + 1, t_switch_s=ts, t_first_s=t1, t_next_s=statistics.median([t2, t3])))
    p.stdin.write("Q\n"); p.stdin.flush(); p.wait()
    sw = [x["t_switch_s"] for x in out]
    ex = [x["t_first_s"] - x["t_next_s"] for x in out]
    G.log(ev, f"GPU class switch: median {statistics.median(sw) * 1e3:.3f} ms, first-run extra median {statistics.median(ex) * 1e3:.3f} ms")
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    ev = open(os.path.join(RES, "sched_inputs_smc_events.log"), "a")
    G.log(ev, "v0.8 Supermicro inputs: idle, CPU device per setting, GPU class switch")
    res = dict(idle=idle(ev), switch=switch(ev), cpu=cpu(ev))
    json.dump(res, open(os.path.join(RES, "sched_inputs_smc_summary.json"), "w"), indent=1)
    ncpu = len({(r["case"], r["scheme"], r["target"], r["rep"]) for r in res["cpu"]})
    G.log(ev, f"SCHED INPUTS SMC {'FINISHED' if ncpu == 36 * REPS else 'INCOMPLETE'}: cpu {ncpu} of {36 * REPS}")


if __name__ == "__main__":
    main()
