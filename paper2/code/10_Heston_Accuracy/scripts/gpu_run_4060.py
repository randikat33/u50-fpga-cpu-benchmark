#!/usr/bin/env python3
"""gpu_run_4060.py - gpu_run.py adapted to the RTX 4060 desktop (one CPU socket). Changes from gpu_run.py:
  - RAPL domains are found by name (package-N, dram), not by index; a missing domain reads as 0.
    On a one-socket desktop: pkg0 = the CPU package, dram0 = 0 unless a "dram" domain exists, pkg1 = dram1 = 0.
  - No numactl pinning by default (one socket); --pin "taskset CPU list" is available.
  - If NVML's total-energy counter is not supported, the GPU energy is the integral of the sampled power.
  - The primary boundary is GPU + CPU package (+ DRAM if present), reported as E_gpu_socket_J (and E_gpu_server_J).
Everything else (sampling, MARK handling, disturbance rules) is as in gpu_run.py.

Original description:
gpu_run.py - run one GPU program with power logging on the Supermicro (protocol v0.6, Sections 21-22).

Samples at about 20 Hz:
  RAPL package and DRAM energy of both sockets (/sys/class/powercap, raw counters, wrap-safe);
  NVML: GPU total energy counter (mJ), power, temperature, SM clock and throttle reasons.
The program runs pinned to socket 1, which the V100 is attached to (numactl --cpunodebind=1 --membind=1).
Energies are taken between the program's MARK start / MARK end lines: GPU energy from the NVML energy counter,
RAPL from its counters, both interpolated at the mark times.

Writes <out>/stdout.log, power.csv, meta.json and DONE.

A run is "disturbed" (disturbed=1 in meta.json; the queue repeats it) if, during the window:
  - a throttle reason other than GPU idle / applications-clocks / display was set (power cap, HW slowdown,
    thermal, power brake), or
  - the GPU temperature exceeded 83 C, or
  - other users' processes used more than 1 core on average or more than 4 in any second.

Usage: python3 scripts/gpu_run.py --out DIR [--no-pin] -- build/hs_gpu --case I ... --timed --window-s 10
Requires: persistence mode on, RAPL readable (sudo chmod a+r .../energy_uj), nvidia-ml-py.
"""
import argparse, csv, json, os, re, statistics, subprocess, sys, threading, time

import pynvml as nv

RAPL = os.environ.get("GPU_RUN_RAPL_DIR", "/sys/class/powercap")   # override for tests only
import glob


def find_domains():
    """map pkg0/dram0/pkg1/dram1 to powercap directories by their name files; missing ones map to None"""
    found = {}
    for d in sorted(glob.glob(os.path.join(RAPL, "intel-rapl:*"))):
        try:
            name = open(os.path.join(d, "name")).read().strip()
        except OSError:
            continue
        top = os.path.basename(d).split(":")[1]
        if name.startswith("package-"):
            found["pkg" + name.split("-")[1]] = os.path.basename(d)
        elif name == "dram":
            found["dram" + top] = os.path.relpath(d, RAPL)
    return [(k, found.get(k)) for k in ("pkg0", "dram0", "pkg1", "dram1")]


DOMAINS = find_domains()
BAD_THROTTLE = 0x4 | 0x8 | 0x20 | 0x40 | 0x80     # SW power cap, HW slowdown, SW/HW thermal, HW power brake


def rapl_read():
    out = {}
    for name, d in DOMAINS:
        if d is None:
            out[name] = 0
            continue
        with open(os.path.join(RAPL, d, "energy_uj")) as f:
            out[name] = int(f.read())
    return out


def rapl_max():
    out = {}
    for name, d in DOMAINS:
        if d is None:
            out[name] = 1 << 62
            continue
        with open(os.path.join(RAPL, d, "max_energy_range_uj")) as f:
            out[name] = int(f.read())
    return out


def busy_cpus_other(own_pids):
    """CPU seconds used by processes that are not ours (own pids and all their descendants), from /proc."""
    stat = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/stat") as f:
                v = f.read().rsplit(")", 1)[1].split()
            stat[int(p)] = (int(v[1]), int(v[11]) + int(v[12]))
        except Exception:
            pass
    mine, changed = set(own_pids), True
    while changed:                                       # descendants (co-execution workers are grandchildren)
        changed = False
        for pid, (ppid, _) in stat.items():
            if ppid in mine and pid not in mine:
                mine.add(pid); changed = True
    return sum(t for pid, (_, t) in stat.items() if pid not in mine) / os.sysconf("SC_CLK_TCK")


