#!/usr/bin/env python3
"""predict_sched_v09.py - the registered prediction of addendum v0.9: the concurrency-aware scheduler in three regimes.
Written before any v0.9 scheduling run.

Model: scripts/sched_model.py with the concurrency matrix (Model(conc=...), Model.combine): the v0.8 costs, plus
C(d, d') W for every pair of devices busy at the same time and each device's time stretched by s(d|d') while d' is busy.
Inputs: everything v0.8 used (unchanged) + results/concurrency/C_matrix.json (scripts/conc_matrix.py).

Regimes (one book: the v0.8 book of 120 options, seed 20261023):
  R1  four devices, two-server boundary, least energy.
      Policies: alone_card, alone_fcpu, alone_gpu, alone_scpu, model_E, model_T, model_dev (+ oracle at run time).
  R2  the same, with a deadline D = 0.170 s on the pass, below the V100 alone's measured pass (0.205 s, v0.8).
      model_M = least energy with predicted T <= D_plan = D / k, where k = the median measured/predicted pass time of the
      nine v0.8 planned policies (1.059): the v0.8 model_M missed its deadline in 5 of 5 repeats with no margin.
      (+ oracle_M at run time: model_M's algorithm on the oracle's corrected costs, deadline D, no margin.)
      Rule fixed before the matrix was known: D = 0.170 s if the corrected model's shortest pass is at most D_plan and
      model_E's pass is above it (the deadline is feasible and binds); otherwise D = k x the midpoint of the shortest
      pass and model_E's pass (v0.8's rule, with the margin).
  R3  frodo only (card and fcpu; boundary frodo: card + both sockets + DRAM), least energy.
      Policies: alone_card_f, alone_fcpu_f, model_E_f (+ oracle_f at run time).
Output: results/predictions_sched_v09/predictions_sched_v09.json (+ .md, .sha256).
  python3 scripts/predict_sched_v09.py                  the registered prediction (needs results/concurrency/C_matrix.json)
  python3 scripts/predict_sched_v09.py --provisional    a dry run on C_matrix_provisional.json -> predictions_sched_v09_provisional/
"""
import argparse, hashlib, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import predict_sched as P8

RES = S.RES
T_V100_MEASURED = 0.1756          # alone_gpu's measured pass with the v0.9 agent (step 5c timing check, median of 3)
K_MARGIN = 1.02                   # model_M plans to D / 1.02 (step 5c: measured / predicted pass time 0.966-0.996)
CALL = 1.05                                        # v0.7 HC3 calling rule: a predicted margin under 5% is not called
R1 = ("alone_card", "alone_fcpu", "alone_gpu", "alone_scpu", "model_E", "model_T", "model_dev")
R2 = ("model_M",)
R3 = ("alone_card_f", "alone_fcpu_f", "model_E_f")


def model_plans(M, opts, deadline=None):
    """model_E, model_T (and model_M at the deadline) found together, as v0.8: each optimiser run also starts from the
    others' current plans, repeated until model_E stops changing (at most 5 rounds); model_dev = energy above idle"""
    E = M.optimise(opts)
    T = M.min_makespan(opts, inits=[E])
    Mp = M.optimise(opts, deadline=deadline, inits=[E, T]) if deadline else None
    for _ in range(5):
        T = M.min_makespan(opts, inits=[x for x in (E, Mp) if x])
        if deadline:
            Mp = M.optimise(opts, deadline=deadline, inits=[E, T])
        E2 = M.optimise(opts, inits=[x for x in (T, Mp) if x])
        if E2 == E:
            break
        E = E2
    saved = M.Pidle
    M.Pidle = 0.0
    dev = M.optimise(opts)
    M.Pidle = saved
    return E, T, Mp, dev


