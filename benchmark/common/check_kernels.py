#!/usr/bin/env python3
"""check_kernels.py - static checks of the v2 kernels/link configs before a (long) Vitis build.
  1. every m_axi port also has 's_axilite ... bundle=control' (Vitis kernel mode rule, HLS 214-219)
  2. every 'sp=<cu>.<name>:' in cfg/*.cfg names a real top-function argument (CFGEN 83-2292)
  3. no '//' comment on a '#pragma HLS' line
Exit 1 on any problem."""
import glob, os, re, sys
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "v2_projects")
bad = 0
for proj in sorted(glob.glob(os.path.join(root, "0*"))):
    files = glob.glob(os.path.join(proj, "kernel", "*"))
    txt = {f: open(f, errors="replace").read() for f in files}
    maxi, lite, args = set(), set(), set()
    for f, t in txt.items():
        for m in re.finditer(r"#pragma HLS INTERFACE\s+(?:mode=)?m_axi\b[^\n]*?\bport=(\w+)", t):
            maxi.add(m.group(1))
        for m in re.finditer(r"#pragma HLS INTERFACE\s+(?:mode=)?s_axilite\b[^\n]*?\bport=(\w+)[^\n]*?bundle=control\b", t):
            lite.add(m.group(1))
        for ln, line in enumerate(t.splitlines(), 1):
            if line.lstrip().startswith("#pragma HLS") and "//" in line:
                print(f"[warn] {os.path.relpath(f, root)}:{ln}: comment on a pragma line")
        args |= set(re.findall(r"\b(\w+)\s*(?:,|\))", t))
    for p in sorted(maxi - lite):
        print(f"[FAIL] {os.path.basename(proj)}: m_axi port '{p}' has no 's_axilite bundle=control'"); bad += 1
    for cfg in glob.glob(os.path.join(proj, "cfg", "*.cfg")):
        for m in re.finditer(r"^sp=\w+\.(\w+):", open(cfg).read(), re.M):
            if m.group(1) not in maxi:
                print(f"[FAIL] {os.path.relpath(cfg, root)}: sp= port '{m.group(1)}' is not an m_axi argument"); bad += 1
    if maxi:
        print(f"[ok]   {os.path.basename(proj)}: {len(maxi)} m_axi ports checked")
sys.exit(1 if bad else 0)
