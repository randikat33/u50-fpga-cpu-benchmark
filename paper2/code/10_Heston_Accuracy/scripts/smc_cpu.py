#!/usr/bin/env python3
"""smc_cpu.py - steady-state rate and energy of the CPU-native code on the Supermicro's CPUs, as input for addendum
v0.7 (co-execution and scheduling, protocol v0.6 Section 25). Measured before v0.7 is registered, like the GPU
microbenchmarks before the GPU prediction; the addendum states this. No co-execution and no GPU kernel runs here.

Programs: build/hq_native, build/hs_native (built on this machine, -march=native). For every case, method and step of
the 36 settings (30 keys), the steady state (about 1e10 path-steps per pricing run; steady-clock protocol, 3 s warm-up,
10 s window), in three CPU configurations, 3 repeats, interleaved, seeds 20262000 + rep:
  s1_t18 : socket 1 without core 10 (CPUs 11-19, 31-39; 18 threads): the CPU part of GPU co-execution, core 10 left
           for the GPU's host thread
  s1_t20 : all of socket 1 (CPUs 10-19, 30-39; 20 threads)
  s0_t20 : all of socket 0 (CPUs 0-9, 20-29; 20 threads): the Supermicro's CPU device in the scheduler
Each job runs under scripts/gpu_run.py (RAPL of both sockets, NVML of the idle GPU) with its own pinning (numactl
--physcpubind, --membind to the socket's node); same quietness rules as the GPU queue.
Output: results/smc_cpu/..., results/smc_cpu_summary.csv. About 1.5 hours.

Usage (from the project folder, after `make native snative`):
    python3 scripts/smc_cpu.py --plan
    nohup python3 scripts/smc_cpu.py > results/smc_cpu.log 2>&1 &
"""
import argparse, csv, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase5_gpu as G
import hslib as HS

ROOT, RES = G.ROOT, G.RES
OUT = os.path.join(RES, "smc_cpu")
BIN = {"main": os.path.join(ROOT, "build", "hq_native"), "sec": os.path.join(ROOT, "build", "hs_native")}
REPS, MAX_TRIES, PATH_STEPS = 3, 4, 1.0e10
MAXP = {"I": 2 ** 31, "II": 2 ** 31, "III": 2 ** 31, "P1": 2 ** 31, "B": 2 ** 29, "A": 2 ** 31}


def cpus(node):
    s = open(f"/sys/devices/system/node/node{node}/cpulist").read().strip()
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def configs():
    s0, s1 = cpus(0), cpus(1)
    first1 = min(s1)
    sib = [c for c in s1 if c != first1 and open(f"/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list").read().strip()
           == open(f"/sys/devices/system/cpu/cpu{first1}/topology/thread_siblings_list").read().strip()]
    t18 = [c for c in s1 if c != first1 and c not in sib]
    return {"s1_t18": (t18, 1), "s1_t20": (s1, 1), "s0_t20": (s0, 0)}


def keys():
    out = {}
    for r in G.settings():
        k = (r["case"], r["scheme"], r["M"])
        n = min(MAXP[r["case"]], max(64, int(PATH_STEPS / (2 * r["M"]))))
        out[k] = (n + 63) // 64 * 64
    return sorted(out.items())


def jobs():
    out = []
    for rep in range(1, REPS + 1):
        for cfg in ("s1_t18", "s1_t20", "s0_t20"):
            for (case, sc, M), n in keys():
                out.append(dict(case=case, scheme=sc, M=M, N=n, cfg=cfg, rep=rep, seed=20262000 + rep,
                                name=f"{cfg}/ss_{case}_{sc}_M{M}/rep{rep}"))
    return out


def command(j, cfgs):
    cl, node = cfgs[j["cfg"]]
    s = "main" if j["case"] in ("I", "II", "III", "P1") else "sec"
    a = ["--case", j["case"], "--scheme", j["scheme"], "--steps", str(j["M"])] if s == "main" else [str(x) for x in HS.args_for(j["case"], j["scheme"], steps=j["M"])]
    a += ["--a0", "0", "--a1", str(j["N"]), "--seed", str(j["seed"]), "--threads", str(len(cl)), "--runs", "1",
          "--warmup-s", str(G.WARM_S), "--window-s", str(G.WINDOW_S)]
    return ["numactl", f"--physcpubind={','.join(map(str, cl))}", f"--membind={node}", BIN[s]] + a, len(cl)


