#!/usr/bin/env bash
# card_check_s.sh - the first card runs of the secondary-case kernels (protocol v0.5), after the registered prediction
# (commit c2f34c2), as test/card_check.sh did for the main kernels. For the three new kernels and the 1% and 0.3%
# settings (8 settings: basket FT and QE, Asian plain and CV) it checks
#   1. the card's moments equal the CPU model's, bit for bit (host --verify: scalar model on the CPU), and
#   2. the card's moment hash equals the matched AVX-512 CPU program's on the same pairs and seed;
# then one 3-second timing look per kernel (rate against the routed design's peak; not a measurement).
#   bash test/card_check_s.sh            (about 5-10 minutes)            log: build/card_check_s.log
set -u -o pipefail
cd "$(dirname "$0")/.."
XRT_SETUP=${XRT_SETUP:-/opt/xilinx/xrt/setup.sh}
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
LOG=build/card_check_s.log
mkdir -p build
exec > >(tee "$LOG") 2>&1
FAIL=0
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
val() { grep -m1 "^RESULT $1=" | cut -d= -f2-; }
echo "card_check_s started $(date -u +%FT%TZ)"
make -s scpu shost; check $? "build hs_cpu and hs_fpga (XRT ${XILINX_XRT:-?})"
for x in bft/hb_ft bqe/hb_qe asian/ha_asian; do [ -f build/$x.xclbin ]; check $? "build/$x.xclbin present ($(cat build/${x%/*}/FREQ_HZ 2>/dev/null) Hz)"; done
for f in results/settings_s.csv results/beta_A.csv results/reference_s.csv; do [ -f $f ]; check $? "$f present"; done
[ $FAIL = 0 ] || { echo "CARD CHECK S: FAILURES (see above)"; exit 1; }
BETA=$(python3 -c "import csv; r=list(csv.DictReader(open('results/beta_A.csv')))[-1]; print(','.join(r['beta%d'%k] for k in range(3)))")

SET=$(python3 - <<'PY'
import csv
for r in csv.DictReader(open("results/settings_s.csv")):
    if r["rule"] == "empirical" and r["target"] in ("1%", "0.3%") and r.get("steps"):
        print(r["case"], r["scheme"], r["target"], r["steps"], r["pairs"])
PY
)
xclbin() { case $1 in ft) echo build/bft/hb_ft.xclbin;; qe) echo build/bqe/hb_qe.xclbin;; *) echo build/asian/ha_asian.xclbin;; esac; }
args() { if [ $1 = B ]; then echo "--case B --scheme $2 --steps $3"; else echo "--case A --estimator $2 --steps $3 --beta $BETA"; fi; }
echo; echo "=== 1. card == CPU, bit for bit (8 settings)"
while read -r C S T M N; do
    A=$(args $C $S $M)
    F=$(./build/hs_fpga --xclbin $(xclbin $S) $A --pairs $N --seed 4242 --runs 2 --warmup 1 --verify 2>&1)
    P=$(./build/hs_cpu $A --pairs $N --seed 4242 --impl avx512 --interleave 1 2>&1)
    fh=$(echo "$F" | val mom_hash); ch=$(echo "$P" | val mom_hash)
    vb=$(echo "$F" | val verify_bit_identical); ok=$(echo "$F" | val ok)
    r=1; [ -n "$fh" ] && [ "$fh" = "$ch" ] && [ "$vb" = 1 ] && [ "$ok" = 1 ] && r=0
    check $r "$C $S $T (M $M, $N pairs): card $fh, cpu $ch, verify $vb ($(echo "$F" | val verify_pairs) pairs), price K100 $(echo "$F" | val price1)"
    [ $r = 0 ] || echo "$F" | tail -n 5 | sed 's/^/    /'
done <<< "$SET"

echo; echo "=== 2. timing look (3 s window each; for information, not a measurement)"
for spec in "B ft 64 708288 1.575" "B qe 8 704768 0.700" "A plain 250 922176 11.2"; do
    set -- $spec
    F=$(./build/hs_fpga --xclbin $(xclbin $2) $(args $1 $2 $3) --pairs $4 --seed 7 --warmup-s 1 --window-s 3 2>&1)
    echo "  $1 $2 M=$3 N=$4: $(echo "$F" | val runs) runs, $(echo "$F" | val t_per_run_s) s/run," \
         "kernel $(echo "$F" | val gsteps_per_s_kernel) G path-steps/s (peak $5), ok $(echo "$F" | val ok)"
done
echo
[ $FAIL = 0 ] && echo "CARD CHECK S: ALL PASS" || echo "CARD CHECK S: FAILURES (see above)"
exit $FAIL
