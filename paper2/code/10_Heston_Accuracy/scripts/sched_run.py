#!/usr/bin/env python3
"""sched_run.py - one scheduling job of addendum v0.8: one policy's plan for the 120-option book, priced pass after
pass on the four devices (frodo: card and fcpu here; Supermicro: gpu and scpu through the agent). Runs on frodo inside
common/run_measured.py (frodo's energy over the MARK window); the Supermicro's energy over the same window comes from
the agent (its clock offset measured by round trips before the window).

  python3 scripts/sched_run.py --plan results/sched/plans/<rep>/<policy>.json --agent 10.8.100.5 --out DIR
                               [--warmup-passes 1] [--window-s 60] [--min-passes 3]

Each device works through its ordered list (sched_model order), one option at a time: a local worker per class
(card: build/h{q,s}_fpga --serve on CPU 0, --wait xrt, registered XRT ini, a new process for a new class; fcpu: the
native serve program on socket 1, 48 threads), the agent's workers for gpu and scpu. A pass ends when every device
is done; the next pass starts then. After the warm-up pass(es) the timed window holds whole passes: at least
--min-passes and at least --window-s seconds. Writes DIR/sched_result.json; prints MARK start/end and RESULT lines.
"""
import argparse, json, os, queue, statistics, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import sched_client as SC

ROOT = S.ROOT
B = os.path.join(ROOT, "build")
XCL = {"ft": "ft/hq_ft.xclbin", "qe": "qe/hq_qe.xclbin", "bft": "bft/hb_ft.xclbin", "bqe": "bqe/hb_qe.xclbin", "asian": "asian/ha_asian.xclbin"}


def cpulist(node):
    s = open(f"/sys/devices/system/node/node{node}/cpulist").read().strip()
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def case_args(case, m, M):
    if case in S.MAIN:
        return ["--case", case, "--scheme", m, "--steps", str(M)]
    import hslib as HS
    return [str(x) for x in HS.args_for(case, m, steps=M)]


def parse(out):
    """one completed part: card 'D' line, or the native program's BEGIN ... RESULT ... END block"""
    res = {}
    while True:
        line = out.readline()
        if not line:
            raise RuntimeError("worker closed")
        line = line.strip()
        if line.startswith("D "):
            f = line.split()
            return float(f[2]), int(f[3]), [float(x) for x in f[4:7]], int(f[7])
        if line.startswith("RESULT ") and "=" in line:
            k, v = line[7:].split("=", 1); res[k] = v
        if line.startswith("END "):
            return float(line.split()[2]), int(float(res.get("pairs", 0))), [float(res.get(f"price{k}", "nan")) for k in range(3)], int(res.get("ok", 0))


