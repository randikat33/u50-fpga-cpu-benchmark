#!/usr/bin/env bash
# run_sim_tests_s.sh - sandbox / frodo verification of the secondary-case code (protocol v0.5), make s_sim_test.
#   1. kernel C-simulation == scalar model (bit-identical): basket FT and QE, Asian; several lane counts
#   2. AVX-512 == scalar model (interleave 1, 2, 4; odd pair counts and offsets)
#   3. FPGA host end to end via xrt_sim == CPU program (sandbox only: needs SHIM)
#   4. host source against the real XRT headers (sandbox: SHIM; frodo: make shost)
#   5. CPU-native code agrees with the matched code within 4 combined SE
# Uses HLS headers from $XILINX_HLS if set (frodo), else from $SHIM (sandbox). Exit 0 = all pass.
set -u -o pipefail
cd "$(dirname "$0")/.."
SHIM=${SHIM:-/home/claude/shim}
V2_ROOT=${V2_ROOT:-$(cd .. && pwd)}
B=build/sim
mkdir -p $B
MC=$V2_ROOT/03_MC_Heston
if [ -n "${XILINX_HLS:-}" ] && [ -f "$XILINX_HLS/include/ap_int.h" ]; then HLS_INC="-I$XILINX_HLS/include"; else HLS_INC="-I$SHIM/include -I$SHIM/HLS_arbitrary_Precision_Types/include"; fi
INC="-I$V2_ROOT/common -I$MC/common -I$MC/cpu -Icommon -Ikernel -Icpu"
CSIM="g++ -std=c++17 -O2 -w -march=native -ffp-contract=off $HLS_INC $INC"
FAIL=0
step() { echo; echo "=== $*"; }
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
kv() { grep -m1 "^RESULT $2=" "$1" | cut -d= -f2; }
SC=(ft qe)

step "1. kernel C-sim == scalar model (bit-identical)"
for S in 0 1; do
  for L in 1 5 $([ $S = 0 ] && echo 14 || echo 7); do
    $CSIM -DHQ_SCHEME=$S -DHQ_LANES=$L test/tb_hb_kernel.cpp kernel/hb_kernel.cpp -o $B/tbb_${S}_$L || { check 1 "build tb_hb_kernel ${SC[$S]} L=$L"; continue; }
    $B/tbb_${S}_$L > $B/tbb_${S}_$L.log; rc=$?; tail -n 1 $B/tbb_${S}_$L.log
    check $rc "tb_hb_kernel basket ${SC[$S]} LANES=$L"
  done
done
for L in 1 5 14; do
  $CSIM -DHA_LANES=$L test/tb_ha_kernel.cpp kernel/ha_kernel.cpp -o $B/tba_$L || { check 1 "build tb_ha_kernel L=$L"; continue; }
  $B/tba_$L > $B/tba_$L.log; rc=$?; tail -n 1 $B/tba_$L.log
  check $rc "tb_ha_kernel Asian LANES=$L"
done

step "2. AVX-512 == scalar model"
ok=0
for s in ft qe; do for sp in 1 4; do for nb in 1 2 4; do
  ./build/hs_cpu --case B --scheme $s --steps-per-year $sp --a0 3 --pairs 1027 --impl avx512 --interleave $nb --threads 3 --verify > $B/avx.log
  [ $? = 0 ] && [ "$(kv $B/avx.log verify_identical_to_scalar_1thread)" = 1 ] || { echo "  mismatch: B $s sp=$sp nb=$nb"; ok=1; }
done; done; done
for M in 250 3 17; do for nb in 1 2 4; do
  ./build/hs_cpu --case A --steps $M --a0 7 --pairs 4099 --impl avx512 --interleave $nb --threads 3 --verify > $B/avx.log
  [ $? = 0 ] && [ "$(kv $B/avx.log verify_identical_to_scalar_1thread)" = 1 ] || { echo "  mismatch: A M=$M nb=$nb"; ok=1; }
done; done
check $ok "AVX-512 bit-identical to the scalar model (12 basket + 9 Asian combinations)"

