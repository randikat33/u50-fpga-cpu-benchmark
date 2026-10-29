#!/usr/bin/env python3
"""predict_sched_v11.py - the registered prediction of addendum v0.11: the calibrated scheduling model
(scripts/calib_model_v11.py: the registered v0.9 model with the stream costs re-measured in the v0.11 calibration runs)
applied to three books. Written after the calibration and the v0.10 check, before any v0.11 run.

Books (options per case at 1% / 0.3% / 0.1%, six cases, 120 options, book seed 20261023 as v0.8-v0.10):
  N3  10 / 10 / 0    new (no run of this book exists): primary
  N2   6 / 12 / 2    new: primary
  S   20 /  0 / 0    the v0.10 check's book S (seen: 1 repeat, 3 s): secondary
Policies per book:
  alone_gpu   every option on the V100 (its lower-energy method)
  model_E     the v0.11 model's least-energy plan over the four devices
  plan_v09    the plan the registered v0.9 model chooses for the book (its model_E): what the calibration changes
Two-server boundary. The model, the plans and the predictions are deterministic.
Output: results/predictions_sched_v11/predictions_sched_v11.json (+ .md, .sha256).
  python3 scripts/predict_sched_v11.py
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sched_model as S
import calib_model_v11 as CM

RES = S.RES
BOOKS = {"N3": (10, 10, 0), "N2": (6, 12, 2), "S": (20, 0, 0)}
PRIMARY = ("N3", "N2")
POLICIES = ("alone_gpu", "model_E", "plan_v09")
INFO_BOOKS = {"S2": (16, 4, 0), "M": (8, 8, 4), "L": (0, 0, 20)}     # predicted for the record, not run in v0.11
SHA = lambda f: hashlib.sha256(open(f, "rb").read()).hexdigest()


def main():
    p9 = CM.v09()
    M, cal = CM.build(p9)
    M9 = S.Model.from_prediction(p9)
    pred, plans, books = {}, {}, {}
    for b, mix in list(BOOKS.items()) + list(INFO_BOOKS.items()):
        opts = S.book(mix)
        pl = {"alone_gpu": M.alone("gpu", opts), "model_E": M.optimise(opts), "plan_v09": M9.optimise(opts)}
        for k, v in pl.items():
            ev = M.evaluate(v, opts)
            pred[f"{b}/{k}"] = dict(T_s=ev["T"], E_J=ev["E"], n=ev["n"], busy_s=ev["busy"], dE_J=ev["dE"], E_engaged_J=ev["E_engaged"],
                                    finish_s=ev["finish"])
            plans[f"{b}/{k}"] = S.plan_json(v)
        books[b] = dict(mix_per_case=dict(zip(("1%", "0.3%", "0.1%"), mix)), options=len(opts), run=b in BOOKS, primary=b in PRIMARY,
                        ratio_model_E_over_alone_gpu=pred[f"{b}/model_E"]["E_J"] / pred[f"{b}/alone_gpu"]["E_J"],
                        ratio_model_E_over_plan_v09=pred[f"{b}/model_E"]["E_J"] / pred[f"{b}/plan_v09"]["E_J"])
    r = {b: books[b]["ratio_model_E_over_alone_gpu"] for b in BOOKS}
    q = {b: books[b]["ratio_model_E_over_plan_v09"] for b in BOOKS}
    hyp = dict(
        HC1=dict(kind="primary", text="book N3: model_E's measured pass energy is at most 0.96 x alone_gpu's (medians of 5 repeats)",
                 book="N3", threshold=0.96, predicted_ratio=r["N3"]),
        HC2=dict(kind="primary", text="books N3 and N2: the v0.11 plan uses less energy than the v0.9 model's plan for the same book: measured "
                                      "E(model_E) / E(plan_v09) at most 0.95 in both", books=["N3", "N2"], threshold=0.95, predicted_ratio=q),
        HC3=dict(kind="primary", text="books N3 and N2: every policy (6 jobs) has measured / predicted pass energy and pass time within 0.90-1.10",
                 books=["N3", "N2"], low=0.90, high=1.10),
        HC4=dict(kind="primary", text="book N2 (with 0.1% options): model_E saves less than 5% against alone_gpu (measured ratio at least 0.95): "
                                      "the model predicts no worthwhile saving", book="N2", threshold=0.95, predicted_ratio=r["N2"]),
        HC5=dict(kind="primary", text="the measured ratio model_E / alone_gpu increases across the books in the predicted order",
                 predicted_order=sorted(r, key=r.get), predicted_ratios=r),
        HC6=dict(kind="secondary", text="book S (seen in the v0.10 check): model_E's measured pass energy is at most 0.85 x alone_gpu's, and its "
                                        "three jobs are within 0.90-1.10 of the predicted pass energy and time", book="S", threshold=0.85,
                 low=0.90, high=1.10, predicted_ratio=r["S"]))
    code = {f: SHA(os.path.join(S.ROOT, "scripts", f)) for f in ("sched_model.py", "calib_model_v11.py", "predict_sched_v11.py", "sched_queue_v11t.py",
                                                                  "analyse_sched_v11.py", "sched_run.py", "sched_agent.py", "sched_client.py")
            if os.path.exists(os.path.join(S.ROOT, "scripts", f))}
    inputs = {os.path.relpath(f, S.ROOT): SHA(f) for f in (CM.V09, CM.CAL_SUMMARY, os.path.join(RES, "sched_calib_v11_events.log"))}
    doc = dict(version="0.11", model="calib_model_v11.Model11 (registered v0.9 model + v0.11 calibration)", v09_prediction_sha256=CM.V09_SHA,
               calibration=cal, book_seed=S.BOOK_SEED, class_seed0=S.CLASS_SEED0, books=books, policies=list(POLICIES), boundary=["frodo", "smc"],
               plans=plans, predictions=pred, hypotheses=hyp, code_sha256=code, inputs_sha256=inputs)
    od = os.path.join(RES, "predictions_sched_v11")
    os.makedirs(od, exist_ok=True)
    f = os.path.join(od, "predictions_sched_v11.json")
    json.dump(doc, open(f, "w"), indent=1, sort_keys=True, default=float)
    sha = SHA(f)
    open(f + ".sha256", "w").write(f"{sha}  predictions_sched_v11.json\n")
    L = ["# Addendum v0.11: predicted pass time and energy (the calibrated model)", "",
         f"Model: the registered v0.9 model (SHA-256 {CM.V09_SHA[:8]}...) with the v0.11 calibration. Two-server boundary.", "",
         "| Book (1% / 0.3% / 0.1%) | Policy | T (ms) | E (J) | Options card, fcpu, gpu, scpu | / alone_gpu | model_E / plan_v09 |", "|---|---|---|---|---|---|---|"]
    for b, mix in list(BOOKS.items()) + list(INFO_BOOKS.items()):
        for k in POLICIES:
            p = pred[f"{b}/{k}"]
            ra = p["E_J"] / pred[f"{b}/alone_gpu"]["E_J"]
            L.append(f"| {b} ({' / '.join(map(str, mix))}){'' if b in BOOKS else ' (not run)'} | {k} | {p['T_s'] * 1e3:.1f} | {p['E_J']:.2f} | "
                     f"{', '.join(str(p['n'].get(d, 0)) for d in S.DEVICES)} | {ra:.3f} | {books[b]['ratio_model_E_over_plan_v09']:.3f} |" if k == "model_E" else
                     f"| {b} ({' / '.join(map(str, mix))}){'' if b in BOOKS else ' (not run)'} | {k} | {p['T_s'] * 1e3:.1f} | {p['E_J']:.2f} | "
                     f"{', '.join(str(p['n'].get(d, 0)) for d in S.DEVICES)} | {ra:.3f} | |")
    L += ["", "## Hypotheses", ""] + [f"- **{k}** ({h['kind']}) {h['text']}." for k, h in hyp.items()] + ["", f"sha256 {sha}"]
    open(os.path.join(od, "predictions_sched_v11.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