class Local:
    """card or fcpu: one serve process for the current class"""

    def __init__(self, dev, ini, logdir):
        self.dev, self.ini, self.p, self.cls, self.changes = dev, ini, None, None, 0
        self.err = os.path.join(logdir, f"{dev}_worker_stderr.log")

    def start(self, case, m, M, seed):
        if self.dev == "card":
            fam = "main" if case in S.MAIN else "sec"
            prog = "hq_fpga" if fam == "main" else "hs_fpga"
            cmd = ["numactl", "--physcpubind=0", "--membind=0", os.path.join(B, prog), "--serve", "--xclbin", os.path.join(B, XCL[S.design(case, m)])] + \
                case_args(case, m, M) + ["--a0", "0", "--a1", "64", "--seed", str(seed), "--wait", "xrt"]
            env = dict(os.environ, XRT_INI_PATH=self.ini, OMP_NUM_THREADS="1")
        else:
            cpus = cpulist(1)
            prog = "hq_native_serve" if case in S.MAIN else "hs_native_serve"
            cmd = ["numactl", f"--physcpubind={','.join(map(str, cpus))}", "--membind=1", os.path.join(B, prog)] + case_args(case, m, M) + \
                ["--seed", str(seed), "--threads", str(len(cpus))]
            env = dict(os.environ, OMP_NUM_THREADS=str(len(cpus)), OMP_PLACES="threads", OMP_PROC_BIND="close", OMP_DYNAMIC="false")
        # the coordinator itself is pinned to one CPU (taskset); numactl refuses CPUs outside its parent's set, so the
        # worker's affinity is first widened to all CPUs, then numactl pins it
        cmd = ["taskset", "-c", f"0-{os.cpu_count() - 1}"] + cmd
        with open(self.err, "a") as fe:
            fe.write(f"--- {time.strftime('%H:%M:%S')} {' '.join(cmd)}\n")
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(self.err, "a"), text=True, bufsize=1, env=env)
        while True:
            line = self.p.stdout.readline()
            if not line:
                tail = open(self.err, errors="replace").read()[-600:]
                raise RuntimeError(f"{self.dev} worker did not start: {tail}")
            if line.strip() == "READY":
                break
        self.cls = (case, m, M, seed)

    def stop(self):
        if self.p:
            try:
                self.p.stdin.write("Q\n"); self.p.stdin.flush(); self.p.wait(timeout=60)
            except Exception:
                self.p.kill(); self.p.wait()
        self.p, self.cls = None, None

    def batch(self, cls, parts):
        """all parts of one class: the class's worker is (re)started if needed, every command is written at once
        (the worker runs them in order), then the results are read in order: [(arrival time, busy, pairs, prices, ok)]"""
        if self.cls != cls:
            self.stop(); self.start(*cls); self.changes += 1
        self.p.stdin.write("".join(f"R {k} {a0} {a1}\n" for k, a0, a1 in parts)); self.p.stdin.flush()
        out = []
        for _ in parts:
            r = parse(self.p.stdout)
            out.append((time.time(),) + r)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--port", type=int, default=50555)
    ap.add_argument("--out", required=True)
    ap.add_argument("--warmup-passes", type=int, default=1)
    ap.add_argument("--window-s", type=float, default=60.0)
    ap.add_argument("--min-passes", type=int, default=3)
    ap.add_argument("--max-passes", type=int, default=100000)
    ap.add_argument("--xrt-ini", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common", "xrt_timing.ini")))
    a = ap.parse_args()
    P = json.load(open(a.plan))
    opts = S.book()
    byid = {o["id"]: o for o in opts}
    plan = {int(i): tuple(x) for i, x in P["plan"].items()}
    order = S.device_order(plan, opts)
    used = [d for d in S.DEVICES if order[d]]
    # two agent processes on the Supermicro: the GPU worker and the sampler on --port, the CPU worker on --port + 1
    ag = SC.Agent(a.agent, a.port)
    agc = SC.Agent(a.agent, a.port + 1)
    sync0 = ag.sync(50)
    agc.sync(10)
    remote_q = {"gpu": queue.Queue(), "scpu": queue.Queue()}
    rem = {"gpu": ag, "scpu": agc}

    def route(src, dev):
        while True:
            _, m = src.runs.get()
            if m is None:
                remote_q[dev].put(None)
                return
            m["t_recv"] = m.get("t_recv", time.time())
            remote_q[dev].put(m)
    threading.Thread(target=route, args=(ag, "gpu"), daemon=True).start()
    threading.Thread(target=route, args=(agc, "scpu"), daemon=True).start()
    os.makedirs(a.out, exist_ok=True)
    local = {d: Local(d, a.xrt_ini, a.out) for d in ("card", "fcpu")}
    jid = [0]
    lock = threading.Lock()

    def next_id():
        with lock:
            jid[0] += 1
            return jid[0]

    def do_pass(d, out):
        """one device's share of a pass, pipelined: local workers get each class's commands at once; the agent gets
        the device's whole list at once (its worker runs them in order and changes class as needed)"""
        items = order[d]
        t0 = time.time()
        if d in local:
            k = 0
            while k < len(items):
                o = byid[items[k][0]]; m = items[k][1]; s = o["set"][m]
                cls = (o["case"], m, s["steps"], s["seed"])
                grp = []
                while k < len(items) and (byid[items[k][0]]["case"], items[k][1], byid[items[k][0]]["set"][items[k][1]]["steps"]) == cls[:3]:
                    grp.append(items[k]); k += 1
                tb = time.time()
                parts = [(next_id(), byid[i]["set"][mm]["a0"], byid[i]["set"][mm]["a1"]) for i, mm in grp]
                res = local[d].batch(cls, parts)
                for (i, mm), (ta, busy, pairs, pr, ok) in zip(grp, res):
                    out.append(dict(id=i, dev=d, m=mm, t0=tb, t1=ta, busy=busy, switch=0.0, pairs=pairs, prices=pr,
                                    ok=int(ok and pairs == byid[i]["set"][mm]["pairs"])))
        else:
            ids = {}
            for i, m in items:
                o = byid[i]; s = o["set"][m]
                k = next_id(); ids[k] = (i, m)
                rem[d].run(dict(id=k, dev="gpu" if d == "gpu" else "cpu", case=o["case"], scheme=m, steps=s["steps"], a0=s["a0"], a1=s["a1"], seed=s["seed"]))
            for _ in items:
                r = remote_q[d].get(timeout=3600)
                if r is None:
                    raise RuntimeError("agent closed")
                i, m = ids[r["id"]]
                out.append(dict(id=i, dev=d, m=m, t0=t0, t1=r["t_recv"], busy=r["busy"], switch=r["switch"], pairs=r["pairs"], prices=r["prices"],
                                ok=int(r["ok"] and r["pairs"] == byid[i]["set"][m]["pairs"])))

    def one_pass():
        res = {d: [] for d in used}
        th = [threading.Thread(target=do_pass, args=(d, res[d])) for d in used]
        t0 = time.time()
        err = []
        for t in th:
            t.start()
        for t in th:
            t.join()
        T = time.time() - t0
        n = sum(len(v) for v in res.values())
        if n != len(opts):
            raise RuntimeError(f"pass incomplete: {n} of {len(opts)} options")
        return T, res, {d: (max(x["t1"] for x in res[d]) - t0) for d in used}

    for _ in range(a.warmup_passes):
        one_pass()
    sync1 = ag.sync(30)
    ag.offset = sync1["offset_s"]
    t_start = time.time()
    print(f"MARK start {t_start:.6f}", flush=True)
    passes = []
    while len(passes) < a.max_passes and (len(passes) < a.min_passes or time.time() - t_start < a.window_s):
        T, res, dev_T = one_pass()
        passes.append(dict(T=T, dev_T=dev_T, options=[x for v in res.values() for x in v]))
    t_end = time.time()
    print(f"MARK end {t_end:.6f}", flush=True)
    smc = ag.energy(t_start, t_end)
    sync2 = ag.sync(30)
    for x in (ag, agc):
        x.stop()
        x.close()
    for w in local.values():
        w.stop()
    ok = int(all(x["ok"] for p in passes for x in p["options"]))
    res = dict(policy=P["policy"], rep=P.get("rep"), plan_file=os.path.abspath(a.plan), t_start=t_start, t_end=t_end, passes=passes,
               smc_energy=smc, sync=[sync0, sync1, sync2], local_changes={d: w.changes for d, w in local.items()}, ok=ok)
    os.makedirs(a.out, exist_ok=True)
    json.dump(res, open(os.path.join(a.out, "sched_result.json"), "w"))
    Ts = [p["T"] for p in passes]
    print(f"RESULT runs={len(passes)}")
    print("RESULT path_steps=1")
    print(f"RESULT pass_median_s={statistics.median(Ts):.6f}")
    print(f"RESULT t_per_pass_s={(t_end - t_start) / len(passes):.6f}")
    print(f"RESULT smc_E_J={sum(smc['E_J'].values()):.3f}")
    print(f"RESULT ok={ok}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
