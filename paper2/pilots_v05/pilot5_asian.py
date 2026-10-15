#!/usr/bin/env python3
"""Pilot 5 (secondary case A): discretely monitored arithmetic Asian call under Black-Scholes, exact log-normal
steps, antithetic pairs, plain estimator and geometric-Asian control variate (closed form). float64 numpy.
Used only to choose the monitoring, strikes and targets for protocol v0.5.
  python3 pilot5_asian.py [pairs]"""
import numpy as np, sys, time
from scipy.stats import norm
S0, r, sig, T = 100.0, 0.05, 0.2, 1.0
STRIKES = (90.0, 100.0, 110.0)

def geo_price(K, M):
    """discrete geometric average over t_i = i T / M, i = 1..M (Kemna and Vorst 1990, discrete form)"""
    h = T / M
    mu = np.log(S0) + (r - 0.5 * sig**2) * h * (M + 1) / 2
    s2 = sig**2 * h * (M + 1) * (2 * M + 1) / (6 * M)
    d2 = (mu - np.log(K)) / np.sqrt(s2); d1 = d2 + np.sqrt(s2)
    return np.exp(-r * T) * (np.exp(mu + 0.5 * s2) * norm.cdf(d1) - K * norm.cdf(d2))

def run(M, pairs, rng, batch=50_000):
    h = T / M; d = np.exp(-r * T); acc = {K: np.zeros(5) for K in STRIKES}; n = 0
    for _ in range(max(1, pairs // batch)):
        Z = rng.standard_normal((batch, M)); Z = np.stack([Z, -Z])
        X = np.log(S0) + np.cumsum((r - 0.5 * sig**2) * h + sig * np.sqrt(h) * Z, axis=2)
        A = np.exp(X).mean(axis=2); G = np.exp(X.mean(axis=2))
        for K in STRIKES:
            y = (d * np.maximum(A - K, 0)).mean(axis=0); g = (d * np.maximum(G - K, 0)).mean(axis=0)
            acc[K] += (y.sum(), g.sum(), (y * y).sum(), (g * g).sum(), (y * g).sum())
        n += batch
    out = {}
    for K, (sy, sg, syy, sgg, syg) in acc.items():
        my, mg = sy / n, sg / n; vy = syy / n - my**2; vg = sgg / n - mg**2; cyg = syg / n - my * mg
        beta = cyg / vg; vcv = vy - 2 * beta * cyg + beta**2 * vg
        out[K] = dict(plain=my, cv=my - beta * (mg - geo_price(K, M)), sd_plain=np.sqrt(vy), sd_cv=np.sqrt(vcv),
                      beta=beta, vr=vy / vcv, geo_mc=mg, geo_cf=geo_price(K, M))
    return out

if __name__ == "__main__":
    pairs = int(sys.argv[1]) if len(sys.argv) > 1 else 400_000
    rng = np.random.default_rng(20261016)
    for M in (12, 52, 250):
        t0 = time.time(); o = run(M, pairs if M < 250 else pairs // 2, rng)
        for K, x in o.items():
            print(f"M={M:<3} K={K:.0f}: plain {x['plain']:.5f} cv {x['cv']:.5f}  sd pair plain {x['sd_plain']:.3f} "
                  f"cv {x['sd_cv']:.4f}  var ratio {x['vr']:.0f}  beta {x['beta']:.4f}  geo mc {x['geo_mc']:.5f} cf {x['geo_cf']:.5f}")
        print(f"  {time.time()-t0:.0f}s", flush=True)
