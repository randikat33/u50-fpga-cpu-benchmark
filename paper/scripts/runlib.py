"""runlib.py - read one measured run (meta.json + power.csv + stdout.log), as revision_analysis.py does.

Kept separate so that small analyses (core_equivalents.py) can reuse the exact same estimator
without executing revision_analysis.py.
"""
import glob, json, os, re
import numpy as np
import pandas as pd

DOM = ["pkg0_w", "dram0_w", "pkg1_w", "dram1_w", "card_w"]


def res(txt, k):
    m = re.search(rf"^RESULT {k}=(\S+)", txt, re.M)
    if not m:
        return np.nan
    try:
        return float(m.group(1))
    except ValueError:
        return m.group(1)


def integrate(p, t0, t1, col):
    """exact energy (J) in [t0, t1]: row i is the mean power over (t_{i-1}, t_i]"""
    t = p.t_unix.values
    v = p[col].astype(float).values
    e = 0.0
    for i in range(1, len(t)):
        a, b = max(t[i - 1], t0), min(t[i], t1)
        if b > a and np.isfinite(v[i]):
            e += v[i] * (b - a)
    return e


def run_record(d):
    mf, pf, sf = (os.path.join(d, f) for f in ("meta.json", "power.csv", "stdout.log"))
    if not all(os.path.exists(f) and os.path.getsize(f) > 0 for f in (mf, pf, sf)):
        return None
    m = json.load(open(mf))
    t0, t1 = m.get("t_start_marker"), m.get("t_end_marker")
    if not (t0 and t1) or not m.get("ok", True):
        return None
    txt = open(sf, errors="replace").read()
    p = pd.read_csv(pf)
    for c in DOM:
        p[c] = pd.to_numeric(p[c], errors="coerce")
    r = dict(window_s=t1 - t0, samples=int(((p.t_unix >= t0) & (p.t_unix <= t1)).sum()),
             t_compute_s=res(txt, "t_compute_s"), repeat=res(txt, "repeat"), runs=res(txt, "runs"),
             bytes=res(txt, "bytes"), mpix=res(txt, "mpix"), paths=res(txt, "paths"),
             steps=res(txt, "steps"), path_steps=res(txt, "path_steps"), fps=res(txt, "fps"),
             frames=res(txt, "frames"), price=res(txt, "price"), stderr=res(txt, "stderr"),
             decode_ms_mean=res(txt, "decode_ms_mean"), t_launch=m.get("t_launch"), t0=t0, t1=t1,
             cores=m.get("cores"), _dir=d)
    for c in DOM:
        r[f"Eexact_{c[:-2]}"] = integrate(p, t0, t1, c)
    return r


def load(pattern, keys):
    rows = []
    for d in sorted(glob.glob(pattern)):
        parts = d.split(os.sep)
        r = run_record(d)
        if r:
            r.update({k: parts[i] for k, i in keys.items()})
            rows.append(r)
    return pd.DataFrame(rows)