def run(j, cfgs, ev):
    d = os.path.join(OUT, j["name"])
    cmd, t = command(j, cfgs)
    env = dict(os.environ, OMP_NUM_THREADS=str(t), OMP_PLACES="threads", OMP_PROC_BIND="close", OMP_DYNAMIC="false")
    for k in range(1, MAX_TRIES + 1):
        while G.other_cores() > 0.5:
            time.sleep(30)
        if os.path.isdir(d):
            shutil.rmtree(d)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gpu_run.py"), "--no-pin", "--out", d, "--"] + cmd,
                           env=env, capture_output=True, text=True)
        try:
            m = json.load(open(os.path.join(d, "meta.json")))
        except Exception:
            m = {}
        if p.returncode == 0 and m.get("disturbed") == 0:
            G.log(ev, f"{j['name']}: ok, {p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''}")
            return True
        G.log(ev, f"{j['name']}: try {k} set aside: rc {p.returncode}, disturbed {m.get('disturbed')} {p.stderr.strip()[-200:]}")
        if os.path.exists(os.path.join(d, "DONE")):
            os.remove(os.path.join(d, "DONE"))
        time.sleep(10)
    return False


def summary():
    groups = {}
    for j in jobs():
        d = os.path.join(OUT, j["name"])
        if os.path.exists(os.path.join(d, "DONE")):
            groups.setdefault(j["name"].rsplit("/rep", 1)[0], (j, []))[1].append(G.record(d))
    rows = []
    for key, (j, rs) in sorted(groups.items()):
        W = rs[0]["path_steps"]
        med = lambda f: statistics.median(f(x) for x in rs)
        sock = "1" if j["cfg"].startswith("s1") else "0"
        rows.append(dict(group=key, cfg=j["cfg"], case=j["case"], scheme=j["scheme"], steps=j["M"], pairs=j["N"], reps=len(rs), path_steps=W,
                         t_per_run_s=med(lambda x: x["t_per_run_s"]), rate_path_steps_per_s=W / med(lambda x: x["t_per_run_s"]),
                         e_own_socket_nJ=med(lambda x: (x["E_socket1_J"] if sock == "1" else x["E_socket0_J"]) / W * 1e9),
                         e_server_nJ=med(lambda x: (x["E_socket0_J"] + x["E_socket1_J"]) / W * 1e9),
                         e_server_gpu_nJ=med(lambda x: (x["E_socket0_J"] + x["E_socket1_J"] + x["E_gpu_J"]) / W * 1e9),
                         P_socket0_W=med(lambda x: x["P_socket0_W"]), P_socket1_W=med(lambda x: x["P_socket1_W"]), P_gpu_idle_W=med(lambda x: x["P_gpu_W"])))
    if rows:
        f = os.path.join(RES, "smc_cpu_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} groups -> {f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        summary(); return
    cfgs = configs()
    js = jobs()
    left = [j for j in js if not os.path.exists(os.path.join(OUT, j["name"], "DONE"))]
    if a.plan:
        print(f"{len(left)} of {len(js)} jobs left; about {len(left) * (G.WARM_S + G.WINDOW_S + 8) / 3600:.1f} h")
        for c, (cl, node) in cfgs.items():
            print(f"  {c}: node {node}, {len(cl)} threads, CPUs {cl}")
        for b in BIN.values():
            print(f"  {b}: {'ok' if os.path.exists(b) else 'MISSING (make native snative)'}")
        return
    for b in BIN.values():
        if not os.path.exists(b):
            sys.exit(f"{b} not found: make native snative")
    os.makedirs(OUT, exist_ok=True)
    ev = open(os.path.join(RES, "smc_cpu_events.log"), "a")
    G.log(ev, f"Supermicro CPU input runs: {len(left)} of {len(js)} jobs left")
    failed = sum(not run(j, cfgs, ev) for j in left)
    summary()
    done = sum(1 for j in js if os.path.exists(os.path.join(OUT, j["name"], "DONE")))
    G.log(ev, f"SMC CPU {'FINISHED' if done == len(js) else 'INCOMPLETE'}: {done} of {len(js)} jobs, {failed} not done")


if __name__ == "__main__":
    main()
