#!/usr/bin/env python3
"""sched_client.py - frodo's side of the link to the Supermicro agent (scripts/sched_agent.py), addendum v0.8.
  Agent(host, port)          connect; replies of "run" go to the queue given (as dicts), other replies are returned
  .sync(n)                   n ping round trips; clock offset = agent time - midpoint of the round trip, from the
                             fastest 20% of round trips (offset added to frodo's time gives the agent's time)
  .run(job)                  send one option part to the agent's GPU or CPU
  .energy(t0, t1)            the agent's energies over [t0, t1] in frodo's clock (converted with the offset)
Also a LAN check (python3 scripts/sched_client.py --check HOST): round trips, offset, and per class and device the
round trip of a small part against the part's own time and its class-change time; writes results/sched_lan.json.
"""
import argparse, json, os, queue, socket, statistics, sys, threading, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")


class Agent:
    def __init__(self, host, port=50555, runs=None, timeout=10):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.settimeout(None)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.f = self.s.makefile("rw", buffering=1)
        self.runs = runs if runs is not None else queue.Queue()
        self.ctl = queue.Queue()
        self.wlock = threading.Lock()
        self.offset, self.rtt = 0.0, None
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.f:
            m = json.loads(line)
            if "op" in m:
                self.ctl.put((time.time(), m))
            else:
                m["t_recv"] = time.time()
                self.runs.put(("agent", m))
        self.ctl.put((time.time(), None))
        self.runs.put(("agent", None))

    def send(self, d):
        with self.wlock:
            self.f.write(json.dumps(d) + "\n"); self.f.flush()

    def call(self, d, timeout=60):
        self.send(d)
        t, m = self.ctl.get(timeout=timeout)
        if m is None:
            raise RuntimeError("agent closed the connection")
        return t, m

    def sync(self, n=50):
        xs = []
        for _ in range(n):
            t0 = time.time()
            t1, m = self.call(dict(op="ping"))
            xs.append((t1 - t0, m["t"] - (t0 + t1) / 2))
        xs.sort()
        best = xs[:max(1, n // 5)]
        self.offset = statistics.median(o for _, o in best)
        self.rtt = statistics.median(r for r, _ in xs)
        return dict(rtt_median_s=self.rtt, rtt_min_s=xs[0][0], rtt_max_s=max(r for r, _ in xs), offset_s=self.offset,
                    offset_spread_s=max(o for _, o in best) - min(o for _, o in best), n=n)

    def run(self, job):
        self.send(dict(op="run", **job))

    def energy(self, t0, t1):
        _, m = self.call(dict(op="energy", t0=t0 + self.offset, t1=t1 + self.offset), timeout=120)
        return m

    def stop(self):
        return self.call(dict(op="stop"), timeout=120)[1]

    def close(self):
        for f in (lambda: self.s.shutdown(socket.SHUT_RDWR), self.f.close, self.s.close):
            try:
                f()
            except Exception:
                pass


CLASSES = [("I", "ft", 2048), ("I", "qe", 320), ("II", "ft", 30720), ("II", "qe", 240), ("III", "ft", 2560), ("III", "qe", 40),
           ("P1", "ft", 64), ("P1", "qe", 8), ("B", "ft", 64), ("B", "qe", 8), ("A", "plain", 250), ("A", "cv", 250)]


def check(host, port, rounds=3):
    a = Agent(host, port)
    _, hello = a.call(dict(op="hello"))
    out = dict(agent_host=hello.get("host"), sync_start=a.sync(200), parts=[])
    jid = 0
    for rnd in range(1, rounds + 1):
        for dev in ("gpu", "cpu"):
            for case, sc, M in CLASSES:
                n = max(256, int((1e9 if dev == "gpu" else 2e8) / (2 * M)) // 256 * 256)
                for k in range(2):                              # the first part of a class pays the class change
                    jid += 1
                    t0 = time.time()
                    a.run(dict(id=jid, dev=dev, case=case, scheme=sc, steps=M, a0=k * n, a1=(k + 1) * n, seed=7))
                    _, m = a.runs.get(timeout=600)
                    rt = m["t_recv"] - t0
                    out["parts"].append(dict(round=rnd, dev=dev, case=case, scheme=sc, steps=M, part=k + 1, pairs=n, round_trip_s=rt,
                                             agent_s=m["t1"] - m["t0"], busy_s=m["busy"], switch_s=m["switch"], ok=m["ok"],
                                             overhead_s=rt - (m["t1"] - m["t0"])))
                    if not m["ok"]:
                        print(f"part not ok: {dev} {case} {sc}", flush=True)
    out["sync_end"] = a.sync(200)
    a.stop(); a.close()
    ps = out["parts"]
    for dev in ("gpu", "cpu"):
        sw = [p["switch_s"] for p in ps if p["dev"] == dev and p["part"] == 1 and p["round"] > 1]
        ov = [p["overhead_s"] for p in ps if p["dev"] == dev]
        out[f"{dev}_switch_median_s"] = statistics.median(sw) if sw else None
        out[f"{dev}_overhead_median_s"] = statistics.median(ov)
    out["drift_s_per_h"] = (out["sync_end"]["offset_s"] - out["sync_start"]["offset_s"]) / max(1e-9, (time.time() - T_START) / 3600)
    out["all_ok"] = int(all(p["ok"] for p in ps))
    return out


T_START = time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="HOST", required=True)
    ap.add_argument("--port", type=int, default=50555)
    a = ap.parse_args()
    try:
        r = check(a.check, a.port)
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        sys.exit(f"cannot reach the agent at {a.check}:{a.port}: {e}. Is scripts/sched_agent.py running there, and is the port open?")
    json.dump(r, open(os.path.join(RES, "sched_lan.json"), "w"), indent=1)
    s0 = r["sync_start"]
    print(f"agent {r['agent_host']}: round trip median {s0['rtt_median_s'] * 1e3:.3f} ms (min {s0['rtt_min_s'] * 1e3:.3f}, max {s0['rtt_max_s'] * 1e3:.3f}), "
          f"clock offset {s0['offset_s'] * 1e3:.2f} ms (spread {s0['offset_spread_s'] * 1e3:.3f} ms)")
    print(f"offset at the end {r['sync_end']['offset_s'] * 1e3:.2f} ms")
    for dev in ("gpu", "cpu"):
        print(f"{dev}: class change median {r[f'{dev}_switch_median_s'] * 1e3:.1f} ms, link + agent overhead per part median {r[f'{dev}_overhead_median_s'] * 1e3:.2f} ms")
    print(f"LAN CHECK {'FINISHED' if r['all_ok'] else 'FINISHED WITH BAD PARTS'}")


if __name__ == "__main__":
    main()
