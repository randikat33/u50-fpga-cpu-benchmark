#!/usr/bin/env bash
# card_check.sh - phase 5, step A: the first card runs of paper 2 (after the prediction was registered,
# commit 88b30f6). For both kernels and all four cases it runs the card at the 1% and 0.3% settings and checks
#   1. the card's moments equal the CPU model's, bit for bit (host --verify: scalar model on the CPU), and
#   2. the card's moment hash equals the matched AVX-512 CPU program's on the same pairs and seed;
# then one 3-second timing look per kernel (rate against the routed design's peak; not a measurement).
#   bash test/card_check.sh            (about 10-15 minutes)            log: build/card_check.log
set -u -o pipefail
cd "$(dirname "$0")/.."
XRT_SETUP=${XRT_SETUP:-/opt/xilinx/xrt/setup.sh}
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
LOG=build/card_check.log
mkdir -p build
exec > >(tee "$LOG") 2>&1
FAIL=0
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
val() { grep -m1 "^RESULT $1=" | cut -d= -f2-; }
echo "card_check started $(date -u +%FT%TZ)"
make -s cpu host; check $? "build hq_cpu and hq_fpga (XRT ${XILINX_XRT:-?})"
for s in qe ft; do [ -f build/$s/hq_$s.xclbin ]; check $? "build/$s/hq_$s.xclbin present ($(cat build/$s/FREQ_HZ 2>/dev/null) Hz)"; done
[ -f results/settings.csv ]; check $? "results/settings.csv present"
[ $FAIL = 0 ] || { echo "CARD CHECK: FAILURES (see above)"; exit 1; }

SET=$(python3 - <<'EOF'
import csv
for r in csv.DictReader(open("results/settings.csv")):
    if r["rule"] == "empirical" and r["target"] in ("1%", "0.3%"):
        print(r["case"], r["scheme"], r["target"], r["steps"], r["pairs"])
EOF
)
echo; echo "=== 1. card == CPU, bit for bit (16 settings)"
while read -r C S T M N; do
    X=build/$S/hq_$S.xclbin
    F=$(./build/hq_fpga --xclbin $X --case $C --scheme $S --steps $M --pairs $N --seed 4242 --runs 2 --warmup 1 --verify 2>&1)
    P=$(./build/hq_cpu --case $C --scheme $S --steps $M --pairs $N --seed 4242 --impl avx512 --interleave 1 2>&1)
    fh=$(echo "$F" | val mom_hash); ch=$(echo "$P" | val mom_hash)
    vb=$(echo "$F" | val verify_bit_identical); ok=$(echo "$F" | val ok)
    r=1; [ -n "$fh" ] && [ "$fh" = "$ch" ] && [ "$vb" = 1 ] && [ "$ok" = 1 ] && r=0
    check $r "$C $S $T (M $M, $N pairs): card $fh, cpu $ch, verify $vb ($(echo "$F" | val verify_pairs) pairs), price K100 $(echo "$F" | val price1)"
    [ $r = 0 ] || echo "$F" | tail -n 5 | sed 's/^/    /'
done <<< "$SET"

echo; echo "=== 2. timing look (3 s window each; for information, not a measurement)"
for spec in "II qe 16 4556096" "II ft 512 509632"; do
    set -- $spec
    F=$(./build/hq_fpga --xclbin build/$2/hq_$2.xclbin --case $1 --scheme $2 --steps $3 --pairs $4 --seed 7 --warmup-s 1 --window-s 3 2>&1)
    echo "  $1 $2 M=$3 N=$4: $(echo "$F" | val runs) runs, $(echo "$F" | val t_per_run_s) s/run," \
         "kernel $(echo "$F" | val gsteps_per_s_kernel) G path-steps/s (peak: ft 12.6, qe 6.3), ok $(echo "$F" | val ok)"
done
echo
[ $FAIL = 0 ] && echo "CARD CHECK: ALL PASS" || echo "CARD CHECK: FAILURES (see above)"
exit $FAIL