if [ -d "$SHIM/xrt_sim" ]; then
step "3. FPGA host end to end via xrt_sim"
for S in 0 1; do
  g++ -std=c++17 -O2 -w -march=native -ffp-contract=off -DXRT_SIM -DHQ_SCHEME=$S -DHQ_LANES=4 -DHA_LANES=4 -I$SHIM/xrt_sim $HLS_INC $INC \
      host/hs_fpga.cpp test/sim_register_hs.cpp kernel/hb_kernel.cpp kernel/ha_kernel.cpp -o $B/hs_fpga_sim_$S -pthread; check $? "build host (xrt_sim) ${SC[$S]}"
  for cfg in "4 501 1" "1 300 2" "8 777 2"; do
    set -- $cfg
    $B/hs_fpga_sim_$S --xclbin sim.xclbin --case B --scheme ${SC[$S]} --steps-per-year $1 --pairs $2 --cus $3 --runs 2 --verify > $B/host.log; rc=$?
    ./build/hs_cpu --case B --scheme ${SC[$S]} --steps-per-year $1 --pairs $2 --impl avx512 > $B/cpu.log
    hf=$(kv $B/host.log mom_hash); hc=$(kv $B/cpu.log mom_hash)
    [ $rc = 0 ] && [ "$hf" = "$hc" ] && [ "$(kv $B/host.log price1)" = "$(kv $B/cpu.log price1)" ]
    check $? "host basket ${SC[$S]} steps/yr $1 pairs $2 CUs $3: fpga $hf == cpu $hc"
  done
done
for cfg in "250 401 1" "250 300 2" "3 1000 2"; do
  set -- $cfg
  $B/hs_fpga_sim_0 --xclbin sim.xclbin --case A --steps $1 --pairs $2 --cus $3 --runs 2 --verify --beta 1.04,1.03,1.06 > $B/host.log; rc=$?
  ./build/hs_cpu --case A --steps $1 --pairs $2 --impl avx512 --beta 1.04,1.03,1.06 > $B/cpu.log
  hf=$(kv $B/host.log mom_hash); hc=$(kv $B/cpu.log mom_hash)
  [ $rc = 0 ] && [ "$hf" = "$hc" ] && [ "$(kv $B/host.log cv1)" = "$(kv $B/cpu.log cv1)" ]
  check $? "host Asian M $1 pairs $2 CUs $3: fpga $hf == cpu $hc"
done
$B/hs_fpga_sim_0 --xclbin sim.xclbin --case B --scheme qe --pairs 10 > /dev/null 2>&1; [ $? = 1 ]; check $? "host refuses the wrong scheme for the xclbin"
for k in project platform impl ok t_compute_s t_kernel_s lanes il path_steps price1 se1 plain1 cv1 se_cv1 beta_hat1 sy_0 syg_2 mom_hash; do
  grep -q "^RESULT $k=" $B/host.log || check 1 "host key $k"
done

step "4. host source against the real XRT headers"
g++ -std=c++17 -fsyntax-only -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver $INC host/hs_fpga.cpp
check $? "host/hs_fpga.cpp against real XRT headers"
else
step "3-4. host"
make -s shost; check $? "build hs_fpga against the installed XRT"
fi

step "5. CPU-native == matched within 4 combined SE"
pyc='
import json, sys
f = lambda p: [json.loads(l[12:]) for l in open(p) if l.startswith("RESULT_JSON ")][-1]
a, b = f(sys.argv[1]), f(sys.argv[2])
z = [abs(float(a["price%d" % k]) - float(b["price%d" % k])) / (float(a["se%d" % k])**2 + float(b["se%d" % k])**2) ** 0.5 for k in range(3)]
print("  z =", " ".join("%.2f" % x for x in z)); sys.exit(0 if max(z) < 4 else 1)'
for s in ft qe; do
  ./build/hs_native --case B --scheme $s --steps-per-year 4 --pairs 300000 > $B/n.log
  ./build/hs_cpu --case B --scheme $s --steps-per-year 4 --pairs 300000 --impl avx512 > $B/m.log
  python3 -c "$pyc" $B/n.log $B/m.log; check $? "native basket $s"
done
for e in plain cv; do
  ./build/hs_native --case A --pairs 300000 --estimator $e --beta 1.04,1.03,1.06 > $B/n.log
  ./build/hs_cpu --case A --pairs 300000 --impl avx512 --estimator $e --beta 1.04,1.03,1.06 > $B/m.log
  python3 -c "$pyc" $B/n.log $B/m.log; check $? "native Asian $e"
done

echo
[ $FAIL = 0 ] && echo "secondary-case sim_test: ALL PASS" || echo "secondary-case sim_test: FAILURES"
exit $FAIL
