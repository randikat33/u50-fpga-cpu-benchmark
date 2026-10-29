#!/usr/bin/env python3
"""calib_model_v11.py - the scheduling model of addendum v0.11: the registered v0.9 model (rebuilt from
results/predictions_sched_v09/predictions_sched_v09.json, SHA-256 checked) with its stream costs re-measured in the
v0.11 calibration runs (results/sched_calib_v11, 28 October 2026, before registration; scripts/sched_queue_v11.py).

Why. The v0.10 run-code check showed the v0.9 model over-predicts books of short options on the V100 (book S alone:
19.3 ms and 4.35 J per pass against 26.6 ms and 7.30 J). Two inputs caused it: the time between results of one class
(the v0.9 gap, 0.057 ms per option, is hidden in the stream) and the fixed energy per remote option (v0.9 dispatch costs,
10.1 mJ at the Supermicro and 10.5 mJ at frodo, measured on 0.494 ms options, that act as power x time).

What changes (everything else is v0.9: idle power of frodo, concurrency matrix C and stretches s, engaged powers,
the card's other-design change, the policies and the optimiser):
  times      t(d, class) of every 1% and 0.3% class measured in the calibration = the median interval between
             consecutive results of that class (pooled over the two repeats). 0.1% classes keep v0.9's t.
  overheads  per device, h(d) = the median over class batches of (batch span - n x t(class)), the span from the end of
             the previous batch (or the pass start) to the batch's last result; and g(d) = the median over jobs of
             (pass time - n_classes x h - sum of t), per pass. A device with two or more classes pays g(d) + h(d) per
             class per pass; a device with one class keeps its worker and pays v0.9's latency per pass (as v0.9; the
             calibration has no single-class run). The card has h per design (qe, asian measured; ft, bft, bqe keep
             v0.9's latency + same-design change) and v0.9's other-design change between designs.
  energy     the Supermicro's idle power is re-measured (median power over the calibration jobs that did not use it).
             fcpu, gpu, scpu: energy above idle on the device's own server = a(d) x sum dE_single + b(d) x busy time,
             a and b solved from the two books (K1: 1% options, K2: 0.3%; medians of the 2 repeats); on the other server
             P_x(d) x busy time (gpu, scpu: frodo's coordinator). dE_single is v0.9's single-run energy above idle.
             0.1% classes keep v0.9's time and energy (with v0.9's dispatch terms per option): the calibration has no
             0.1% option, and v0.9 predicted book L (0.1% only) within 3% in the check.
             card: energy above idle = r(design) x (sum dE_single + P_ch x overhead time), P_ch = v0.9's same-design
             change power (dE / t), r measured per design (qe, asian); other designs r = 1.
  The v0.9 per-option dispatch terms (overhead_J, coord_J) are replaced by the rules above for calibrated classes.
  combining  powers of devices busy at the same time add: v0.9's pair term C (card+fcpu, -19.0 W, measured on long
             runs) is dropped and the coordinator's power is paid also while frodo is awake for other reasons. This was
             selected after the v0.10 check (8 jobs, 1 repeat, 3 s windows, seen before this model was fixed): of six
             variants (with or without C, the coordinator rule, the engaged powers) it had the largest error
             smallest (measured / predicted pass energy 0.995-1.072 over the 8 jobs, against 1.016-1.097 with v0.9's
             rules). The check jobs are therefore in-sample for this model.

  python3 scripts/calib_model_v11.py          prints the calibration and the in-sample fit
"""
import csv, hashlib, itertools, json, math, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

RES = S.RES
V09 = os.path.join(RES, "predictions_sched_v09", "predictions_sched_v09.json")
V09_SHA = "d4ad8c03b807be53e3f4b75763cb013c70dc75ea600dd97b66f0e233c66a778f"
CAL = os.path.join(RES, "sched_calib_v11")
CAL_SUMMARY = os.path.join(RES, "sched_calib_v11_summary.csv")
K1, K2, K = (10, 0, 0), (0, 10, 0), (10, 10, 0)
JOBS = {"K1_alone_fcpu": (K1, "fcpu"), "K1_alone_gpu": (K1, "gpu"), "K1_alone_scpu": (K1, "scpu"),
        "K2_alone_fcpu": (K2, "fcpu"), "K2_alone_gpu": (K2, "gpu"), "K2_alone_scpu": (K2, "scpu"),
        "K_card_qe": (K, "card"), "K_card_asian": (K, "card")}
