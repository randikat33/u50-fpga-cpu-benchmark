#!/usr/bin/env python3
"""
r56_guard.py - is anyone else using this server? (helper of r56_queue.sh)

  r56_guard.py check [--window 10]     exit 0 = quiet, 1 = busy; prints one line with the reason
  r56_guard.py log FILE                append one line every 2 s: t_unix, other users' CPU (cores), system CPU
                                       (cores, other than ours and kernel threads), other users logged in
  r56_guard.py tree PID SIGNAL         send SIGNAL (TERM, KILL, STOP, CONT) to PID and all its descendants,
                                       including children that started their own session (run_measured.py does)

"Busy" means, measured over the window from /proc (not from ps, whose %CPU is a lifetime average):
  - processes of OTHER human users (uid >= 1000) use more than OTHER_CPU_MAX cores (default 0.10), or
  - processes of any other account (root services, updates, indexers ...) use more than SYS_CPU_MAX cores
    (default 1.0; kernel threads are ignored), or
  - STRICT_LOGIN=1 and another human user has a login session (who), even an idle one.
"""
import os, pwd, signal, subprocess, sys, time

ME = os.getuid()
HZ = os.sysconf("SC_CLK_TCK")
OTHER_CPU_MAX = float(os.environ.get("OTHER_CPU_MAX", "0.10"))
SYS_CPU_MAX = float(os.environ.get("SYS_CPU_MAX", "1.0"))
STRICT_LOGIN = os.environ.get("STRICT_LOGIN", "0") == "1"


def procs():
    """pid -> (uid, ppid, cpu ticks, comm) for user-space processes"""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                s = f.read()
            comm = s[s.index("(") + 1:s.rindex(")")]
            rest = s[s.rindex(")") + 2:].split()
            ppid, flags = int(rest[1]), int(rest[6])
            if flags & 0x00200000 or ppid == 2 or d == "2":   # PF_KTHREAD / kernel threads
                continue
            ticks = int(rest[11]) + int(rest[12])
            uid = os.stat(f"/proc/{d}").st_uid
            out[int(d)] = (uid, ppid, ticks, comm)
        except (OSError, ValueError, IndexError):
            continue
    return out


def human(uid):
    return uid >= 1000 and uid != 65534


def others_logged_in():
    try:
        w = subprocess.run(["who"], capture_output=True, text=True, timeout=5).stdout.split("\n")
    except Exception:
        return []
    me = pwd.getpwuid(ME).pw_name
    return sorted({ln.split()[0] for ln in w if ln.strip() and ln.split()[0] != me})


def usage(window, step=1.0):
    """average cores used by other users / other accounts over the window, sampled every `step` seconds, so
    short-lived processes (that end inside the window) are counted too"""
    other = system = 0.0
    top = {}
    t_end = time.time() + window
    a = procs()
    n = 0
    while True:
        time.sleep(step)
        b = procs()

        def ours(pid, depth=0):    # our processes and their children (e.g. perf/turbostat started with sudo)
            while pid in b and depth < 64:
                if b[pid][0] == ME:
                    return True
                pid, depth = b[pid][1], depth + 1
            return False

        for pid, (uid, _, t1, comm) in b.items():
            if pid not in a or a[pid][0] != uid or ours(pid):
                continue
            c = (t1 - a[pid][2]) / HZ
            if c <= 0:
                continue
            if human(uid):
                other += c
            else:
                system += c
            name = pwd.getpwuid(uid).pw_name if uid in _names() else str(uid)
            key = f"{name}:{comm}"
            top[key] = top.get(key, 0.0) + c
        a = b
        n += 1
        if time.time() >= t_end:
            break
    span = n * step
    return other / span, system / span, sorted(((k, v / span) for k, v in top.items()), key=lambda x: -x[1])[:3]


_NAMES = None


def _names():
    global _NAMES
    if _NAMES is None:
        _NAMES = {p.pw_uid for p in pwd.getpwall()}
    return _NAMES


def check(window):
    other, system, top = usage(window)
    users = others_logged_in()
    reasons = []
    if other > OTHER_CPU_MAX:
        reasons.append(f"other users use {other:.2f} cores")
    if system > SYS_CPU_MAX:
        reasons.append(f"system processes use {system:.2f} cores")
    if STRICT_LOGIN and users:
        reasons.append("logged in: " + ",".join(users))
    tops = ", ".join(f"{k} {v:.2f}" for k, v in top)
    if reasons:
        print(f"BUSY: {'; '.join(reasons)} (top: {tops}; logged in: {','.join(users) or '-'})")
        return 1
    print(f"quiet: other users {other:.2f} cores, system {system:.2f} cores, logged in: {','.join(users) or '-'}")
    return 0


def log(path):
    new = not os.path.exists(path)
    with open(path, "a", buffering=1) as f:
        if new:
            f.write("t_unix,other_users_cores,system_cores,others_logged_in,top\n")
        while True:
            other, system, top = usage(2.0)
            users = "+".join(others_logged_in())
            f.write(f"{time.time():.1f},{other:.3f},{system:.3f},{users},{top[0][0] if top else ''}\n")


def tree(pid, sig):
    ps = procs()
    kids = {}
    for p, (_, pp, _, _) in ps.items():
        kids.setdefault(pp, []).append(p)
    todo, allp = [pid], []
    while todo:
        p = todo.pop()
        allp.append(p)
        todo += kids.get(p, [])
    s = getattr(signal, "SIG" + sig)
    for p in allp:                     # children first for STOP/TERM does not matter; ignore EPERM (root helpers)
        try:
            os.kill(p, s)
        except (ProcessLookupError, PermissionError):
            pass
    print(f"sent SIG{sig} to {len(allp)} process(es)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "check":
        w = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--window" else 10.0
        sys.exit(check(w))
    if cmd == "log":
        log(sys.argv[2])
    elif cmd == "tree":
        tree(int(sys.argv[2]), sys.argv[3].upper())
    else:
        sys.exit(__doc__)
