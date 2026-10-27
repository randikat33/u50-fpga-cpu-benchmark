#!/usr/bin/env python3
"""conc_matrix.py - the concurrency matrix of addendum v0.9 (step 5 of the revision), from the pair measurements.

For a pair of devices (a, b), with every job's power the median over its repeats (the boundary of the servers involved:
frodo = card + both sockets + DRAM; Supermicro = GPU + both sockets + DRAM; a cross-server pair = both servers):
  C(a, b)  = P(a + b) - P(a alone) - P(b alone) + P(idle)                         W
  s(a | b) = rate of a alone / rate of a with b busy                              the stretch of a's time (> 1 = slower)
A pair measured at more than one load gives one C per load; the matrix takes the median over its primary loads (the
classes and thread counts the v0.8 plans used) and reports the others (the half CPU load) as the load check.
Per-domain columns (pkg0, pkg1, dram, card, gpu) attribute C: dC_x = P_x(a + b) - P_x(a) - P_x(b) + P_x(idle).

Inputs (any that exist):  results/conc_frodo_summary.csv   card + fcpu (scripts/conc_frodo.py, 26 Oct 2026)
                          results/conc_cross2_summary.csv  every pair with a Supermicro device, both servers'
                                                           boundary (scripts/conc_smc.py run 2, batch feeding)
                          results/conc_smc2_summary.csv    gpu + scpu at the Supermicro's boundary (attribution)
                          results/conc_smc_summary.csv, conc_cross_summary.csv   run 1 (single feeding), descriptive
Output: results/concurrency/C_matrix.csv, C_matrix.json. A pair with no measurement is absent; the model then takes
C = 0 and s = 1 for it, and C_matrix.json lists it under "not_measured".

  python3 scripts/conc_matrix.py                 the matrix from the measurements
  python3 scripts/conc_matrix.py --provisional   adds the PROVISIONAL values below for the pairs not yet measured and
                                                 writes results/concurrency/C_matrix_provisional.json (a dry run of the
                                                 v0.9 prediction only; never registered)
"""
import argparse, csv, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

RES = S.RES
OUT = os.path.join(RES, "concurrency")
PAIRS = ("card+fcpu", "card+gpu", "card+scpu", "fcpu+gpu", "fcpu+scpu", "gpu+scpu")
# (summary file, pair, job a, job b, job a+b, load label, primary)
SPECS = [
    ("conc_frodo_summary.csv", "card+fcpu", "card", "fcpu_qe", "card+fcpu_qe", "fcpu III QE 0.1%, 48 threads", True),
    ("conc_frodo_summary.csv", "card+fcpu", "card", "fcpu_ft", "card+fcpu_ft", "fcpu II FT 1%, 48 threads", True),
    ("conc_frodo_summary.csv", "card+fcpu", "card", "fcpu_qe_half", "card+fcpu_qe_half", "fcpu III QE 0.1%, 24 threads", False),
    # run 2 (batch feeding, as the scheduler): both servers' boundary for every pair with a Supermicro device
    ("conc_cross2_summary.csv", "gpu+scpu", "gpu", "scpu", "gpu+scpu", "run 2, scpu 20 threads, two servers", True),
    ("conc_cross2_summary.csv", "gpu+scpu", "gpu", "scpu_half", "gpu+scpu_half", "run 2, scpu 10 threads, two servers", False),
    ("conc_smc2_summary.csv", "gpu+scpu", "gpu", "scpu", "gpu+scpu", "run 2, scpu 20 threads, Supermicro only", False),
] + [("conc_cross2_summary.csv", f"{a}+{b}", a, b, f"{a}+{b}", "run 2, one class each", True)
     for a, b in (("card", "gpu"), ("card", "scpu"), ("fcpu", "gpu"), ("fcpu", "scpu"))] + [
    # run 1 (one command per reply): descriptive only, its alone powers include the feeding's own cost
    ("conc_smc_summary.csv", "gpu+scpu", "gpu", "scpu", "gpu+scpu", "run 1 (single feed), Supermicro only", False),
    ("conc_smc_summary.csv", "gpu+scpu", "gpu", "scpu_half", "gpu+scpu_half", "run 1 (single feed), 10 threads, Supermicro only", False),
] + [("conc_cross_summary.csv", f"{a}+{b}", a, b, f"{a}+{b}", "run 1 (single feed)", False)
     for a, b in (("card", "gpu"), ("card", "scpu"), ("fcpu", "gpu"), ("fcpu", "scpu"))]
# Dry-run values for pairs not yet measured. card + fcpu: the conc_frodo summary as printed on frodo (26 Oct 2026),
# replaced by the CSV once it is here. gpu + scpu: the v0.8 runs' estimate (35-40 W, Section 7.11), stretch unknown (1).
# Cross-server pairs: 0 W, no stretch. NOT model inputs for registration.
PROVISIONAL = {
    "card+fcpu": dict(C_W=-19.0, s={"card|fcpu": 0.990, "fcpu|card": 0.9985}, source="conc_frodo summary as printed (26 Oct)"),
    "gpu+scpu": dict(C_W=37.5, s={"gpu|scpu": 1.0, "scpu|gpu": 1.0}, source="v0.8 estimate 35-40 W; stretch not measured"),
    "card+gpu": dict(C_W=0.0, s={}, source="assumed 0"), "card+scpu": dict(C_W=0.0, s={}, source="assumed 0"),
    "fcpu+gpu": dict(C_W=0.0, s={}, source="assumed 0"), "fcpu+scpu": dict(C_W=0.0, s={}, source="assumed 0"),
}


