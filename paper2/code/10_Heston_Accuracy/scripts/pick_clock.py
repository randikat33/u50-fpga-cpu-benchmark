#!/usr/bin/env python3
"""pick_clock.py - the locked SM clock for the RTX 4070 Ti SUPER (addendum v0.9), from the microbenchmarks.
Rule (fixed before any pricing run): use 2340 MHz (the card's base clock, the V100 analogue of locking at its base
clock 1245 MHz) if the fp32, int and sfu microbenchmarks at 2340 MHz were all undisturbed, never power-capped
(throttle bit 0x4), held 2340 MHz within 1%, and drew at most 95% of the 285 W limit (270.75 W). Otherwise use
2100 MHz, which must pass the same checks. Writes results/locked_clock.txt."""
import json, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LIMIT = 0.95 * 285.0


def check(clk):
    notes, ok = [], True
    for b in ("fp32", "int", "sfu"):
        p = os.path.join(ROOT, "results", "gpu_ubench_4060", str(clk), b, "meta.json")
        if not os.path.exists(p):
            return False, [f"{b}: missing {p}"]
        m = json.load(open(p))
        P, sm, thr, dist = m["P_W"]["gpu"], m.get("sm_mhz_median") or 0, int(m.get("throttle_or", 0)), m.get("disturbed")
        good = dist == 0 and not (thr & 0x4) and abs(sm - clk) <= 0.01 * clk and P <= LIMIT
        ok &= good
        notes.append(f"{b}: GPU {P:.1f} W, SM {sm} MHz, throttle 0x{thr:x}, disturbed {dist} -> {'ok' if good else 'NOT ok'}")
    return ok, notes


out = []
for clk in (2340, 2100):
    ok, notes = check(clk)
    out += [f"{clk} MHz:"] + ["  " + n for n in notes]
    if ok:
        out.insert(0, f"LOCKED_CLOCK_MHZ={clk}")
        break
else:
    out.insert(0, "LOCKED_CLOCK_MHZ=NONE (send the output to Claude)")
open(os.path.join(ROOT, "results", "locked_clock.txt"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
sys.exit(0 if not out[0].endswith(")") else 1)
