#!/usr/bin/env bash
# frodo_check.sh - phase 3, step 1 on frodo: builds and checks everything that does not need the card,
# then runs HLS (C synthesis) for both kernels and prints their resource estimates.
#   bash test/frodo_check.sh            (about 30-90 minutes; most of it is the two HLS runs)
#   SKIP_HLS=1 bash test/frodo_check.sh (a few minutes: everything except HLS)
# Writes build/frodo_check.log. Uses the card: no.
set -u -o pipefail
cd "$(dirname "$0")/.."
VITIS_SETTINGS=${VITIS_SETTINGS:-/tools/Xilinx/Vitis/2023.1/settings64.sh}
XRT_SETUP=${XRT_SETUP:-/opt/xilinx/xrt/setup.sh}
LOG=build/frodo_check.log
mkdir -p build
exec > >(tee "$LOG") 2>&1
FAIL=0
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
echo "frodo_check started $(date -u +%FT%TZ)"
[ -f "$VITIS_SETTINGS" ] && source "$VITIS_SETTINGS" >/dev/null 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
echo "vitis_hls: $(command -v vitis_hls || echo MISSING)   XILINX_HLS: ${XILINX_HLS:-MISSING}   XRT: ${XILINX_XRT:-MISSING}"

echo; echo "=== 1. CPU program and phase-2 tests"
make -s cpu; check $? "build hq_cpu"
make -s test > build/test.log 2>&1; check $? "make test (phase-2 tests)"; tail -n 1 build/test.log

echo; echo "=== 2. AVX-512 == scalar model on this CPU"
ok=0
for c in I II III P1; do for s in ft qe; do for nb in 1 2 4; do
  ./build/hq_cpu --case $c --scheme $s --steps-per-year 4 --a0 7 --pairs 4099 --impl avx512 --interleave $nb --threads 8 --verify \
     | grep -q "^RESULT verify_identical_to_scalar_1thread=1" || { echo "  mismatch: $c $s nb=$nb"; ok=1; }
done; done; done
check $ok "AVX-512 bit-identical to the scalar model (24 combinations)"

echo; echo "=== 3. kernel C-simulation with the real Vitis HLS headers"
V2_ROOT=$(cd .. && pwd); MC=$V2_ROOT/03_MC_Heston
if [ -n "${XILINX_HLS:-}" ] && [ -f "$XILINX_HLS/include/ap_int.h" ]; then
  for S in 0 1; do
    L=$([ $S = 0 ] && echo 14 || echo 7)
    g++ -std=c++17 -O2 -w -march=native -ffp-contract=off -I"$XILINX_HLS/include" -I$V2_ROOT/common -I$MC/common \
        -Icommon -Ikernel -DHQ_SCHEME=$S -DHQ_LANES=$L test/tb_kernel.cpp kernel/hq_kernel.cpp -o build/tb_kernel_$S \
      && ./build/tb_kernel_$S > build/tb_kernel_$S.log 2>&1
    check $? "tb_kernel scheme $S (lanes $L): $(tail -n 1 build/tb_kernel_$S.log)"
  done
else
  check 1 "XILINX_HLS not found (set VITIS_SETTINGS=/path/to/settings64.sh)"
fi

echo; echo "=== 4. FPGA host against the installed XRT"
make -s host; check $? "build hq_fpga"

echo; echo "=== 5. HLS (C synthesis) of both kernels: resource and latency estimates"
[ "${SKIP_HLS:-0}" = 1 ] && echo "  skipped (SKIP_HLS=1; test/build_xclbins.sh runs HLS before each build)"
for s in $([ "${SKIP_HLS:-0}" = 1 ] || echo ft qe); do
  t0=$(date +%s)
  nice -n 10 make -s csynth SCHEME=$s > build/csynth_$s.log 2>&1; rc=$?
  check $rc "csynth $s ($(( ($(date +%s) - t0) / 60 )) min)"
  rpt=$(find build/$s/hls -path "*syn/report*" -name "hq_${s}_csynth.rpt" | head -1)
  [ -z "$rpt" ] && rpt=$(find build/$s/hls -path "*syn/report*" -name "csynth.rpt" | head -1)
  if [ -n "$rpt" ]; then
    echo "  report: $rpt"
    awk '/== Utilization Estimates/{f=1} f&&/^\|(Name|Total|Available|Utilization)/{print "  "$0} /Detail/{if(f)exit}' "$rpt"
    grep -A12 "Latency (cycles)" "$rpt" | head -14 | sed 's/^/  /'
  fi
  for r in $(find build/$s/hls -path "*syn/report*" -name "lane*_csynth.rpt" | head -1); do
    echo "  lane loop:"; grep -E "RING|II|Depth" "$r" | head -6 | sed 's/^/    /'
  done
done
echo
[ $FAIL = 0 ] && echo "FRODO CHECK: ALL PASS" || echo "FRODO CHECK: FAILURES (see above)"
echo "Send build/frodo_check.log and the two folders build/ft/hls/*/prj/sol/syn/report and build/qe/hls/*/prj/sol/syn/report"
exit $FAIL
