#!/usr/bin/env python3
"""sched_model.py - the book, the cost model and the scheduling policies of addendum v0.8 (two-server scheduling).

Devices: card = frodo's U50 (host on CPU 0, --wait xrt, registered XRT ini), fcpu = frodo's socket 1 (48 threads,
CPU-native), gpu = the Supermicro's V100 (block wait, 1245 MHz), scpu = the Supermicro's socket 0 (20 threads,
CPU-native). Each device runs any option with either of its two methods at the option's registered setting.

Book: 120 options, 20 per case (I, II, III, P1, B, A): 8 at 1%, 8 at 0.3%, 4 at 0.1%, in an order shuffled with seed
20261023. An option is one complete pricing run at its setting (steps, pairs of results/settings*.csv, rule empirical).
Options of one class (case, method, steps) use the class's seed and consecutive pair ranges, so every option has its
own draws and a device keeps one worker per class.

One pass prices the whole book: every device works through its options in a fixed order (classes sorted by design,
case, method, steps; options by id), one at a time; the pass ends when the last device finishes (its makespan T).
The book is priced again and again (passes), so a device with more than one class changes class at every class
boundary, including the wrap-around from its last class to its first; a device with one class keeps its worker.

Cost model (all inputs measured before v0.8, in results/):
  t(d, option, method)   time of one run at the setting on device d alone (card: phase 6 sessions 1-2, --wait xrt;
                         fcpu: sched_inputs_frodo; gpu: GPU sessions 1-2, block; scpu: sched_inputs_smc), median
  dE(d, option, method)  its energy above idle at the two-server boundary: (energy of all of that server's components
                         during the run) - (that server's idle power) x t
  t_ov(d)                per-option overhead outside the run: link + agent (LAN check) for gpu, scpu; 0.1 ms local
  change(d)              class change: card same design (new process) / other design (new process + xclbin), time and
                         energy above idle (card_switch); fcpu restart + first-run extra; gpu, scpu from the LAN check
                         (first part - second part, through the agent); the CPU and GPU changes add time, no energy
  E(pass) = sum_d [sum_i dE_i + sum_changes dE_c] + (P_idle_frodo + P_idle_smc) x T
  T(pass) = max_d [sum_i (t_i + t_ov) + sum_changes t_c]
The method of an option on a device is chosen by the policy (model policies: the lower-energy method of the two on
that device, evaluated with the rest of the plan).

Policies: alone_<d> (every option on d, its lower-dE method), round_robin (option k in book order on device k mod 4,
order card, fcpu, gpu, scpu, lower-dE method), model_E (least predicted pass energy), model_T (shortest pass, then
energy), model_M (least energy with T <= the midpoint of model_E's and model_T's T), model_dev (energy above idle only),
oracle (model_E's algorithm on the costs measured in the same repeat's alone runs).
Optimiser: start with every option on its least-dE (device, method); then repeat the best improving move until none:
move one option, or all of a device's options of one class, to another (device, method). The objective is E, or with
a deadline (T - D)+ x 1e6 + E. Ties: the first in the fixed order (devices card, fcpu, gpu, scpu; options by id).
"""
import csv, itertools, json, math, os, random, statistics as st
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
DEVICES = ("card", "fcpu", "gpu", "scpu")
MACHINE = {"card": "frodo", "fcpu": "frodo", "gpu": "smc", "scpu": "smc"}
CASES = ("I", "II", "III", "P1", "B", "A")
MAIN = ("I", "II", "III", "P1")
METHODS = {c: ("ft", "qe") for c in CASES}
METHODS["A"] = ("plain", "cv")
MIX = (("1%", 8), ("0.3%", 8), ("0.1%", 4))
BOOK_SEED = 20261023
CLASS_SEED0 = 20262400
T_OV_LOCAL = 1e-4
FDESIGN = {("main", "ft"): "ft", ("main", "qe"): "qe", ("sec", "ft"): "bft", ("sec", "qe"): "bqe", ("sec", "plain"): "asian", ("sec", "cv"): "asian"}
DORDER = {"ft": 0, "qe": 1, "bft": 2, "bqe": 3, "asian": 4}


