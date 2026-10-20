#!/usr/bin/env python3
"""clock_logger.py - measured CPU clocks during a frodo session (protocol v0.6, Section 23 item 4), by the method of
turbostat: the APERF (0xE8), MPERF (0xE7) and TSC (0x10) model-specific registers of every CPU, read every --interval
seconds. Per socket and interval: busy fraction = dMPERF / dTSC, busy frequency = TSC MHz x dAPERF / dMPERF
(weighted by dMPERF over the socket's CPUs), average frequency = dAPERF / dt. Needs root and the msr module.
Usage: sudo modprobe msr; sudo nohup python3 scripts/clock_logger.py --out results/phase6/s1_clocks.csv --hours 14 > /dev/null 2>&1 &"""
import argparse, os, struct, time

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--hours", type=float, default=14.0)
ap.add_argument("--interval", type=float, default=10.0)
ap.add_argument("--stop-file", default="")
a = ap.parse_args()


def cpus_of(node):
    s = open(f"/sys/devices/system/node/node{node}/cpulist").read().strip()
    out = []
    for part in s.split(","):
        x, _, y = part.partition("-")
        out += list(range(int(x), int(y) + 1)) if y else [int(x)]
    return out


SOCK = {n: cpus_of(n) for n in (0, 1) if os.path.exists(f"/sys/devices/system/node/node{n}")}
FD = {c: os.open(f"/dev/cpu/{c}/msr", os.O_RDONLY) for cs in SOCK.values() for c in cs}


def rd(c, reg):
    return struct.unpack("<Q", os.pread(FD[c], 8, reg))[0]


def sample():
    return {c: (rd(c, 0xE8), rd(c, 0xE7), rd(c, 0x10)) for c in FD}, time.time()


os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
new = not os.path.exists(a.out)
f = open(a.out, "a")
if new:
    f.write("t_unix,socket,cpus,busy_frac_mean,busy_mhz,avg_mhz,tsc_mhz\n")
prev, tp = sample()
t_end = time.time() + a.hours * 3600
while time.time() < t_end and not (a.stop_file and os.path.exists(a.stop_file)):
    time.sleep(a.interval)
    cur, tc = sample()
    dt = tc - tp
    for s, cs in SOCK.items():
        dA = [(cur[c][0] - prev[c][0]) % 2 ** 64 for c in cs]
        dM = [(cur[c][1] - prev[c][1]) % 2 ** 64 for c in cs]
        dT = [(cur[c][2] - prev[c][2]) % 2 ** 64 for c in cs]
        tsc = sum(dT) / len(dT) / dt / 1e6
        busy = sum(m / t for m, t in zip(dM, dT) if t) / len(cs)
        bzy = tsc * sum(dA) / sum(dM) if sum(dM) else 0.0
        avg = sum(dA) / len(cs) / dt / 1e6
        f.write(f"{tc:.3f},{s},{len(cs)},{busy:.5f},{bzy:.1f},{avg:.1f},{tsc:.1f}\n")
    f.flush()
    prev, tp = cur, tc
