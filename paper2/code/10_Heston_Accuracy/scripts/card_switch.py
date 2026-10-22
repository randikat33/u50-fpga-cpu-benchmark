#!/usr/bin/env python3
"""card_switch.py - helper of sched_inputs_frodo.py (addendum v0.8 inputs), run inside common/run_measured.py so the
10 Hz power log covers it. Measures on frodo:
  card: the cost of changing the card's option class, by the card's serve program (build/h{q,s}_fpga --serve on CPU 0,
        --wait xrt, registered XRT ini): quit the running program and start the next one, until it prints READY. The
        next program loads its design (xclbin) when it differs from the loaded one; all 25 ordered pairs of the 5
        designs (same design = new class, no new design), 3 rounds. After READY, two small parts are timed (the first
        one shows any first-run cost).
  cpu : the same for the CPU device (the native serve program on socket 1, 48 threads): restart for a new class,
        12 classes x 3 rounds.
Prints MARK start / MARK end around everything and one line per change:
  SW <device> <from> <to> <round> <t0> <t_ready> <t_first_end> <t_second_end> <first_s> <second_s>
(t in Unix seconds; first_s, second_s are the parts' own times). The energy of each change is integrated afterwards
from the power log over [t0, t_ready].
"""
import os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coexec_run as CR

B = CR.B
INI = os.environ.get("CARD_SWITCH_INI", "")
DESIGNS = {"ft": ("I", "ft", 2048), "qe": ("II", "qe", 240), "bft": ("B", "ft", 64), "bqe": ("B", "qe", 8), "asian": ("A", "cv", 250)}
CLASSES = [("I", "ft", 2048), ("I", "qe", 320), ("II", "ft", 30720), ("II", "qe", 240), ("III", "ft", 2560), ("III", "qe", 40),
           ("P1", "ft", 64), ("P1", "qe", 8), ("B", "ft", 64), ("B", "qe", 8), ("A", "plain", 250), ("A", "cv", 250)]
ROUNDS = int(os.environ.get("CARD_SWITCH_ROUNDS", "3"))


def card_cmd(d, seed):
    case, sc, M = DESIGNS[d]
    fam = "main" if case in CR.MAIN else "sec"
    prog = "hq_fpga" if fam == "main" else "hs_fpga"
    return ["numactl", "--physcpubind=0", "--membind=0", os.path.join(B, prog), "--serve", "--xclbin", os.path.join(B, CR.XCL[(fam, sc)])] + \
        CR.case_args(case, sc, M) + ["--a0", "0", "--a1", "64", "--seed", str(seed), "--wait", "xrt"], dict(os.environ, XRT_INI_PATH=INI, OMP_NUM_THREADS="1")


def cpu_cmd(k, seed):
    case, sc, M = k
    cpus = CR.cpulist(1)
    prog = "hq_native_serve" if case in CR.MAIN else "hs_native_serve"
    return ["numactl", f"--physcpubind={','.join(map(str, cpus))}", "--membind=1", os.path.join(B, prog)] + CR.case_args(case, sc, M) + \
        ["--seed", str(seed), "--threads", str(len(cpus))], \
        dict(os.environ, OMP_NUM_THREADS=str(len(cpus)), OMP_PLACES="threads", OMP_PROC_BIND="close", OMP_DYNAMIC="false")


class Serve:
    def __init__(self, cmd, env):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError(f"no READY from {cmd[3]}")
            if line.strip() == "READY":
                break

    def part(self, i, a0, a1):
        self.p.stdin.write(f"R {i} {a0} {a1}\n"); self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("worker closed")
            f = line.split()
            if f and f[0] == "D":
                return float(f[2])
            if f and f[0] == "END":
                return float(f[2])

    def quit(self):
        try:
            self.p.stdin.write("Q\n"); self.p.stdin.flush(); self.p.wait(timeout=60)
        except Exception:
            self.p.kill(); self.p.wait()


def small(case, M, acc):
    """pairs of a small part: about 2e8 path-steps on the card, 1e9 on the CPU (a multiple of 64)"""
    n = int((2e8 if acc else 1e9) / (2 * M))
    return max(64, n // 64 * 64)


def change(dev, frm, to, rnd, cur, cmd_env, k):
    """one recorded change: quit the running program, start the next (labels frm -> to, its class k)"""
    t0 = time.time()
    if cur:
        cur.quit()
    cmd, env = cmd_env
    s = Serve(cmd, env)
    tr = time.time()
    case, _, M = k
    n = small(case, M, dev == "card")
    a = s.part(1, 0, n); t1 = time.time()
    b = s.part(2, n, 2 * n); t2 = time.time()
    print(f"SW {dev} {frm} {to} {rnd} {t0:.6f} {tr:.6f} {t1:.6f} {t2:.6f} {a:.6f} {b:.6f}", flush=True)
    return s


def main():
    print(f"MARK start {time.time():.6f}", flush=True)
    names = list(DESIGNS)
    cur, at = None, None
    n = 0
    for rnd in range(1, ROUNDS + 1):
        for frm in names:
            for to in names:
                if at != frm:                                   # get to the 'from' design first (not a recorded change)
                    if cur:
                        cur.quit()
                    cmd, env = card_cmd(frm, 7 + rnd)
                    cur, at = Serve(cmd, env), frm
                    time.sleep(0.5)
                time.sleep(1.0)                                 # a quiet second before each recorded change
                cur = change("card", frm, to, rnd, cur, card_cmd(to, 7 + rnd), DESIGNS[to])
                at = to
                n += 1
    if cur:
        cur.quit()
    cur = None
    labels = [f"{c}_{s}_{M}" for c, s, M in CLASSES]
    for rnd in range(1, ROUNDS + 1):
        for i, k in enumerate(CLASSES):
            time.sleep(1.0)
            frm = labels[i - 1] if cur else "none"
            cur = change("cpu", frm, labels[i], rnd, cur, cpu_cmd(k, 7 + rnd), k)
            n += 1
    cur.quit()
    print(f"MARK end {time.time():.6f}", flush=True)
    print(f"RESULT runs={n}")
    print("RESULT path_steps=1")
    print("RESULT ok=1")


if __name__ == "__main__":
    main()