def design(case, m):
    return FDESIGN[("main" if case in MAIN else "sec", m)]


# ------------------------------------------------------------------ book
def settings():
    S = {}
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("steps"):
                S[(r["case"], r["target"], r["scheme"])] = dict(steps=int(r["steps"]), pairs=int(r["pairs"]), eps=float(r["eps"]))
    return S


def book_mix(mix=None):
    """options per case at 1% / 0.3% / 0.1%: the argument, else the SCHED_BOOK environment variable ("20,0,0"),
    else MIX (8 / 8 / 4, the v0.8 and v0.9 book). Addendum v0.10 prices other books with the same model."""
    if mix is None and os.environ.get("SCHED_BOOK"):
        mix = tuple(int(x) for x in os.environ["SCHED_BOOK"].split(","))
    if mix is None:
        return MIX
    return tuple(zip(("1%", "0.3%", "0.1%"), mix))


def book(mix=None):
    S = settings()
    opts = []
    for c in CASES:
        for t, n in book_mix(mix):
            for _ in range(n):
                opts.append(dict(case=c, target=t))
    random.Random(BOOK_SEED).shuffle(opts)
    classes = {}
    for i, o in enumerate(opts):
        o["id"] = i
        o["eps"] = S[(o["case"], o["target"], METHODS[o["case"]][0])]["eps"]
        o["set"] = {m: S[(o["case"], o["target"], m)] for m in METHODS[o["case"]]}
    # per class: seed and the option's pair range [k N, (k + 1) N)
    for m in ("ft", "qe", "plain", "cv"):
        pass
    for o in opts:
        for m, s in o["set"].items():
            key = (o["case"], m, s["steps"])
            if key not in classes:
                classes[key] = dict(seed=CLASS_SEED0 + len(classes), n=0)
            k = classes[key]["n"]; classes[key]["n"] += 1
            s["a0"], s["a1"] = k * s["pairs"], (k + 1) * s["pairs"]
            s["seed"] = classes[key]["seed"]
    return opts


def ckey(o, m):
    return (o["case"], m, o["set"][m]["steps"])


# ------------------------------------------------------------------ inputs
def idle():
    f = json.load(open(os.path.join(RES, "sched_inputs_frodo_summary.json")))["idle"]
    s = json.load(open(os.path.join(RES, "sched_inputs_smc_summary.json")))["idle"]
    pf = st.median(sum(x[k] for k in ("pkg0", "dram0", "pkg1", "dram1", "card")) for x in f)
    ps = st.median(sum(x[k] for k in ("pkg0", "dram0", "pkg1", "dram1", "gpu")) for x in s)
    return dict(frodo=pf, smc=ps)


