#!/usr/bin/env python3
"""coexec_run.py - one co-execution job (addendum v0.7): an accelerator worker and a CPU worker in serve mode, driven
pricing run by pricing run, under the steady-clock protocol (>= 3 s untimed warm-up, timed window >= 10 s between the
MARK lines, wall-clock times). Run inside the machine's measurement harness (Supermicro: scripts/gpu_run.py --no-pin;
frodo: common/run_measured.py), which gives the energy of the window.

One pricing run of N pairs, by policy:
  acc      : all pairs on the accelerator           cpu : all pairs on the CPU
  static   : pairs [0, N - Nc) on the accelerator and [N - Nc, N) on the CPU, Nc = round(f N), both started together
  dynamic  : K equal blocks in pair order; each worker takes the next block whenever it is free (self-scheduling)
The run ends when every part is done (its makespan). The price of the run is the pair-weighted mean of the parts' prices.
Workers: Supermicro: build/hs_coexec on CPU 10 (block wait) and build/h{q,s}_native_serve on socket 1's other CPUs;
frodo: build/h{q,s}_fpga --serve on CPU 0 and the native serve program on socket 0's other CPUs.
Prints RESULT lines (runs, t_per_run_s, makespan, busy times, parts, prices, ok).
"""
import argparse, os, queue, statistics, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hslib as HS

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
B = os.path.join(ROOT, "build")
MAIN = ("I", "II", "III", "P1")
XCL = {("main", "ft"): "ft/hq_ft.xclbin", ("main", "qe"): "qe/hq_qe.xclbin", ("sec", "ft"): "bft/hb_ft.xclbin",
       ("sec", "qe"): "bqe/hb_qe.xclbin", ("sec", "plain"): "asian/ha_asian.xclbin", ("sec", "cv"): "asian/ha_asian.xclbin"}


def cpulist(node):
    s = open(f"/sys/devices/system/node/node{node}/cpulist").read().strip()
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def siblings(c):
    s = open(f"/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list").read().strip()
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def case_args(case, sc, M):
    if case in MAIN:
        return ["--case", case, "--scheme", sc, "--steps", str(M)]
    return [str(x) for x in HS.args_for(case, sc, steps=M)]


def gpu_args(case, sc, M):
    return ["--case", case, "--scheme", sc, "--steps", str(M)]


