#!/usr/bin/env bash
# native_check.sh <mc_native> <mc_cpu> - the CPU-native variant prices the same options as the
# bit-exact engine within 3 combined standard errors (different normals, so not bit-identical).
set -u
NAT=$1; REF=$2; FAIL=0
kv() { grep -m1 "^RESULT $2=" "$1" | cut -d= -f2; }
for cfg in "524288 32" "262144 64 --put" "262144 16 --corr 0.5" "131072 128 --strike 90"; do
    set -- $cfg
    "$NAT" --paths $1 --steps $2 "${@:3}" > /tmp/nat.$$ 2>&1 || FAIL=1
    "$REF" --paths $1 --steps $2 "${@:3}" > /tmp/ref.$$ 2>&1 || FAIL=1
    if python3 - "$(kv /tmp/nat.$$ price)" "$(kv /tmp/nat.$$ stderr)" "$(kv /tmp/ref.$$ price)" "$(kv /tmp/ref.$$ stderr)" "$cfg" <<'PY'
import math, sys
pn, sn, pr, sr = map(float, sys.argv[1:5]); tol = 3 * math.hypot(sn, sr)
ok = abs(pn - pr) <= tol
print(f"[{'PASS' if ok else 'FAIL'}] {sys.argv[5]:28s} native {pn:.6f} ref {pr:.6f} |diff| {abs(pn-pr):.6f} <= {tol:.6f}")
sys.exit(0 if ok else 1)
PY
    then :; else FAIL=1; fi
done
rm -f /tmp/nat.$$ /tmp/ref.$$
[ $FAIL = 0 ] && echo "native_check: ALL PASS" || echo "native_check: FAILED"
exit $FAIL
