#!/usr/bin/env python3
"""sched_inputs_serve.py - serve-mode input runs for addendum v0.8 (time only), on frodo with the Supermicro's agent.
The check of 22 October 2026 showed that a serve-mode option costs more than a single run at its setting when the
setting is small (the program's per-call work, the link). This measures, for each device alone and each of the 36
settings whose single run takes under 50 ms on that device, the cost of an option in the pipelined serve mode that
scripts/sched_run.py uses: the class's worker is started, 2 warm-up calls, then n = 1 s / t_single calls (5 to 50)
sent at once. Recorded: effective time per option (time from sending the batch to the last result / n), the median
part time reported by the worker (busy), and the latency of the first result (arrival - send - its busy).
Classes use seeds 20262900 + index (not the book's). Settings of 50 ms and more are not measured (the per-call part
is then a few per cent of the run). Output: results/sched_inputs_serve.json
Usage (frodo, project folder, agent running): python3 scripts/sched_inputs_serve.py --agent 10.8.100.5
Second run (22 October 2026, after check 4): with the final two-process agent; the first run used the single process.
"""
import argparse, json, os, queue, statistics as st, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import sched_run as R
import sched_client as SC

LIMIT = 0.05


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="10.8.100.5")
    ap.add_argument("--port", type=int, default=50555)
    ap.add_argument("--xrt-ini", default=os.path.abspath(os.path.join(S.ROOT, "..", "..", "common", "xrt_timing.ini")))
    a = ap.parse_args()
    C = S.Model.from_prediction(json.load(open(os.path.join(S.RES, "predictions_sched", "predictions_sched.json")))).C
    sets = S.settings()
    keys = sorted({(c, t, m) for (c, t, m) in sets}, key=lambda k: (S.class_order((k[0], k[2], sets[k]["steps"])), k[1]))
    out = []
    logdir = os.path.join(S.RES, "sched_inputs_serve")
    os.makedirs(logdir, exist_ok=True)
    # the agent's two processes (v0.8 Section 2): the GPU worker on --port, the CPU worker on --port + 1
    agents = {"gpu": SC.Agent(a.agent, a.port), "scpu": SC.Agent(a.agent, a.port + 1)}
    for x in agents.values():
        x.sync(20)
    jid = [0]
    for d in S.DEVICES:
        w = R.Local(d, a.xrt_ini, logdir) if d in ("card", "fcpu") else None
        for idx, (case, target, m) in enumerate(keys):
            ts = C[(d, case, target, m)].get("t_single", C[(d, case, target, m)]["t"])
            if ts >= LIMIT:
                continue
            s = sets[(case, target, m)]
            N, M, seed = s["pairs"], s["steps"], 20262900 + idx
            n = max(5, min(50, int(1.0 / ts)))

            def batch(k0, cnt):
                parts = [(jid[0] + j + 1, (k0 + j) * N, (k0 + j + 1) * N) for j in range(cnt)]
                jid[0] += cnt
                t0 = time.time()
                if w:
                    res = w.batch((case, m, M, seed), parts)
                    return t0, [(x[0], x[1], x[2], x[4]) for x in res]
                for k, a0, a1 in parts:
                    agents[d].run(dict(id=k, dev="gpu" if d == "gpu" else "cpu", case=case, scheme=m, steps=M, a0=a0, a1=a1, seed=seed))
                res = []
                for _ in parts:
                    _, r = agents[d].runs.get(timeout=600)
                    if r is None:
                        raise RuntimeError("agent closed")
                    res.append((r["t_recv"], r["busy"], r["pairs"], r["ok"]))
                return t0, res
            batch(0, 2)
            t0, res = batch(2, n)
            ok = all(x[3] == 1 and x[2] == N for x in res)
            busy = [x[1] for x in res]
            row = dict(device=d, case=case, target=target, method=m, steps=M, pairs=N, n=n, t_single=ts, t_eff=(res[-1][0] - t0) / n,
                       busy_med=st.median(busy), latency=res[0][0] - t0 - busy[0], ok=int(ok))
            out.append(row)
            print(f"{d} {case} {m} {target}: single {ts * 1e3:.3f} ms, serve {row['t_eff'] * 1e3:.3f} ms/option (busy {row['busy_med'] * 1e3:.3f}), "
                  f"latency {row['latency'] * 1e3:.2f} ms, ok {int(ok)}", flush=True)
        if w:
            w.stop()
    for x in agents.values():
        x.stop(); x.close()
    json.dump(out, open(os.path.join(S.RES, "sched_inputs_serve.json"), "w"), indent=1)
    print(f"SERVE INPUTS {'FINISHED' if out and all(r['ok'] for r in out) else 'INCOMPLETE'}: {len(out)} settings")


if __name__ == "__main__":
    main()