def load(f):
    p = os.path.join(RES, f)
    if not os.path.exists(p):
        return None
    rows = list(csv.DictReader(open(p)))
    for r in rows:
        for k, v in list(r.items()):
            try:
                r[k] = float(v) if v not in ("", None) else None
            except ValueError:
                pass
    return rows


def med(rows, job, key):
    v = [r[key] for r in rows if r["job"] == job and r.get(key) is not None]
    return statistics.median(v) if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provisional", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    lines, per_pair = [], {}
    for f, pair, ja, jb, jab, label, primary in SPECS:
        rows = load(f)
        if not rows or not any(r["job"] == jab for r in rows):
            continue
        da, db = pair.split("+")
        Pi = med(rows, "idle", "P_total_W")
        C = med(rows, jab, "P_total_W") - med(rows, ja, "P_total_W") - med(rows, jb, "P_total_W") + Pi
        doms = sorted(k for k in rows[0] if k.startswith("P_") and k != "P_total_W")
        dC = {k[2:-2]: med(rows, jab, k) - med(rows, ja, k) - med(rows, jb, k) + med(rows, "idle", k) for k in doms
              if None not in (med(rows, jab, k), med(rows, ja, k), med(rows, jb, k), med(rows, "idle", k))}
        sa = med(rows, ja, f"{da}_rate_per_s") / med(rows, jab, f"{da}_rate_per_s")
        sb = med(rows, jb, f"{db}_rate_per_s") / med(rows, jab, f"{db}_rate_per_s")
        n = min(sum(r["job"] == j for r in rows) for j in (ja, jb, jab, "idle"))
        x = dict(pair=pair, load=label, primary=primary, repeats=n, P_idle_W=Pi, P_a_W=med(rows, ja, "P_total_W"),
                 P_b_W=med(rows, jb, "P_total_W"), P_ab_W=med(rows, jab, "P_total_W"), C_W=C, s_a=sa, s_b=sb, source=f,
                 **{f"dC_{k}_W": v for k, v in dC.items()})
        lines.append(x)
        per_pair.setdefault(pair, []).append(x)
    Cm, sm, src, notm = {}, {}, {}, []
    for pair in PAIRS:
        xs = [x for x in per_pair.get(pair, []) if x["primary"]]
        da, db = pair.split("+")
        if xs:
            Cm[pair] = statistics.median(x["C_W"] for x in xs)
            sm[f"{da}|{db}"] = statistics.median(x["s_a"] for x in xs)
            sm[f"{db}|{da}"] = statistics.median(x["s_b"] for x in xs)
            src[pair] = "measured: " + ", ".join(sorted({x["source"] for x in xs}))
        elif a.provisional:
            p = PROVISIONAL[pair]
            Cm[pair] = p["C_W"]
            sm.update(p["s"])
            src[pair] = "PROVISIONAL: " + p["source"]
        else:
            notm.append(pair)
    doc = dict(C_W=Cm, s=sm, source=src, not_measured=notm, per_load=lines, provisional=a.provisional,
               definition="C = P(a+b) - P(a) - P(b) + P(idle); s(a|b) = rate of a alone / rate of a with b busy")
    name = "C_matrix_provisional.json" if a.provisional else "C_matrix.json"
    json.dump(doc, open(os.path.join(OUT, name), "w"), indent=1, sort_keys=True)
    if not a.provisional and lines:
        keys = sorted({k for x in lines for k in x}, key=lambda k: (k not in ("pair", "load", "primary", "repeats", "C_W", "s_a", "s_b"), k))
        with open(os.path.join(OUT, "C_matrix.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(lines)
    print(f"{'pair':<10} {'C (W)':>7}  stretch                       source")
    for pair in PAIRS:
        if pair in Cm:
            da, db = pair.split("+")
            print(f"{pair:<10} {Cm[pair]:+7.1f}  {da}|{db} {sm.get(f'{da}|{db}', 1):.3f}, {db}|{da} {sm.get(f'{db}|{da}', 1):.3f}   {src[pair]}")
        else:
            print(f"{pair:<10} {'-':>7}  not measured")
    for x in lines:
        print(f"  {x['pair']:<10} {x['load']:<32} C {x['C_W']:+6.1f} W  s {x['s_a']:.3f} / {x['s_b']:.3f}  ({x['repeats']} repeats)  " +
              "  ".join(f"{k[3:-2]} {v:+.1f}" for k, v in x.items() if k.startswith("dC_")))
    print("written", os.path.join(OUT, name))


if __name__ == "__main__":
    main()
