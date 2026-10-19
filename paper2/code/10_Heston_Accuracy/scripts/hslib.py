"""hslib.py - helpers of the secondary-case scripts (protocol v0.5, Section 4b), the counterpart of hqlib.py:
run hs_cpu / hs_native, add the moments of consecutive pair ranges exactly, turn moments into prices and SEs.

Cases:
  B  paper 1's 8-asset Heston basket; "schemes" ft and qe; strikes 90, 100, 110; put payoff + parity at
     K = 90 and 100 (B(0) = 100.35); steps per year from a grid (powers of two).
  A  arithmetic Asian call under Black-Scholes, 250 dates; "schemes" plain and cv (two estimators from the
     same paths; the card and the matched CPU code compute both, the CPU-native code the geometric average
     only for cv); beta fixed per strike from the calibration.
Reference prices are read from results/reference_s.csv (scripts/reference_s.py) once it exists.
"""
import csv
import json
import math
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
BIN = os.path.join(ROOT, "build", "hs_cpu")
BIN_NATIVE = os.path.join(ROOT, "build", "hs_native")
BIN_FPGA = os.path.join(ROOT, "build", "hs_fpga")
NK = 3
QBITS = 16
TARGETS = (0.01, 0.003, 0.001)          # RMSE as a fraction of the at-the-money (K = 100) price
REF_CSV = os.path.join(ROOT, "results", "reference_s.csv")
BETA_CSV = os.path.join(ROOT, "results", "beta_A.csv")

S0B = [100.0 + 0.1 * j for j in range(8)]
CASES = {   # must match common/hb_model.hpp and common/ha_model.hpp
    "B": dict(T=1.0, r=0.01, K=(90.0, 100.0, 110.0), puts=(1, 1, 0), S0=sum(S0B) / 8, schemes=("ft", "qe"),
              ref=None, atm_approx=3.437),
    "A": dict(T=1.0, r=0.05, K=(90.0, 100.0, 110.0), puts=(0, 0, 0), S0=100.0, sigma=0.2, M=250,
              schemes=("plain", "cv"), ref=None, atm_approx=5.782),
}
PRICE_IMPLS = ("scalar", "avx512", "fpga", "double", "native")
INT_IMPLS = ("scalar", "avx512", "fpga")


def load_refs(path=REF_CSV):
    """reference prices per case (None until reference_s.py has accepted them)"""
    if not os.path.exists(path):
        return
    for r in csv.DictReader(open(path)):
        if r.get("accepted", "1") in ("1", "yes", "True"):
            CASES[r["case"]]["ref"] = tuple(float(r[f"ref{k}"]) for k in range(NK))


load_refs()


def ref(case, k):
    r = CASES[case]["ref"]
    return r[k] if r else float("nan")


def atm(case):
    """the at-the-money reference price (K = 100); the pilot value until the reference exists"""
    r = CASES[case]["ref"]
    return r[1] if r else CASES[case]["atm_approx"]


def load_beta(path=BETA_CSV):
    if not os.path.exists(path):
        return None
    r = list(csv.DictReader(open(path)))[-1]
    return tuple(float(r[f"beta{k}"]) for k in range(NK))


def geo_call(K):
    """discounted geometric-average call of Case A, discrete monitoring (Kemna and Vorst 1990)"""
    c = CASES["A"]
    M, T, r, s = c["M"], c["T"], c["r"], c["sigma"]
    h = T / M
    mu = math.log(c["S0"]) + (r - 0.5 * s * s) * h * (M + 1) / 2
    s2 = s * s * h * (M + 1) * (2 * M + 1) / (6 * M)
    d2 = (mu - math.log(K)) / math.sqrt(s2)
    d1 = d2 + math.sqrt(s2)
    nc = lambda x: 0.5 * math.erfc(-x / math.sqrt(2))
    return math.exp(-r * T) * (math.exp(mu + 0.5 * s2) * nc(d1) - K * nc(d2))


def args_for(case, scheme, spy=None, steps=None):
    """command-line arguments of hs_cpu / hs_native / hs_fpga for a case and scheme"""
    if case == "B":
        a = ["--case", "B", "--scheme", scheme]
        a += ["--steps", steps] if steps is not None else ["--steps-per-year", spy]
        return a
    a = ["--case", "A", "--estimator", scheme]
    if steps is not None:
        a += ["--steps", steps]
    b = load_beta()
    if b:
        a += ["--beta", ",".join(f"{x:.9f}" for x in b)]
    return a


