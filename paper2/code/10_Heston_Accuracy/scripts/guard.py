#!/usr/bin/env python3
"""guard.py - is anyone else using this machine? (RTX 4070 Ti SUPER desktop, addendum v0.9)

Checks, in about one second:
  logins      sessions in `who` that are not from our own SSH address (SSH_CLIENT of the shell that started us,
              or --own-host); tmux panes of our own account are ours; a session on the machine's own screen or
              console counts as someone else
  gpu         compute processes on the GPU (nvidia-smi) that are not ours (not in our process tree)
  containers  any running container (a containerd-shim process)
  cpu         other processes using more than --cpu-max cores (default 0.5) over one second

Usage:
  python3 scripts/guard.py --once                 print the state; exit 0 if quiet, 1 if not
  python3 scripts/guard.py --wait [--quiet-s 120] wait until quiet for --quiet-s seconds, logging every pause
  python3 scripts/guard.py --watch --log FILE     log the state every 5 s until stopped (for the records)
From Python (the queue scripts):  import guard; reasons = guard.busy(own_root=os.getpid())
"""
import argparse, getpass, os, subprocess, sys, time

CLK = os.sysconf("SC_CLK_TCK")


def _procs():
    out = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/stat") as f:
                s = f.read()
            comm = s[s.index("(") + 1:s.rindex(")")]
            v = s.rsplit(")", 1)[1].split()
            out[int(p)] = (int(v[1]), comm, int(v[11]) + int(v[12]))
        except Exception:
            pass
    return out


def own_tree(root, procs):
    mine, changed = {root, os.getpid()}, True
    while changed:
        changed = False
        for pid, (ppid, _, _) in procs.items():
            if ppid in mine and pid not in mine:
                mine.add(pid); changed = True
    return mine


def own_hosts(extra=()):
    hosts = set(h for h in extra if h)
    sc = os.environ.get("SSH_CLIENT", "").split()
    if sc:
        hosts.add(sc[0])
    return hosts


def logins(hosts):
    out = []
    try:
        lines = subprocess.run(["who"], capture_output=True, text=True, timeout=5).stdout.splitlines()
    except Exception:
        return out
    me = getpass.getuser()
    for ln in lines:
        host = ln[ln.find("(") + 1:ln.rfind(")")] if "(" in ln else ""
        if ln.split()[0] == me and host.startswith("tmux("):
            continue                                   # a tmux pane of our own account (who lists each pane)
        if host not in hosts:
            out.append(" ".join(ln.split()[:2]) + (f" from {host}" if host else " (local screen/console)"))
    return out


def gpu_others(mine):
    try:
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return [f"nvidia-smi failed: {e}"]
    out = []
    for ln in r.splitlines():
        parts = [x.strip() for x in ln.split(",")]
        if parts and parts[0].isdigit() and int(parts[0]) not in mine:
            out.append(f"pid {parts[0]} {parts[1] if len(parts) > 1 else ''}")
    return out


def busy(own_root=None, hosts=(), cpu_max=0.5, interval=1.0):
    """list of reasons why the machine is not quiet (empty list = quiet)"""
    root = own_root or os.getpid()
    a = _procs()
    time.sleep(interval)
    b = _procs()
    mine = own_tree(root, b) | own_tree(root, a)
    reasons = []
    reasons += [f"login: {x}" for x in logins(own_hosts(hosts))]
    reasons += [f"gpu: {x}" for x in gpu_others(mine)]
    if any(c.startswith("containerd-shim") for _, c, _ in b.values()):
        reasons.append("containers: a container is running")
    other = sum(b[p][2] - a[p][2] for p in b if p in a and p not in mine and b[p][2] >= a[p][2]) / CLK / interval
    if other > cpu_max:
        top = sorted(((b[p][2] - a[p][2], b[p][1]) for p in b if p in a and p not in mine), reverse=True)[:3]
        reasons.append(f"cpu: other processes use {other:.2f} cores (" + ", ".join(f"{c} {t / CLK / interval:.2f}" for t, c in top) + ")")
    return reasons


def log(path, msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


def wait_quiet(own_root=None, hosts=(), quiet_s=120, logfile=None, cpu_max=0.5):
    """block until quiet for quiet_s seconds; returns the seconds spent waiting (0 if it was quiet at once)"""
    t0, quiet_since, paused = time.time(), None, False
    while True:
        r = busy(own_root, hosts, cpu_max)
        if not r:
            if not paused:
                return 0.0
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= quiet_s:
                log(logfile, f"GUARD resume after {time.time() - t0:.0f} s")
                return time.time() - t0
        else:
            if not paused:
                log(logfile, "GUARD pause: " + "; ".join(r))
                paused = True
            quiet_since = None
        time.sleep(4)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--wait", action="store_true")
    g.add_argument("--watch", action="store_true")
    ap.add_argument("--own-host", action="append", default=[])
    ap.add_argument("--quiet-s", type=float, default=120)
    ap.add_argument("--cpu-max", type=float, default=0.5)
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    if a.once:
        r = busy(None, a.own_host, a.cpu_max)
        print("QUIET" if not r else "BUSY:\n  " + "\n  ".join(r))
        print("own SSH address:", ", ".join(own_hosts(a.own_host)) or "none (not an SSH session)")
        sys.exit(0 if not r else 1)
    if a.wait:
        wait_quiet(None, a.own_host, a.quiet_s, a.log, a.cpu_max)
        return
    while True:
        r = busy(None, a.own_host, a.cpu_max)
        log(a.log, "quiet" if not r else "busy: " + "; ".join(r))
        time.sleep(4)


if __name__ == "__main__":
    main()
