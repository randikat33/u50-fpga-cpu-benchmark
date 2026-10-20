#!/usr/bin/env python3
"""card_wait_diag.py - check of the card hosts' two wait modes before the frodo sessions of protocol v0.6
(Section 23 item 2). Diagnostic only: its numbers are not outcomes; they fix the settings that the addendum
(v0.6.1) registers before any HF1 measurement.

1. Correctness: the rebuilt hosts (build/hq_fpga, build/hs_fpga) in both wait modes (--wait xrt, --wait callback)
   give the same mom_hash as the matched CPU programs (build/hq_cpu, build/hs_cpu --impl avx512) for the same
   case, scheme, steps, pairs and seed (Case I FT, P1 QE, B FT, A CV).
2. Where the host's time goes while the card computes. For an idle window and for each wait mode, with a long-run
   kernel (Case I FT, 1280 steps, about 0.8 s per pricing run) and a short-run kernel (Case P1 QE, 1 step,
   about 1 ms per pricing run), over a 20 s window (host pinned to socket 0 as in phase 5, the same xrt.ini):
     - CPU time of the host process (all its threads), and of every thread on the machine, kernel threads
       included (the xocl driver's CU threads), from /proc;
     - busy cores of socket 0 and socket 1, from /proc/stat;
     - RAPL power of both sockets (package + DRAM).
   It also records the XRT version and the driver's KDS settings (sysfs, read only).
Output: results/card_wait_diag/diag.json and diag.txt. About 3 minutes; the card must be free.

Usage (from the project folder, after `make host shost`; RAPL readable):
    python3 scripts/card_wait_diag.py
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile, threading, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "card_wait_diag")
B = os.path.join(ROOT, "build")
COMMON = os.path.abspath(os.path.join(ROOT, "..", "..", "common"))
XCL = {"ft": "ft/hq_ft.xclbin", "qe": "qe/hq_qe.xclbin", "bft": "bft/hb_ft.xclbin", "asian": "asian/ha_asian.xclbin"}
HZ = os.sysconf("SC_CLK_TCK")
WINDOW_S, WARM_S = 20.0, 3.0
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def node_cpus(n):
    s = open(f"/sys/devices/system/node/node{n}/cpulist").read().strip()
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def rapl():
    out = {}
    for d in glob.glob("/sys/class/powercap/intel-rapl:[0-9]") + glob.glob("/sys/class/powercap/intel-rapl:[0-9]:[0-9]"):
        try:
            name = open(os.path.join(d, "name")).read().strip()
            sock = d.split("intel-rapl:")[1].split(":")[0]
            key = ("pkg" if name.startswith("package") else name) + sock
            out[key] = (int(open(os.path.join(d, "energy_uj")).read()), int(open(os.path.join(d, "max_energy_range_uj")).read()))
        except (OSError, ValueError):
            pass
    return out


def threads():
    t = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        for tid in os.listdir(f"/proc/{p}/task") if os.path.isdir(f"/proc/{p}/task") else []:
            try:
                s = open(f"/proc/{p}/task/{tid}/stat").read()
                comm = s[s.index("(") + 1:s.rindex(")")]
                f = s[s.rindex(")") + 2:].split()
                t[(int(p), int(tid))] = (comm, (int(f[11]) + int(f[12])) / HZ)
            except (OSError, ValueError, IndexError):
                pass
    return t


def cpustat():
    out = {}
    for line in open("/proc/stat"):
        if line.startswith("cpu") and line[3].isdigit():
            f = line.split()
            v = [int(x) for x in f[1:]]
            out[int(f[0][3:])] = (sum(v) - v[3] - v[4], sum(v))
    return out


def snap():
    return dict(t=time.time(), rapl=rapl(), thr=threads(), cpu=cpustat())


def diff(a, b, host_pid, s0, s1):
    dt = b["t"] - a["t"]
    pw = {}
    for k in b["rapl"]:
        if k in a["rapl"]:
            e = b["rapl"][k][0] - a["rapl"][k][0]
            if e < 0:
                e += b["rapl"][k][1]
            pw[k] = e * 1e-6 / dt
    thr = []
    for k, (comm, c) in b["thr"].items():
        c0 = a["thr"].get(k, (comm, 0.0))[1]
        if c - c0 > 0.01 * dt:
            thr.append(dict(pid=k[0], tid=k[1], comm=comm, cores=(c - c0) / dt, host=k[0] == host_pid))
    thr.sort(key=lambda x: -x["cores"])
    busy = lambda cpus: sum((b["cpu"][c][0] - a["cpu"][c][0]) / max(1, b["cpu"][c][1] - a["cpu"][c][1]) for c in cpus if c in a["cpu"])
    host = sum(x["cores"] for x in thr if x["host"])
    return dict(seconds=dt, power_W=pw, socket0_W=pw.get("pkg0", 0) + pw.get("dram0", 0), socket1_W=pw.get("pkg1", 0) + pw.get("dram1", 0),
                busy_cores_socket0=busy(s0), busy_cores_socket1=busy(s1), host_process_cores=host, top_threads=thr[:12])


def run_host(cmd, cwd, env, s0, s1):
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    a = b = None
    lines = []
    for line in p.stdout:
        lines.append(line)
        if line.startswith("MARK start"):
            a = snap()
        elif line.startswith("MARK end"):
            b = snap()
    p.wait()
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", "".join(lines), re.M))
    if p.returncode != 0 or not a or not b:
        raise RuntimeError(f"host failed (rc {p.returncode}): {' '.join(cmd)}\n{''.join(lines)[-1500:]}")
    d = diff(a, b, p.pid, s0, s1)
    d.update(runs=int(res["runs"]), t_per_run_s=float(res["t_per_run_s"]), host_cpu_frac=float(res.get("host_cpu_frac", "nan")),
             mom_hash=res.get("mom_hash"), wait=res.get("wait"))
    return d


def hashes():
    """mom_hash of the card (both modes) against the matched CPU program"""
    import hslib as HS
    jobs = [("hq", "I", "ft", 1280, 65536, ["--case", "I", "--scheme", "ft", "--steps", "1280"]),
            ("hq", "P1", "qe", 1, 1 << 20, ["--case", "P1", "--scheme", "qe", "--steps", "1"]),
            ("hs", "B", "bft", 16, 65536, [str(x) for x in HS.args_for("B", "ft", steps=16)]),
            ("hs", "A", "asian", 250, 65536, [str(x) for x in HS.args_for("A", "cv", steps=250)])]
    out = []
    for prog, case, x, M, n, args in jobs:
        common = args + ["--a0", "0", "--a1", str(n), "--seed", "4242"]
        cpu = subprocess.run([os.path.join(B, f"{prog}_cpu"), "--impl", "avx512", "--threads", "8"] + common, capture_output=True, text=True).stdout
        hc = re.search(r"^RESULT mom_hash=(\S+)", cpu, re.M)
        row = dict(case=case, xclbin=x, cpu=hc.group(1) if hc else None)
        for mode in ("xrt", "callback"):
            o = subprocess.run([os.path.join(B, f"{prog}_fpga"), "--xclbin", os.path.join(B, XCL[x]), "--wait", mode] + common,
                               capture_output=True, text=True)
            h = re.search(r"^RESULT mom_hash=(\S+)", o.stdout, re.M)
            row[mode] = h.group(1) if h else f"FAILED rc {o.returncode}: {o.stderr.strip()[-200:]}"
        row["equal"] = row["cpu"] is not None and row["cpu"] == row["xrt"] == row["callback"]
        out.append(row)
        print(f"  {case} {x}: cpu {row['cpu']}  xrt {row['xrt']}  callback {row['callback']}  {'EQUAL' if row['equal'] else 'DIFFERENT'}", flush=True)
    return out


def info():
    d = {}
    for f in (os.path.join(os.environ.get("XILINX_XRT", "/opt/xilinx/xrt"), "version.json"),):
        if os.path.exists(f):
            try:
                d["xrt_version"] = json.load(open(f))
            except ValueError:
                d["xrt_version"] = open(f).read()[:400]
    for dev in glob.glob("/sys/bus/pci/drivers/xocl/0000:*"):
        e = {}
        for a in ("kds_stat", "kds_interrupt", "kds_interval", "ert_disable", "kds_echo"):
            try:
                e[a] = open(os.path.join(dev, a)).read().strip()[:1500]
            except OSError:
                pass
        if e:
            d[os.path.basename(dev)] = e
    return d


def main():
    for f in ("hq_fpga", "hs_fpga", "hq_cpu", "hs_cpu"):
        if not os.path.exists(os.path.join(B, f)):
            sys.exit(f"build/{f} not found (make cpu scpu host shost)")
    if len(rapl()) < 4:
        sys.exit("RAPL not readable: sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj /sys/class/powercap/intel-rapl:*:*/energy_uj")
    os.makedirs(OUT, exist_ok=True)
    s0, s1 = node_cpus(0), node_cpus(1)
    res = dict(info=info())
    print("1. card == matched CPU (mom_hash), both wait modes", flush=True)
    res["hashes"] = hashes()
    work = tempfile.mkdtemp(prefix="cwd_", dir=OUT)
    ini = os.path.join(COMMON, "xrt_timing.ini")
    env = dict(os.environ, OMP_NUM_THREADS="1")
    if os.path.exists(ini):
        shutil.copy(ini, os.path.join(work, "xrt.ini"))
        env["XRT_INI_PATH"] = os.path.join(work, "xrt.ini")
    pin = ["numactl", f"--physcpubind={','.join(str(c) for c in s0[:24])}", "--membind=0"] if shutil.which("numactl") else \
          ["taskset", "-c", ",".join(str(c) for c in s0[:24])]
    print("2. where the host's time goes (20 s windows)", flush=True)
    res["windows"] = []
    a = snap(); time.sleep(WINDOW_S); b = snap()
    w = diff(a, b, -1, s0, s1); w.update(label="idle")
    res["windows"].append(w)
    print(f"  idle: socket0 {w['socket0_W']:.1f} W, socket1 {w['socket1_W']:.1f} W, busy cores {w['busy_cores_socket0']:.2f} / {w['busy_cores_socket1']:.2f}", flush=True)
    for label, x, args in (("long run (I FT, 1280 steps)", "ft", ["--case", "I", "--scheme", "ft", "--steps", "1280", "--a1", "3906304"]),
                           ("short run (P1 QE, 1 step)", "qe", ["--case", "P1", "--scheme", "qe", "--steps", "1", "--a1", "118400"])):
        for mode in ("xrt", "callback"):
            cmd = pin + [os.path.join(B, "hq_fpga"), "--xclbin", os.path.join(B, XCL[x]), "--wait", mode, "--a0", "0", "--seed", "4242",
                         "--warmup", "1", "--warmup-s", str(WARM_S), "--window-s", str(WINDOW_S)] + args
            w = run_host(cmd, work, env, s0, s1)
            w.update(label=f"{label}, wait {mode}")
            res["windows"].append(w)
            top = ", ".join(f"{t['comm']} {t['cores']:.2f}" for t in w["top_threads"][:4])
            print(f"  {w['label']}: {w['t_per_run_s'] * 1e3:.2f} ms/run; host process {w['host_process_cores']:.3f} cores "
                  f"(getrusage {w['host_cpu_frac']:.3f}); socket0 {w['socket0_W']:.1f} W, socket1 {w['socket1_W']:.1f} W; "
                  f"busy cores {w['busy_cores_socket0']:.2f} / {w['busy_cores_socket1']:.2f}; top threads: {top}", flush=True)
    json.dump(res, open(os.path.join(OUT, "diag.json"), "w"), indent=1)
    ok = all(h["equal"] for h in res["hashes"])
    print(f"\nCARD WAIT DIAG FINISHED: hashes {'all equal' if ok else 'NOT EQUAL'}; results/card_wait_diag/diag.json", flush=True)


if __name__ == "__main__":
    main()
