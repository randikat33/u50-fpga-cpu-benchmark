#!/usr/bin/env python3
"""
heston_validation.py - validate the portfolio Monte Carlo prices against the semi-closed-form
Heston price (Heston 1993; 'little trap' form of Albrecher et al. 2007) for European trades.

Inputs (produced by the v2 CPU code, which is bit-identical to the FPGA kernel):
  pf_gen --trades 1000 --portfolio p1k.bin --market m1k.bin
  pf_cpu --portfolio p1k.bin --market m1k.bin --topk 1000 --paths2 131072 --steps2 S --out r1k_S.bin
Run:  python3 heston_validation.py <dir with p1k.bin m1k.bin r1k_*.bin>
Writes paper/data/rev_heston_validation.csv (per trade) and prints a summary.
"""
import os, sys
import numpy as np
import pandas as pd
from scipy.integrate import quad

U = 1024
TRADE = np.dtype([("uid", "<u4"), ("K", "<f4"), ("T", "<f4"), ("otype", "u1"), ("okind", "u1"), ("pad", "<u2"),
                  ("notional", "<f4"), ("position", "<f4"), ("v0", "<f4"), ("kappa", "<f4"),
                  ("theta", "<f4"), ("sigma", "<f4"), ("rho", "<f4")])


def cf(u, S0, r, q, T, v0, k, th, s, rho):
    """characteristic function of ln S_T (Albrecher et al. 2007 formulation, no branch-cut problem)"""
    iu = 1j * u
    b = k - rho * s * iu
    d = np.sqrt(b * b + s * s * (iu + u * u))
    g = (b - d) / (b + d)
    e = np.exp(-d * T)
    C = (k * th / (s * s)) * ((b - d) * T - 2.0 * np.log((1 - g * e) / (1 - g)))
    D = ((b - d) / (s * s)) * (1 - e) / (1 - g * e)
    return np.exp(iu * (np.log(S0) + (r - q) * T) + C + D * v0)


def heston_call(S0, K, T, r, q, v0, k, th, s, rho):
    lk = np.log(K)
    fwd = S0 * np.exp((r - q) * T)
    f2 = lambda u: (np.exp(-1j * u * lk) * cf(u, S0, r, q, T, v0, k, th, s, rho) / (1j * u)).real
    f1 = lambda u: (np.exp(-1j * u * lk) * cf(u - 1j, S0, r, q, T, v0, k, th, s, rho) / (1j * u * fwd)).real
    P1 = 0.5 + quad(f1, 1e-10, 400, limit=800)[0] / np.pi
    P2 = 0.5 + quad(f2, 1e-10, 400, limit=800)[0] / np.pi
    return S0 * np.exp(-q * T) * P1 - K * np.exp(-r * T) * P2


def bs_call(S, K, T, r, q, vol):
    from scipy.stats import norm
    d1 = (np.log(S / K) + (r - q + 0.5 * vol * vol) * T) / (vol * np.sqrt(T))
    return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d1 - vol * np.sqrt(T))


def main(d):
    # self-check: Heston -> Black-Scholes when the variance is (almost) deterministic
    hc = heston_call(100, 95, 1.0, 0.02, 0.01, 0.04, 5.0, 0.04, 1e-4, -0.5)
    bc = bs_call(100, 95, 1.0, 0.02, 0.01, 0.2)
    assert abs(hc - bc) < 1e-4, (hc, bc)
    raw = open(os.path.join(d, "p1k.bin"), "rb").read()
    n = np.frombuffer(raw[:4], "<u4")[0]
    tr = np.frombuffer(raw[4:4 + 44 * n], TRADE)
    m = np.fromfile(os.path.join(d, "m1k.bin"), "<f4")
    spot, rate, divq = m[:U], m[U:2 * U], m[2 * U:3 * U]
    runs = {}
    for f in sorted(os.listdir(d)):
        if f.startswith("r1k_") and f.endswith(".bin"):
            b = open(os.path.join(d, f), "rb").read()
            runs[f[4:-4]] = np.frombuffer(b[4:], "<f4").reshape(-1, 2)
    rows = []
    for i, t in enumerate(tr):
        if t["okind"] != 0:          # Asian: no closed form
            continue
        S0, r, q = float(spot[t["uid"]]), float(rate[t["uid"]]), float(divq[t["uid"]])
        K, T = float(t["K"]), float(t["T"])
        v0 = max(float(t["v0"]), 1e-4)
        c = heston_call(S0, K, T, r, q, v0, float(t["kappa"]), float(t["theta"]), float(t["sigma"]), float(t["rho"]))
        cfp = c if t["otype"] == 0 else c - S0 * np.exp(-q * T) + K * np.exp(-r * T)
        row = dict(trade=i, put=int(t["otype"] != 0), S0=S0, K=K, T=T, v0=v0, kappa=float(t["kappa"]),
                   theta=float(t["theta"]), sigma=float(t["sigma"]), rho=float(t["rho"]),
                   feller=2 * t["kappa"] * t["theta"] > t["sigma"] ** 2, closed_form=cfp)
        scale = float(t["notional"]) * float(t["position"])
        for k, R in runs.items():
            row[f"mc_{k}"] = R[i, 0] / scale
            row[f"se_{k}"] = R[i, 1] / float(t["notional"])
        rows.append(row)
    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "rev_heston_validation.csv")
    df.to_csv(out, index=False)
    print(f"{len(df)} European trades ({df.feller.mean() * 100:.0f}% satisfy the Feller condition)")
    for k in runs:
        z = (df[f"mc_{k}"] - df.closed_form) / df[f"se_{k}"]
        rel = (df[f"mc_{k}"] - df.closed_form) / df.closed_form.abs().clip(lower=0.5)
        print(f"{k:>6}: mean z {z.mean():+.2f}, median |z| {z.abs().median():.2f}, |z|>3: {(z.abs() > 3).mean() * 100:.1f}%, "
              f"median |error|/price {rel.abs().median() * 100:.2f}%, 95th pct {rel.abs().quantile(.95) * 100:.2f}%, "
              f"median SE/price {(df[f'se_{k}'] / df.closed_form.abs().clip(lower=0.5)).median() * 100:.2f}%")
    return df


if __name__ == "__main__":
    main(sys.argv[1])
