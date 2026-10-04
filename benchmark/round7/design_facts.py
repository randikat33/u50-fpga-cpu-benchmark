#!/usr/bin/env python3
"""
design_facts.py - the design inputs of the model, read from the build outputs (no card needed):
  kernel clock (MHz) and CU names   xclbinutil --info  (build/reports/xclbin_info.txt)
  P_viv (W)                         Vivado routed power report, "Total On-Chip Power"
  WNS (ns)                          routed timing summary
  II and pipeline depth             HLS log: "Pipelining result : Target II = x, Final II = y, Depth = z, loop 'L'"
  lanes                             Makefile LANES used for the build (08_MC_Asian)
Usage: design_facts.py --fir <07_FIR dir> --asian <08_MC_Asian dir> --asian-lanes N --out design.json
"""
import argparse, glob, json, os, re, sys


def first(pattern, root):
    f = sorted(glob.glob(os.path.join(root, "**", pattern), recursive=True))
    return f[0] if f else None


def clock_from_link(log, kname):
    """fallback: the requested kernel clock in the v++ link log, lowered by automatic frequency scaling"""
    if not log or not os.path.exists(log):
        return None
    txt = open(log, errors="replace").read()
    f = None
    m = re.search(rf"-clock\.freqHz (\d+):{kname}_1\.ap_clk", txt) or re.search(r"-clock\.defaultFreqHz (\d+)", txt)
    if m:
        f = int(m.group(1)) / 1e6
    for line in txt.splitlines():
        if "AUTO-FREQ-SCALING" in line and "kernel" in line and "exceeds" not in line:
            mm = re.search(r"([\d.]+) MHz", line)
            if mm and (f is None or float(mm.group(1)) < f):
                f = float(mm.group(1))
    return f


def clock_mhz(info):
    if not info or not os.path.exists(info) or os.path.getsize(info) == 0:
        return None
    txt = open(info, errors="replace").read()
    # "Name: DATA_CLK ... Type: DATA ... Frequency: 300 MHz"
    for blk in re.split(r"\n\s*\n", txt):
        if re.search(r"Type:\s*DATA\b", blk):
            m = re.search(r"Frequency:\s*([\d.]+)\s*MHz", blk)
            if m:
                return float(m.group(1))
    m = re.search(r"DATA_CLK.*?Frequency:\s*([\d.]+)\s*MHz", txt, re.S)
    return float(m.group(1)) if m else None


def cu_names(files, kname):
    names = set()
    for f in files:
        if f and os.path.exists(f):
            names |= set(re.findall(rf"\b({kname}_\d+)\b", open(f, errors="replace").read()))
    return sorted(names)


def total_power(rpt):
    m = re.search(r"Total On-Chip Power \(W\)\s*\|\s*([\d.]+)", open(rpt, errors="replace").read())
    return float(m.group(1)) if m else None


def wns(rpt):
    txt = open(rpt, errors="replace").read()
    m = re.search(r"WNS\(ns\)\s+TNS\(ns\).*?\n[-\s]+\n\s*(-?[\d.]+)", txt, re.S)
    return float(m.group(1)) if m else None


def pipelines(root):
    res = {}
    for log in glob.glob(os.path.join(root, "build", "hls", "**", "*.log"), recursive=True):
        for m in re.finditer(r"Pipelining result : Target II = (\d+|NA), Final II = (\d+), Depth = (\d+), loop '([\w.]+)'",
                             open(log, errors="replace").read()):
            res[m.group(4)] = dict(target_ii=m.group(1), ii=int(m.group(2)), depth=int(m.group(3)))
    return res


def facts(root, kname, key_loops):
    rep = os.path.join(root, "build", "reports")
    lnk = os.path.join(root, "build", "link")
    info = os.path.join(rep, "xclbin_info.txt")
    pw = (first("*hw_bb_locked_power_routed.rpt", rep) or first("*power_routed.rpt", rep)
          or first("*hw_bb_locked_power_routed.rpt", lnk) or first("*power_routed.rpt", lnk))
    tm = (first("*hw_bb_locked_timing_summary_routed.rpt", rep) or first("*timing_summary_routed.rpt", rep)
          or first("*timing_summary_routed.rpt", lnk))
    link_log = os.path.join(rep, "v++_link.log") if os.path.exists(os.path.join(rep, "v++_link.log")) \
        else os.path.join(lnk, "link.log")
    if not pw or not os.path.exists(pw):
        sys.exit(f"{root}: missing the routed power report (run: make fpga_reports)")
    pl = pipelines(root)
    loops = {k: v for k, v in pl.items() if any(k.endswith(x) or x in k for x in key_loops)}
    ii = max([v["ii"] for v in loops.values()], default=1)
    f_info, f_link = clock_mhz(info), clock_from_link(link_log, kname)
    # The compute unit runs on the clock requested for it in the link (clock.cfg: freqHz=<f>:<cu>.ap_clk, generated
    # by a clock wizard). That clock is not scalable, so a finished build means it met timing at exactly this
    # frequency. The xclbin's DATA clock (xclbinutil) is the platform's default kernel clock (300 MHz) and is NOT
    # the CU clock when a per-CU frequency was requested, so it is only used when the link log has none.
    d = dict(f_mhz=f_link if f_link else f_info,
             f_source="per-CU clock requested in the v++ link (unscalable, met timing)" if f_link else "xclbinutil --info (DATA clock)",
             f_mhz_link_log=f_link, f_mhz_xclbin_data_clk=f_info, cus=cu_names([info, link_log], kname), P_viv_W=total_power(pw),
             wns_ns=wns(tm) if tm else None, loops=loops, ii=ii,
             ii_source="HLS log" if loops else "assumed 1 (no HLS pipelining message found)",
             reports=dict(info=info, power=pw, timing=tm, link_log=link_log))
    d["n_cu"] = len(d["cus"])
    if d["f_mhz"] is None:
        sys.exit(f"{root}: kernel clock found neither in {info} nor in {link_log}")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fir", required=True)
    ap.add_argument("--asian", required=True)
    ap.add_argument("--asian-lanes", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D = dict(fir=facts(a.fir, "fir_v2", ["FIR_MAIN", "RD", "WR"]),
             asian=facts(a.asian, "mc_asian", ["RING", "MERGE", "SETTLE"]))
    D["asian"]["lanes"] = a.asian_lanes
    ring = D["asian"]["loops"].get("RING") or next((v for k, v in D["asian"]["loops"].items() if "RING" in k), None)
    problems = []
    if ring and ring["depth"] >= 128:
        problems.append(f"MC Asian RING depth {ring['depth']} >= IL=128: results would be wrong, rebuild with AS_IL_LOG2=8")
    for k in ("fir", "asian"):
        if D[k]["ii"] != 1:
            problems.append(f"{k}: a key loop has II={D[k]['ii']} (the prediction divides R_k by it)")
        if D[k]["wns_ns"] is not None and D[k]["wns_ns"] < 0:
            problems.append(f"{k}: routed WNS {D[k]['wns_ns']} ns < 0 (the clock in the xclbin is used)")
    D["problems"] = problems
    json.dump(D, open(a.out, "w"), indent=1)
    print(json.dumps(D, indent=1))
    if any("wrong" in p for p in problems):
        sys.exit(2)


if __name__ == "__main__":
    main()