class Sampler(threading.Thread):
    def __init__(self, h, period=0.05):
        super().__init__(daemon=True)
        self.h, self.period, self.rows, self.stop = h, period, [], threading.Event()
        self.own = set()
        self.energy_counter = True

    def run(self):
        last_other = 0.0
        while not self.stop.is_set():
            t = time.time()
            r = rapl_read()
            row = dict(t_unix=t, **r)
            try:
                if self.energy_counter:
                    try:
                        row["gpu_mj"] = nv.nvmlDeviceGetTotalEnergyConsumption(self.h)
                    except nv.NVMLError:
                        self.energy_counter = False
                row["gpu_mw"] = nv.nvmlDeviceGetPowerUsage(self.h)
                row["gpu_temp_c"] = nv.nvmlDeviceGetTemperature(self.h, nv.NVML_TEMPERATURE_GPU)
                row["sm_mhz"] = nv.nvmlDeviceGetClockInfo(self.h, nv.NVML_CLOCK_SM)
                row["throttle"] = nv.nvmlDeviceGetCurrentClocksThrottleReasons(self.h)
            except nv.NVMLError as e:
                row["nvml_error"] = str(e)
            if t - last_other >= 1.0:                     # other users' CPU time, once per second
                row["other_cpu_s"] = busy_cpus_other(self.own)
                last_other = t
            self.rows.append(row)
            time.sleep(max(0.0, self.period - (time.time() - t)))


