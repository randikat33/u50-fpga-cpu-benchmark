#!/usr/bin/env python3
"""sched_inputs_frodo.py - frodo input runs for addendum v0.8 (scheduling), measured before v0.8 is registered:
  1. idle   : 3 windows of 30 s with nothing running (both sockets' RAPL and the card's power, power logger at 10 Hz)
  2. cpu    : frodo's CPU device for the scheduler (socket 1, all 48 hardware threads, CPU-native code) at every one of
              the 36 settings, single pricing runs under the steady-clock protocol (3 s warm-up, 10 s window), 3 repeats,
              seeds 20262300 + rep
  3. switch : scripts/card_switch.py inside the harness: the card's class and design changes (25 ordered design pairs
              x 3 rounds) and the CPU device's class changes (12 classes x 3 rounds), time and energy
  4. lan    : (with --agent HOST) the link to the Supermicro agent: round trips, clock offset and drift, per-part
              overhead, the agent's class-change times (scripts/sched_client.py --check)
Harness: common/run_measured.py; quietness guard as phase 6 (other users < 0.5 cores before a job; a job during which
they used > 1 core on average or > 4 in any second is repeated, up to 6 tries).
Output: results/sched_inputs_frodo/..., results/sched_inputs_frodo_summary.json. About 50 minutes.
Usage (from the project folder, F4 finished, RAPL readable): python3 scripts/sched_inputs_frodo.py --agent 10.8.100.5
"""
import argparse, csv, json, os, re, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu_measure as CM
import cpu_measure_s as CMS
import phase5 as P5
import phase5_s as P5S

ROOT, RES = CM.ROOT, CM.RES
OUT = os.path.join(RES, "sched_inputs_frodo")
REPS, MAX_TRIES = 3, 6
COLS = ("pkg0_w", "dram0_w", "pkg1_w", "dram1_w", "card_w")


def log(ev, s):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + s
    print(line, flush=True); ev.write(line + "\n"); ev.flush()


def power_rows(d):
    rows = []
    for r in csv.DictReader(open(os.path.join(d, "power.csv"))):
        x = {"t_unix": float(r["t_unix"])}
        for c in COLS:
            try:
                x[c] = float(r[c])
            except (ValueError, KeyError, TypeError):
                x[c] = None
        rows.append(x)
    return rows


def energies(rows, t0, t1):
    return {c[:-2]: CM.integrate(rows, t0, t1, c) for c in COLS}


def measured(d, cmd, cores, node, env, others, ev, common, label, extra=()):
    """one job in the harness with the quietness guard; True when kept"""
    for t in range(1, MAX_TRIES + 1):
        if os.path.isdir(d):
            shutil.rmtree(d)
        CM.wait_quiet(others, lambda s: log(ev, s))
        t0 = time.time()
        p = subprocess.run([sys.executable, os.path.join(common, "run_measured.py"), "--out", d, "--timeout", "3600", "--label", label,
                            "--start-marker", "^MARK start", "--end-marker", "^MARK end", "--cores", cores, "--node", node] + list(extra) + ["--"] + cmd,
                           env=env, capture_output=True, text=True)
        t1 = time.time()
        mean, peak = others.stats(t0, t1)
        if os.path.exists(os.path.join(d, "DONE")) and mean <= CM.BUSY_MEAN and peak <= CM.BUSY_PEAK:
            json.dump(dict(others_cores_mean=mean, others_cores_peak=peak, cmd=cmd), open(os.path.join(d, "contamination.json"), "w"), indent=1)
            return True
        log(ev, f"  {label}: try {t} set aside (rc {p.returncode}, others {mean:.2f} cores, peak {peak:.1f}) {p.stderr.strip()[-300:]}")
        if os.path.exists(os.path.join(d, "DONE")):
            os.remove(os.path.join(d, "DONE"))
        time.sleep(10)
    return False


def window(d):
    m = json.load(open(os.path.join(d, "meta.json")))
    return m["t_start_marker"], m["t_end_marker"]