def run(args, threads=None, check=True, binary=BIN):
    cmd = [binary] + [str(a) for a in args]
    if threads:
        cmd += ["--threads", str(threads)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON ")]
    if not line:
        raise RuntimeError(f"{os.path.basename(binary)} failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr}")
    r = json.loads(line[-1][len("RESULT_JSON "):])
    if check and int(r.get("ok", 0)) != 1:
        raise RuntimeError(f"{os.path.basename(binary)} returned ok=0: {' '.join(cmd)}\n{p.stdout[-2000:]}")
    return r


SUMS_B = ("s1", "sp")
SUMS_A = ("sy", "syy", "sg", "sgg", "syg")
DKEYS_A = {"sy": "dy", "syy": "dyy", "sg": "dg", "sgg": "dgg", "syg": "dyg"}


class Moments:
    """sums over pairs of one case; exact integers for scalar/avx512/fpga, floats otherwise.
    Case B: s1, sp per strike. Case A: sy, syy, sg, sgg, syg per strike (pair sums ys = yA + yB, gs = gA + gB)."""

    def __init__(self, case, impl):
        self.case, self.impl = case, impl
        self.pairs = 0
        self.ovf = 0
        self.seconds = 0.0
        self.path_steps = 0.0
        names = SUMS_B if case == "B" else SUMS_A
        self.s = {n: [0] * NK for n in names}

    def add(self, r):
        assert r["impl"] == self.impl, (r["impl"], self.impl)
        self.pairs += int(r["pairs"])
        self.ovf += int(r.get("payoff_overflows", 0))
        self.seconds += float(r["t_compute_s"])
        self.path_steps += float(r["path_steps"])
        for k in range(NK):
            if self.case == "B":
                if self.impl in INT_IMPLS:
                    self.s["s1"][k] += int(r[f"s1_{k}"][1:], 16)
                    self.s["sp"][k] += int(r[f"sp_{k}"][1:], 16)
                else:
                    self.s["s1"][k] += float(r[f"d1_{k}"])
                    self.s["sp"][k] += float(r[f"dp_{k}"])
            else:
                for n in SUMS_A:
                    if self.impl in INT_IMPLS:
                        self.s[n][k] += int(r[f"{n}_{k}"][1:], 16)
                    elif self.impl == "native":
                        nk = {"sy": "d1", "syy": "dp", "sg": "dg", "sgg": "dgg", "syg": "dyg"}[n]
                        if f"{nk}_{k}" in r:
                            self.s[n][k] += float(r[f"{nk}_{k}"])
                    else:
                        self.s[n][k] += float(r[f"{DKEYS_A[n]}_{k}"])

    def _scaled(self, n, k):
        v = self.s[n][k]
        if self.impl in INT_IMPLS:
            v = v * 2.0 ** (-QBITS * (2 if n in ("sp", "syy", "sgg", "syg") else 1))
        return v

    def basket(self, k):
        """(call price, SE, pv) for Case B"""
        c = CASES["B"]
        disc = math.exp(-c["r"] * c["T"])
        n = self.pairs
        if n < 2:
            return float("nan"), float("nan"), float("nan")
        mean = self._scaled("s1", k) / (2 * n)
        vp = max(self._scaled("sp", k) / 4 / n - mean * mean, 0.0)
        pv = disc * mean
        est = pv + ((c["S0"] - c["K"][k] * disc) if (c["puts"][k] and self.impl in PRICE_IMPLS) else 0.0)
        return est, disc * math.sqrt(vp / (n - 1)), pv

    def asian(self, k, beta=None):
        """dict(plain, se_plain, cv, se_cv, beta_hat, var_y, var_cv, geo_mc) for Case A; beta=None: own estimate"""
        c = CASES["A"]
        disc = math.exp(-c["r"] * c["T"])
        n = self.pairs
        if n < 2:
            return None
        ym = self._scaled("sy", k) / (2 * n)
        vyy = max(self._scaled("syy", k) / 4 / n - ym * ym, 0.0)
        out = dict(plain=disc * ym, se_plain=disc * math.sqrt(vyy / (n - 1)), var_y=disc * disc * vyy)
        if self.s["sg"][k] == 0 and self.s["sgg"][k] == 0:
            return out
        gm = self._scaled("sg", k) / (2 * n)
        vgg = max(self._scaled("sgg", k) / 4 / n - gm * gm, 0.0)
        vyg = self._scaled("syg", k) / 4 / n - ym * gm
        bh = vyg / vgg if vgg > 0 else 0.0
        b = bh if beta is None else beta
        vcv = max(vyy - 2 * b * vyg + b * b * vgg, 0.0)
        out.update(cv=disc * ym - b * (disc * gm - geo_call(c["K"][k])), se_cv=disc * math.sqrt(vcv / (n - 1)),
                   beta=b, beta_hat=bh, var_cv=disc * disc * vcv, geo_mc=disc * gm, geo_cf=geo_call(c["K"][k]))
        return out

    def estimate(self, scheme, k, beta=None):
        """(price, SE, variance of one pair average) of a case/scheme at strike k"""
        if self.case == "B":
            est, se, _ = self.basket(k)
            return est, se, se * se * (self.pairs - 1)
        a = self.asian(k, beta)
        if a is None:
            return float("nan"), float("nan"), float("nan")
        if scheme == "plain":
            return a["plain"], a["se_plain"], a["var_y"]
        return a["cv"], a["se_cv"], a["var_cv"]
