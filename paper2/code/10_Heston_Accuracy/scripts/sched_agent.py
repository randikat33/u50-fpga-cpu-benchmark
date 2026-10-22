#!/usr/bin/env python3
"""sched_agent.py - the Supermicro's agent for the two-machine scheduler (addendum v0.8). It runs on the Supermicro,
listens on a TCP port, and serves one coordinator (on frodo) at a time with JSON lines:
  {"op": "hello"} / {"op": "ping"}      -> {"t": <agent wall time>}  (clock offset and round-trip time)
  {"op": "run", "id", "dev": "gpu"|"cpu", "case", "scheme", "steps", "a0", "a1", "seed"}
                                         -> {"id", "dev", "t0", "t1", "busy", "pairs", "prices", "ok", "switch"}
       gpu: build/hs_coexec on CPU 10 (block wait); an option class change is the serve command C (same CUDA context)
       cpu: the CPU-native serve program on all of socket 0 (20 threads); a class change restarts it
       The two devices run concurrently; each runs its options in the order received.
  {"op": "energy", "t0", "t1"}           -> energies (J) of pkg0, dram0, pkg1, dram1 and the GPU over [t0, t1] (agent clock)
                                            from its own 20 Hz sampler, and other users' CPU (cores, mean and peak)
  {"op": "stop"}                         -> stops the workers (the agent keeps listening)
The sampler (RAPL of both sockets, NVML energy, temperature and SM clock) runs while the agent runs.
Usage (Supermicro, from the project folder; GPU clock locked at 1245 MHz, RAPL readable):
    nohup python3 scripts/sched_agent.py --port 50555 > results/sched_agent.log 2>&1 &
This starts two processes: the GPU worker and the sampler on port 50555, the CPU worker on port 50556 (v0.8 check 3:
two workers in one Python process slowed each other's command handling).
"""
import argparse, json, os, socket, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpu_run as GR
import coexec_run as CR
import pynvml as nv

ROOT, B = CR.ROOT, CR.B


