#!/usr/bin/env python3
"""pf_native_check.py <portfolio.bin> <results_bitexact.bin> <results_native.bin>

The CPU-native portfolio engine (Box-Muller normals, polynomial exp, FMA, double moments) is not
bit-identical to the algorithm-matched engine. Both are unbiased Monte Carlo estimates of the same
prices (same model, discretisation, paths and steps), so they must agree within their standard errors:
  * per trade: |p_a - p_b| <= 3 * sqrt(se_a^2 + se_b^2) * |position| for >= 99% of the trades with se > 0
  * portfolio: |sum p_a - sum p_b| <= 3 * sqrt(sum (se_a^2 + se_b^2) * position^2)
Exit 0 and print PASS if both hold."""
import struct, sys, math

def results(path):
    with open(path, 'rb') as f:
        n = struct.unpack('<I', f.read(4))[0]
        v = struct.unpack('<%df' % (2 * n), f.read(8 * n))
    return [(v[2 * i], v[2 * i + 1]) for i in range(n)]

def positions(path):
    with open(path, 'rb') as f:
        n = struct.unpack('<I', f.read(4))[0]
        out = []
        for _ in range(n):
            w = f.read(44)
            out.append(struct.unpack('<f', w[20:24])[0])
    return out

pos = positions(sys.argv[1]); a = results(sys.argv[2]); b = results(sys.argv[3])
assert len(a) == len(b) == len(pos), "sizes differ"
tested = within = 0
var_sum = 0.0
for (pa, sa), (pb, sb), q in zip(a, b, pos):
    s = math.hypot(sa, sb) * abs(q)
    var_sum += s * s
    if s > 0:
        tested += 1
        within += abs(pa - pb) <= 3 * s
sa_ = sum(p for p, _ in a); sb_ = sum(p for p, _ in b)
tol = 3 * math.sqrt(var_sum)
frac = within / max(tested, 1)
ok = frac >= 0.99 and abs(sa_ - sb_) <= tol
print(f"trades {len(a)}  tested {tested}  within 3 SE: {within} ({100*frac:.2f}%)")
print(f"portfolio: bit-exact {sa_:.3f}  native {sb_:.3f}  |diff| {abs(sa_-sb_):.3f} <= {tol:.3f}")
print("pf_native_check: " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
