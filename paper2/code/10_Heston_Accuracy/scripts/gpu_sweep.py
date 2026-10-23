#!/usr/bin/env python3
"""gpu_sweep.py - the exploratory GPU runs of protocol v0.6 (Sections 21 and 22b), reported as such:
  clock sweep : steady state at locked SM clocks 945, 1095, 1245 and 1380 MHz (threads per block 256);
  launch shape: threads per block 64, 128, 256, 512 at 1245 MHz.
Six kernels, each at one time step in the steady state (>= 1e11 path-steps per run where the pairs allow, as in
phase 5): Heston FT and QE (Case I, steps of the 0.3% settings), basket FT and QE (Case B, 0.3%), Asian plain and CV.
Host wait: block (the primary GPU mode, fixed by session 1). 3 repeats, interleaved; seeds 20261900 + rep.
Same program (build/hs_gpu, sha256 checked), wrapper (scripts/gpu_run.py) and disturbance rules as phase 5; a job is
also repeated if the median SM clock is not within 1% of the locked clock.
Run with sudo (it sets the clock with nvidia-smi -lgc); each measurement runs as the calling user, and the script restores
the 1245 MHz lock at the end. Output: results/gpu_sweep/..., results/gpu_sweep_summary.csv. About 40 minutes.

Usage (from the project folder):
    sudo python3 scripts/gpu_sweep.py --plan
    sudo nohup python3 scripts/gpu_sweep.py > results/gpu_sweep.log 2>&1 &
"""
import argparse, csv, hashlib, json, os, shutil, statistics, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase5_gpu as G

ROOT, RES = G.ROOT, G.RES
OUT = os.path.join(RES, "gpu_sweep")
BIN_SHA = "fbddf8db898f232d7889f96ad7ed86ecc03b0b562c642bfb2454c8bd946f4bf9"
REPS, MAX_TRIES = 3, 4
CLOCKS = (945, 1095, 1245, 1380)
TPBS = (64, 128, 512)                     # 256 is the clock sweep's 1245 MHz job
KERNELS = [("I", "ft", "0.3%"), ("I", "qe", "0.3%"), ("B", "ft", "0.3%"), ("B", "qe", "0.3%"), ("A", "plain", "0.3%"), ("A", "cv", "0.3%")]


