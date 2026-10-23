#!/usr/bin/env python3
"""gpu_counts.py - dynamic instruction counts per pair-step and per pair of each GPU pricing kernel, from the SASS
of the built binary (protocol v0.6, Section 22a). No GPU run.

Reading of Section 22a (stated in the GPU prediction file):
  * Loops: the innermost loop is the time-step loop (one pair-step per iteration; both sides of the antithetic
    pair). In Case B the next loop out is the asset loop (8 per pair), then the pair loop; elsewhere the next loop
    out is the pair loop. Work outside the step loop is counted per asset (B) or per pair.
  * Execution frequency of a basic block in the step loop:
      - cuRAND Philox4_32_10 refill (a block with >= 10 Philox multiplier constants, and the counter-increment
        block before it): 1/4 per word drawn (one refill gives 4 words); the counter-carry block: 0;
      - the word-select switch of each curand() call: A + 3/4 B + 1/2 C + 1/4 (D + E) + F (average of the four
        buffer positions);
      - a region skipped every other step (the normal pair drawn every second step: QE spot normals, Asian dates):
        1/2, including its curand() calls;
      - all other blocks, including both branches of QE and of normcdfinvf: 1 (full divergence in a warp of 32).
    Outside the step loop every instruction counts once.
  * Pipes (sm_70; thread instructions per SM per clock): FMA pipe 64 (FFMA FMUL FADD IMAD*; IMAD.WIDE counts 2),
    ALU 64 (IADD3 LOP3 SHF ISETP FSETP FMNMX FSEL SEL LEA MOV PRMT P2R R2P ...), XU 16 (MUFU, conversions),
    FP64 32 (DADD DMUL DFMA DSETP), and an issue limit of 128 (4 warp instructions per clock) for all.
Writes results/gpu_counts.json.
Usage: python3 scripts/gpu_counts.py --sass build/hs_gpu.sass
"""
import argparse, collections, json, os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PHILOX = ("-0x2daee0ad", "-0x326172a9")
KERNELS = {(0, 0, 0): "heston_ft", (0, 1, 0): "heston_qe", (1, 0, 0): "basket_ft", (1, 1, 0): "basket_qe",
           (2, 0, 0): "asian_plain", (2, 0, 1): "asian_cv"}


def pipe(op):
    o = op.split(".")[0]
    if o.startswith("MUFU") or o in ("I2F", "F2I", "F2F", "FRND", "I2I"):
        return "xu"
    if o in ("DADD", "DMUL", "DFMA", "DSETP", "DMNMX"):
        return "fp64"
    if o in ("FFMA", "FMUL", "FADD") or o.startswith("IMAD"):
        return "fma"
    if o in ("BRA", "BSSY", "BSYNC", "EXIT", "NOP", "BAR", "YIELD", "WARPSYNC", "CALL", "RET", "BMOV", "JMP", "BRX", "SHFL"):
        return "ctrl"
    if o.startswith(("LD", "ST", "ATOM", "RED")):
        return "mem"
    return "alu"


def vec(instrs, w=1.0):
    v = collections.Counter()
    for t in instrs:
        op = t.split()[0]
        p = pipe(op)
        v[p] += w * (2 if op.startswith("IMAD.WIDE") else 1)
        v["issue"] += w
    return v


def parse(path):
    F, cur = {}, None
    for line in open(path):
        m = re.match(r"\s*Function : (\S+)", line)
        if m:
            cur = m.group(1); F[cur] = []; continue
        if cur is None:
            continue
        m = re.match(r"\s*/\*([0-9a-f]{4,})\*/\s+(.*?);", line)
        if m:
            t = re.sub(r"^@!?U?P\w+\s+", "", m.group(2).strip())
            F[cur].append((int(m.group(1), 16), t))
    return F


def back_edges(ins):
    out = []
    for a, t in ins:
        m = re.search(r"BRA\S*\s+(0x[0-9a-f]+)", t)
        if m and int(m.group(1), 16) < a:
            out.append((int(m.group(1), 16), a))
    return sorted(out, key=lambda x: x[1] - x[0])


def blocks(body):
    addrs = [a for a, _ in body]
    lead = {addrs[0]}
    for a, t in body:
        m = re.search(r"BRA\S*\s+(0x[0-9a-f]+)", t)
        if m:
            tg = int(m.group(1), 16)
            if addrs[0] <= tg <= addrs[-1]:
                lead.add(tg)
            if a + 0x10 <= addrs[-1]:
                lead.add(a + 0x10)
    out, cur = [], []
    for a, t in body:
        if a in lead and cur:
            out.append(cur); cur = []
        cur.append((a, t))
    out.append(cur)
    return out


