#!/usr/bin/env python3
"""coexec_check.py - functional check of the serve-mode programs before the co-execution runs (addendum v0.7):
the serve program's prices for a pair range must equal the normal program's for the same range and seed.
  Supermicro: build/hs_coexec against build/hs_gpu (functional mode, <= 1e7 path-steps), and the CPU-native serve
              programs against build/hq_native, build/hs_native.
  frodo     : build/hq_fpga --serve, build/hs_fpga --serve against the same programs without --serve, and the
              CPU-native serve programs.
No time or energy is recorded. Usage: python3 scripts/coexec_check.py --machine smc|frodo
"""
import argparse, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coexec_run as CR

ROOT, B = CR.ROOT, CR.B
CASES = [("I", "ft", 64, 4096), ("P1", "qe", 8, 65536), ("B", "qe", 8, 8192), ("A", "cv", 250, 4096)]


def serve(cmd, a0, a1, env=None):
    p = subprocess.run(cmd, input=f"R 1 {a0} {a1}\nQ\n", capture_output=True, text=True, env=env)
    for line in p.stdout.splitlines():
        if line.startswith("D "):
            f = line.split()
            return [float(x) for x in f[4:7]]
    res = dict(re.findall(r"^RESULT (price\d)=(\S+)", p.stdout, re.M))
    if res:
        return [float(res[f"price{k}"]) for k in range(3)]
    raise RuntimeError(f"serve failed: {' '.join(cmd)}\n{p.stdout[-500:]}{p.stderr[-500:]}")


def normal(cmd, env=None):
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    res = dict(re.findall(r"^RESULT (price\d)=(\S+)", p.stdout, re.M))
    if len(res) < 3:
        raise RuntimeError(f"program failed: {' '.join(cmd)}\n{p.stdout[-500:]}{p.stderr[-500:]}")
    return [float(res[f"price{k}"]) for k in range(3)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", choices=("smc", "frodo"), required=True)
    a = ap.parse_args()
    bad = 0
    for case, sc, M, n in CASES:
        fam = "main" if case in CR.MAIN else "sec"
        a0, a1, seed = 1000, 1000 + n, 777
        ca = CR.case_args(case, sc, M)
        checks = []
        nat = "hq_native" if fam == "main" else "hs_native"
        checks.append(("cpu-native", [os.path.join(B, nat + "_serve")] + ca + ["--seed", str(seed), "--threads", "4"],
                       [os.path.join(B, nat)] + ca + ["--seed", str(seed), "--threads", "4", "--a0", str(a0), "--a1", str(a1)], None))
        if a.machine == "smc":
            g = CR.gpu_args(case, sc, M) + ["--seed", str(seed), "--sync", "block"]
            checks.append(("gpu", [os.path.join(B, "hs_coexec")] + g, [os.path.join(B, "hs_gpu")] + g + ["--a0", str(a0), "--a1", str(a1)], None))
        else:
            prog = "hq_fpga" if fam == "main" else "hs_fpga"
            x = ["--xclbin", os.path.join(B, CR.XCL[(fam, sc)])] + ca + ["--seed", str(seed)]
            env = dict(os.environ, XRT_INI_PATH=os.path.abspath(os.path.join(ROOT, "..", "..", "common", "xrt_timing.ini")))
            checks.append(("card", [os.path.join(B, prog), "--serve"] + x + ["--a0", "0", "--a1", "64"],
                           [os.path.join(B, prog)] + x + ["--a0", str(a0), "--a1", str(a1)], env))
        for name, sc_cmd, nm_cmd, env in checks:
            try:
                s, m = serve(sc_cmd, a0, a1, env), normal(nm_cmd, env)
                same = all(abs(x - y) <= 1e-9 * max(1.0, abs(y)) for x, y in zip(s, m))
            except Exception as e:
                print(f"  {case} {sc} {name}: ERROR {e}")
                bad += 1
                continue
            bad += not same
            print(f"  {case} {sc} {name}: serve {s[1]:.10f}  normal {m[1]:.10f}  {'EQUAL' if same else 'DIFFERENT'}", flush=True)
    print("COEXEC CHECK " + ("ALL EQUAL" if not bad else f"{bad} PROBLEMS"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
