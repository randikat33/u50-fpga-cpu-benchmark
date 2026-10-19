#!/usr/bin/env bash
# frodo_check_s.sh - secondary cases (protocol v0.5) on frodo, before the builds: builds the three programs,
# runs the secondary-case tests with the real Vitis HLS headers (kernel C-sim == model, AVX-512 == model,
# CPU-native == matched), builds the FPGA host against the installed XRT, then HLS (C synthesis) of the
# three new kernels with their resource estimates.
#   bash test/frodo_check_s.sh              (about 1-2 hours; most of it is the three HLS runs)
#   SKIP_HLS=1 bash test/frodo_check_s.sh   (about 10 minutes)
# Log: build/frodo_check_s.log. Uses the card: no.
set -u -o pipefail
cd "$(dirname "$0")/.."
VITIS_SETTINGS=${VITIS_SETTINGS:-/tools/Xilinx/Vitis/2023.1/settings64.sh}
XRT_SETUP=${XRT_SETUP:-/opt/xilinx/xrt/setup.sh}
LOG=build/frodo_check_s.log
mkdir -p build
exec > >(tee "$LOG") 2>&1
FAIL=0
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
echo "frodo_check_s started $(date -u +%FT%TZ)"
[ -f "$VITIS_SETTINGS" ] && source "$VITIS_SETTINGS" >/dev/null 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
echo "vitis_hls: $(command -v vitis_hls || echo MISSING)   XILINX_HLS: ${XILINX_HLS:-MISSING}   XRT: ${XILINX_XRT:-MISSING}"

echo; echo "=== 1. programs"
make -s scpu snative; check $? "build hs_cpu and hs_native"
make -s shost; check $? "build hs_fpga (installed XRT)"

echo; echo "=== 2. tests (kernel C-sim, AVX-512, native)"
SHIM=/nonexistent bash test/run_sim_tests_s.sh > build/sim_test_s.log 2>&1; rc=$?
grep -E "^\[(PASS|FAIL)\]|^  z =" build/sim_test_s.log | sed 's/^/  /'
check $rc "test/run_sim_tests_s.sh"

echo; echo "=== 3. HLS (C synthesis) of the three new kernels"
[ "${SKIP_HLS:-0}" = 1 ] && echo "  skipped (SKIP_HLS=1; test/build_xclbins_s.sh runs HLS before each build)"
for s in $([ "${SKIP_HLS:-0}" = 1 ] || echo asian bqe bft); do
  t=$(case $s in bft) echo hb_ft;; bqe) echo hb_qe;; asian) echo ha_asian;; esac)
  t0=$(date +%s)
  nice -n 10 make -s csynth SCHEME=$s > build/csynth_$s.log 2>&1; rc=$?
  check $rc "csynth $s ($(( ($(date +%s) - t0) / 60 )) min)"
  rpt=$(find build/$s/hls -path "*syn/report*" -name "${t}_csynth.rpt" | head -1)
  [ -z "$rpt" ] && rpt=$(find build/$s/hls -path "*syn/report*" -name "csynth.rpt" | head -1)
  if [ -n "$rpt" ]; then
    awk '/== Utilization Estimates/{f=1} f&&/^\|(Name|Total|Available|Utilization)/{print "  "$0} /Detail/{if(f)exit}' "$rpt"
  fi
  grep -h -E "Pipelining result|Final II|RING|SETTLE" build/$s/hls/*/hls.log 2>/dev/null | grep -i -E "ring|settle" | head -6 | sed 's/^/  /'
done
echo
[ $FAIL = 0 ] && echo "FRODO CHECK S: ALL PASS" || echo "FRODO CHECK S: FAILURES (see above)"
exit $FAIL