def step_loop(body):
    """frequency of each block of the step loop -> instruction vector per pair-step"""
    B = blocks(body)
    nph = [sum(any(c in t for c in PHILOX) for _, t in b) for b in B]
    # regions skipped every other step: forward branch whose skipped blocks contain a Box-Muller (MUFU.SIN)
    half = set()
    for i, b in enumerate(B):
        m = re.search(r"BRA\S*\s+(0x[0-9a-f]+)", b[-1][1])
        if not m:
            continue
        tg = int(m.group(1), 16)
        skipped = [j for j, c in enumerate(B) if c[0][0] > b[-1][0] and c[0][0] < tg]
        if skipped and any("MUFU.SIN" in t for j in skipped for _, t in B[j]) and len(skipped) > 3:
            half.update(skipped)
    freq = [0.5 if i in half else 1.0 for i in range(len(B))]
    # cuRAND calls: refill block (>= 10 Philox constants), counter block before it, carry block between
    calls = [i for i, n in enumerate(nph) if n >= 10]
    for i in calls:
        f = freq[i]
        freq[i] = f / 4                                    # refill
        j = i - 1
        if j >= 0 and nph[j] == 0 and len(B[j]) <= 10:      # counter carry
            freq[j] = 0.0
            j -= 1
        if j >= 0 and nph[j] >= 1:                          # counter increment
            freq[j] = f / 4
            j -= 1
        sel = []                                            # select switch: small blocks before, back to the previous call or loop start
        while j >= 0 and nph[j] == 0 and len(B[j]) <= 5 and not any(t.startswith("MUFU") for _, t in B[j]):
            sel.append(j); j -= 1
        sel.reverse()                                       # A, B, C, D, E, F
        wts = [1.0, 0.75, 0.5, 0.25, 0.25, 1.0]
        if len(sel) >= 6:
            sel = sel[-6:]
            for k, jj in enumerate(sel):
                freq[jj] = f * wts[k]
            # anything before A in the same run is part of the previous region (kept)
    v = collections.Counter()
    for b, f in zip(B, freq):
        v.update(vec([t for _, t in b], f))
    words = sum(freq[i] * 4 for i in calls)               # words drawn per pair-step
    return v, dict(blocks=len(B), calls=len(calls), words_per_step=words, half_blocks=len(half),
                   table=[(hex(b[0][0]), len(b), round(f, 3)) for b, f in zip(B, freq)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sass", default=os.path.join(ROOT, "build", "hs_gpu.sass"))
    ap.add_argument("--tpb", type=int, default=256)
    a = ap.parse_args()
    F = parse(a.sass)
    out = {}
    for name, ins in F.items():
        m = re.search(r"price_kernelILi(\d+)ELi(\d+)ELi(\d+)ELi(\d+)E", name)
        if not m or int(m.group(1)) != a.tpb:
            continue
        key = KERNELS[(int(m.group(2)), int(m.group(3)), int(m.group(4)))]
        loops = [(s, e) for s, e in back_edges(ins) if e - s > 0x10]
        idx = {ad: i for i, (ad, _) in enumerate(ins)}
        L = [(idx[s], idx[e]) for s, e in loops]          # innermost first
        step = ins[L[0][0]:L[0][1] + 1]
        vs, info = step_loop(step)
        res = dict(kernel=name, per_pair_step={k: round(v, 3) for k, v in vs.items()}, step_info=info)
        if key.startswith("basket"):
            asset = ins[L[1][0]:L[1][1] + 1]
            pair = ins[L[2][0]:L[2][1] + 1]
            va = vec([t for _, t in asset]) - vec([t for _, t in step])
            vp = vec([t for _, t in pair]) - vec([t for _, t in asset])
            res.update(per_asset={k: v for k, v in va.items()}, per_pair={k: v for k, v in vp.items()})
        else:
            pair = ins[L[1][0]:L[1][1] + 1]
            vp = vec([t for _, t in pair]) - vec([t for _, t in step])
            res.update(per_pair={k: v for k, v in vp.items()})
        out[key] = res
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "results", "gpu_counts.json"), "w"), indent=1)
    for k, r in out.items():
        print(k, "per pair-step", r["per_pair_step"], "words/step", round(r["step_info"]["words_per_step"], 3),
              "| per asset" if "per_asset" in r else "", r.get("per_asset", ""), "| per pair", r["per_pair"])


if __name__ == "__main__":
    main()