def kernels():
    rows = {(r["case"], r["scheme"], r["target"]): r for r in G.settings()}
    out = []
    for c, s, t in KERNELS:
        r = rows[(c, s, t)]
        n = min(G.MAX_PAIRS, max(r["N"], int(G.STEADY_PATH_STEPS / (2 * r["M"]))))
        out.append(dict(case=c, scheme=s, M=r["M"], N=(n + 255) // 256 * 256))
    return out


def jobs():
    out = []
    for rep in range(1, REPS + 1):
        seed = 20261900 + rep
        for clk in CLOCKS:
            for k in kernels():
                out.append(dict(k, kind="clock", clk=clk, tpb=256, rep=rep, seed=seed, name=f"clock{clk}/{k['case']}_{k['scheme']}_M{k['M']}/rep{rep}"))
        for tpb in TPBS:
            for k in kernels():
                out.append(dict(k, kind="tpb", clk=1245, tpb=tpb, rep=rep, seed=seed, name=f"tpb{tpb}/{k['case']}_{k['scheme']}_M{k['M']}/rep{rep}"))
    return out


def lock(clk):
    subprocess.run(["nvidia-smi", "-lgc", f"{clk},{clk}"], capture_output=True, text=True)
    time.sleep(2)


def run(j, ev):
    d = os.path.join(OUT, j["name"])
    user = os.environ.get("SUDO_USER")              # the measurement itself runs as the calling user (their nvidia-ml-py)
    pre = ["sudo", "-u", user, "-H"] if user and os.geteuid() == 0 else []
    cmd = pre + [sys.executable, os.path.join(ROOT, "scripts", "gpu_run.py"), "--out", d, "--", G.BIN, "--case", j["case"], "--scheme", j["scheme"],
           "--steps", str(j["M"]), "--a0", "0", "--a1", str(j["N"]), "--seed", str(j["seed"]), "--sync", "block", "--tpb", str(j["tpb"]),
           "--timed", "--warmup-s", str(G.WARM_S), "--window-s", str(G.WINDOW_S)]
    for t in range(1, MAX_TRIES + 1):
        while G.other_cores() > 0.5:
            time.sleep(30)
        if os.path.isdir(d):
            shutil.rmtree(d)
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            m = json.load(open(os.path.join(d, "meta.json")))
        except Exception:
            m = {}
        sm = m.get("sm_mhz_median") or 0
        if p.returncode == 0 and m.get("disturbed") == 0 and abs(sm - j["clk"]) <= 0.01 * j["clk"]:
            G.log(ev, f"{j['name']}: ok, SM {sm} MHz, {p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''}")
            return True
        G.log(ev, f"{j['name']}: try {t} set aside: rc {p.returncode}, disturbed {m.get('disturbed')}, SM {sm} (locked {j['clk']})")
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
        med = lambda f: statistics.median(f(x) for x in rs)
        W = rs[0]["path_steps"]
        rows.append(dict(group=key, kind=j["kind"], case=j["case"], scheme=j["scheme"], steps=j["M"], pairs=j["N"], clk_mhz=j["clk"], tpb=j["tpb"],
                         reps=len(rs), sm_mhz=med(lambda x: x["sm_mhz"]), t_per_run_s=med(lambda x: x["t_per_run_s"]), path_steps=W,
                         rate_path_steps_per_s=W / med(lambda x: x["t_per_run_s"]),
                         e_gpu_socket_nJ=med(lambda x: (x["E_gpu_J"] + x["E_socket1_J"]) / W * 1e9), e_gpu_alone_nJ=med(lambda x: x["E_gpu_J"] / W * 1e9),
                         P_gpu_W=med(lambda x: x["P_gpu_W"]), P_socket1_W=med(lambda x: x["P_socket1_W"]), temp_max_c=max(x["temp_max_c"] for x in rs)))
    if rows:
        f = os.path.join(RES, "gpu_sweep_summary.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} groups -> {f}")
        for r in rows:
            print(f"  {r['group']:<28} SM {r['sm_mhz']:>6.0f}  {r['rate_path_steps_per_s'] / 1e9:8.1f} G/s  {r['e_gpu_socket_nJ']:7.3f} nJ (GPU+socket1)  GPU {r['P_gpu_W']:6.1f} W")
    return rows


def give_back():
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        for p in (OUT, os.path.join(RES, "gpu_sweep_summary.csv"), os.path.join(RES, "gpu_sweep.log"), os.path.join(RES, "gpu_sweep_events.log")):
            if os.path.exists(p):
                subprocess.run(["chown", "-R", f"{uid}:{gid}", p])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    js = jobs()
    left = [j for j in js if not os.path.exists(os.path.join(OUT, j["name"], "DONE"))]
    sha = hashlib.sha256(open(G.BIN, "rb").read()).hexdigest() if os.path.exists(G.BIN) else ""
    if a.plan:
        print(f"{len(left)} of {len(js)} jobs left; about {len(left) * (G.WARM_S + G.WINDOW_S + 6) / 60:.0f} min")
        print("binary sha256 ok" if sha == BIN_SHA else "BINARY SHA256 DIFFERS: do not run")
        print("running as root" if os.geteuid() == 0 else "NOT ROOT: start it with sudo")
        return
    if a.summary:
        summary(); give_back()
        return
    if sha != BIN_SHA:
        sys.exit("STOP: build/hs_gpu is not the registered binary")
    if os.geteuid() != 0:
        sys.exit("STOP: run with sudo (the clock is set with nvidia-smi -lgc)")
    if not G.check_gpu().startswith("Enabled"):
        sys.exit("STOP: persistence mode is off (sudo nvidia-smi -pm 1)")
    os.makedirs(OUT, exist_ok=True)
    give_back()                                      # the folder must be writable by the calling user
    ev = open(os.path.join(RES, "gpu_sweep_events.log"), "a")
    G.log(ev, f"GPU sweep: {len(left)} of {len(js)} jobs left")
    cur, failed = None, 0
    try:
        for j in left:
            if j["clk"] != cur:
                lock(j["clk"]); cur = j["clk"]
            failed += not run(j, ev)
    finally:
        lock(1245)
        summary()
        done = sum(1 for j in js if os.path.exists(os.path.join(OUT, j["name"], "DONE")))
        G.log(ev, f"GPU SWEEP {'FINISHED' if done == len(js) else 'INCOMPLETE'}: {done} of {len(js)} jobs, {failed} not done")
        ev.close()
        give_back()


if __name__ == "__main__":
    main()
