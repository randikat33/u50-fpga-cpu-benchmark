#!/usr/bin/env python3
"""test_program.py - tests of build/hq_cpu and the calibration scripts. Exit 0 = all pass."""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import hqlib as H

fails = 0


def check(ok, what):
    global fails
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    fails += 0 if ok else 1


# 1. thread count does not change a single bit (both schemes, all cases)
for case in H.CASES:
    for sc in ("ft", "qe"):
        r = H.run(["--case", case, "--scheme", sc, "--steps-per-year", 8, "--pairs", 3000, "--verify"], threads=2)
        check(int(r["verify_identical_to_scalar_1thread"]) == 1, f"{case} {sc}: 2 threads equal the scalar model on 1 thread")

# 2. consecutive pair ranges add up exactly to one run
for sc in ("ft", "qe"):
    whole = H.run(["--case", "I", "--scheme", sc, "--steps-per-year", 4, "--a0", 0, "--a1", 10000])
    m = H.Moments("scalar")
    for a0, a1 in ((0, 1234), (1234, 6000), (6000, 10000)):
        m.add(H.run(["--case", "I", "--scheme", sc, "--steps-per-year", 4, "--a0", a0, "--a1", a1]))
    w = H.Moments("scalar")
    w.add(whole)
    check(m.s1 == w.s1 and m.s2 == w.s2 and m.sp == w.sp and m.pairs == w.pairs,
          f"{sc}: three pair ranges add up exactly to the whole run")

# 3. statistics: QE close to the reference where the pilot found no bias (P1, 4 steps/yr; Case I, 16/yr)
for case, spy, extra in (("P1", 4, 0.01), ("I", 16, 0.03), ("III", 16, 0.06)):
    r = H.run(["--case", case, "--scheme", "qe", "--steps-per-year", spy, "--pairs", 200000])
    ok = all(abs(float(r[f"bias{k}"])) <= 4 * float(r[f"se{k}"]) + extra for k in range(3))
    check(ok, f"QE {case} at {spy}/yr within 4 SE (+{extra}) of the semi-closed form at all strikes: "
          + ", ".join(f"{float(r[f'bias{k}']):+.4f}+-{float(r[f'se{k}']):.4f}" for k in range(3)))
# FT: the pilot's first-order bias in Case I (0.56 at 16/yr) is reproduced
r = H.run(["--case", "I", "--scheme", "ft", "--steps-per-year", 16, "--pairs", 200000])
check(0.45 < float(r["bias1"]) < 0.68, f"FT Case I at 16/yr: bias {float(r['bias1']):+.3f} (pilot +0.56)")

# 4. float32 model against double precision on the same paths: QE (well conditioned) to 1e-4
for case in ("P1", "I"):
    r = H.run(["--case", case, "--scheme", "qe", "--steps-per-year", 8, "--impl", "diff", "--pairs", 20000])
    d = max(abs(float(r[f"mean_diff{k}"])) for k in range(3))
    check(d < 1e-4, f"QE {case}: |float - double| = {d:.2e} < 1e-4")

# 5. coupled FT estimator agrees with two separate double runs (P1, where coupling works well)
c = H.run(["--case", "P1", "--scheme", "ft", "--impl", "coupled", "--steps-per-year", 8, "--pairs", 100000])
f8 = H.run(["--case", "P1", "--scheme", "ft", "--impl", "double", "--steps-per-year", 8, "--pairs", 400000, "--seed", 9])
f4 = H.run(["--case", "P1", "--scheme", "ft", "--impl", "double", "--steps-per-year", 4, "--pairs", 400000, "--seed", 9])
d_sep = float(f8["price1"]) - float(f4["price1"])
se_sep = (float(f8["se1"]) ** 2 + float(f4["se1"]) ** 2) ** 0.5
z = (float(c["mean_diff1"]) - d_sep) / (float(c["se1"]) ** 2 + se_sep ** 2) ** 0.5
check(abs(z) < 4 and float(c["se1"]) / 2 < se_sep / 2,   # coupled: 1/4 of the pairs; SE scaled to equal pairs
      f"coupled b(1/8)-b(1/4) = {float(c['mean_diff1']):+.4f}+-{float(c['se1']):.4f} vs separate runs {d_sep:+.4f}+-{se_sep:.4f}"
      " (coupled needs < 1/4 of the pairs for the same SE)")

# 6. calibration controller and fit run end to end (loose targets, small grid)
with tempfile.TemporaryDirectory() as td:
    cmd = [sys.executable, os.path.join(H.HERE, "calibrate.py"), "--threads", "2", "--only", "P1",
           "--target-scale", "20", "--max-spy", "16", "--results", td]
    p1 = subprocess.run(cmd, capture_output=True, text=True)
    p2 = subprocess.run(cmd, capture_output=True, text=True)        # second call: resumes, nothing to do
    p3 = subprocess.run([sys.executable, os.path.join(H.HERE, "fit_bias.py"), "--results", td], capture_output=True, text=True)
    ok = ("ALL CALIBRATION JOBS FINISHED" in p1.stdout and "ALL CALIBRATION JOBS FINISHED" in p2.stdout
          and "  P1_" not in p2.stdout and p3.returncode == 0 and os.path.exists(os.path.join(td, "settings.csv")))
    check(ok, "calibrate.py runs, resumes without repeating work, fit_bias.py writes settings.csv")
    if not ok:
        print(p1.stdout[-1500:], p1.stderr[-1500:], p2.stdout[-800:], p3.stdout[-800:], p3.stderr[-1500:])

print("ALL PROGRAM TESTS PASSED" if not fails else f"{fails} PROGRAM TEST(S) FAILED")
sys.exit(1 if fails else 0)
