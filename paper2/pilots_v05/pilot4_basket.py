#!/usr/bin/env python3
"""Pilot 4 (secondary case B): paper 1's 8-asset basket under Heston, FT and QE, float64 numpy, antithetic pairs.
Market (paper 1, v1_market): S0_i = 100 + 0.1 i (i = 0..7), v0 = theta = 0.04, kappa = 1.5, eps = 0.3, rho = -0.6,
q = 0, assets independent (Cholesky factor = identity), r = 0.01, T = 1. Arithmetic basket B = mean_i S_i(T).
Used only to choose strikes, targets and steps for protocol v0.5.
  python3 pilot4_basket.py [pairs_per_setting]"""
import numpy as np, sys, time, csv
from scipy.special import ndtri
NA, T, r = 8, 1.0, 0.01
S0 = 100.0 + 0.1 * np.arange(NA)
v0 = th = 0.04; k = 1.5; eps = 0.3; rho = -0.6
STRIKES = (90.0, 100.0, 110.0)

def terminal(scheme, n, M, rng):
    """returns S_T for sides A and B, shape (2, n, NA)"""
    h = T / M; sq = np.sqrt(h)
    X = np.zeros((2, n, NA)); V = np.full((2, n, NA), v0)
    if scheme == "QE":
        e = np.exp(-k * h); c1 = eps**2 * e * (1 - e) / k; c2 = th * eps**2 * (1 - e)**2 / (2 * k)
        K0 = -rho * k * th * h / eps; K1 = 0.5 * h * (k * rho / eps - 0.5) - rho / eps
        K2 = 0.5 * h * (k * rho / eps - 0.5) + rho / eps; K3 = K4 = 0.5 * h * (1 - rho**2)
    for _ in range(M):
        if scheme == "QE":
            U = rng.random((n, NA)); U = np.stack([U, 1 - U]); Zv = ndtri(np.clip(U, 1e-300, 1 - 1e-16))
            Z = rng.standard_normal((n, NA)); Z = np.stack([Z, -Z])
            m = th + (V - th) * e; s2 = V * c1 + c2; psi = s2 / (m * m)
            ip = 2.0 / np.maximum(psi, 1e-300); b2 = ip - 1 + np.sqrt(ip) * np.sqrt(np.maximum(ip - 1, 0))
            Vq = m / (1 + b2) * (np.sqrt(b2) + Zv)**2
            p = (psi - 1) / (psi + 1); beta = (1 - p) / m
            Ve = np.where(U <= p, 0.0, np.log((1 - p) / np.maximum(1 - U, 1e-300)) / beta)
            Vn = np.where(psi <= 1.5, Vq, Ve)
            X += r * h + K0 + K1 * V + K2 * Vn + np.sqrt(np.maximum(K3 * V + K4 * Vn, 0)) * Z
            V = Vn
        else:
            Z1 = rng.standard_normal((n, NA)); Z2 = rng.standard_normal((n, NA))
            Z1 = np.stack([Z1, -Z1]); Z2 = np.stack([Z2, -Z2]); Zv = rho * Z1 + np.sqrt(1 - rho**2) * Z2
            Vp = np.maximum(V, 0)
            X += (r - 0.5 * Vp) * h + np.sqrt(Vp) * sq * Z1
            V = V + k * (th - Vp) * h + eps * np.sqrt(Vp) * sq * Zv
    return S0 * np.exp(X)

def run(scheme, M, pairs, rng, batch=100_000):
    d = np.exp(-r * T); acc = {(K, f): [0.0, 0.0] for K in STRIKES for f in ("call", "put")}; nb = 0
    for _ in range(max(1, pairs // batch)):
        B = terminal(scheme, batch, M, rng).mean(axis=2)          # (2, n)
        for K in STRIKES:
            for f in ("call", "put"):
                pay = d * (np.maximum(B - K, 0) if f == "call" else np.maximum(K - B, 0))
                pa = pay.mean(axis=0); a = acc[(K, f)]; a[0] += pa.sum(); a[1] += (pa * pa).sum()
        nb += batch
    out = {}
    for (K, f), (s1, s2) in acc.items():
        mu = s1 / nb; var = s2 / nb - mu * mu
        price = mu if f == "call" else mu + S0.mean() - K * d          # parity: C = P + B0 - K e^{-rT}
        out[(K, f)] = (price, np.sqrt(var / nb), var)
    return out

if __name__ == "__main__":
    pairs = int(sys.argv[1]) if len(sys.argv) > 1 else 400_000
    rng = np.random.default_rng(20261015); rows = []
    for scheme, Ms in (("QE", (64, 1, 2, 4, 8)), ("FT", (1, 2, 4, 8, 16, 32, 64))):
        for M in Ms:
            t0 = time.time(); o = run(scheme, M, pairs, rng)
            for (K, f), (p, se, var) in o.items(): rows.append((scheme, M, K, f, p, se, var))
            print(f"{scheme} M={M:<3} " + "  ".join(f"K{K:.0f} C {o[(K,'call')][0]:.4f}±{o[(K,'call')][1]:.4f} "
                  f"P {o[(K,'put')][0]:.4f} sdC {np.sqrt(o[(K,'call')][2]):.2f} sdP {np.sqrt(o[(K,'put')][2]):.2f}"
                  for K in STRIKES) + f"  {time.time()-t0:.0f}s", flush=True)
    with open("pilot4_basket.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["scheme", "steps_per_year", "K", "form", "price", "se", "var_pair"])
        for x in rows: w.writerow([x[0], x[1], x[2], x[3], f"{x[4]:.6f}", f"{x[5]:.6f}", f"{x[6]:.6f}"])
