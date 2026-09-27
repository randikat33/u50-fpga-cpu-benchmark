#!/usr/bin/env python3
"""
run_measured.py - runs ONE benchmark process under identical conditions and records
everything needed later (timing markers, power, temperatures, XRT files).

  run_measured.py --out DIR [options] -- <command> [args...]

Options
  --cwd DIR               working directory for the program (default: DIR/work)
  --timeout S             hard timeout (default 1800)
  --start-marker REGEX    output line that marks "processing started"
  --end-marker REGEX      output line that marks "processing finished"
  --kill-after-end S      server-style programs: SIGTERM S seconds after end marker
  --xrt-ini FILE          xrt.ini to use for this run (swapped in, original restored)
  --xclbin PATH           bitstream this run loads (for swap/cached bookkeeping)
  --state-dir DIR         where the "last loaded xclbin" file lives
  --env K=V               extra environment variables (repeatable)
  --no-pin                do not wrap with numactl/taskset
  --cores LIST / --node N CPU pinning (default 0-23 / 0)
  --idle S                idle power baseline before and after (default 3)
  --interval S            power sampling interval (default 0.1)
  --label TEXT            free-text label stored in meta.json
"""
import argparse, json, os, re, shutil, signal, socket, subprocess, sys, threading, time, glob

HERE = os.path.dirname(os.path.abspath(__file__))
XRT_PATTERNS = ["summary.csv", "*.run_summary", "xrt.run_summary", "*trace*.csv",
                "power_profile_*.csv", "user_events.csv", "*.xclbin.run_summary"]


def temps():
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "power_monitor.py"), "--temps"],
                             capture_output=True, text=True, timeout=30).stdout.split()
        return [None if x == "nan" else float(x) for x in out[:2]]
    except Exception:
        return [None, None]


def parse_cores(spec):
    out = []
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def cpu_times(cores):
    """(busy, total) jiffies summed over the given CPUs, from /proc/stat."""
    busy = total = 0
    try:
        for line in open("/proc/stat"):
            if not line.startswith("cpu") or line.startswith("cpu "):
                continue
            f = line.split()
            if int(f[0][3:]) not in cores:
                continue
            v = [int(x) for x in f[1:9]]
            idle = v[3] + v[4]
            busy += sum(v) - idle
            total += sum(v)
    except Exception:
        return None
    return (busy, total)


def util_pct(a, b):
    if not a or not b or b[1] <= a[1]:
        return None
    return 100.0 * (b[0] - a[0]) / (b[1] - a[1])


