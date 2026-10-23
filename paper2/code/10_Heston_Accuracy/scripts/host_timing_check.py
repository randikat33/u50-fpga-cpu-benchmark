#!/usr/bin/env python3
"""host_timing_check.py - why are sub-millisecond card runs slower in phase 6 than in Part A? (diagnostic, no energy)
Times the Part A host (build/hq_fpga_partA) and the current host (build/hq_fpga, --wait xrt) back to back on the
three shortest settings (P1 FT 1%, P1 QE 1%, I QE 1%), 3 alternating rounds, 10 s window each, same xrt.ini and
pinning style as phase 5 (cores of socket 0). Output: results/host_timing_check.csv and a printed table.
Usage (frodo, card free): python3 scripts/host_timing_check.py
"""
import csv, os, re, statistics, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
B = os.path.join(ROOT, "build")
INI = os.path.abspath(os.path.join(ROOT, "..", "..", "common", "xrt_timing.ini"))
JOBS = [("P1", "ft", "1%"), ("P1", "qe", "1%"), ("I", "qe", "1%")]


def setting(case, sc, t):
    for r in csv.DictReader(open(os.path.join(ROOT, "results", "settings.csv"))):
        if r["rule"] == "empirical" and (r["case"], r["scheme"], r["target"]) == (case, sc, t):
            return int(r["steps"]), int(r["pairs"])


def run(prog, case, sc, M, N, extra):
    cmd = ["numactl", "--cpunodebind=0", "--membind=0", os.path.join(B, prog), "--xclbin", os.path.join(B, sc, f"hq_{sc}.xclbin"),
           "--case", case, "--scheme", sc, "--steps", str(M), "--a0", "0", "--a1", str(N), "--seed", "4242", "--warmup", "1",
           "--warmup-s", "3", "--window-s", "10"] + extra
    p = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, XRT_INI_PATH=INI))
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", p.stdout, re.M))
    if p.returncode != 0:
        raise RuntimeError(f"{prog} failed: {p.stderr[-400:]}")
    return {k: float(res[k]) for k in ("t_per_run_s", "t_h2d_s", "t_kernel_s", "t_d2h_s", "t_compute_s")}


def main():
    for p in ("hq_fpga_partA", "hq_fpga"):
        if not os.path.exists(os.path.join(B, p)):
            sys.exit(f"build/{p} not found")
    rows = []
    for case, sc, t in JOBS:
        M, N = setting(case, sc, t)
        for rnd in range(3):
            for prog, extra in (("hq_fpga_partA", []), ("hq_fpga", ["--wait", "xrt"])):
                r = run(prog, case, sc, M, N, extra)
                r.update(case=case, scheme=sc, target=t, program=prog, round=rnd + 1)
                rows.append(r)
                print(f"  {case} {sc} {t} {prog:<14} {r['t_per_run_s'] * 1e3:8.3f} ms/run  (h2d {r['t_h2d_s'] * 1e3:.3f}, kernel {r['t_kernel_s'] * 1e3:.3f}, "
                      f"d2h {r['t_d2h_s'] * 1e3:.3f} ms)", flush=True)
    f = os.path.join(ROOT, "results", "host_timing_check.csv")
    with open(f, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    for case, sc, t in JOBS:
        a = statistics.median(r["t_per_run_s"] for r in rows if (r["case"], r["scheme"], r["program"]) == (case, sc, "hq_fpga_partA"))
        b = statistics.median(r["t_per_run_s"] for r in rows if (r["case"], r["scheme"], r["program"]) == (case, sc, "hq_fpga"))
        print(f"{case} {sc} {t}: Part A host {a * 1e3:.3f} ms, current host {b * 1e3:.3f} ms, ratio {b / a:.3f}")
    print("HOST TIMING CHECK FINISHED")


if __name__ == "__main__":
    main()
