#!/usr/bin/env python3
"""
fpga_reports_v2.py - implementation tables for the paper from the Vitis/Vivado reports.

  fpga_reports_v2.py --system results/system --out results/v2/tables

Reads what build_v2.sh / system_info.sh collected:
  system/v2_builds/<NN_project>_reports/*csynth.rpt          (Vitis HLS)
  system/v2_builds/<NN_project>_reports/*timing_summary_routed.rpt, *kernel_util_routed.rpt
  system/xclbin_info/v2_*.txt                                  (xclbinutil --info: achieved clocks)
Writes:
  fpga_hls_v2.csv        one row per module/loop: iteration latency, II, pipelined, resources
  fpga_hls_top_v2.csv    one row per kernel: target/estimated clock (Fmax), top-level resources
  fpga_impl_v2.csv       one row per project: WNS/TNS (kernel & HBM clocks), achieved clock,
                         kernel utilisation (absolute and % of the user budget) per kernel and per CU
"""
import argparse, glob, os, re
import pandas as pd

UTIL_COLS = ["LUT", "LUTAsMem", "REG", "BRAM", "URAM", "DSP"]


def num(s):
    try:
        return float(str(s).strip().split()[0].replace(",", ""))
    except (ValueError, IndexError):
        return None


def parse_csynth(path, project):
    txt = open(path, errors="replace").read()
    top = dict(project=project, report=os.path.basename(path))
    xml = path[:-4] + ".xml"
    if os.path.exists(xml):   # csynth.xml carries the clock numbers
        try:
            import xml.etree.ElementTree as ET
            r = ET.parse(xml).getroot()
            top.update(target_ns=float(r.findtext("UserAssignments/TargetClockPeriod")),
                       uncertainty_ns=float(r.findtext("UserAssignments/ClockUncertainty") or "nan"),
                       estimated_ns=float(r.findtext("PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod")),
                       part=r.findtext("UserAssignments/Part"))
        except Exception:
            pass
    if top.get("estimated_ns"):
        top["fmax_est_mhz"] = 1000.0 / top["estimated_ns"]
    rows = []
    sec = re.search(r"Performance & Resource Estimates:(.*?)(?:\n=+\n|HW Interfaces)", txt, re.S)
    if sec:
        for line in sec.group(1).splitlines():
            line = line.strip()
            if not line.startswith("|") or "Modules" in line or "& Loops" in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 14:
                continue
            name = cells[0]
            kind = {"+": "module", "o": "loop", "*": "dataflow"}.get(name.lstrip()[:1], "")
            depth = len(cells[0]) - len(cells[0].lstrip())
            nm = name.lstrip("+o* ").strip()
            dataflow = nm.endswith("*")
            nm = nm.rstrip("*").strip()
            r = dict(project=project, name=nm, kind="dataflow" if dataflow else kind, issue=cells[1],
                     slack_ns=num(cells[2]), latency_cycles=num(cells[3]), latency_ns=num(cells[4]),
                     iteration_latency=num(cells[5]), II=num(cells[6]), trip_count=num(cells[7]),
                     pipelined=cells[8], BRAM=num(cells[9]), DSP=num(cells[10]), FF=num(cells[11]),
                     LUT=num(cells[12]), URAM=num(cells[13]), report=os.path.basename(path))
            rows.append(r)
        if rows:
            t = rows[0]
            top.update(top_module=t["name"], issue=t["issue"], BRAM=t["BRAM"], DSP=t["DSP"], FF=t["FF"],
                       LUT=t["LUT"], URAM=t["URAM"])
            loops = [r for r in rows if r["kind"] == "loop"]
            top["loops"] = len(loops)
            top["loops_II_gt_1"] = sum(1 for r in loops if r["II"] and r["II"] > 1)
            top["timing_issues"] = sum(1 for r in rows if "Timing" in (r["issue"] or ""))
    return top, rows