CARD_JOB = {"qe": "K_card_qe", "asian": "K_card_asian"}
STREAM = ("fcpu", "gpu", "scpu")
OTHER = {"fcpu": "smc", "gpu": "frodo", "scpu": "frodo"}
REMOTE = ("gpu", "scpu")
LOCAL = ("card", "fcpu")


def v09():
    sha = hashlib.sha256(open(V09, "rb").read()).hexdigest()
    if sha != V09_SHA:
        sys.exit(f"{V09} is not the registered v0.9 prediction")
    return json.load(open(V09))


def load_jobs():
    """[(name, rep, device, options of the plan, plan, result, summary row)]"""
    rows = {(r["job"], int(r["rep"])): r for r in csv.DictReader(open(CAL_SUMMARY))}
    out = []
    for (name, rep), r in sorted(rows.items()):
        mix, dev = JOBS[name]
        pj = json.load(open(os.path.join(CAL, name, f"rep{rep}.plan.json")))
        plan = {int(k): tuple(v) for k, v in pj["plan"].items()}
        opts = [o for o in S.book(mix) if o["id"] in plan]
        res = json.load(open(os.path.join(CAL, name, f"rep{rep}", "sched_result.json")))
        out.append(dict(name=name, rep=rep, dev=dev, mix=mix, opts=opts, plan=plan, res=res, row=r))
    return out


def batches(res, byid):
    """per pass: [(class key, [results in arrival order])] in arrival order, and the pass start"""
    out = []
    for p in res["passes"]:
        xs = sorted(p["options"], key=lambda x: x["t1"])
        bs = []
        for x in xs:
            k = (x["dev"],) + S.ckey(byid[x["id"]], x["m"])
            if bs and bs[-1][0] == k:
                bs[-1][1].append(x)
            else:
                bs.append((k, [x]))
        out.append((min(x["t0"] for x in p["options"]), p["T"], bs))
    return out


