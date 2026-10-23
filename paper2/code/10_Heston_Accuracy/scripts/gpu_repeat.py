#!/usr/bin/env python3
"""gpu_repeat.py - a steady-state repeat of the GPU measurements on another day (covers deviation 1 of addendum
v0.6.1: both GPU sessions ran on 20 October 2026). Reported as a reproducibility check, not as a third session.

The steady-state jobs ("ss", 29 keys) of scripts/phase5_gpu.py in the primary host wait mode (block), 5 repeats,
seeds 20261700 + rep (session number 3 in phase5_gpu's seed rule), same program (sha256 checked), wrapper, clock
lock and disturbance rules. Output: results/phase5g/s3/..., results/phase5g_summary_s3.csv. About 1 hour.

Usage (from the project folder; GPU clock locked to 1245 MHz, RAPL readable):
    python3 scripts/gpu_repeat.py --plan
    nohup python3 scripts/gpu_repeat.py > results/gpu_repeat.log 2>&1 &
"""
import hashlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phase5_gpu as G

BIN_SHA = "fbddf8db898f232d7889f96ad7ed86ecc03b0b562c642bfb2454c8bd946f4bf9"
_jobs = G.jobs
G.jobs = lambda session: [j for j in _jobs(session) if j["kind"] == "ss" and j["mode"] == "block"]

if __name__ == "__main__":
    sha = hashlib.sha256(open(G.BIN, "rb").read()).hexdigest() if os.path.exists(G.BIN) else ""
    if sha != BIN_SHA:
        sys.exit("STOP: build/hs_gpu is not the registered binary (sha256 differs)")
    if "--plan" in sys.argv:
        print("binary sha256 ok")
    sys.argv = [sys.argv[0], "--session", "3"] + [a for a in sys.argv[1:] if a in ("--plan", "--summary")]
    G.main()
