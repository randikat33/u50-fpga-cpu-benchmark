#!/usr/bin/env python3
"""gpu_functional_test.py - functional tests of the GPU-native code (protocol v0.6, Section 22: allowed before the
GPU prediction is registered; every run has at most 10^7 path-steps and no time is recorded).

 1. Prices: each case and method at its 1% setting (time step as registered; pairs cut to <= 10^7 path-steps)
    against reference + the matched code's calibrated bias at that step:  |z| <= 4 at every strike, with
    z = (price - ref - bias_cal) / sqrt(se^2 + se_bias^2).
 2. Reproducibility: the same run twice gives identical sums.
 3. Launch shape: 128 and 256 threads per block give the same sums up to summation-order rounding (1e-12).
Usage: python3 scripts/gpu_functional_test.py [--bin build/hs_gpu]
"""
import argparse, csv, math, os, re, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MAXPS = 1e7


def run(binary, args):
    p = subprocess.run([binary] + args, capture_output=True, text=True)
    r = dict(re.findall(r"^RESULT (\w+)=(\S+)", p.stdout, re.M))
    if p.returncode != 0 or r.get("ok") != "1":
        raise RuntimeError(f"{' '.join(args)}: rc {p.returncode}\n{p.stdout[-800:]}{p.stderr[-800:]}")
    return r


def settings():
    S = {}
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(ROOT, "results", f))):
            if r["rule"] == "empirical" and r["target"] == "1%" and r.get("steps"):
                S[(r["case"], r["scheme"])] = r
    return S


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.path.join(ROOT, "build", "hs_gpu"))
    a = ap.parse_args()
    S = settings()
    fails = 0
    print("1. prices at the 1% settings (pairs cut to <= 1e7 path-steps)")
    for case, schemes in (("I", ("ft", "qe")), ("II", ("ft", "qe")), ("III", ("ft", "qe")), ("P1", ("ft", "qe")),
                          ("B", ("ft", "qe")), ("A", ("plain", "cv"))):
        for sc in schemes:
            r0 = S[(case, sc)]
            steps = int(r0["steps"])
            pairs = min(int(r0["pairs"]) * 4, int(MAXPS / (2 * steps)))
            r = run(a.bin, ["--case", case, "--scheme", sc, "--steps", str(steps), "--pairs", str(pairs), "--seed", "777"])
            zs = []
            for k in range(3):
                b, sb = float(r0.get(f"bias{k}") or 0), float(r0.get(f"bias_se{k}") or 0)
                se = float(r[f"se{k}"])
                zs.append((float(r[f"price{k}"]) - float(r[f"ref{k}"]) - b) / math.sqrt(se * se + sb * sb))
            ok = all(abs(z) <= 4 for z in zs)
            fails += not ok
            print(f"  {case:3s} {sc:5s} steps {steps:5d} pairs {pairs:8d}  z = " + " ".join(f"{z:+5.2f}" for z in zs)
                  + f"   regs {r['regs_per_thread']}  {'PASS' if ok else 'FAIL'}")
    print("2. reproducibility and 3. launch shape (Case I QE, Case B FT, Case A CV)")
    for case, sc, steps in (("I", "qe", 40), ("B", "ft", 8), ("A", "cv", 250)):
        args = ["--case", case, "--scheme", sc, "--steps", str(steps), "--pairs", "20000", "--seed", "99"]
        x, y = run(a.bin, args), run(a.bin, args)
        z = run(a.bin, args + ["--tpb", "128"])
        same = all(x[f"d1_{k}"] == y[f"d1_{k}"] and x[f"dp_{k}"] == y[f"dp_{k}"] for k in range(3))
        close = all(abs(float(x[f"d1_{k}"]) - float(z[f"d1_{k}"])) <= 1e-12 * max(1.0, abs(float(x[f"d1_{k}"]))) for k in range(3))
        fails += (not same) + (not close)
        print(f"  {case:3s} {sc:5s}: same run twice identical {'PASS' if same else 'FAIL'}; tpb 128 vs 256 {'PASS' if close else 'FAIL'}")
    print("GPU FUNCTIONAL TEST: " + ("ALL PASS" if fails == 0 else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
