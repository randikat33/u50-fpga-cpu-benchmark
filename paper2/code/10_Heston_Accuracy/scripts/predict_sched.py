#!/usr/bin/env python3
"""predict_sched.py - the registered prediction of addendum v0.8: the book, the cost model's inputs, every policy's plan
and its predicted pass time and energy, and the hypotheses' predicted values. Written before any scheduling run.
Output: results/predictions_sched/predictions_sched.json (+ .md, .sha256)
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S

RES = S.RES
OUT = os.path.join(RES, "predictions_sched")
INPUTS = ["settings.csv", "settings_s.csv", "phase6_summary_s1.csv", "phase6_summary_s2.csv", "phase5g_summary_s1.csv", "phase5g_summary_s2.csv",
          "sched_inputs_frodo_summary.json", "sched_inputs_smc_summary.json", "sched_inputs_serve.json"]
POLICIES = ("alone_card", "alone_fcpu", "alone_gpu", "alone_scpu", "round_robin", "model_E", "model_T", "model_M", "model_dev")


def plans(M, opts):
    """the planned policies. model_E, model_T and model_M are found together: each optimiser run also starts from the
    other two's current plans, repeated until none changes (at most 5 rounds)"""
    P = {f"alone_{d}": M.alone(d, opts) for d in S.DEVICES}
    P["round_robin"] = M.round_robin(opts)
    E, T, Mp = M.optimise(opts), M.min_makespan(opts), None
    for _ in range(5):
        T = M.min_makespan(opts, inits=[x for x in (E, Mp) if x])
        D = (M.evaluate(E, opts)["T"] + M.evaluate(T, opts)["T"]) / 2
        Mp = M.optimise(opts, deadline=D, inits=[E, T])
        E2 = M.optimise(opts, inits=[T, Mp])
        if E2 == E:
            break
        E = E2
    D = (M.evaluate(E, opts)["T"] + M.evaluate(T, opts)["T"]) / 2
    P["model_E"], P["model_T"], P["model_M"], P["_deadline_M"] = E, T, M.optimise(opts, deadline=D, inits=[E, T]), D
    saved = M.Pidle
    M.Pidle = 0.0
    P["model_dev"] = M.optimise(opts)
    M.Pidle = saved
    return P


def main():
    M = S.Model()
    opts = S.book()
    P = plans(M, opts)
    pred = {}
    for k in POLICIES:
        ev = M.evaluate(P[k], opts)
        pred[k] = dict(T_s=ev["T"], E_J=ev["E"], busy_s=ev["busy"], dE_J=ev["dE"], n=ev["n"])
    E = {k: pred[k]["E_J"] for k in POLICIES}
    call = lambda r: "lower" if r >= 1.05 else "no call"        # as v0.7 HC3: a predicted margin under 5% is not called
    hs2 = {k: E[k] / E["model_E"] for k in ("alone_card", "alone_fcpu", "alone_gpu", "alone_scpu", "round_robin")}
    hs4 = {k: E[k] / E["model_E"] for k in ("model_T", "model_dev")}
    hyp = dict(
        HS1=dict(text="model_E's measured pass energy is at most 1.05 x the oracle's (median over the 5 repeats of the per-repeat ratio)",
                 predicted_ratio=1.0, threshold=1.05),
        HS2=dict(text="model_E's measured pass energy (median of 5 repeats) is lower than that of each policy called 'lower' "
                      "(predicted ratio >= 1.05); the others are reported only",
                 predicted=hs2, calls={k: call(v) for k, v in hs2.items()}),
        HS3=dict(text="for each of the 9 planned policies, measured/predicted pass energy and pass time (medians of 5 repeats) are within 0.85-1.15",
                 low=0.85, high=1.15),
        HS4=dict(text="model_E's measured pass energy (median) is lower than that of each policy called 'lower' among model_T and model_dev",
                 predicted=hs4, calls={k: call(v) for k, v in hs4.items()}))
    tmin = pred["model_T"]["T_s"]
    out = dict(
        version="0.8", book_seed=S.BOOK_SEED, class_seed0=S.CLASS_SEED0, mix=S.MIX,
        book=[dict(id=o["id"], case=o["case"], target=o["target"], eps=o["eps"], set=o["set"]) for o in opts],
        idle_W=M.P, P_idle_total_W=M.Pidle, check_gaps=S.check_gaps(opts), changes=M.CH, t_ov_s=M.t_ov, latency_s=M.latency,
        costs=[dict(device=k[0], case=k[1], target=k[2], method=k[3], **v) for k, v in sorted(M.C.items())],
        plans={k: S.plan_json(P[k]) for k in POLICIES}, predictions=pred, hypotheses=hyp, deadline_M_s=P["_deadline_M"],
        deadlines_dropped=dict(T_model_E=pred["model_E"]["T_s"], T_min=tmin, gap=pred["model_E"]["T_s"] / tmin - 1),
        inputs_sha256={f: hashlib.sha256(open(os.path.join(RES, f), "rb").read()).hexdigest() for f in INPUTS if os.path.exists(os.path.join(RES, f))},
        code_sha256={f: hashlib.sha256(open(os.path.join(S.ROOT, "scripts", f), "rb").read()).hexdigest()
                     for f in ("sched_model.py", "predict_sched.py", "sched_run.py", "sched_queue.py", "sched_client.py", "sched_agent.py", "sched_inputs_serve.py")})
    os.makedirs(OUT, exist_ok=True)
    f = os.path.join(OUT, "predictions_sched.json")
    json.dump(out, open(f, "w"), indent=1, sort_keys=True)
    sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
    open(f + ".sha256", "w").write(f"{sha}  predictions_sched.json\n")
    L = ["# Addendum v0.8: predicted pass time and energy per policy", "",
         f"Idle power: frodo {M.P['frodo']:.1f} W, Supermicro {M.P['smc']:.1f} W (two-server {M.Pidle:.1f} W).", "",
         "| Policy | T (s) | E (J) | E / model_E | options card, fcpu, gpu, scpu |", "|---|---|---|---|---|"]
    for k in POLICIES:
        p = pred[k]
        L.append(f"| {k} | {p['T_s']:.3f} | {p['E_J']:.1f} | {p['E_J'] / E['model_E']:.2f} | {', '.join(str(p['n'][d]) for d in S.DEVICES)} |")
    L += ["", f"model_E's T is {out['deadlines_dropped']['gap'] * 100:.1f}% above the shortest pass found ({tmin:.3f} s); model_M's deadline {P['_deadline_M']:.4f} s.",
          "", f"sha256 {sha}"]
    open(os.path.join(OUT, "predictions_sched.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