def interp(rows, key, t, wrap=None):
    """counter value at time t (linear between samples), unwrapped"""
    xs, ys, off, prev = [], [], 0, None
    for r in rows:
        if key not in r:
            continue
        v = r[key]
        if prev is not None and v < prev and wrap:
            off += wrap
        prev = v
        xs.append(r["t_unix"]); ys.append(v + off)
    for i in range(1, len(xs)):
        if xs[i] >= t:
            a = (t - xs[i - 1]) / (xs[i] - xs[i - 1]) if xs[i] > xs[i - 1] else 0.0
            return ys[i - 1] + a * (ys[i] - ys[i - 1])
    return ys[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-pin", action="store_true", help="kept for compatibility; no pinning is the default here")
    ap.add_argument("--pin", default=None, help="optional CPU list for taskset, e.g. 0-7")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    os.makedirs(a.out, exist_ok=True)
    nv.nvmlInit()
    h = nv.nvmlDeviceGetHandleByIndex(0)
    if nv.nvmlDeviceGetPersistenceMode(h) != 1:
        sys.exit("persistence mode is off: run  sudo nvidia-smi -pm 1")
    try:
        rapl_read()
    except PermissionError:
        sys.exit("RAPL not readable: run  sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj /sys/class/powercap/intel-rapl:*:*/energy_uj")
    if dict(DOMAINS)["pkg0"] is None:
        sys.exit("no RAPL package domain found under /sys/class/powercap (send me: ls /sys/class/powercap; cat .../name)")
    wraps = rapl_max()
    s = Sampler(h)
    s.own.add(os.getpid())
    s.start()
    time.sleep(0.5)
    full = ["taskset", "-c", a.pin] + cmd if a.pin else cmd
    t_start = time.time()
    p = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    s.own.add(p.pid)
    lines, recv = [], {}
    for line in p.stdout:                            # read live: the receipt time of each MARK line is kept
        lines.append(line)
        mm = re.match(r"^MARK (\w+) ", line)
        if mm:
            recv[mm.group(1)] = time.time()
    p.wait()
    out = "".join(lines)
    t_end = time.time()
    time.sleep(0.3)
    s.stop.set(); s.join()
    open(os.path.join(a.out, "stdout.log"), "w").write(out)
    keys = ["t_unix", "pkg0", "dram0", "pkg1", "dram1", "gpu_mj", "gpu_mw", "gpu_temp_c", "sm_mhz", "throttle", "other_cpu_s"]
    with open(os.path.join(a.out, "power.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(s.rows)
    marks = dict((m.group(1), float(m.group(2))) for m in re.finditer(r"^MARK (\w+) ([0-9.]+)", out, re.M))
    # A program whose MARK time is not wall-clock time (the CPU programs print steady_clock, seconds since boot) is
    # timed by the receipt of its MARK lines; the GPU programs print wall-clock time, which is used as before.
    mark_clock = "printed"
    if marks and recv and any(abs(marks[k] - recv[k]) > 2.0 for k in marks if k in recv):
        marks, mark_clock = dict(recv), "received"
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", out, re.M))
    meta = dict(rapl_domains=dict(DOMAINS), cmd=full, returncode=p.returncode, t_start=t_start, t_end=t_end, samples=len(s.rows), mark_clock=mark_clock,
                gpu=nv.nvmlDeviceGetName(h) if isinstance(nv.nvmlDeviceGetName(h), str) else nv.nvmlDeviceGetName(h).decode(),
                driver=nv.nvmlSystemGetDriverVersion() if isinstance(nv.nvmlSystemGetDriverVersion(), str) else nv.nvmlSystemGetDriverVersion().decode())
    if "start" in marks and "end" in marks:
        t0, t1 = marks["start"], marks["end"]
        win = t1 - t0
        E = {d: (interp(s.rows, d, t1, wraps[d]) - interp(s.rows, d, t0, wraps[d])) * 1e-6 for d, _ in DOMAINS}
        if s.energy_counter and all("gpu_mj" in r for r in s.rows):
            E["gpu"] = (interp(s.rows, "gpu_mj", t1) - interp(s.rows, "gpu_mj", t0)) * 1e-3
            meta["gpu_energy_source"] = "nvml_total_energy"
        else:                                         # trapezoid of the sampled power over [t0, t1]
            pts = [(r["t_unix"], r["gpu_mw"] * 1e-3) for r in s.rows if "gpu_mw" in r]
            E["gpu"] = sum((min(b[0], t1) - max(a_[0], t0)) * (a_[1] + b[1]) / 2 for a_, b in zip(pts, pts[1:])
                           if b[0] > t0 and a_[0] < t1)
            meta["gpu_energy_source"] = "integrated_power"
        inw = [r for r in s.rows if t0 <= r["t_unix"] <= t1]
        thr = 0
        for r in inw:
            thr |= int(r.get("throttle", 0))
        other = (interp(s.rows, "other_cpu_s", t1) - interp(s.rows, "other_cpu_s", t0)) / win if win > 0 else 0
        oth = [r for r in s.rows if "other_cpu_s" in r and t0 - 1.0 <= r["t_unix"] <= t1 + 1.0]
        per_s = [(oth[i]["other_cpu_s"] - oth[i - 1]["other_cpu_s"]) / (oth[i]["t_unix"] - oth[i - 1]["t_unix"])
                 for i in range(1, len(oth)) if oth[i]["t_unix"] > oth[i - 1]["t_unix"]]
        temp_max = max((r.get("gpu_temp_c", 0) for r in inw), default=0)
        meta.update(window_s=win, E_J=E, P_W={k: v / win for k, v in E.items()},
                    E_gpu_socket_J=E["gpu"] + E["pkg0"] + E["dram0"], E_gpu_server_J=E["gpu"] + sum(E[d] for d, _ in DOMAINS),
                    sm_mhz_median=statistics.median([r["sm_mhz"] for r in inw if "sm_mhz" in r]) if inw else None,
                    temp_max_c=temp_max, throttle_or=thr, other_cores_avg=other, other_cores_max=max(per_s, default=0.0),
                    runs=int(res.get("runs", 0)) if res.get("runs") else None)
        meta["disturbed"] = int(bool(thr & BAD_THROTTLE) or temp_max > 83 or other > 1.0 or meta["other_cores_max"] > 4.0)
    else:
        meta["disturbed"] = None
    json.dump(meta, open(os.path.join(a.out, "meta.json"), "w"), indent=1)
    if p.returncode == 0:
        open(os.path.join(a.out, "DONE"), "w").write("ok\n")
    if "window_s" in meta:
        print(f"window {meta['window_s']:.2f} s, runs {meta.get('runs')}, GPU {meta['P_W']['gpu']:.1f} W ({meta['gpu_energy_source']}), CPU package "
              f"{meta['P_W']['pkg0'] + meta['P_W']['dram0']:.1f} W, SM {meta['sm_mhz_median']} MHz, Tmax {temp_max} C, "
              f"throttle 0x{thr:x}, other cores {other:.2f}, disturbed {meta['disturbed']}")
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