def parse_timing(path):
    txt = open(path, errors="replace").read()
    d = {}
    m = re.search(r"Design Timing Summary.*?\n\s*-+.*?\n\s*([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+(\d+)\s+([-\d.]+)", txt, re.S)
    if m:
        d.update(WNS_ns=float(m.group(1)), TNS_ns=float(m.group(2)), failing_endpoints=int(m.group(3)),
                 WHS_ns=float(m.group(5)))
    # the REAL kernel clock of the design is the ULP clocking-wizard output
    # (clk_out1_ulp_clk_wiz_0).  clk_kernel_0x_unbuffered_net are platform nets and keep the
    # platform default period whatever we asked for, so they must not be used.
    wiz = [(float(per), float(mhz)) for _, per, mhz in
           re.findall(r"^\s*(clk_out1_ulp_clk_wiz_\d+)\s+\{[^}]*\}\s+([\d.]+)\s+([\d.]+)", txt, re.M)]
    if wiz:
        per, mhz = min(wiz)
        d["kernel_clock_mhz"] = mhz
        d["kernel_clock_period_ns"] = per
    else:
        # no clock override requested: the CUs run on the platform clock itself
        m2 = re.search(r"^\s*clk_kernel_00_unbuffered_net\s+\{[\d. ]+\}\s+([\d.]+)\s+([\d.]+)", txt, re.M)
        if m2:
            d["kernel_clock_period_ns"] = float(m2.group(1))
            d["kernel_clock_mhz"] = float(m2.group(2))
    intra = re.search(r"Intra Clock Table(.*?)(?:\n\n\n|Inter Clock Table)", txt, re.S)
    if intra:
        for line in intra.group(1).splitlines():
            f = line.split()
            if len(f) >= 3 and (f[0].startswith("clk_kernel") or f[0].startswith("clk_out1_ulp_clk_wiz")
                                or f[0] == "hbm_aclk"):
                try:
                    d[f"WNS_{f[0]}"] = float(f[1])
                except ValueError:
                    pass
    kern = [v for k, v in d.items() if k.startswith("WNS_clk_kernel")]
    if kern:
        d["WNS_kernel_clocks_min"] = min(kern)
    wns = [v for k, v in d.items() if k.startswith("WNS_clk_out1_ulp_clk_wiz")]
    if not wns and "WNS_clk_kernel_00_unbuffered_net" in d:
        wns = [d["WNS_clk_kernel_00_unbuffered_net"]]
    if wns and "kernel_clock_period_ns" in d:
        d["kernel_WNS_ns"] = min(wns)
        d["kernel_fmax_mhz"] = round(1000.0 / (d["kernel_clock_period_ns"] - min(wns)), 1)
    return d


def parse_util(path):
    out = []
    for line in open(path, errors="replace"):
        if not line.startswith("|") or "Name" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 7:
            continue
        r = dict(name=cells[0])
        for col, c in zip(UTIL_COLS, cells[1:]):
            m = re.match(r"([\d.]+)\s*\[\s*([\d.]+)%\]", c)
            if m:
                r[col] = float(m.group(1))
                r[col + "_pct"] = float(m.group(2))
        out.append(r)
    return out


def parse_xclbin_clocks(path):
    """Clocks from 'xclbinutil --info'.

    Two tables matter. The first ("Clocks") lists the PLATFORM's clock ids and is the same for
    every design. The second ("System Clocks") gives, per scalable clock, the frequency this
    design actually requested and achieved - that is the kernel clock to report."""
    txt = open(path, errors="replace").read()
    out = {f"clock_{n}_{t}_mhz": float(f) for n, t, f in re.findall(
        r"Name:\s*(\S+)\s*\n\s*Index:\s*\d+\s*\n\s*Type:\s*(\S+)\s*\n\s*Frequency:\s*([\d.]+)\s*MHz", txt)}
    for n, req, ach in re.findall(
            r"Name:\s*(\S+)\s*\n\s*Type:\s*SCALABLE\s*\n\s*Default Freq:\s*[\d.]+\s*MHz\s*\n"
            r"\s*Requested Freq:\s*([\d.]+)\s*MHz\s*\n\s*Achieved Freq:\s*([\d.]+)\s*MHz", txt):
        out[f"sysclk_{n}_requested_mhz"] = float(req)
        out[f"sysclk_{n}_achieved_mhz"] = float(ach)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tops, loops, impl = [], [], []
    for d in sorted(glob.glob(os.path.join(a.system, "v2_builds", "*_reports"))):
        project = os.path.basename(d)[:-len("_reports")]
        seen = set()
        for f in sorted(glob.glob(os.path.join(d, "*csynth.rpt"))):
            t, rows = parse_csynth(f, project)
            key = t.get("top_module")
            if key in seen:
                continue
            seen.add(key)
            tops.append(t)
            loops += rows
        row = dict(project=project)
        tf = sorted(glob.glob(os.path.join(d, "*timing_summary_routed.rpt")))
        if tf:
            row.update(parse_timing(tf[0]))
        uf = sorted(glob.glob(os.path.join(d, "*kernel_util_routed.rpt")))
        if uf:
            for u in parse_util(uf[0]):
                if u["name"] in ("Platform", "User Budget", "Unused Resources"):
                    continue
                tag = "kernels_total" if u["name"] == "Used Resources" else u["name"]
                for k, v in u.items():
                    if k != "name":
                        row[f"{tag}.{k}"] = v
        xi = os.path.join(d, "xclbin_info.txt")
        if os.path.exists(xi):
            row.update(parse_xclbin_clocks(xi))
        impl.append(row)
    for f in sorted(glob.glob(os.path.join(a.system, "xclbin_info", "v2_*.txt"))):
        name = os.path.basename(f)[:-4]
        c = parse_xclbin_clocks(f)
        if c:
            impl.append(dict(project=f"xclbin:{name}", **c))
    pd.DataFrame(tops).to_csv(os.path.join(a.out, "fpga_hls_top_v2.csv"), index=False)
    pd.DataFrame(loops).to_csv(os.path.join(a.out, "fpga_hls_v2.csv"), index=False)
    pd.DataFrame(impl).to_csv(os.path.join(a.out, "fpga_impl_v2.csv"), index=False)
    print(f"fpga_reports_v2: {len(tops)} kernels, {len(loops)} module/loop rows, {len(impl)} implementation rows -> {a.out}")


if __name__ == "__main__":
    main()