def calibrate(p9=None, jobs=None):
    p9 = p9 or v09()
    jobs = jobs or load_jobs()
    M9 = S.Model.from_prediction(p9)
    C9 = M9.C
    # 1. times per class: median interval between consecutive results of one class
    iv = defaultdict(list)
    for j in jobs:
        byid = {o["id"]: o for o in j["opts"]}
        for _, _, bs in batches(j["res"], byid):
            for k, xs in bs:
                for a, b in zip(xs, xs[1:]):
                    iv[(k[0], k[1], byid[b["id"]]["target"], k[2])].append(b["t1"] - a["t1"])
    t_cal = {k: st.median(v) for k, v in iv.items() if len(v) >= 10}
    # 2. per-class overhead h and per-pass overhead g
    unit = lambda j: j["dev"] if j["dev"] != "card" else ("card", "qe" if j["name"] == "K_card_qe" else "asian")
    hs = defaultdict(list)
    for j in jobs:
        byid = {o["id"]: o for o in j["opts"]}
        for t0, T, bs in batches(j["res"], byid):
            prev = t0
            for k, xs in bs:
                hs[unit(j)].append(xs[-1]["t1"] - prev - sum(t_cal[(k[0], k[1], byid[x["id"]]["target"], k[2])] for x in xs))
                prev = xs[-1]["t1"]
    h = {u: st.median(v) for u, v in hs.items()}
    gs = defaultdict(list)
    for j in jobs:
        n_cls = len({S.ckey(o, j["plan"][o["id"]][1]) for o in j["opts"]})
        sum_t = sum(t_cal[(j["dev"], o["case"], o["target"], j["plan"][o["id"]][1])] for o in j["opts"])
        gs[unit(j)].append(float(j["row"]["T_pass_s"]) - n_cls * h[unit(j)] - sum_t)
    g = {u: st.median(v) for u, v in gs.items()}
    # 3. the Supermicro's idle power: jobs that did not use it
    p_smc = st.median(float(j["row"]["E_smc_J"]) / (float(j["row"]["T_pass_s"]) * int(j["row"]["passes"]))
                      for j in jobs if j["dev"] in ("fcpu", "card"))
    P = dict(M9.P, smc=p_smc)
    above = lambda j, srv: (float(j["row"][f"E_{'frodo' if srv == 'frodo' else 'smc'}_J"]) / int(j["row"]["passes"])
                            - P[srv] * float(j["row"]["T_pass_s"]))
    med = lambda xs: st.median(xs)
    # 4. energy: a, b per stream device from K1 and K2; P_x on the other server
    fit = {}
    for d in STREAM:
        eq = []
        for book in ("K1", "K2"):
            js = [j for j in jobs if j["name"] == f"{book}_alone_{d}"]
            sdE = med([sum(C9[(d, o["case"], o["target"], j["plan"][o["id"]][1])]["dE_single"] for o in j["opts"]) for j in js])
            T = med([float(j["row"]["T_pass_s"]) for j in js])
            E = med([above(j, S.MACHINE[d]) for j in js])
            Ex = med([above(j, OTHER[d]) for j in js])
            eq.append((sdE, T, E, Ex))
        (x1, y1, e1, _), (x2, y2, e2, _) = eq
        det = x1 * y2 - x2 * y1
        a = (e1 * y2 - e2 * y1) / det
        b = (x1 * e2 - x2 * e1) / det
        px = sum(q[3] for q in eq) / sum(q[1] for q in eq)
        fit[d] = dict(a=a, b_W=b, P_other_W=px, books=[dict(zip(("sum_dE_single_J", "T_s", "E_own_above_J", "E_other_above_J"), q)) for q in eq])
    # card: r per design
    p_ch = p9["changes"]["card_same"]["dE"] / p9["changes"]["card_same"]["t"]
    card = {}
    for des, name in CARD_JOB.items():
        js = [j for j in jobs if j["name"] == name]
        sdE = med([sum(C9[("card", o["case"], o["target"], j["plan"][o["id"]][1])]["dE_single"] for o in j["opts"]) for j in js])
        sum_t = med([sum(t_cal[("card", o["case"], o["target"], j["plan"][o["id"]][1])] for o in j["opts"]) for j in js])
        T = med([float(j["row"]["T_pass_s"]) for j in js])
        E = med([above(j, "frodo") for j in js])
        Ex = med([above(j, "smc") for j in js])
        card[des] = dict(r=E / (sdE + p_ch * (T - sum_t)), sum_dE_single_J=sdE, T_s=T, overhead_s=T - sum_t, E_frodo_above_J=E, E_smc_above_J=Ex,
                         h_s=h[("card", des)], g_s=g[("card", des)])
    return dict(t_cal={"|".join(k): v for k, v in t_cal.items()}, n_intervals={"|".join(k): len(iv[k]) for k in t_cal},
                h_s={d: h[d] for d in STREAM}, g_s={d: g[d] for d in STREAM}, P_idle_W=P, P_idle_smc_v09_W=M9.P["smc"],
                fit=fit, card=card, P_ch_W=p_ch)


