#!/usr/bin/env python3
"""design_facts7.py --sweep <09_MC_Asian_Sweep dir> --lanes L --out design.json
Design inputs of the round-7 prediction, read from the build outputs of the lane-sweep design (no card):
kernel clock, CUs, II and pipeline depth, Vivado power, WNS. Same parser as round 6 (design_facts.py)."""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from design_facts import facts  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--lanes", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D = dict(sweep=facts(a.sweep, "mc_asian", ["RING", "MERGE", "SETTLE"]))
    D["sweep"]["lanes"] = a.lanes
    ring = D["sweep"]["loops"].get("RING") or next((v for k, v in D["sweep"]["loops"].items() if "RING" in k), None)
    problems = []
    if ring and ring["depth"] >= 128:
        problems.append(f"RING depth {ring['depth']} >= IL=128: results would be wrong, rebuild with AS_IL_LOG2=8")
    if D["sweep"]["ii"] != 1:
        problems.append(f"a key loop has II={D['sweep']['ii']} (the prediction divides R_k by it)")
    if D["sweep"]["wns_ns"] is not None and D["sweep"]["wns_ns"] < 0:
        problems.append(f"routed WNS {D['sweep']['wns_ns']} ns < 0")
    D["problems"] = problems
    json.dump(D, open(a.out, "w"), indent=1)
    print(json.dumps(D, indent=1))
    if any("wrong" in p for p in problems):
        sys.exit(2)


if __name__ == "__main__":
    main()