def idle(ev, others, common, n0):
    out = []
    prog = "import time; print('MARK start %.6f' % time.time(), flush=True); time.sleep(30); print('MARK end %.6f' % time.time(), flush=True); print('RESULT runs=1'); print('RESULT path_steps=1'); print('RESULT ok=1')"
    for i in range(1, REPS + 1):
        d = os.path.join(OUT, f"idle/rep{i}")
        if not os.path.exists(os.path.join(d, "DONE")):
            measured(d, [sys.executable, "-c", prog], n0.split(",")[0].split("-")[0], "0", dict(os.environ), others, ev, common, f"idle rep{i}")
        if os.path.exists(os.path.join(d, "DONE")):
            t0, t1 = window(d)
            E = energies(power_rows(d), t0, t1)
            P = {k: v / (t1 - t0) for k, v in E.items()}
            out.append(P)
            log(ev, f"idle rep{i}: " + ", ".join(f"{k} {v:.2f} W" for k, v in P.items()))
    return out


def cpu(ev, others, common, n1):
    rows = []
    cfgs = {"s1_t48": dict(cores=n1, node="1", places="threads")}
    sets = [(P5, r) for r in CM.settings()] + [(P5S, r) for r in CMS.settings()]
    for rep in range(1, REPS + 1):
        for mod, r in sets:
            mod.WARM_S, mod.WINDOW_S = 3.0, 10.0
            j = dict(impl="native", cfg="s1_t48", case=r["case"], scheme=r["scheme"], M=r["M"], N=r["N"], seed=20262300 + rep)
            name = f"cpu/set_{r['case']}_{r['scheme']}_{r['target'].replace('%', 'pct')}/rep{rep}"
            d = os.path.join(OUT, name)
            if not os.path.exists(os.path.join(d, "DONE")):
                cmd, t = mod.command(j, cfgs, {})
                env = dict(os.environ, OMP_NUM_THREADS=str(t), OMP_PLACES="threads", OMP_PROC_BIND="close", OMP_DYNAMIC="false")
                if not measured(d, cmd, n1, "1", env, others, ev, common, name):
                    log(ev, f"  {name}: given up")
                    continue
            t0, t1 = window(d)
            txt = open(os.path.join(d, "stdout.log"), errors="replace").read()
            res = dict(re.findall(r"^RESULT (\w+)=(\S+)", txt, re.M))
            E = energies(power_rows(d), t0, t1)
            runs = int(res["runs"])
            x = dict(case=r["case"], scheme=r["scheme"], target=r["target"], steps=r["M"], pairs=r["N"], rep=rep, runs=runs,
                     t_per_run_s=(t1 - t0) / runs, ok=int(res.get("ok", 0)),
                     E_socket1_J=(E["pkg1"] + E["dram1"]) / runs, E_socket0_J=(E["pkg0"] + E["dram0"]) / runs, E_card_J=E["card"] / runs,
                     P_socket1_W=(E["pkg1"] + E["dram1"]) / (t1 - t0), P_socket0_W=(E["pkg0"] + E["dram0"]) / (t1 - t0), P_card_W=E["card"] / (t1 - t0))
            rows.append(x)
            log(ev, f"{name}: {x['t_per_run_s'] * 1e3:.3f} ms/run, socket 1 {x['P_socket1_W']:.1f} W, socket 0 {x['P_socket0_W']:.1f} W, ok {x['ok']}")
    return rows


