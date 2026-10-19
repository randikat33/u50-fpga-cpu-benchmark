#!/usr/bin/env bash
# run_sim_tests.sh - sandbox verification of the phase-3 code (make sim_test). Exit 0 = all pass.
#   1. kernel C-simulation == scalar model (bit-identical), both schemes, several lane counts
#   2. AVX-512 code == scalar model (interleave 1, 2, 4), all cases, both schemes
#   3. FPGA host end to end via xrt_sim (C-simulated kernel as the device) == CPU program
#   4. host source against the real XRT headers
set -u -o pipefail
cd "$(dirname "$0")/.."
SHIM=${SHIM:-/home/claude/shim}
V2_ROOT=${V2_ROOT:-$(cd .. && pwd)}
B=build/sim
mkdir -p $B
MC=$V2_ROOT/03_MC_Heston
HLS_INC="-I$SHIM/include -I$SHIM/HLS_arbitrary_Precision_Types/include"
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
    $CSIM -DHQ_SCHEME=$S -DHQ_LANES=$L test/tb_kernel.cpp kernel/hq_kernel.cpp -o $B/tb_${S}_$L || { check 1 "build tb_kernel ${SC[$S]} L=$L"; continue; }
    $B/tb_${S}_$L > $B/tb_${S}_$L.log; rc=$?
    tail -n 1 $B/tb_${S}_$L.log
    check $rc "tb_kernel ${SC[$S]} LANES=$L"
  done
done

step "2. AVX-512 == scalar model (all cases, both schemes, interleave 1/2/4, odd pair counts)"
for c in I II III P1; do
  for s in ft qe; do
    for nb in 1 2 4; do
      ./build/hq_cpu --case $c --scheme $s --steps-per-year 4 --a0 7 --pairs 4099 --impl avx512 --interleave $nb --threads 3 --verify > $B/avx.log
      [ $? = 0 ] && [ "$(kv $B/avx.log verify_identical_to_scalar_1thread)" = 1 ] || { check 1 "avx512 $c $s nb=$nb"; }
    done
  done
done
check 0 "AVX-512 runs finished (any mismatch is listed above as FAIL)"

step "3. FPGA host end to end via xrt_sim"
for S in 0 1; do
  g++ -std=c++17 -O2 -w -march=native -ffp-contract=off -DXRT_SIM -DHQ_SCHEME=$S -DHQ_LANES=4 -I$SHIM/xrt_sim $HLS_INC $INC \
      host/hq_fpga.cpp test/sim_register.cpp kernel/hq_kernel.cpp -o $B/hq_fpga_sim_$S -pthread; check $? "build host (xrt_sim) ${SC[$S]}"
  for cfg in "I 4 2001 1" "P1 2 777 2" "III 8 3001 2" "II 2 5000 1"; do
    set -- $cfg
    $B/hq_fpga_sim_$S --xclbin sim.xclbin --case $1 --scheme ${SC[$S]} --steps-per-year $2 --pairs $3 --cus $4 --runs 2 --verify > $B/host.log; rc=$?
    ./build/hq_cpu --case $1 --scheme ${SC[$S]} --steps-per-year $2 --pairs $3 --impl avx512 > $B/cpu.log
    hf=$(kv $B/host.log mom_hash); hc=$(kv $B/cpu.log mom_hash)
    [ $rc = 0 ] && [ "$hf" = "$hc" ] && [ "$(kv $B/host.log price1)" = "$(kv $B/cpu.log price1)" ]
    check $? "host ${SC[$S]} case $1 steps/yr $2 pairs $3: fpga $hf == cpu $hc"
  done
  $B/hq_fpga_sim_$S --xclbin sim.xclbin --case I --scheme ${SC[$S]} --steps-per-year 2 --a0 1000 --pairs 6000 --verify --verify-pairs 100 > $B/host.log
  check $? "host ${SC[$S]}: offset a0, subset verify (extra kernel call)"
  for k in project platform impl ok t_compute_s t_kernel_s lanes il path_steps price1 se1 bias1 s1_0 sp_2 mom_hash; do
    grep -q "^RESULT $k=" $B/host.log || check 1 "host key $k"
  done
done
$B/hq_fpga_sim_0 --xclbin sim.xclbin --case I --scheme qe --pairs 10 > /dev/null 2>&1; [ $? = 1 ]; check $? "host refuses the wrong scheme for the xclbin"
python3 - "$B" <<'PY'
import sys, json, subprocess
sys.path.insert(0, "scripts")
import hqlib as H
B = sys.argv[1]
out = subprocess.run([f"{B}/hq_fpga_sim_1", "--xclbin", "sim.xclbin", "--case", "III", "--scheme", "qe", "--steps-per-year", "4", "--pairs", "500"], capture_output=True, text=True).stdout
r = json.loads([l for l in out.splitlines() if l.startswith("RESULT_JSON ")][-1][12:])
m = H.Moments("fpga"); m.add(r)
print("hqlib reads the FPGA result:", m.summary("III")["est1"])
PY
check $? "scripts/hqlib.py parses the FPGA host output"

step "4. host source against the real XRT headers"
g++ -std=c++17 -fsyntax-only -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver $INC host/hq_fpga.cpp
check $? "host/hq_fpga.cpp against real XRT headers"

echo
[ $FAIL = 0 ] && echo "10_Heston_Accuracy sim_test: ALL PASS" || echo "10_Heston_Accuracy sim_test: FAILURES"
exit $FAIL