class Model11(S.Model):
    """the v0.11 model (see the module doc)"""

    def __init__(self, p9, cal):
        M9 = S.Model.from_prediction(p9)
        self.cal = cal
        t_cal = {tuple(k.split("|")): v for k, v in cal["t_cal"].items()}
        C = {}
        for k, c in M9.C.items():
            d = k[0]
            if k in t_cal:
                t = t_cal[k]
                if d in STREAM:
                    f = cal["fit"][d]
                    dE = (f["a"] * c["dE_single"] + (f["b_W"] + f["P_other_W"]) * t)
                    cJ = f["P_other_W"] * t if d in REMOTE else 0.0
                else:
                    des = S.design(k[1], k[3])
                    dE, cJ = cal["card"][des]["r"] * c["dE_single"], 0.0
            else:                                 # not calibrated (0.1% classes; card designs ft, bft, bqe): v0.9
                t = c["t"]
                dE = c["dE"] + M9.overhead(d) + M9.coord(d)
                cJ = M9.coord(d)
            C[k] = dict(c, t=t, dE=dE, coord_J=cJ, calibrated=int(k in t_cal))
        conc = dict(M9.conc, overhead_J={}, coord_J={}, C_W={})      # powers add (selection on the v0.10 check, see the doc)
        super().__init__(C=C, CH=M9.CH, P=cal["P_idle_W"], latency=M9.latency, conc=conc, devices=M9.devices, boundary=M9.boundary)
        self.t_ov = {d: 0.0 for d in S.DEVICES}

    def class_cost(self, d, classes):
        """per pass (time, energy above idle, coordinator part of that energy): g(d) + h(d) per class; card: g of its
        calibrated designs (largest), h of the class's design, v0.9's latency + same-design change for designs not
        calibrated, v0.9's other-design change at every design boundary"""
        cls = sorted(classes, key=S.class_order)
        if not cls:
            return 0.0, 0.0, 0.0
        cal = self.cal
        if len(cls) == 1 and d in LOCAL:          # local device, one class: the worker is kept (as v0.9), v0.9's latency per pass
            t = self.latency[d]
            if d in STREAM:
                f = cal["fit"][d]
                return t, (f["b_W"] + f["P_other_W"]) * t, (f["P_other_W"] * t if d in REMOTE else 0.0)
            x = S.design(cls[0][0], cls[0][1])
            return t, (cal["card"][x]["r"] if x in cal["card"] else 1.0) * cal["P_ch_W"] * t, 0.0
        if d in STREAM:
            f = cal["fit"][d]
            t = cal["g_s"][d] + len(cls) * cal["h_s"][d]
            return t, (f["b_W"] + f["P_other_W"]) * t, (f["P_other_W"] * t if d in REMOTE else 0.0)
        des = [S.design(k[0], k[1]) for k in cls]
        gs = [cal["card"][x]["g_s"] for x in set(des) if x in cal["card"]]
        t = max(gs) if gs else 0.0
        e = cal["P_ch_W"] * t
        for x in des:
            if x in cal["card"]:
                t += cal["card"][x]["h_s"]
                e += cal["card"][x]["r"] * cal["P_ch_W"] * cal["card"][x]["h_s"]
            else:
                t += self.latency["card"] + self.CH["card_same"]["t"]
                e += self.CH["card_same"]["dE"]
        if len(set(des)) > 1:
            for a, b in zip(des, des[1:] + des[:1]):
                if a != b:
                    t += self.CH["card_other"]["t"]
                    e += self.CH["card_other"]["dE"]
        return t, e, 0.0

    def device_load(self, d, items):
        """(busy time, energy above idle, coordinator energy in it)"""
        t = sum(self.cost(d, o, m)["t"] for o, m in items)
        e = sum(self.cost(d, o, m)["dE"] for o, m in items)
        cj = sum(self.cost(d, o, m)["coord_J"] for o, m in items)
        ct, ce, cc = self.class_cost(d, {S.ckey(o, m) for o, m in items})
        return t + ct, e + ce, cj + cc

    def combine(self, loads, detail=False):
        """v0.9's combine (stretches s, engaged powers) with the powers of the busy devices adding: no pair term C and
        the coordinator energy of a remote device always paid (selected on the v0.10 check, see the module doc)"""
        w = {d: v[0] for d, v in loads.items() if v[0] > 0}
        pw = {d: loads[d][1] / loads[d][0] for d in w}
        pc = {d: loads[d][2] / loads[d][0] for d in w}
        used = set(w)
        fin = {d: 0.0 for d in loads}
        ov = defaultdict(float)
        A = sorted(w, key=S.DEVICES.index)
        t = E = Ec = Ee = 0.0
        while A:
            sig = {d: math.prod(self.stretch(d, x) for x in A if x != d) for d in A}
            dt = min(w[d] * sig[d] for d in A)
            pairs = list(itertools.combinations(A, 2))
            c = sum(self.C_pair(a, b) for a, b in pairs)
            busy_srv = {S.MACHINE[x] for x in A}
            eg = sum(self.engaged(d) for d in used if d not in A and S.MACHINE[d] not in busy_srv)
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

    def descend(self, opts, deadline=None, init=None, max_iter=100000):
        """v0.9's optimiser (best-improving moves of one option or one class group), on this model's loads"""
        byid = {o["id"]: o for o in opts}
        plan = dict(init) if init else {o["id"]: min(((d, m) for d in self.devices for m in S.METHODS[o["case"]]),
                                                     key=lambda x: self.cost(x[0], o, x[1])["dE"]) for o in opts}
        c = {}
        for o in opts:
            for d in self.devices:
                for m in S.METHODS[o["case"]]:
                    x = self.cost(d, o, m)
                    c[(o["id"], d, m)] = (x["t"], x["dE"], x["coord_J"], S.ckey(o, m))
        st_ = {d: dict(t=0.0, e=0.0, j=0.0, cls=defaultdict(int)) for d in self.devices}

        def add(i, d, m, s):
            t, e, j, k = c[(i, d, m)]
            st_[d]["t"] += s * t; st_[d]["e"] += s * e; st_[d]["j"] += s * j; st_[d]["cls"][k] += s
            if st_[d]["cls"][k] == 0:
                del st_[d]["cls"][k]
        for i, (d, m) in plan.items():
            add(i, d, m, 1)

        def dev_load(d, dt=0.0, de=0.0, dj=0.0, add_k=(), rem_k=()):
            cl = dict(st_[d]["cls"])
            for k in rem_k:
                cl[k] -= 1
                if cl[k] == 0:
                    del cl[k]
            for k in add_k:
                cl[k] = cl.get(k, 0) + 1
            ct, ce, cc = self.class_cost(d, cl.keys())
            return st_[d]["t"] + dt + ct, st_[d]["e"] + de + ce, st_[d]["j"] + dj + cc

        base = {d: dev_load(d) for d in self.devices}

        def obj(loads):
            T, E = self.combine(loads)
            return (max(0.0, T - deadline) * 1e6 if deadline else 0.0) + E

        def try_move(ids, d1, m1):
            d0 = plan[ids[0]][0]
            loads = dict(base)
            r = [c[(i, d0, plan[i][1])] for i in ids]
            a = [c[(i, d1, m1)] for i in ids]
            rt, re, rj = (sum(x[q] for x in r) for q in range(3))
            at, ae, aj = (sum(x[q] for x in a) for q in range(3))
            rk, ak = [x[3] for x in r], [x[3] for x in a]
            if d1 == d0:
                loads[d0] = dev_load(d0, at - rt, ae - re, aj - rj, ak, rk)
            else:
                loads[d0] = dev_load(d0, -rt, -re, -rj, (), rk)
                loads[d1] = dev_load(d1, at, ae, aj, ak, ())
            return obj(loads)

        def apply(ids, d1, m1):
            for i in ids:
                d0, m0 = plan[i]
                add(i, d0, m0, -1)
                add(i, d1, m1, 1)
                plan[i] = (d1, m1)
            for d in self.devices:
                base[d] = dev_load(d)

        cur = obj(base)
        for _ in range(max_iter):
            best = None
            for o in opts:
                d0 = plan[o["id"]]
                for d in self.devices:
                    for m in S.METHODS[o["case"]]:
                        if (d, m) == d0:
                            continue
                        v = try_move([o["id"]], d, m)
                        if v < cur - 1e-9 and (best is None or v < best[0] - 1e-9):
                            best = (v, [o["id"]], d, m)
            groups = defaultdict(list)
            for i, (d, m) in plan.items():
                groups[(d, S.ckey(byid[i], m))].append(i)
            for (d0, k), ids in sorted(groups.items(), key=lambda x: (S.DEVICES.index(x[0][0]), str(x[0][1]))):
                if len(ids) < 2:
                    continue
                for d in self.devices:
                    for m in S.METHODS[k[0]]:
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


