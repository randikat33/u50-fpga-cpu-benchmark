#!/usr/bin/env python3
"""
tune_eval.py - helper for tune_v2.sh (reads Vitis/Vivado reports and host logs).

  tune_eval.py reports <build_dir>            -> key=value lines (clock, slack, utilisation)
  tune_eval.py host <log> <metric>            -> ok=<0|1> value=<float>
  tune_eval.py pick <state_dir>               -> best=<lanes> (highest score among ok variants)
"""
import glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "analysis"))
from fpga_reports_v2 import parse_csynth, parse_timing, parse_util, parse_xclbin_clocks  # noqa: E402


def reports(bdir):
    r = os.path.join(bdir, "reports")
    out = {"xclbin": int(bool([x for x in glob.glob(os.path.join(bdir, "*.xclbin"))
                               if not os.path.basename(x).startswith("partial_")]))}
    fmax = []
    for f in glob.glob(os.path.join(r, "*_csynth.rpt")):
        t, rows = parse_csynth(f, "")
        if t.get("fmax_est_mhz"):
            fmax.append(t["fmax_est_mhz"])
            out["hls_target_mhz"] = round(1000.0 / t["target_ns"], 1)
        out["hls_loops_II_gt_1"] = out.get("hls_loops_II_gt_1", 0) + (t.get("loops_II_gt_1") or 0)
    if fmax:
        out["hls_fmax_est_mhz"] = round(min(fmax), 1)
    tf = sorted(glob.glob(os.path.join(r, "*timing_summary_routed.rpt")))
    if tf:
        d = parse_timing(tf[0])
        for k in ("WNS_ns", "WNS_kernel_clocks_min", "WNS_hbm_aclk", "failing_endpoints",
                  "kernel_clock_mhz", "kernel_clock_period_ns", "kernel_WNS_ns", "kernel_fmax_mhz"):
            if k in d:
                out[k] = d[k]
    uf = sorted(glob.glob(os.path.join(r, "*kernel_util_routed.rpt")))
    if uf:
        for u in parse_util(uf[0]):
            if u["name"] == "Used Resources":
                for k in ("LUT", "REG", "BRAM", "URAM", "DSP"):
                    if k + "_pct" in u:
                        out[f"util_{k}_pct"] = u[k + "_pct"]
        pcts = [v for k, v in out.items() if k.startswith("util_")]
        if pcts:
            out["util_max_pct"] = max(pcts)
    # the routed timing report is the only reliable source for the signed-off kernel clock
    if "kernel_clock_mhz" in out:
        out["achieved_kernel_clock_mhz"] = out["kernel_clock_mhz"]
    xi = os.path.join(r, "xclbin_info.txt")
    if os.path.exists(xi):
        txt = open(xi, errors="replace").read()
        req_hz = [int(h) for h in re.findall(r"--clock\.freqHz\s+(\d+):", txt)]
        if req_hz:
            out["requested_clock_mhz_vpp"] = round(min(req_hz) / 1e6, 1)
        clocks = parse_xclbin_clocks(xi)
        # the kernel clock of THIS design: "System Clocks" -> ulp_ucs_aclk_kernel_00 achieved.
        # (the plain "Clocks" table only lists the platform's clock ids, always 300/500 MHz)
        ach = [v for k, v in clocks.items() if k.endswith("_achieved_mhz") and "kernel_00" in k]
        req = [v for k, v in clocks.items() if k.endswith("_requested_mhz") and "kernel_00" in k]
        if not ach:
            ach = [v for k, v in clocks.items() if k.endswith("_achieved_mhz")]
        if ach:
            out.setdefault("achieved_kernel_clock_mhz", min(ach))
            if req:
                out["kernel_clock_requested_mhz"] = min(req)
        else:
            data = [v for k, v in clocks.items() if "_DATA_" in k]
            if data:
                out.setdefault("achieved_kernel_clock_mhz", min(data))
    for k, v in out.items():
        print(f"{k}={v}")


def host(log, metric):
    ok, val = 0, ""
    try:
        for line in open(log, errors="replace"):
            if line.startswith("RESULT ok="):
                ok = int(float(line.split("=", 1)[1]))
            if line.startswith(f"RESULT {metric}="):
                val = line.split("=", 1)[1].strip()
    except OSError:
        pass
    print(f"ok={ok}")
    print(f"value={val}")


def read_kv(path):
    d = {}
    try:
        for line in open(path):
            if "=" in line:
                k, v = line.strip().split("=", 1)
                d[k] = v
    except OSError:
        pass
    return d


def pick(state):
    best, best_score = None, -1.0
    for f in sorted(glob.glob(os.path.join(state, "L*.result"))):
        d = read_kv(f)
        if d.get("ok") != "1":
            continue
        try:
            s = float(d.get("score", "nan"))
        except ValueError:
            continue
        if s == s and s > best_score:
            best, best_score = int(re.search(r"L(\d+)\.result$", f).group(1)), s
    print(f"best={best if best is not None else ''}")
    print(f"score={best_score if best is not None else ''}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "reports":
        reports(sys.argv[2])
    elif cmd == "host":
        host(sys.argv[2], sys.argv[3])
    elif cmd == "pick":
        pick(sys.argv[2])
    else:
        sys.exit(__doc__)
