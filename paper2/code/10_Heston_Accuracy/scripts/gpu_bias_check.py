#!/usr/bin/env python3
"""gpu_bias_check.py - the GPU bias check of protocol v0.6, Section 22 ("GPU bias check").

Run after the GPU measurements; the protocol puts it before them, so the paper reports this order as a deviation.
For each of the 36 settings (results/settings.csv, results/settings_s.csv, empirical rule) the registered GPU program
(build/hs_gpu, sha256 checked; not rebuilt) prices the setting's time step with 12.5 x the setting's pairs, as the
CPU-native check does (standard error <= eps/5 at every strike). It reports per strike:
  - the GPU bias (price - reference) and its standard error;
  - whether |bias| exceeds the bias budget eps/sqrt(2) (to be reported; the setting is not changed);
  - its distance from the matched code's calibrated bias and from the CPU-native bias, in combined standard errors;
  - the GPU's predicted RMSE at the setting, sqrt(b^2 + sigma^2 / N) / eps.
Accuracy only: no time or energy is an outcome. One GPU run per setting (--timed, one run, no warm-up or window).
Seeds 9001-9006 (not used by any other run). Output: results/gpu_bias/<setting>.txt, results/gpu_bias_check.csv.
It continues where it stopped if started again.

Usage (from the project folder; clock locked as in the measurement sessions):
    python3 scripts/gpu_bias_check.py --plan
    nohup python3 scripts/gpu_bias_check.py > results/gpu_bias_check.log 2>&1 &
"""
import argparse, csv, hashlib, math, os, re, subprocess, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "gpu_bias")
BIN = os.path.join(ROOT, "build", "hs_gpu")
BIN_SHA = "fbddf8db898f232d7889f96ad7ed86ecc03b0b562c642bfb2454c8bd946f4bf9"
FACTOR = 12.5
SEED = {"I": 9001, "II": 9002, "III": 9003, "P1": 9004, "B": 9005, "A": 9006}
RATE = {"ft": 2.0e11, "qe": 9.0e10, "plain": 3.3e11, "cv": 2.7e11}        # GPU path-steps per s (plan only)
RATE_B = {"ft": 2.3e10, "qe": 1.0e10}


def settings():
    rows = []
    for f in ("settings.csv", "settings_s.csv"):
        for r in csv.DictReader(open(os.path.join(RES, f))):
            if r["rule"] == "empirical" and r.get("steps"):
                r["M"], r["N"] = int(r["steps"]), int(r["pairs"])
                r["N_check"] = int(math.ceil(FACTOR * r["N"] / 256.0)) * 256
                r["key"] = f"{r['case']}_{r['scheme']}_{r['target'].replace('%', 'pct')}"
                rows.append(r)
    return rows


def native():
    out = {}
    for f in ("native_check.csv", "native_check_s.csv"):
        p = os.path.join(RES, f)
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                out[(r["case"], r["target"], r["scheme"])] = r
    return out


def parse(path):
    if not os.path.exists(path):
        return None
    res = dict(re.findall(r"^RESULT (\w+)=(\S+)", open(path).read(), re.M))
    return res if res.get("ok") == "1" else None


def secs(r):
    w = 2.0 * r["N_check"] * r["M"]
    return 2 * w / (RATE_B[r["scheme"]] if r["case"] == "B" else RATE[r["scheme"]]) + 1.0


def sha(p):
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def summary(rows):
    nat = native()
    out = []
    for r in rows:
        res = parse(os.path.join(OUT, r["key"] + ".txt"))
        if not res:
            continue
        eps, n = float(r["eps"]), float(res["pairs"])
        row = dict(case=r["case"], target=r["target"], scheme=r["scheme"], steps=r["M"], pairs_setting=r["N"], pairs_check=int(n), eps=eps)
        worst, zm, zn, over = 0.0, 0.0, 0.0, 0
        for k in range(3):
            b, se = float(res[f"bias{k}"]), float(res[f"se{k}"])
            rmse = math.sqrt(b * b + se * se * (n - 1) / r["N"]) / eps
            bm, sem = float(r[f"bias{k}"]), float(r[f"bias_se{k}"])
            z_m = (b - bm) / math.hypot(se, sem)
            nr = nat.get((r["case"], r["target"], r["scheme"]))
            z_n = (b - float(nr[f"bias_native{k}"])) / math.hypot(se, float(nr[f"se_native{k}"])) if nr else float("nan")
            row.update({f"bias_gpu{k}": b, f"se_gpu{k}": se, f"bias_over_eps{k}": b / eps, f"z_matched{k}": z_m, f"z_native{k}": z_n,
                        f"rmse_over_eps{k}": rmse})
            worst, zm = max(worst, rmse), max(zm, abs(z_m))
            zn = max(zn, abs(z_n)) if not math.isnan(z_n) else zn
            over += abs(b) > eps / math.sqrt(2)
        row.update(rmse_over_eps_max=worst, z_matched_abs_max=zm, z_native_abs_max=zn, strikes_bias_over_budget=over,
                   meets_target=int(worst <= 1.0))
        out.append(row)
    if out:
        f = os.path.join(RES, "gpu_bias_check.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0]))
            w.writeheader(); w.writerows(out)
        print(f"\n{'setting':<16} {'pairs':>12} {'RMSE/eps':>9} {'|z| matched':>11} {'|z| native':>10} {'bias>budget':>11}")
        for o in out:
            print(f"{o['case'] + ' ' + o['scheme'] + ' ' + o['target']:<16} {o['pairs_check']:>12,} {o['rmse_over_eps_max']:>9.3f} "
                  f"{o['z_matched_abs_max']:>11.2f} {o['z_native_abs_max']:>10.2f} {o['strikes_bias_over_budget']:>11}")
        print(f"\n{len(out)} of {len(rows)} settings done; meets target {sum(o['meets_target'] for o in out)}; "
              f"bias over eps/sqrt2 at {sum(o['strikes_bias_over_budget'] > 0 for o in out)} settings; written {f}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    rows = settings()
    left = [r for r in rows if not parse(os.path.join(OUT, r["key"] + ".txt"))]
    if a.plan:
        print(f"{len(left)} of {len(rows)} settings left; about {sum(secs(r) for r in left) / 60:.1f} min of GPU time")
        print("binary sha256 ok" if sha(BIN) == BIN_SHA else "BINARY SHA256 DIFFERS: do not run")
        return
    if a.summary:
        summary(rows)
        return
    if sha(BIN) != BIN_SHA:
        sys.exit("STOP: build/hs_gpu is not the registered binary (sha256 differs)")
    os.makedirs(OUT, exist_ok=True)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"GPU bias check: {len(left)} of {len(rows)} settings left", flush=True)
    for r in left:
        cmd = [BIN, "--case", r["case"], "--scheme", r["scheme"], "--steps", str(r["M"]), "--a0", "0", "--a1", str(r["N_check"]),
               "--seed", str(SEED[r["case"]]), "--sync", "block", "--timed", "--runs", "1", "--warmup-s", "0", "--window-s", "0"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        path = os.path.join(OUT, r["key"] + ".txt")
        open(path, "w").write(" ".join(cmd) + "\n" + p.stdout + p.stderr)
        res = parse(path)
        print(time.strftime("%Y-%m-%d %H:%M:%S"), r["key"], "ok" if res else f"FAILED (rc {p.returncode})", flush=True)
    out = summary(rows)
    print("GPU BIAS CHECK FINISHED" if len(out) == len(rows) else "GPU BIAS CHECK INCOMPLETE (run again to continue)")


if __name__ == "__main__":
    main()
