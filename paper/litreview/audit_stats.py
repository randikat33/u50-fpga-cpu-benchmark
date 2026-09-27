#!/usr/bin/env python3
"""Summarise the methodology audit of FPGA-vs-CPU papers (coding.csv).

Writes audit_summary.csv (one row per audit item) and audit_summary.json,
and prints the numbers quoted in Section 2 of the paper.
Codes: Y = stated/done, partial = partly, N = not done, NS = not stated.
"""
import csv, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "coding.csv"))))
N = len(rows)


def energy_kind(x):
    v = x["c4_energy"]
    if v.startswith("not"):
        return "none"
    if "estimated" in v or "TDP" in v:
        return "mixed"
    return "measured"


def yes(k):
    return lambda x: x[k] == "Y"


energy = [x for x in rows if energy_kind(x) != "none"]
IDLE = {"NE21", "Q19"}          # idle-subtracted or dynamic power reported (from quotes)
HOST_IN_FPGA = {"K22", "BM21", "FA20"}  # host power counted during FPGA runs (from quotes)

items = [
    ("C1", "CPU baseline multithreaded", sum(yes("c1_mt")(x) for x in rows), N),
    ("C1", "CPU SIMD / tuned library stated", sum(yes("c1_simd")(x) for x in rows), N),
    ("C1", "both multithreaded and SIMD stated", sum(x["c1_mt"] == "Y" and x["c1_simd"] == "Y" for x in rows), N),
    ("C2", "CPU cores/threads stated", sum(yes("c2_cores")(x) for x in rows), N),
    ("C3", "transfer treatment stated", sum(not x["c3_transfers"].startswith("NS") for x in rows), N),
    ("C3", "transfers excluded from FPGA time", sum(x["c3_transfers"].startswith("excluded") for x in rows), N),
    ("C4", "power/energy reported", len(energy), N),
    ("C4", "measured on both sides", sum(energy_kind(x) == "measured" for x in rows), N),
    ("C4b", "measurement boundary fully stated", sum(x["c4b_boundary"] == "Y" for x in energy), len(energy)),
    ("C4b", "idle vs dynamic separated", sum(x["id"] in IDLE for x in energy), len(energy)),
    ("C4b", "host counted during FPGA runs", sum(x["id"] in HOST_IN_FPGA for x in energy), len(energy)),
    ("C5", "CPU frequency / turbo / warm-up controlled", sum(yes("c5_freq_warmup")(x) for x in rows), N),
    ("C6", "run-to-run variability reported", sum(yes("c6_variability")(x) for x in rows), N),
    ("C6", "variability reported (energy papers)", sum(yes("c6_variability")(x) for x in energy), len(energy)),
    ("C7", "output equivalence checked", sum(yes("c7_equivalence")(x) for x in rows), N),
    ("C8", "code/artefact available", sum(yes("c8_artifact")(x) for x in rows), N),
]


def score(x):
    return sum([
        x["c1_mt"] == "Y" and x["c1_simd"] == "Y",
        x["c2_cores"] == "Y",
        not x["c3_transfers"].startswith("NS"),
        energy_kind(x) == "measured",
        x["c4b_boundary"] == "Y",
        x["c5_freq_warmup"] == "Y",
        x["c6_variability"] == "Y",
        x["c7_equivalence"] == "Y",
        x["c8_artifact"] == "Y",
    ])


scores = sorted(score(x) for x in rows)
median = (scores[(N - 1) // 2] + scores[N // 2]) / 2
years = sorted(int(x["year"]) for x in rows)

with open(os.path.join(HERE, "audit_summary.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["item", "description", "count", "denominator", "percent"])
    for it, d, c, n in items:
        w.writerow([it, d, c, n, round(100 * c / n)])

out = {
    "papers": N, "years": [years[0], years[-1]],
    "items": [{"item": it, "description": d, "count": c, "of": n, "pct": round(100 * c / n)} for it, d, c, n in items],
    "score_max": 9, "score_median": median, "score_max_seen": max(scores),
    "papers_with_all_9": sum(s == 9 for s in scores),
    "scores": {x["id"]: score(x) for x in rows},
}
json.dump(out, open(os.path.join(HERE, "audit_summary.json"), "w"), indent=1)

for it, d, c, n in items:
    print(f"{it:4s} {d:45s} {c:2d}/{n:2d} = {100*c/n:4.0f}%")
print("years", years[0], "-", years[-1], "| median score", median, "of 9 | max", max(scores),
      "| all nine:", out["papers_with_all_9"])
