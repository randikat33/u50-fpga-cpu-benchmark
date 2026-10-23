#!/usr/bin/env python3
"""sass_count.py - instruction counts per loop iteration of each GPU pricing kernel, from the compiled SASS
(protocol v0.6, Section 22a: static counts, no GPU run). Runs `cuobjdump -sass` on the binary, finds each
kernel's innermost loops (backward branches whose range holds no other backward branch), and counts the
instructions in each loop body by class:
  fp32  FADD FMUL FFMA FMNMX FSETP FSET FSEL FCHK      (64 per SM per clock on sm_70)
  int   IADD3 IMAD* LOP3 SHF LEA ISETP IMNMX IABS SEL PRMT POPC FLO BREV IMUL MOV  (64; IMAD see note)
  sfu   MUFU.*                                          (16)
  conv  I2F F2I F2F FRND I2I                            (16)
  fp64  DADD DMUL DFMA DSETP DMNMX                      (32)
  mem   LD* ST* (global, local, shared, constant)
  ctrl  BRA BSSY BSYNC EXIT NOP BAR YIELD WARPSYNC CALL RET BMOV
  other everything else
Writes results/gpu_sass/summary.json, summary.md and one file per kernel with its loop bodies.
Usage: python3 scripts/sass_count.py [--bin build/hs_gpu] [--tpb 256]
"""
import argparse, collections, json, os, re, subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "results", "gpu_sass")
KIND = {0: "heston", 1: "basket", 2: "asian"}
SCHEME = {0: "ft", 1: "qe"}
CLASSES = [
    ("sfu", re.compile(r"^MUFU")),
    ("fp64", re.compile(r"^D(ADD|MUL|FMA|SETP|MNMX)")),
    ("conv", re.compile(r"^(I2F|F2I|F2F|FRND|I2I)")),
    ("fp32", re.compile(r"^(FADD|FMUL|FFMA|FMNMX|FSETP|FSET|FSEL|FCHK)")),
    ("mem", re.compile(r"^(LD|ST|ATOM|RED)")),
    ("ctrl", re.compile(r"^(BRA|BSSY|BSYNC|EXIT|NOP|BAR|YIELD|WARPSYNC|CALL|RET|BMOV|JMP|BRX)")),
    ("int", re.compile(r"^(IADD3|IMAD|LOP3|SHF|LEA|ISETP|IMNMX|IABS|SEL|PRMT|POPC|FLO|BREV|IMUL|MOV|IADD|P2R|R2P|PLOP3|S2R|CS2R|UIADD3|UMOV|ULDC|USHF|ULOP3)")),
]


def classify(op):
    for name, rx in CLASSES:
        if rx.match(op):
            return name
    return "other"


def parse(text):
    funcs, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"\s*Function : (\S+)", line)
        if m:
            cur = m.group(1)
            funcs[cur] = []
            continue
        if cur is None:
            continue
        m = re.match(r"\s*(\.L_x_\d+):", line)
        if m:
            funcs[cur].append(("label", m.group(1)))
            continue
        m = re.match(r"\s*/\*([0-9a-f]{4,})\*/\s+(.*?);", line)
        if m:
            ins = m.group(2).strip()
            ins = re.sub(r"^@!?U?P\w+\s+", "", ins)
            funcs[cur].append(("ins", int(m.group(1), 16), ins))
    return funcs


def loops(items):
    """innermost loops: (start, end) indices into the instruction list, from backward branches.
    Branch targets may be labels (BRA `(.L_x_3)) or addresses (BRA 0x1c0), depending on the cuobjdump version."""
    lab_addr, ins_idx, pending = {}, [], []
    for it in items:
        if it[0] == "label":
            pending.append(it[1])
        else:
            for l in pending:
                lab_addr[l] = it[1]
            pending = []
            ins_idx.append(it)
    pos = {it[1]: j for j, it in enumerate(ins_idx)}
    back = []
    for j, it in enumerate(ins_idx):
        if not it[2].startswith("BRA"):
            continue
        m = re.search(r"\(\s*(\.L_x_\d+)\s*\)", it[2])
        tgt = lab_addr.get(m.group(1)) if m else None
        if tgt is None:
            m = re.search(r"BRA\S*\s+(?:`\()?\s*(0x[0-9a-f]+)", it[2])
            tgt = int(m.group(1), 16) if m else None
        if tgt is not None and tgt in pos and pos[tgt] <= j:
            back.append((pos[tgt], j))
    out = []
    for s, e in back:
        if not any((s2, e2) != (s, e) and s <= s2 and e2 <= e for s2, e2 in back):
            out.append((s, e))
    return ins_idx, sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.path.join(ROOT, "build", "hs_gpu"))
    ap.add_argument("--tpb", type=int, default=256)
    a = ap.parse_args()
    text = subprocess.run(["cuobjdump", "-sass", a.bin], capture_output=True, text=True, check=True).stdout
    os.makedirs(OUT, exist_ok=True)
    funcs = parse(text)
    summary, md = [], ["# Instruction counts per innermost loop iteration (SASS, sm_70)", "",
                       "| Kernel | Loop | Instructions | fp32 | int | sfu | conv | fp64 | mem | ctrl | other | MUFU kinds |",
                       "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, items in funcs.items():
        m = re.search(r"price_kernelILi(\d+)ELi(\d+)ELi(\d+)ELi(\d+)E", name)
        if not m or int(m.group(1)) != a.tpb:
            continue
        kind, sch, cv = int(m.group(2)), int(m.group(3)), int(m.group(4))
        label = f"{KIND[kind]}_{'cv' if cv else ('plain' if kind == 2 else SCHEME[sch])}"
        ins, lps = loops(items)
        lines = [f"# {label}  ({name})", f"total instructions: {len(ins)}", ""]
        for li, (s, e) in enumerate(lps):
            body = ins[s:e + 1]
            cnt = collections.Counter(classify(x[2].split()[0]) for x in body)
            mufu = collections.Counter(x[2].split()[0] for x in body if x[2].startswith("MUFU"))
            rec = dict(kernel=label, loop=li, start=hex(body[0][1]), end=hex(body[-1][1]), n=len(body), classes=dict(cnt),
                       mufu=dict(mufu), imad_wide=sum(1 for x in body if x[2].startswith("IMAD.WIDE")),
                       imad_hi=sum(1 for x in body if x[2].startswith("IMAD.HI")))
            summary.append(rec)
            md.append(f"| {label} | {li} ({rec['start']}-{rec['end']}) | {len(body)} | " +
                      " | ".join(str(cnt.get(c, 0)) for c in ("fp32", "int", "sfu", "conv", "fp64", "mem", "ctrl", "other")) +
                      " | " + ", ".join(f"{k} {v}" for k, v in sorted(mufu.items())) + " |")
            lines += [f"## loop {li}: {rec['start']}-{rec['end']}, {len(body)} instructions, {dict(cnt)}"] + [f"  {x[1]:05x}  {x[2]}" for x in body] + [""]
        open(os.path.join(OUT, f"{label}.txt"), "w").write("\n".join(lines) + "\n")
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    open(os.path.join(OUT, "summary.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