def build(p9=None):
    p9 = p9 or v09()
    cal = calibrate(p9)
    return Model11(p9, cal), cal


def main():
    M, cal = build()
    print(f"Supermicro idle {cal['P_idle_W']['smc']:.2f} W (v0.9 {cal['P_idle_smc_v09_W']:.2f}); frodo {cal['P_idle_W']['frodo']:.2f} W")
    for d in STREAM:
        f = cal["fit"][d]
        print(f"{d}: h {cal['h_s'][d] * 1e3:.3f} ms per class, g {cal['g_s'][d] * 1e3:.3f} ms per pass, a {f['a']:.3f}, b {f['b_W']:.2f} W, P_other {f['P_other_W']:.2f} W")
    for x, c in cal["card"].items():
        print(f"card {x}: h {c['h_s'] * 1e3:.2f} ms, g {c['g_s'] * 1e3:.2f} ms, r {c['r']:.3f}")
    print("in-sample (medians of 2 repeats):")
    jobs = load_jobs()
    for name in JOBS:
        js = [j for j in jobs if j["name"] == name]
        j = js[0]
        ev = M.evaluate(j["plan"], j["opts"])
        T = st.median(float(x["row"]["T_pass_s"]) for x in js)
        E = st.median(float(x["row"]["E_pass_J"]) - (cal["P_idle_smc_v09_W"] - cal["P_idle_W"]["smc"]) * 0 for x in js)
        print(f"  {name}: T {T * 1e3:.1f} / {ev['T'] * 1e3:.1f} ms ({T / ev['T']:.3f}); E {E:.2f} / {ev['E']:.2f} J ({E / ev['E']:.3f})")


if __name__ == "__main__":
    main()