class Worker:
    """a serve-mode process; completed parts arrive on the shared queue as (name, id, seconds, pairs, prices, ok)"""

    def __init__(self, name, cmd, env, q):
        self.name, self.q = name, q
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        self.ready = threading.Event()
        self.busy = 0.0
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        cur, res = None, {}
        for line in self.p.stdout:
            line = line.strip()
            if line == "READY":
                self.ready.set()
            elif line.startswith("D "):                             # accelerator protocol
                f = line.split()
                self.q.put((self.name, int(f[1]), float(f[2]), int(f[3]), [float(x) for x in f[4:7]], int(f[7])))
            elif line.startswith("BEGIN "):                         # CPU-native protocol
                cur, res = int(line.split()[1]), {}
            elif line.startswith("RESULT ") and cur is not None and "=" in line:
                k, v = line[7:].split("=", 1)
                res[k] = v
            elif line.startswith("END ") and cur is not None:
                f = line.split()
                pr = [float(res.get(f"price{k}", "nan")) for k in range(3)]
                self.q.put((self.name, cur, float(f[2]), int(float(res.get("pairs", 0))), pr, int(res.get("ok", 0))))
                cur = None
        self.q.put((self.name, -1, 0.0, 0, [], 0))

    def send(self, i, a0, a1):
        self.p.stdin.write(f"R {i} {a0} {a1}\n")
        self.p.stdin.flush()

    def close(self):
        try:
            self.p.stdin.write("Q\n"); self.p.stdin.flush()
            self.p.wait(timeout=30)
        except Exception:
            self.p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", choices=("smc", "frodo"), required=True)
    ap.add_argument("--case", required=True); ap.add_argument("--scheme", required=True)
    ap.add_argument("--steps", type=int, required=True); ap.add_argument("--pairs", type=int, required=True)
    ap.add_argument("--policy", choices=("acc", "cpu", "static", "dynamic"), required=True)
    ap.add_argument("--fcpu", type=float, default=0.0); ap.add_argument("--K", type=int, default=64)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--warmup-s", type=float, default=3.0); ap.add_argument("--window-s", type=float, default=10.0)
    ap.add_argument("--xrt-ini", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common", "xrt_timing.ini")))
    a = ap.parse_args()
    fam = "main" if a.case in MAIN else "sec"
    N, M = a.pairs, a.steps
    if os.environ.get("COEXEC_TEST"):
        node, host = 0, 0
    elif a.machine == "smc":
        node, host = 1, min(cpulist(1))
    else:
        node, host = 0, min(cpulist(0))
    cpus = [c for c in cpulist(node) if c not in siblings(host)] if not os.environ.get("COEXEC_TEST") else [0]
    if os.environ.get("COEXEC_TEST"):
        node, host = 0, 0
    q = queue.Queue()
    need_acc, need_cpu = a.policy != "cpu", a.policy != "acc"
    ws = {}
    if need_acc:
        if a.machine == "smc":
            cmd = [os.path.join(B, "hs_coexec")] + gpu_args(a.case, a.scheme, M) + ["--seed", str(a.seed), "--sync", "block"]
            env = dict(os.environ)
        else:
            prog = "hq_fpga" if fam == "main" else "hs_fpga"
            cmd = [os.path.join(B, prog), "--serve", "--xclbin", os.path.join(B, XCL[(fam, a.scheme)])] + case_args(a.case, a.scheme, M) + \
                  ["--a0", "0", "--a1", "64", "--seed", str(a.seed), "--wait", "xrt"]
            env = dict(os.environ, XRT_INI_PATH=a.xrt_ini, OMP_NUM_THREADS="1")
        if os.environ.get("COEXEC_TEST"):                       # sandbox test only: a one-thread native program as "accelerator"
            prog = "hq_native_serve" if fam == "main" else "hs_native_serve"
            cmd, env = [os.path.join(B, prog)] + case_args(a.case, a.scheme, M) + ["--seed", str(a.seed), "--threads", "1"], dict(os.environ)
        ws["acc"] = Worker("acc", (["numactl", f"--physcpubind={host}", f"--membind={node}"] if not os.environ.get("COEXEC_TEST") else []) + cmd, env, q)
    if need_cpu:
        prog = "hq_native_serve" if fam == "main" else "hs_native_serve"
        cmd = [os.path.join(B, prog)] + case_args(a.case, a.scheme, M) + ["--seed", str(a.seed), "--threads", str(len(cpus))]
        env = dict(os.environ, OMP_NUM_THREADS=str(len(cpus)), OMP_PLACES="threads", OMP_PROC_BIND="close", OMP_DYNAMIC="false")
        pin = ["numactl", f"--physcpubind={','.join(map(str, cpus))}", f"--membind={node}"] if not os.environ.get("COEXEC_TEST") else []
        ws["cpu"] = Worker("cpu", pin + cmd, env, q)
    for w in ws.values():
        if not w.ready.wait(120):
            sys.exit(f"worker {w.name} not ready: {w.p.stderr.read()[-500:] if w.p.poll() is not None else ''}")
    ids = [0]

    def one_run():
        """one pricing run; returns makespan, busy times, parts, combined prices, ok"""
        t0 = time.time()
        parts = []
        if a.policy in ("acc", "cpu", "static"):
            nc = N if a.policy == "cpu" else (0 if a.policy == "acc" else int(round(a.fcpu * N)))
            todo = []
            if N - nc > 0:
                todo.append(("acc", 0, N - nc))
            if nc > 0:
                todo.append(("cpu", N - nc, N))
            for w, x0, x1 in todo:
                ids[0] += 1; ws[w].send(ids[0], x0, x1)
            for _ in todo:
                parts.append(q.get())
        else:
            K = max(1, min(a.K, N))
            bnd = [N * i // K for i in range(K + 1)]
            nxt, out = 0, 0
            for w in ("acc", "cpu"):
                if nxt < K:
                    ids[0] += 1; ws[w].send(ids[0], bnd[nxt], bnd[nxt + 1]); nxt += 1; out += 1
            while out:
                r = q.get(); parts.append(r); out -= 1
                if nxt < K and r[1] >= 0:
                    ids[0] += 1; ws[r[0]].send(ids[0], bnd[nxt], bnd[nxt + 1]); nxt += 1; out += 1
        T = time.time() - t0
        busy = {w: sum(p[2] for p in parts if p[0] == w) for w in ("acc", "cpu")}
        npairs = {w: sum(p[3] for p in parts if p[0] == w) for w in ("acc", "cpu")}
        tot = sum(p[3] for p in parts)
        pr = [sum(p[3] * p[4][k] for p in parts) / tot if tot else float("nan") for k in range(3)]
        ok = int(all(p[5] == 1 for p in parts) and tot == N)
        return T, busy, npairs, pr, ok, sum(1 for p in parts if p[0] == "cpu")

    tw = time.time()
    while True:
        r = one_run()
        if time.time() - tw >= a.warmup_s:
            break
    print(f"MARK start {time.time():.6f}", flush=True)
    t1, runs = time.time(), []
    while not runs or time.time() - t1 < a.window_s:
        runs.append(one_run())
    t2 = time.time()
    print(f"MARK end {t2:.6f}", flush=True)
    for w in ws.values():
        w.close()
    med = lambda xs: statistics.median(xs)
    T, busy, npairs, pr, ok, ncpu_blocks = runs[-1]
    out = dict(machine=a.machine, case=a.case, scheme=a.scheme, steps=M, pairs=N, policy=a.policy, fcpu=a.fcpu, K=a.K if a.policy == "dynamic" else 0,
               seed=a.seed, runs=len(runs), t_window_s=t2 - t1, t_per_run_s=(t2 - t1) / len(runs),
               makespan_median_s=med([x[0] for x in runs]), makespan_min_s=min(x[0] for x in runs), makespan_max_s=max(x[0] for x in runs),
               busy_acc_median_s=med([x[1]["acc"] for x in runs]), busy_cpu_median_s=med([x[1]["cpu"] for x in runs]),
               pairs_cpu_mean=statistics.mean(x[2]["cpu"] for x in runs), blocks_cpu_mean=statistics.mean(x[5] for x in runs),
               path_steps=2.0 * N * M, ok=int(all(x[4] for x in runs)))
    for k in range(3):
        out[f"price{k}"] = f"{pr[k]:.10f}"
        out[f"se{k}"] = "nan"
    for k, v in out.items():
        print(f"RESULT {k}={v}")
    sys.exit(0 if out["ok"] else 2)


if __name__ == "__main__":
    main()
