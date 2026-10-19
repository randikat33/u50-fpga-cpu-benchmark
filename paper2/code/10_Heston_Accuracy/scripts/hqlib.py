"""hqlib.py - shared helpers of the paper-2 scripts: run hq_cpu, parse its RESULT_JSON, add the
moments of consecutive pair ranges exactly, and turn moments into prices, SEs and biases."""
import json
import math
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
BIN = os.path.join(ROOT, "build", "hq_cpu")
BIN_NATIVE = os.path.join(ROOT, "build", "hq_native")
NK = 3
QBITS = 16

CASES = {   # protocol Section 4 (T, r, strikes, reference prices) - must match common/hq_model.hpp
    "I":   dict(T=10, r=0.0,  K=(70, 100, 140), ref=(35.849770, 13.084670, 0.295774), puts=(1, 0, 0)),
    "II":  dict(T=15, r=0.0,  K=(70, 100, 140), ref=(37.169665, 16.649223, 5.138190), puts=(1, 1, 1)),
    "III": dict(T=5,  r=0.0,  K=(70, 100, 140), ref=(38.772044, 21.795288, 9.983068), puts=(1, 1, 1)),
    "P1":  dict(T=1,  r=0.01, K=(70, 100, 140), ref=(31.260968, 8.095646, 0.141170), puts=(1, 0, 0)),
}
S0 = 100.0
PRICE_IMPLS = ("scalar", "avx512", "fpga", "double", "native")
# Since 13 Oct 2026 (protocol v0.4) each strike is priced from its call or put payoff ("puts" below, the same as
# hq::Case::puts): the form with the smaller variance among those with finite variance. A put gives the call by
# parity, C = P + S0 - K exp(-rT). The call payoff has infinite variance in Case II (Heston moment explosion).
TARGETS = (0.01, 0.003, 0.001)          # RMSE as a fraction of the at-the-money (K = 100) price


def atm(case):
    return CASES[case]["ref"][1]


def run(args, threads=None, check=True, binary=BIN):
    """run hq_cpu (or hq_native) with a list of arguments; return the parsed RESULT_JSON dict"""
    cmd = [binary] + [str(a) for a in args]
    if threads:
        cmd += ["--threads", str(threads)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON ")]
    if not line:
        raise RuntimeError(f"hq_cpu failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr}")
    r = json.loads(line[-1][len("RESULT_JSON "):])
    if check and int(r.get("ok", 0)) != 1:
        raise RuntimeError(f"hq_cpu returned ok=0: {' '.join(cmd)}\n{p.stdout[-2000:]}")
    return r


class Moments:
    """sums over pairs; exact integers for impl=scalar, floats for double/diff/coupled"""

    def __init__(self, impl):
        self.impl = impl
        self.pairs = 0
        self.ovf = 0
        self.s1 = [0] * NK
        self.s2 = [0] * NK
        self.sp = [0] * NK
        self.seconds = 0.0
        self.path_steps = 0.0

    def add(self, r):
        assert r["impl"] == self.impl
        self.pairs += int(r["pairs"])
        self.ovf += int(r.get("payoff_overflows", 0))
        self.seconds += float(r["t_compute_s"])
        self.path_steps += float(r["path_steps"])
        for k in range(NK):
            if self.impl in ("scalar", "avx512", "fpga"):
                self.s1[k] += int(r[f"s1_{k}"][1:], 16)
                if f"s2_{k}" in r:                       # phase-2 files also carry a third sum
                    self.s2[k] += int(r[f"s2_{k}"][1:], 16)
                self.sp[k] += int(r[f"sp_{k}"][1:], 16)
            else:
                self.s1[k] += float(r[f"d1_{k}"])
                self.sp[k] += float(r[f"dp_{k}"])

    def estimate(self, case, k):
        """(mean, SE) of the call price (scalar/avx512/fpga/double/native; put strikes by parity) or of the
        mean difference (diff/coupled)"""
        c = CASES[case]
        disc = math.exp(-c["r"] * c["T"])
        n = self.pairs
        if n < 2:
            return float("nan"), float("nan")
        if self.impl in ("scalar", "avx512", "fpga"):
            q = 2.0 ** -QBITS
            mean = self.s1[k] * q / (2 * n)
            vp = self.sp[k] * q * q / 4 / n - mean * mean
        else:
            mean = self.s1[k] / (2 * n)
            vp = self.sp[k] / 4 / n - mean * mean
        vp = max(vp, 0.0)
        est = disc * mean
        if self.impl in PRICE_IMPLS and c["puts"][k]:  # a put strike: call by put-call parity
            est += S0 - c["K"][k] * disc
        return est, disc * math.sqrt(vp / (n - 1))

    def summary(self, case):
        out = {}
        for k in range(NK):
            m, se = self.estimate(case, k)
            out[f"est{k}"] = m
            out[f"se{k}"] = se
            if self.impl in PRICE_IMPLS:
                out[f"bias{k}"] = m - CASES[case]["ref"][k]
        return out