class Sampler(threading.Thread):
    def __init__(self, h):
        super().__init__(daemon=True)
        self.h, self.rows, self.lock = h, [], threading.Lock()
        self.own = {os.getpid()}

    def run(self):
        last = 0.0
        while True:
            t = time.time()
            r = dict(t_unix=t, **GR.rapl_read())
            try:
                r["gpu_mj"] = nv.nvmlDeviceGetTotalEnergyConsumption(self.h)
                r["sm_mhz"] = nv.nvmlDeviceGetClockInfo(self.h, nv.NVML_CLOCK_SM)
                r["temp"] = nv.nvmlDeviceGetTemperature(self.h, nv.NVML_TEMPERATURE_GPU)
            except Exception:
                pass
            if t - last >= 1.0:
                r["other_cpu_s"] = GR.busy_cpus_other(self.own)
                last = t
            with self.lock:
                self.rows.append(r)
                if len(self.rows) > 20 * 3600 * 6:                    # keep about 6 hours
                    del self.rows[:20 * 600]
            time.sleep(max(0.0, 0.05 - (time.time() - t)))

    def energy(self, t0, t1, wraps):
        with self.lock:
            rows = list(self.rows)
        E = {d: (GR.interp(rows, d, t1, wraps[d]) - GR.interp(rows, d, t0, wraps[d])) * 1e-6 for d, _ in GR.DOMAINS}
        E["gpu"] = (GR.interp(rows, "gpu_mj", t1) - GR.interp(rows, "gpu_mj", t0)) * 1e-3
        oth = [r for r in rows if "other_cpu_s" in r and t0 - 1 <= r["t_unix"] <= t1 + 1]
        per = [(b["other_cpu_s"] - a["other_cpu_s"]) / (b["t_unix"] - a["t_unix"]) for a, b in zip(oth, oth[1:]) if b["t_unix"] > a["t_unix"]]
        inw = [r for r in rows if t0 <= r["t_unix"] <= t1]
        sm = sorted(r["sm_mhz"] for r in inw if "sm_mhz" in r)
        return dict(E_J=E, other_mean=(sum(per) / len(per)) if per else 0.0, other_max=max(per, default=0.0),
                    sm_mhz_median=sm[len(sm) // 2] if sm else None, temp_max=max((r.get("temp", 0) for r in inw), default=0), samples=len(inw))


class Device:
    """one device's worker process and option queue"""

    def __init__(self, name, reply, sampler):
        self.name, self.reply, self.sampler = name, reply, sampler
        self.p, self.cls, self.q = None, None, []
        self.cv = threading.Condition()
        threading.Thread(target=self.loop, daemon=True).start()

    def start(self, cls):
        case, sc, M, seed = cls
        if self.name == "gpu":
            cmd = ["numactl", "--physcpubind=10", "--membind=1", os.path.join(B, "hs_coexec")] + CR.gpu_args(case, sc, M) + ["--seed", str(seed), "--sync", "block"]
            env = dict(os.environ)
        else:
            cpus = CR.cpulist(0)
            prog = "hq_native_serve" if case in CR.MAIN else "hs_native_serve"
            cmd = ["numactl", f"--physcpubind={','.join(map(str, cpus))}", "--membind=0", os.path.join(B, prog)] + CR.case_args(case, sc, M) + \
                  ["--seed", str(seed), "--threads", str(len(cpus))]
            env = dict(os.environ, OMP_NUM_THREADS=str(len(cpus)), OMP_PLACES="threads", OMP_PROC_BIND="close", OMP_DYNAMIC="false")
        # the agent is pinned off socket 0 and off the GPU host's core; numactl refuses CPUs outside its parent's set,
        # so the worker's CPU set is widened first, then numactl pins it
        cmd = ["taskset", "-c", f"0-{os.cpu_count() - 1}"] + cmd
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1, env=env)
        self.sampler.own.add(self.p.pid)
        line = self.p.stdout.readline().strip()
        if line != "READY":
            raise RuntimeError(f"{self.name} worker did not start: {line}")
        self.cls = cls

    def stop(self):
        if self.p:
            try:
                self.p.stdin.write("Q\n"); self.p.stdin.flush(); self.p.wait(timeout=30)
            except Exception:
                self.p.kill()
        self.p, self.cls = None, None

    def submit(self, job):
        with self.cv:
            self.q.append(job); self.cv.notify()

    def loop(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                j = self.q.pop(0)
            t0 = time.time()
            cls = (j["case"], j["scheme"], int(j["steps"]), int(j["seed"]))
            sw = 0.0
            try:
                if self.cls != cls:
                    s0 = time.time()
                    if self.name == "gpu" and self.p:
                        self.p.stdin.write(f"C {cls[0]} {cls[1]} {cls[2]} {cls[3]}\n"); self.p.stdin.flush()
                        assert self.p.stdout.readline().startswith("S ")
                        self.cls = cls
                    else:
                        self.stop(); self.start(cls)
                    sw = time.time() - s0
                self.p.stdin.write(f"R {j['id']} {j['a0']} {j['a1']}\n"); self.p.stdin.flush()
                busy, pairs, pr, ok = parse(self.p.stdout)
            except Exception as e:
                busy, pairs, pr, ok = 0.0, 0, [float("nan")] * 3, 0
                print(f"error on {self.name}: {e}", flush=True)
                self.stop()
            self.reply(dict(id=j["id"], dev=self.name, t0=t0, t1=time.time(), busy=busy, pairs=pairs, prices=pr, ok=ok, switch=sw))


def parse(out):
    """one completed part from a serve process (accelerator 'D' line, or the CPU program's BEGIN ... END block)"""
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


class _NoSampler:
    def __init__(self):
        self.own = {os.getpid()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=50555)
    ap.add_argument("--role", choices=("main", "cpu", "both"), default="main",
                    help="main: the GPU worker and the power sampler on --port, and a second agent process (role cpu) "
                         "for the CPU worker on --port + 1, so the two workers never share one Python process; "
                         "both: one process for both workers (as in the input runs)")
    ap.add_argument("--cpus", default="auto", help="the agent's own CPUs (auto: socket 1 without CPU 10 and its sibling, "
                                                    "first half for main, second half for cpu)")
    a = ap.parse_args()
    allc = [c for c in CR.cpulist(1) if c not in CR.siblings(10)]
    if a.cpus != "auto":
        own = [int(x) for x in a.cpus.split(",")]
    elif a.role == "main":
        own = allc[:len(allc) // 2]
    elif a.role == "cpu":
        own = allc[len(allc) // 2:]
    else:
        own = allc
    os.sched_setaffinity(0, own)
    child = None
    if a.role == "main":
        child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--role", "cpu", "--port", str(a.port + 1)])
    if a.role != "cpu":
        nv.nvmlInit()
        h = nv.nvmlDeviceGetHandleByIndex(0)
        wraps = GR.rapl_max()
        smp = Sampler(h); smp.start()
    else:
        smp, wraps = _NoSampler(), None
    names = {"main": ("gpu",), "cpu": ("cpu",), "both": ("gpu", "cpu")}[a.role]
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", a.port)); srv.listen(1)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"agent ({a.role}: {', '.join(names)}) listening on port {a.port}, pinned to CPUs {','.join(map(str, own))}", flush=True)
    while True:
        conn, addr = srv.accept()
        print(time.strftime("%Y-%m-%d %H:%M:%S"), f"coordinator {addr[0]} connected on port {a.port}", flush=True)
        f = conn.makefile("rw", buffering=1)
        lock = threading.Lock()

        def reply(d):
            with lock:
                f.write(json.dumps(d) + "\n"); f.flush()
        devs = {n: Device(n, reply, smp) for n in names}
        try:
            for line in f:
                m = json.loads(line)
                op = m.get("op")
                if op in ("hello", "ping"):
                    reply(dict(op=op, t=time.time(), host=socket.gethostname(), devices=list(names)))
                elif op == "run":
                    devs[m["dev"]].submit(m)
                elif op == "energy":
                    if wraps is None:
                        reply(dict(op="energy", error="this agent process has no sampler"))
                    else:
                        reply(dict(op="energy", **smp.energy(float(m["t0"]), float(m["t1"]), wraps)))
                elif op == "stop":
                    for d in devs.values():
                        d.stop()
                    reply(dict(op="stop", ok=1))
        except Exception as e:
            print(f"connection ended: {e}", flush=True)
        for d in devs.values():
            d.stop()
        conn.close()
        print(time.strftime("%Y-%m-%d %H:%M:%S"), f"coordinator disconnected from port {a.port}", flush=True)


if __name__ == "__main__":
    main()