def switch(ev, others, common, n0, n1, ini):
    d = os.path.join(OUT, "switch")
    if not os.path.exists(os.path.join(d, "DONE")):
        env = dict(os.environ, CARD_SWITCH_INI=ini)
        if not measured(d, [sys.executable, os.path.join(ROOT, "scripts", "card_switch.py")], f"{n0},{n1}", "0,1", env, others, ev, common, "switch"):
            return []
    rows = power_rows(d)
    out = []
    for line in open(os.path.join(d, "stdout.log"), errors="replace"):
        f = line.split()
        if not f or f[0] != "SW":
            continue
        dev, frm, to, rnd = f[1], f[2], f[3], int(f[4])
        t0, tr, ta, tb, a, b = map(float, f[5:11])
        E = energies(rows, t0, tr)
        out.append(dict(dev=dev, frm=frm, to=to, round=rnd, t_change_s=tr - t0, first_part_s=a, second_part_s=b,
                        E_change_J={k: v for k, v in E.items()}))
    for dev in ("card", "cpu"):
        same = [x["t_change_s"] for x in out if x["dev"] == dev and x["frm"] == x["to"]]
        diff = [x["t_change_s"] for x in out if x["dev"] == dev and x["frm"] != x["to"]]
        log(ev, f"{dev} change: same design/class median {statistics.median(same) if same else float('nan'):.3f} s, "
                f"other median {statistics.median(diff) if diff else float('nan'):.3f} s ({len(out)} changes recorded)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="", help="the Supermicro's address, for the LAN check (agent running there)")
    ap.add_argument("--only", default="idle,cpu,switch,lan")
    ap.add_argument("--common", default=os.path.abspath(os.path.join(ROOT, "..", "..", "common")))
    a = ap.parse_args()
    parts = a.only.split(",")
    for f in ("run_measured.py", "power_monitor.py", "xrt_timing.ini"):
        if not os.path.exists(os.path.join(a.common, f)):
            sys.exit(f"{f} not found in {a.common}")
    for b in ("hq_native", "hs_native", "hq_fpga", "hs_fpga", "hq_native_serve", "hs_native_serve"):
        if not os.path.exists(os.path.join(ROOT, "build", b)):
            sys.exit(f"build/{b} not found")
    busy = subprocess.run(["pgrep", "-f", "replicates.py|calibrate_s24.py|calibrate.py|calibrate_s.py|phase6.py|coexec_queue.py"], capture_output=True, text=True).stdout.split()
    busy = [p for p in busy if int(p) != os.getpid()]
    if busy:
        sys.exit(f"other measurement or accuracy runs are still going (pids {' '.join(busy)}): wait until they finish")
    n0, n1 = CM.node_cpus(0), CM.node_cpus(1)
    if CM.count(n1) != 48:
        sys.exit(f"socket 1 has {CM.count(n1)} hardware threads, not 48")
    os.makedirs(OUT, exist_ok=True)
    ev = open(os.path.join(RES, "sched_inputs_frodo_events.log"), "a")
    log(ev, f"v0.8 frodo inputs: {', '.join(parts)}")
    sf = os.path.join(RES, "sched_inputs_frodo_summary.json")
    res = json.load(open(sf)) if os.path.exists(sf) else {}
    others = CM.Others()
    if "idle" in parts:
        res["idle"] = idle(ev, others, a.common, n0)
    if "switch" in parts:
        res["switch"] = switch(ev, others, a.common, n0, n1, os.path.join(a.common, "xrt_timing.ini"))
    if "cpu" in parts:
        res["cpu"] = cpu(ev, others, a.common, n1)
    json.dump(res, open(sf, "w"), indent=1)
    if "lan" in parts and a.agent:
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "sched_client.py"), "--check", a.agent], capture_output=True, text=True)
        for line in (p.stdout + p.stderr).strip().splitlines():
            log(ev, "lan: " + line)
        if os.path.exists(os.path.join(RES, "sched_lan.json")):
            res["lan"] = json.load(open(os.path.join(RES, "sched_lan.json")))
    others.stop = True
    json.dump(res, open(sf, "w"), indent=1)
    ncpu = len({(r["case"], r["scheme"], r["target"], r["rep"]) for r in res.get("cpu", [])})
    nsw = len(res.get("switch", []))
    ok = ncpu == 36 * REPS and len(res.get("idle", [])) == REPS and nsw == 25 * 3 + 12 * 3 and ("lan" in res or not a.agent)
    log(ev, f"SCHED INPUTS FRODO {'FINISHED' if ok else 'INCOMPLETE'}: idle {len(res.get('idle', []))} of {REPS}, cpu {ncpu} of {36 * REPS}, "
            f"changes {nsw} of {25 * 3 + 12 * 3}, lan {'done' if 'lan' in res else 'not done'}")


if __name__ == "__main__":
    main()
