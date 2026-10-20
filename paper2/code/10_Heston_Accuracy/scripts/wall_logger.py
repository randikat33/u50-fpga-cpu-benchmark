#!/usr/bin/env python3
"""wall_logger.py - wall power from the server's BMC (IPMI DCMI) at about 1 Hz, for a whole measurement session
(protocol v0.6, Sections 21 and 23). Needs root (sudo). Stops after --hours or when --stop-file appears.
Usage: sudo nohup python3 scripts/wall_logger.py --out results/phase5g/s1_wall.csv --hours 6 > /dev/null 2>&1 &"""
import argparse, os, re, subprocess, time

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--hours", type=float, default=8.0)
ap.add_argument("--stop-file", default="")
a = ap.parse_args()
os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
new = not os.path.exists(a.out)
f = open(a.out, "a")
if new:
    f.write("t_unix,wall_w\n")
t_end = time.time() + a.hours * 3600
while time.time() < t_end and not (a.stop_file and os.path.exists(a.stop_file)):
    t = time.time()
    try:
        o = subprocess.run(["ipmitool", "dcmi", "power", "reading"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"Instantaneous power reading:\s+(\d+)\s+Watts", o)
        if m:
            f.write(f"{t:.3f},{m.group(1)}\n"); f.flush()
    except Exception:
        pass
    time.sleep(max(0.0, 1.0 - (time.time() - t)))