def costs():
    """C[(device, case, target, method)] = dict(t, dE, E) (medians), from the measured inputs"""
    P = idle()
    raw = defaultdict(list)
    for f in ("phase6_summary_s1.csv", "phase6_summary_s2.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["kind"] == "set" and r["impl"] == "fpga" and r["wait"] == "xrt":
                raw[("card", r["case"], r["target"], r["scheme"])].append((float(r["t_per_run_s"]), float(r["E_server_per_run_J"])))
    for f in ("phase5g_summary_s1.csv", "phase5g_summary_s2.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["kind"] == "set" and r["mode"] == "block":
                raw[("gpu", r["case"], r["target"], r["scheme"])].append((float(r["t_per_run_s"]), float(r["E_gpu_server_per_run_J"])))
    for r in json.load(open(os.path.join(RES, "sched_inputs_frodo_summary.json")))["cpu"]:
        raw[("fcpu", r["case"], r["target"], r["scheme"])].append((r["t_per_run_s"], r["E_socket0_J"] + r["E_socket1_J"] + r["E_card_J"]))
    for r in json.load(open(os.path.join(RES, "sched_inputs_smc_summary.json")))["cpu"]:
        raw[("scpu", r["case"], r["target"], r["scheme"])].append((r["t_per_run_s"], r["E_socket0_J"] + r["E_socket1_J"] + r["E_gpu_J"]))
    C = {}
    for k, v in raw.items():
        t, E = st.median(x[0] for x in v), st.median(x[1] for x in v)
        C[k] = dict(t=t, E=E, dE=E - P[MACHINE[k[0]]] * t)
    return C


def changes():
    """class-change time and energy above idle per device"""
    P = idle()
    f = json.load(open(os.path.join(RES, "sched_inputs_frodo_summary.json")))
    sw = f["switch"]
    card = {}
    for kind in ("same", "other"):
        xs = [x for x in sw if x["dev"] == "card" and ((x["frm"] == x["to"]) == (kind == "same"))]
        card[kind] = dict(t=st.median(x["t_change_s"] for x in xs),
                          dE=st.median(sum(x["E_change_J"].values()) - P["frodo"] * x["t_change_s"] for x in xs))
    cx = [x for x in sw if x["dev"] == "cpu"]
    fc = st.median(x["t_change_s"] + x["first_part_s"] - x["second_part_s"] for x in cx)
    lan = f["lan"]
    out = dict(card_same=card["same"], card_other=card["other"], fcpu=dict(t=fc, dE=0.0))
    for dev, name in (("gpu", "gpu"), ("cpu", "scpu")):
        ps = [p for p in lan["parts"] if p["dev"] == dev]
        d = []
        for p in ps:
            if p["part"] == 1 and p["round"] > 1:
                q = next(x for x in ps if (x["round"], x["case"], x["scheme"], x["steps"], x["part"]) == (p["round"], p["case"], p["scheme"], p["steps"], 2))
                d.append(p["agent_s"] - q["agent_s"])
        out[name] = dict(t=max(0.0, st.median(d)), dE=0.0)
        out[f"t_ov_{name}"] = st.median(p["overhead_s"] for p in ps)
    return out


def serve_inputs():
    f = os.path.join(RES, "sched_inputs_serve.json")
    return json.load(open(f)) if os.path.exists(f) else None


CHECKS = ("check2", "check3", "check4", "check5_final")


def check_gaps(opts=None, sources=None):
    """per device, the time between consecutive results of one class beyond the part's own time, in the alone runs
    of the pre-registration checks 2-5 (results/sched_checks/<check>/rep1/alone_<d>): the median per check, then the
    median over the checks. The serve-mode input runs priced each class in a short burst and overstated it (GPU 0.45 and
    0.98 ms in the two runs against 0.15-0.18 ms in the book's passes)."""
    opts = opts or book()
    byid = {o["id"]: o for o in opts}
    out = {}
    for d in DEVICES:
        per = []
        for c in (sources or [os.path.join(RES, "sched_checks", c, "rep1") for c in CHECKS]):
            f = os.path.join(c, f"alone_{d}", "sched_result.json")
            if not os.path.exists(f):
                continue
            r = json.load(open(f))
            g = []
            for p in r["passes"]:
                xs = sorted([x for x in p["options"] if x["dev"] == d], key=lambda x: x["t1"])
                for x0, x1 in zip(xs, xs[1:]):
                    if ckey(byid[x0["id"]], x0["m"]) == ckey(byid[x1["id"]], x1["m"]):
                        g.append(x1["t1"] - x0["t1"] - x1["busy"])
            if g:
                per.append(st.median(g))
        if per:
            out[d] = dict(gap_s=st.median(per), per_check_s=per)
    return out


def apply_serve(C, rows, gaps=None):
    """serve-mode costs (sched_inputs_serve.json): a measured setting takes its effective serve time per option and
    its energy above idle = (single-run power above idle) x its serve busy time; an unmeasured (>= 50 ms) setting takes
    its single-run time x the device's median serve/single ratio over its measured settings of 20 ms or more (effective,
    and busy for the energy; the median extra in ms if it has none). Returns the device's median latency of a batch's
    first result."""
    by = {(r["device"], r["case"], r["target"], r["method"]): r for r in rows}
    out = {}
    lat = {}
    for d in DEVICES:
        rs = [r for r in rows if r["device"] == d]
        fx_t = st.median(r["t_eff"] - r["t_single"] for r in rs) if rs else 0.0
        fx_b = st.median(r["busy_med"] - r["t_single"] for r in rs) if rs else 0.0
        lat[d] = max(0.0, st.median(r["latency"] for r in rs)) if rs else 0.0
        big = [r for r in rs if r["t_single"] >= 0.02]
        rt_t = st.median(r["t_eff"] / r["t_single"] for r in big) if big else None
        rt_b = st.median(r["busy_med"] / r["t_single"] for r in big) if big else None
        for k, v in C.items():
            if k[0] != d:
                continue
            ts, ds = v["t"], v["dE"]
            r = by.get(k)
            if r:
                t, b = r["t_eff"], r["busy_med"]
            elif rt_t:
                t, b = ts * rt_t, ts * rt_b
            else:
                t, b = ts + fx_t, ts + fx_b
            if gaps and d in gaps:                   # effective time = the part's serve time + the book's gap
                t = b + gaps[d]["gap_s"]
            out[k] = dict(v, t_single=ts, dE_single=ds, t=t, busy=b, dE=ds / ts * max(b, 0.0), serve_measured=int(bool(r)))
    return out, lat


class Model:
    def __init__(self, C=None, CH=None, P=None, latency=None, conc=None, devices=DEVICES, boundary=("frodo", "smc"), gaps=None):
        """conc (addendum v0.9): the measured concurrency matrix, {"C_W": {"a+b": W}, "s": {"a|b": stretch}} for devices
        a < b in DEVICES order (results/concurrency/C_matrix.json); None gives the additive model of v0.8.
        devices, boundary: the devices a plan may use and the servers whose idle power is in E (v0.9 R3: card and fcpu,
        frodo only)."""
        self.CH = CH or changes()
        self.P = P or idle()
        self.devices = tuple(devices)
        self.boundary = tuple(boundary)
        self.conc = conc
        self.Pidle = sum(self.P[b] for b in self.boundary)
        if C is None:
            C = costs()
            sv = serve_inputs()
            if sv:
                g = check_gaps()
                if gaps:                         # v0.9: gaps re-measured for some devices (scripts/conc_timing.py)
                    g = dict(g, **gaps)
                C, latency = apply_serve(C, sv, g)
        self.C = C
        self.latency = latency
        if latency is not None:          # pipelined serve mode: one latency per class batch (local) or per pass (agent)
            self.t_ov = {d: 0.0 for d in DEVICES}
        else:
            self.t_ov = {"card": T_OV_LOCAL, "fcpu": T_OV_LOCAL, "gpu": self.CH["t_ov_gpu"], "scpu": self.CH["t_ov_scpu"]}

    @classmethod
    def from_prediction(cls, pred):
        """the registered model, rebuilt from predictions_sched.json (costs, changes, idle powers, overheads)"""
        C = {(c["device"], c["case"], c["target"], c["method"]): {k: v for k, v in c.items() if k not in ("device", "case", "target", "method")}
             for c in pred["costs"]}
        M = cls(C=C, CH=pred["changes"], P=pred["idle_W"], latency=pred.get("latency_s"), conc=pred.get("concurrency"),
                devices=pred.get("devices", DEVICES), boundary=pred.get("boundary", ("frodo", "smc")))
        M.t_ov = dict(pred["t_ov_s"])
        return M

    def cost(self, d, o, m):
        return self.C[(d, o["case"], o["target"], m)]

    def device_load(self, d, items):
        """items: list of (option, method) on device d -> (busy time, energy above idle)"""
        t = sum(self.cost(d, o, m)["t"] + self.t_ov[d] for o, m in items)
        e = sum(self.cost(d, o, m)["dE"] for o, m in items) + (self.overhead(d) + self.coord(d)) * len(items)
        ct, ce = self.class_cost(d, {ckey(o, m) for o, m in items})
        return t + ct, e + ce, len(items)

    def evaluate(self, plan, opts):
        """plan: {option id: (device, method)} -> T, E and the per-device loads (busy: alone, before any stretch;
        finish: the device's finish time in the pass with the concurrency stretch; overlap: time both busy, per pair)"""
        per = {d: [] for d in self.devices}
        for o in opts:
            d, m = plan[o["id"]]
            per[d].append((o, m))
        loads = {d: self.device_load(d, per[d]) for d in self.devices}
        T, E, fin, ov, Ec, Ee = self.combine(loads, detail=True)
        return dict(T=T, E=E, busy={d: loads[d][0] for d in self.devices}, dE={d: loads[d][1] for d in self.devices},
                    n={d: len(per[d]) for d in self.devices}, finish=fin, overlap=ov, E_conc=Ec, E_engaged=Ee)

    # ---------------------------------------------------------- v0.9: the concurrency term
    def C_pair(self, a, b):
        if not self.conc:
            return 0.0
        a, b = sorted((a, b), key=DEVICES.index)
        return self.conc["C_W"].get(f"{a}+{b}", 0.0)

    def stretch(self, d, other):
        return self.conc["s"].get(f"{d}|{other}", 1.0) if self.conc else 1.0

    def engaged(self, d):
        """v0.9 (step 5b): power above idle while d has options in the plan but has finished them, and no other device
        of its server is busy (that device already keeps the server awake: card_f10+fcpu)"""
        return (self.conc or {}).get("engaged_W", {}).get(d, 0.0)

    def overhead(self, d):
        """v0.9 (step 5b): energy per option through the agent beyond the single-run inputs, at the Supermicro
        (remote devices)"""
        return (self.conc or {}).get("overhead_J", {}).get(d, 0.0)

    def coord(self, d):
        """v0.9 (step 5b): frodo's energy per remote option (the coordinator keeps frodo's sockets awake); not paid
        while frodo is awake anyway: the card has options in the plan, or fcpu is busy"""
        return (self.conc or {}).get("coord_J", {}).get(d, 0.0)

    def combine(self, loads, detail=False):
        """pass time and energy from the devices' loads {d: (busy alone, energy above idle)}.
        v0.8 (conc None): T = max busy, E = sum of energies above idle + P_idle T.
        v0.9: every device starts at the start of the pass and draws its alone power above idle, dE / busy, while it
        works; while a set A of devices is busy, device d works at 1 / prod_{d' in A, d' != d} s(d|d') of its alone
        rate, and the servers draw C(d, d') more for every pair in A. E = integral of that power + P_idle T; T = the
        last finish. (C and s from the pair measurements of step 5; a pair not measured has C = 0 and s = 1.)"""
        if not self.conc:
            T = max(v[0] for v in loads.values())
            E = sum(v[1] for v in loads.values()) + self.Pidle * T
            return (T, E, {d: v[0] for d, v in loads.items()}, {}, 0.0, 0.0) if detail else (T, E)
        w = {d: v[0] for d, v in loads.items() if v[0] > 0}
        pw = {d: loads[d][1] / loads[d][0] for d in w}
        b0 = dict(w)
        nn = {d: (loads[d][2] if len(loads[d]) > 2 else 0) for d in w}
        used = set(w)
        fin = {d: 0.0 for d in loads}
        ov = defaultdict(float)
        A = sorted(w, key=DEVICES.index)
        t = E = Ec = Ee = 0.0
        while A:
            sig = {d: math.prod(self.stretch(d, x) for x in A if x != d) for d in A}
            dt = min(w[d] * sig[d] for d in A)
            pairs = list(itertools.combinations(A, 2))
            c = sum(self.C_pair(a, b) for a, b in pairs)
            busy_srv = {MACHINE[x] for x in A}
            eg = sum(self.engaged(d) for d in used if d not in A and MACHINE[d] not in busy_srv)
            if "card" in used or "fcpu" in A:          # frodo awake anyway: the remote options' frodo part is not paid
                eg -= sum(self.coord(d) * nn[d] * (dt / sig[d]) / b0[d] for d in A if d in ("gpu", "scpu")) / dt
            E += (sum(pw[d] for d in A) + c + eg) * dt
            Ec += c * dt
            Ee += eg * dt
            if detail:
                for a, b in pairs:
                    ov[f"{a}+{b}"] += dt
            t += dt
            nxt = []
            for d in A:
                w[d] -= dt / sig[d]
                if w[d] * sig[d] <= dt * 1e-12 + 1e-15:
                    fin[d] = t
                else:
                    nxt.append(d)
            A = nxt
        E += self.Pidle * t
        return (t, E, fin, dict(ov), Ec, Ee) if detail else (t, E)

    # ---------------------------------------------------------- policies
    def best_method(self, d, o):
        return min(METHODS[o["case"]], key=lambda m: (self.cost(d, o, m)["dE"], m))

    def alone(self, d, opts):
        return {o["id"]: (d, self.best_method(d, o)) for o in opts}

    def round_robin(self, opts):
        D = self.devices
        return {o["id"]: (D[k % len(D)], self.best_method(D[k % len(D)], o)) for k, o in enumerate(opts)}

    def objective(self, plan, opts, deadline=None):
        ev = self.evaluate(plan, opts)
        return (max(0.0, ev["T"] - deadline) * 1e6 if deadline else 0.0) + ev["E"]

    def optimise(self, opts, deadline=None, inits=()):
        """best local optimum over several starting plans: every option on its least-dE (device, method); each alone
        policy; round robin; and any plans given (inits). Ties: the first start in this order."""
        starts = [None] + [self.alone(d, opts) for d in self.devices] + [self.round_robin(opts)] + [dict(p) for p in inits]
        best, bv = None, None
        for st0 in starts:
            p = self.descend(opts, deadline, st0)
            v = self.objective(p, opts, deadline)
            if bv is None or v < bv - 1e-9:
                best, bv = p, v
        return best

    def class_cost(self, d, classes):
        """(time, energy above idle) of a device's class changes per pass, and its batch latencies"""
        t = e = 0.0
        cls = sorted(classes, key=class_order)
        if self.latency is not None and cls:
            t += self.latency[d] * (len(cls) if d in ("card", "fcpu") else 1)
        if len(cls) > 1:
            for a, b in zip(cls, cls[1:] + cls[:1]):
                if d == "card":
                    c = self.CH["card_same"] if design(a[0], a[1]) == design(b[0], b[1]) else self.CH["card_other"]
                else:
                    c = self.CH[d]
                t += c["t"]; e += c["dE"]
        return t, e

    def descend(self, opts, deadline=None, init=None, max_iter=100000):
        """best-improving moves until none improves (incremental form of evaluate: per-device sums of the options'
        time and energy and the device's class set; identical objective)"""
        byid = {o["id"]: o for o in opts}
        if init:
            plan = dict(init)
        else:
            plan = {o["id"]: min(((d, m) for d in self.devices for m in METHODS[o["case"]]), key=lambda x: self.cost(x[0], o, x[1])["dE"]) for o in opts}
        c = {}
        for o in opts:
            for d in self.devices:
                for m in METHODS[o["case"]]:
                    x = self.cost(d, o, m)
                    c[(o["id"], d, m)] = (x["t"] + self.t_ov[d], x["dE"] + self.overhead(d) + self.coord(d), ckey(o, m))
        st_ = {d: dict(t=0.0, e=0.0, cls=defaultdict(int)) for d in self.devices}
        for i, (d, m) in plan.items():
            t, e, k = c[(i, d, m)]
            st_[d]["t"] += t; st_[d]["e"] += e; st_[d]["cls"][k] += 1

        def dev_load(d, dt=0.0, de=0.0, add=(), rem=()):
            cl = dict(st_[d]["cls"])
            for k in rem:
                cl[k] -= 1
                if cl[k] == 0:
                    del cl[k]
            for k in add:
                cl[k] = cl.get(k, 0) + 1
            ct, ce = self.class_cost(d, cl.keys())
            return st_[d]["t"] + dt + ct, st_[d]["e"] + de + ce, sum(cl.values())

        base = {d: dev_load(d) for d in self.devices}

        def obj(loads):
            T, E = self.combine(loads)
            return (max(0.0, T - deadline) * 1e6 if deadline else 0.0) + E

        def try_move(ids, d1, m1):
            """objective after moving the options ids (all on one (device, method)) to (d1, m1)"""
            d0 = plan[ids[0]][0]
            loads = dict(base)
            rem_t = sum(c[(i, d0, plan[i][1])][0] for i in ids); rem_e = sum(c[(i, d0, plan[i][1])][1] for i in ids)
            add_t = sum(c[(i, d1, m1)][0] for i in ids); add_e = sum(c[(i, d1, m1)][1] for i in ids)
            rk = [c[(i, d0, plan[i][1])][2] for i in ids]; ak = [c[(i, d1, m1)][2] for i in ids]
            if d1 == d0:
                loads[d0] = dev_load(d0, add_t - rem_t, add_e - rem_e, ak, rk)
            else:
                loads[d0] = dev_load(d0, -rem_t, -rem_e, (), rk)
                loads[d1] = dev_load(d1, add_t, add_e, ak, ())
            return obj(loads)

        def apply(ids, d1, m1):
            for i in ids:
                d0, m0 = plan[i]
                t, e, k = c[(i, d0, m0)]
                st_[d0]["t"] -= t; st_[d0]["e"] -= e; st_[d0]["cls"][k] -= 1
                if st_[d0]["cls"][k] == 0:
                    del st_[d0]["cls"][k]
                t, e, k = c[(i, d1, m1)]
                st_[d1]["t"] += t; st_[d1]["e"] += e; st_[d1]["cls"][k] += 1
                plan[i] = (d1, m1)
            for d in self.devices:
                base[d] = dev_load(d)

        cur = obj(base)
        for _ in range(max_iter):
            best = None
            for o in opts:
                d0 = plan[o["id"]]
                for d in self.devices:
                    for m in METHODS[o["case"]]:
                        if (d, m) == d0:
                            continue
                        v = try_move([o["id"]], d, m)
                        if v < cur - 1e-9 and (best is None or v < best[0] - 1e-9):
                            best = (v, [o["id"]], d, m)
            groups = defaultdict(list)
            for i, (d, m) in plan.items():
                groups[(d, ckey(byid[i], m))].append(i)
            for (d0, k), ids in sorted(groups.items(), key=lambda x: (DEVICES.index(x[0][0]), str(x[0][1]))):
                if len(ids) < 2:
                    continue
                for d in self.devices:
                    for m in METHODS[k[0]]:
                        if d == d0 and m == k[1]:
                            continue
                        v = try_move(sorted(ids), d, m)
                        if v < cur - 1e-9 and (best is None or v < best[0] - 1e-9):
                            best = (v, sorted(ids), d, m)
            if best is None:
                break
            apply(best[1], best[2], best[3])
            cur = best[0]
        return plan

    def min_makespan(self, opts, inits=()):
        """the shortest pass the optimiser finds (objective T, then E): the lower end for the deadlines"""
        saved = self.Pidle
        self.Pidle = 1e6                     # makes T dominate E
        plan = self.optimise(opts, inits=inits)
        self.Pidle = saved
        return plan


def class_order(k):
    return (DORDER[design(k[0], k[1])], CASES.index(k[0]), k[1], k[2])


def device_order(plan, opts):
    """per device, its (option id, method) in execution order: classes as class_order, options by id"""
    byid = {o["id"]: o for o in opts}
    out = {d: [] for d in DEVICES}
    for i, (d, m) in plan.items():
        out[d].append((i, m))
    for d in DEVICES:
        out[d].sort(key=lambda x: (class_order(ckey(byid[x[0]], x[1])), x[0]))
    return out


def measured_pass(res, E_frodo_J, smc=True):
    """a job's measured pass: T = window / passes, E = (frodo + Supermicro energy in the window) / passes
    (smc False, v0.9 R3: frodo's energy only)"""
    n = len(res["passes"])
    E = (E_frodo_J + (sum(res["smc_energy"]["E_J"].values()) if smc else 0.0)) / n
    return dict(T=(res["t_end"] - res["t_start"]) / n, E=E, passes=n)


def corrected(M, opts, alone):
    """the oracle's costs: the model's, corrected with the same repeat's alone runs. alone[d] = (result, E_frodo_J).
    Times: per setting d ran in alone_d, the median interval between consecutive results of one class (the pipelined
    effective time per option); the model's time x d's median measured/model ratio for d's other settings.
    Energy above idle: the model's x d's ratio (measured pass energy - idle x pass time - model class-change energy) /
    (model energy of the options). Class changes and latencies as the model."""
    C = dict(M.C)
    byid = {o["id"]: o for o in opts}
    for d, (res, Ef) in alone.items():
        eff = defaultdict(list)
        for p in res["passes"]:
            xs = sorted(p["options"], key=lambda x: x["t1"])
            for x0, x1 in zip(xs, xs[1:]):
                o0, o1 = byid[x0["id"]], byid[x1["id"]]
                if ckey(o0, x0["m"]) == ckey(o1, x1["m"]):
                    eff[(d, o1["case"], o1["target"], x1["m"])].append(x1["t1"] - x0["t1"])
        ratios = []
        for k, v in eff.items():
            tm = st.median(v)
            ratios.append(tm / M.C[k]["t"])
            C[k] = dict(M.C[k], t=tm)
        rt = st.median(ratios) if ratios else 1.0
        for k in list(C):
            if k[0] == d and k not in eff:
                C[k] = dict(M.C[k], t=M.C[k]["t"] * rt)
        mp = measured_pass(res, Ef, smc="smc" in M.boundary)
        plan = M.alone(d, opts)
        ev = M.evaluate(plan, opts)
        items = [(o, plan[o["id"]][1]) for o in opts]
        e_opts = sum(M.cost(d, o, m)["dE"] + M.overhead(d) + M.coord(d) for o, m in items)
        e_change = ev["dE"][d] - e_opts
        r = max(1e-6, (mp["E"] - M.Pidle * mp["T"] - e_change)) / e_opts
        for k in list(C):
            if k[0] == d:
                C[k] = dict(C[k], dE=M.C[k]["dE"] * r)
    N = Model(C=C, CH=M.CH, P=M.P, latency=M.latency, conc=M.conc, devices=M.devices, boundary=M.boundary)
    N.t_ov = dict(M.t_ov)
    return N


def plan_json(plan):
    return {str(i): list(x) for i, x in sorted(plan.items())}


if __name__ == "__main__":
    M = Model()
    opts = book()
    print("idle", M.P, "changes", {k: v for k, v in M.CH.items()})
    for d in DEVICES:
        ev = M.evaluate(M.alone(d, opts), opts); print(f"alone_{d}: T {ev['T']:.3f} s E {ev['E']:.1f} J")
    ev = M.evaluate(M.round_robin(opts), opts); print(f"round_robin: T {ev['T']:.3f} E {ev['E']:.1f}")
    pe = M.optimise(opts); ev = M.evaluate(pe, opts); print(f"model_E: T {ev['T']:.3f} E {ev['E']:.1f} n {ev['n']} busy {ev['busy']}")
    pm = M.min_makespan(opts); ev2 = M.evaluate(pm, opts); print(f"min T: T {ev2['T']:.3f} E {ev2['E']:.1f} n {ev2['n']}")