def pred_of(M, plan, opts):
    ev = M.evaluate(plan, opts)
    return dict(T_s=ev["T"], E_J=ev["E"], busy_alone_s=ev["busy"], finish_s=ev["finish"], dE_J=ev["dE"], n=ev["n"],
                overlap_s=ev["overlap"], E_conc_J=ev["E_conc"], E_engaged_J=ev["E_engaged"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provisional", action="store_true")
    a = ap.parse_args()
    cfile = os.path.join(RES, "concurrency", "C_matrix_provisional.json" if a.provisional else "C_matrix.json")
    efile = os.path.join(RES, "concurrency", "engaged.json")
    gfile = os.path.join(RES, "concurrency", "gaps_v09.json")
    out_dir = os.path.join(RES, "predictions_sched_v09_provisional" if a.provisional else "predictions_sched_v09")
    matrix = json.load(open(cfile))
    eng = json.load(open(efile))
    gaps = json.load(open(gfile))
    # the model's concurrency input (step 5, 5b, 5c): the frodo pair's C and stretch (a socket wake-up, independent of
    # load); engaged power, the Supermicro devices' per-option overhead and frodo's coordinator energy per remote option.
    # The Supermicro pairs' C measured at full load is reported, not used (it over-corrects at the plans' rates).
    conc = dict(C_W={"card+fcpu": matrix["C_W"]["card+fcpu"]}, s={k: matrix["s"][k] for k in ("card|fcpu", "fcpu|card")},
                engaged_W=eng["engaged_W"], overhead_J=eng["overhead_J"], coord_J=eng["coord_J"])
    opts = S.book()
    M = S.Model(conc=conc, gaps=gaps)                        # R1, R2
    A8 = S.Model()                                           # v0.8 additive model, for comparison only
    F = S.Model(conc=conc, devices=("card", "fcpu"), boundary=("frodo",), gaps=gaps)    # R3
    Pl = {f"alone_{d}": M.alone(d, opts) for d in S.DEVICES}
    E, T, _, dev = model_plans(M, opts)
    Pl.update(model_E=E, model_T=T, model_dev=dev)
    tmin = M.evaluate(T, opts)["T"]
    m8 = json.load(open(os.path.join(RES, "analysis_sched", "sched.json")))
    k_T = K_MARGIN
    tE = M.evaluate(E, opts)["T"]
    D = k_T * (tmin + min(tE, T_V100_MEASURED)) / 2
    rule = "1.02 x the midpoint of the shortest predicted pass and the lesser of model_E's pass and the V100 alone's measured pass"
    D_plan = D / k_T
    _, T2, Mp, _ = model_plans(M, opts, deadline=D_plan)
    Pl["model_M"] = Mp
    PF = {"alone_card_f": F.alone("card", opts), "alone_fcpu_f": F.alone("fcpu", opts)}
    PF["model_E_f"] = F.optimise(opts)
    pred = {k: pred_of(M, Pl[k], opts) for k in R1 + R2}
    pred.update({k: pred_of(F, PF[k], opts) for k in R3})
    add = {k: dict(T_s=v["T"], E_J=v["E"]) for k, v in ((k, A8.evaluate(Pl[k], opts)) for k in R1 + R2)}
    # ------------------------------------------------ the v0.8 plans under the corrected model (post hoc, descriptive)
    p8 = json.load(open(os.path.join(RES, "predictions_sched", "predictions_sched.json")))
    retro = {}
    for k, plan in p8["plans"].items():
        pl = {int(i): tuple(x) for i, x in plan.items()}
        ev = M.evaluate(pl, opts)
        retro[k] = dict(T_corr_s=ev["T"], E_corr_J=ev["E"], T_v08_pred_s=p8["predictions"][k]["T_s"], E_v08_pred_J=p8["predictions"][k]["E_J"],
                        T_meas_s=m8["T"][k], E_meas_J=m8["E"][k], E_meas_over_corr=m8["E"][k] / ev["E"], T_meas_over_corr=m8["T"][k] / ev["T"])
    Ep = {k: pred[k]["E_J"] for k in pred}
    r = lambda x, y: Ep[x] / Ep[y]
    call = lambda v: "called" if v >= CALL else "no call"
    same_as_gpu = Pl["model_E"] == Pl["alone_gpu"]
    hyp = dict(
        HS5=dict(text="in each regime, the corrected model policy's measured pass energy is at most 1.05 x the corrected oracle's "
                      "(R1 model_E vs oracle, R2 model_M vs oracle_M, R3 model_E_f vs oracle_f; median over 5 repeats of the per-repeat ratio)",
                 predicted_ratio=1.0, threshold=1.05),
        HS6=dict(text="R1: model_E's measured pass energy is at most 1.02 x alone_gpu's (medians of 5 repeats)",
                 predicted_ratio=r("model_E", "alone_gpu"), threshold=1.02, model_E_is_alone_gpu=same_as_gpu),
        HS7=dict(text="R2: model_M meets the deadline (pass time <= D) in at least 4 of 5 repeats, and its measured pass energy is lower than "
                      "model_T's if that comparison is called (predicted ratio >= 1.05)",
                 deadline_s=D, deadline_rule=rule, planning_deadline_s=D_plan, margin_k=k_T, predicted_T_s=pred["model_M"]["T_s"], predicted_ratio_T_over_M=r("model_T", "model_M"),
                 call=call(r("model_T", "model_M"))),
        HS8=dict(text="R3: model_E_f's measured pass energy is at least 5% lower than each of alone_card_f's and alone_fcpu_f's",
                 predicted_saving={k: 1 - Ep["model_E_f"] / Ep[k] for k in ("alone_card_f", "alone_fcpu_f")}, threshold=0.05,
                 predicted_holds=all(1 - Ep["model_E_f"] / Ep[k] >= 0.05 for k in ("alone_card_f", "alone_fcpu_f"))),
        HS9=dict(text="every planned policy (R1-R3, 11 policies) has measured/predicted pass energy and pass time within 0.90-1.10 (medians of 5)",
                 low=0.90, high=1.10, n=len(pred)),
        HS10=dict(text="R1: model_E's measured pass energy is lower than model_T's if called (predicted ratio >= 1.05) (v0.8 HS4, repeated)",
                  predicted_ratio=r("model_T", "model_E"), call=call(r("model_T", "model_E"))))
    inputs = P8.INPUTS + ["predictions_sched/predictions_sched.json", "analysis_sched/sched.json",
                          os.path.relpath(cfile, RES), os.path.relpath(efile, RES), os.path.relpath(gfile, RES)] + \
             [f for f in ("conc_frodo_summary.csv", "conc_smc2_summary.csv", "conc_cross2_summary.csv", "conc_duty2_summary.csv")]
    out = dict(
        version="0.9" + (" PROVISIONAL DRY RUN - NOT REGISTERED" if a.provisional else ""), book_seed=S.BOOK_SEED, class_seed0=S.CLASS_SEED0,
        concurrency=conc, matrix_measured=dict(C_W=matrix["C_W"], s=matrix["s"], source=matrix.get("source")), engaged_fit=eng,
        gaps_s=gaps,
        idle_W=M.P, P_idle_R1_W=M.Pidle, P_idle_R3_W=F.Pidle, changes=M.CH, t_ov_s=M.t_ov, latency_s=M.latency,
        costs=[dict(device=k[0], case=k[1], target=k[2], method=k[3], **v) for k, v in sorted(M.C.items())],
        regimes=dict(R1=dict(devices=list(S.DEVICES), boundary=["frodo", "smc"], policies=list(R1)),
                     R2=dict(devices=list(S.DEVICES), boundary=["frodo", "smc"], policies=list(R2), deadline_s=D, rule=rule, planning_deadline_s=D_plan, margin_k=k_T),
                     R3=dict(devices=["card", "fcpu"], boundary=["frodo"], policies=list(R3))),
        plans={k: S.plan_json(v) for k, v in {**Pl, **PF}.items()}, predictions=pred, additive_v08_model=add, v08_plans_corrected=retro,
        hypotheses=hyp,
        inputs_sha256={f: hashlib.sha256(open(os.path.join(RES, f), "rb").read()).hexdigest() for f in inputs if os.path.exists(os.path.join(RES, f))},
        code_sha256={f: hashlib.sha256(open(os.path.join(S.ROOT, "scripts", f), "rb").read()).hexdigest()
                     for f in ("sched_model.py", "predict_sched.py", "predict_sched_v09.py", "conc_matrix.py", "conc_engaged.py", "conc_timing.py",
                               "conc_duty.py", "conc_smc.py", "sched_agent.py", "sched_client.py", "sched_queue_v09.py", "analyse_sched_v09.py")})
    os.makedirs(out_dir, exist_ok=True)
    f = os.path.join(out_dir, "predictions_sched_v09.json")
    json.dump(out, open(f, "w"), indent=1, sort_keys=True)
    sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
    open(f + ".sha256", "w").write(f"{sha}  predictions_sched_v09.json\n")
    L = [f"# Addendum v0.9: predicted pass time and energy{' (PROVISIONAL DRY RUN, not registered)' if a.provisional else ''}", "",
         "Model inputs: C " + "; ".join(f"{p} {v:+.1f} W" for p, v in conc["C_W"].items()) + "; stretch " +
         ", ".join(f"{k} {v:.3f}" for k, v in conc["s"].items()) + "; engaged " + ", ".join(f"{k} {v:.1f} W" for k, v in conc["engaged_W"].items()) +
         "; overhead " + ", ".join(f"{k} {v * 1e3:.1f} mJ" for k, v in conc["overhead_J"].items()) + "; coordinator " +
         ", ".join(f"{k} {v * 1e3:.1f} mJ" for k, v in conc["coord_J"].items()) + "; gaps " + ", ".join(f"{k} {v['gap_s'] * 1e3:.3f} ms" for k, v in gaps.items()) + ".", "",
         f"Idle: frodo {M.P['frodo']:.1f} W, Supermicro {M.P['smc']:.1f} W. R2 deadline {D:.4f} s ({rule}); model_M plans to {D_plan:.4f} s (k = {k_T:.3f}).", "",
         "| Regime | Policy | T (s) | E (J) | E, additive v0.8 model (J) | from C (J) | engaged, net of coordinator (J) | options card, fcpu, gpu, scpu |", "|---|---|---|---|---|---|---|---|"]
    for k in R1 + R2 + R3:
        p = pred[k]
        reg = "R1" if k in R1 else "R2" if k in R2 else "R3"
        ea = f"{add[k]['E_J']:.1f}" if k in add else "-"
        L.append(f"| {reg} | {k} | {p['T_s']:.4f} | {p['E_J']:.2f} | {ea} | {p['E_conc_J']:+.2f} | {p['E_engaged_J']:+.2f} | {', '.join(str(p['n'].get(d, 0)) for d in S.DEVICES)} |")
    L += ["", "## Hypotheses: predicted values", ""]
    for k, h in hyp.items():
        extra = {kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in h.items() if kk != "text"}
        L.append(f"- **{k}** {h['text']}. Predicted: {extra}")
    L += ["", "## The v0.8 plans under the corrected model (post hoc; the v0.8 runs are not a test of v0.9)", "",
          "| Policy | E v0.8 pred (J) | E corrected (J) | E measured (J) | meas / corrected | T v0.8 pred (s) | T corrected (s) | T measured (s) | meas / corrected |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k, x in retro.items():
        L.append(f"| {k} | {x['E_v08_pred_J']:.1f} | {x['E_corr_J']:.1f} | {x['E_meas_J']:.1f} | {x['E_meas_over_corr']:.3f} | {x['T_v08_pred_s']:.4f} | "
                 f"{x['T_corr_s']:.4f} | {x['T_meas_s']:.4f} | {x['T_meas_over_corr']:.3f} |")
    L += ["", f"sha256 {sha}"]
    open(os.path.join(out_dir, "predictions_sched_v09.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