def move_xrt_files(src_dir, dst_dir):
    moved = []
    for pat in XRT_PATTERNS:
        for f in glob.glob(os.path.join(src_dir, pat)):
            if os.path.isfile(f):
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(f, os.path.join(dst_dir, os.path.basename(f)))
                moved.append(os.path.basename(f))
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cwd")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--start-marker")
    ap.add_argument("--end-marker")
    ap.add_argument("--kill-after-end", type=float, default=None)
    ap.add_argument("--xrt-ini")
    ap.add_argument("--xclbin")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--no-pin", action="store_true")
    ap.add_argument("--cores", default="0-23")
    ap.add_argument("--node", default="0")
    ap.add_argument("--idle", type=float, default=3.0)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--label", default="")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        sys.exit("no command given")

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    cwd = os.path.abspath(a.cwd) if a.cwd else os.path.join(out, "work")
    os.makedirs(cwd, exist_ok=True)
    exe_dir = os.path.dirname(os.path.abspath(cmd[0])) if os.path.sep in cmd[0] else cwd

    meta = dict(label=a.label, cmd=cmd, cwd=cwd, host=socket.gethostname(),
                cores=a.cores, node=a.node, pinned=not a.no_pin, xclbin=a.xclbin,
                env_extra=a.env, timeout_s=a.timeout)

    # ---- bitstream bookkeeping (swap = card held a different xclbin before) ----
    if a.xclbin and a.state_dir:
        os.makedirs(a.state_dir, exist_ok=True)
        sf = os.path.join(a.state_dir, "last_xclbin.txt")
        prev = open(sf).read().strip() if os.path.exists(sf) else ""
        cur = os.path.realpath(a.xclbin)
        meta["bitstream_state"] = "cached" if prev == cur else ("swap" if prev else "unknown")
        meta["previous_xclbin"] = prev
        open(sf, "w").write(cur)
    else:
        meta["bitstream_state"] = "n/a"

    # ---- xrt.ini swap-in (original files are restored afterwards) ----
    ini_dirs = sorted({cwd, exe_dir})
    backups = []
    stale_dir = os.path.join(out, "_stale_xrt_files")
    for d in ini_dirs:
        move_xrt_files(d, stale_dir)          # never mix old trace files into this run
    if a.xrt_ini:
        for d in ini_dirs:
            p = os.path.join(d, "xrt.ini")
            b = p + ".thesis_backup"
            try:
                if os.path.exists(p) and not os.path.exists(b):
                    os.rename(p, b)
                    backups.append((p, b))
                elif os.path.exists(b):
                    backups.append((p, b))
                shutil.copy(a.xrt_ini, p)
            except PermissionError:
                pass
        shutil.copy(a.xrt_ini, os.path.join(out, "xrt.ini.used"))

    env = os.environ.copy()
    for kv in a.env:
        k, _, v = kv.partition("=")
        env[k] = v
    if a.xrt_ini:
        env["XRT_INI_PATH"] = os.path.abspath(a.xrt_ini)

    full = cmd if a.no_pin else ["numactl", f"--cpunodebind={a.node}", f"--membind={a.node}",
                                 "taskset", "-c", a.cores] + cmd

    # ---- power monitor + idle baseline ----
    t_pre = temps()
    pm = subprocess.Popen([sys.executable, os.path.join(HERE, "power_monitor.py"),
                           os.path.join(out, "power.csv"), str(a.interval)])
    time.sleep(a.idle)

    raw = open(os.path.join(out, "stdout.log"), "wb")
    tsv = open(os.path.join(out, "stdout_ts.tsv"), "w", buffering=1)
    tsv.write("t_unix\tline\n")
    marks = {"start": None, "end": None}
    cores = parse_cores(a.cores) if not a.no_pin else list(range(os.cpu_count() or 1))
    jiff = {"launch": None, "start": None, "end": None, "exit": None}
    start_re = re.compile(a.start_marker) if a.start_marker else None
    end_re = re.compile(a.end_marker) if a.end_marker else None
    end_evt = threading.Event()

    jiff["launch"] = cpu_times(cores)
    t_launch = time.time()
    proc = subprocess.Popen(full, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)

    def reader():
        buf = b""
        while True:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            now = time.time()
            raw.write(chunk)
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for p in parts:
                line = p.decode("utf-8", "replace").strip()
                if not line or "profiling will not be available" in line:
                    continue
                tsv.write(f"{now:.4f}\t{line}\n")
                if start_re and marks["start"] is None and start_re.search(line):
                    marks["start"] = now
                    jiff["start"] = cpu_times(cores)
                if end_re and marks["end"] is None and end_re.search(line):
                    marks["end"] = now
                    jiff["end"] = cpu_times(cores)
                    end_evt.set()
        if buf.strip():
            tsv.write(f"{time.time():.4f}\t{buf.decode('utf-8', 'replace').strip()}\n")

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    def kill_group(sig):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    killed, timed_out = False, False
    deadline = t_launch + a.timeout
    while proc.poll() is None:
        if a.kill_after_end is not None and end_evt.is_set():
            time.sleep(a.kill_after_end)
            if proc.poll() is None:
                kill_group(signal.SIGTERM)
                killed = True
                try:
                    proc.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    kill_group(signal.SIGKILL)
            break
        if time.time() > deadline:
            timed_out = True
            kill_group(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill_group(signal.SIGKILL)
            break
        time.sleep(0.05)
    rc = proc.wait()
    t_exit = time.time()
    jiff["exit"] = cpu_times(cores)
    th.join(timeout=10)
    raw.close(); tsv.close()
    kill_group(signal.SIGKILL)       # leftover children (ffmpeg etc.)

    time.sleep(a.idle)
    pm.send_signal(signal.SIGTERM)
    try:
        pm.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pm.kill()
    t_post = temps()

    # ---- collect XRT outputs, restore original xrt.ini ----
    moved = []
    for d in ini_dirs:
        moved += move_xrt_files(d, os.path.join(out, "xrt"))
    if a.xrt_ini:
        for d in ini_dirs:
            p = os.path.join(d, "xrt.ini")
            if os.path.exists(p) and not any(p == bp for bp, _ in backups):
                os.remove(p)
        for p, b in backups:
            if os.path.exists(b):
                os.replace(b, p)

    ok = (rc == 0) or (killed and marks["end"] is not None)
    meta.update(t_launch=t_launch, t_exit=t_exit, wall_s=t_exit - t_launch,
                t_start_marker=marks["start"], t_end_marker=marks["end"],
                proc_s=(marks["end"] - marks["start"]) if marks["start"] and marks["end"] else None,
                launch_to_end_s=(marks["end"] - t_launch) if marks["end"] else None,
                exit_code=rc, killed_after_end=killed, timed_out=timed_out, ok=ok,
                idle_s=a.idle, fpga_temp_pre=t_pre[0], cpu_temp_pre=t_pre[1],
                fpga_temp_post=t_post[0], cpu_temp_post=t_post[1],
                loadavg=os.getloadavg(), xrt_files=moved,
                cpu_util_run_pct=util_pct(jiff["launch"], jiff["exit"]),
                cpu_util_proc_pct=util_pct(jiff["start"], jiff["end"]),
                finished=time.strftime("%Y-%m-%d %H:%M:%S"))
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=1)
    if ok:
        open(os.path.join(out, "DONE"), "w").write("ok\n")
    print(f"    -> {'OK ' if ok else 'FAIL'} wall={meta['wall_s']:.2f}s"
          + (f" proc={meta['proc_s']:.2f}s" if meta['proc_s'] else "")
          + f" bitstream={meta['bitstream_state']} rc={rc}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
