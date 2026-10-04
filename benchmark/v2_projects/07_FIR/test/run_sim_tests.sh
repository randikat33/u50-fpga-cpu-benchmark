#!/usr/bin/env bash
# run_sim_tests.sh - sandbox verification of 07_FIR (make sim_test). Exit 0 = all pass.
set -u -o pipefail
cd "$(dirname "$0")/.."
SHIM=${SHIM:-/home/USER/shim}
V2_ROOT=${V2_ROOT:-$(cd .. && pwd)}
TAPS=${TAPS:-32}
B=build/sim
mkdir -p $B
HLS_INC="-I$SHIM/include -I$SHIM/HLS_arbitrary_Precision_Types/include"
INC="-I$V2_ROOT/common -Icommon -Ikernel -Icpu -DFIR_TAPS=$TAPS"
CPUF="-std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -ffp-contract=off -Wall -Wno-unknown-pragmas $INC"
FAIL=0
step() { echo; echo "=== $*"; }
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
kv() { grep -m1 "^RESULT $2=" "$1" | cut -d= -f2; }

step "1. kernel C-sim == reference model (bit-identical), TAPS=$TAPS and 16, 48"
for T in $TAPS 16 48; do
  g++ -std=c++17 -O2 -w -march=native $HLS_INC -I$V2_ROOT/common -Icommon -Ikernel -DFIR_TAPS=$T \
      test/tb_fir.cpp kernel/fir_v2.cpp -o $B/tb_fir_$T || { check 1 "build tb_fir T=$T"; continue; }
  $B/tb_fir_$T | tee $B/tb_fir_$T.log | tail -n 2
  check ${PIPESTATUS[0]} "tb_fir TAPS=$T"
done

step "2. CPU baseline (production flags): VNNI == BW == scalar, thread-count invariance, --verify"
g++ $CPUF cpu/fir_cpu.cpp -o $B/fir_cpu; check $? "build fir_cpu"
ISAS="bw"; grep -q avx512_vnni /proc/cpuinfo && ISAS="vnni bw"
for cfg in "1 $TAPS" "3 $TAPS" "5 16" "2 64" "1 6" "1 256"; do
  set -- $cfg
  H=()
  for isa in $ISAS; do
    for th in 1 3; do
      $B/fir_cpu --size-mb $1 --repeat 1 --taps $2 --threads $th --isa $isa --verify > $B/cpu.log; rc=$?
      H+=("$(kv $B/cpu.log out_fnv1a64)"); [ $rc = 0 ] || check 1 "fir_cpu $isa threads=$th size=$1 taps=$2 rc=$rc"
    done
  done
  $B/fir_cpu --size-mb $1 --repeat 1 --taps $2 --impl scalar > $B/cpu.log; H+=("$(kv $B/cpu.log out_fnv1a64)")
  u=$(printf '%s\n' "${H[@]}" | sort -u | wc -l); [ "$u" = 1 ]; check $? "size=$1 MiB taps=$2: ${H[*]}"
done
$B/fir_cpu --taps 33 > /dev/null 2>&1; [ $? != 0 ]; check $? "rejects odd --taps"

step "3. FPGA host end-to-end via xrt_sim (kernel C-sim as device), 2 CUs"
g++ -std=c++17 -O2 -w -march=native -pthread -DXRT_SIM -I$SHIM/xrt_sim $HLS_INC $INC \
    host/fir_fpga.cpp test/sim_register.cpp kernel/fir_v2.cpp -o $B/fir_fpga_sim; check $? "build host (xrt_sim)"
# --no-io: the chunk output must equal the CPU output of the same block (fir_cpu --size-mb = chunk)
for cfg in "3 1 1" "5 2 1" "4 1 2" "2 2 3"; do
  set -- $cfg        # size-mb chunk-mb streams
  $B/fir_fpga_sim --xclbin sim.xclbin --cus 2 --size-mb $1 --chunk-mb $2 --streams $3 --verify > $B/host.log; rc=$?
  $B/fir_cpu --size-mb $2 --repeat 1 > $B/cpu.log
  hf=$(kv $B/host.log chunk_out_fnv1a64); hc=$(kv $B/cpu.log out_fnv1a64)
  [ $rc = 0 ] && [ "$hf" = "$hc" ] && [ "$(kv $B/host.log ok)" = 1 ]
  check $? "no-io size=$1 chunk=$2 streams=$3: workers $(kv $B/host.log n_workers) chunks $(kv $B/host.log nchunks), chunk hash $hf == cpu $hc"
done
# --mem: a real stream split into chunks with history == the CPU output of the whole signal
for cfg in "3 1 1" "5 2 2" "1 1 1"; do
  set -- $cfg
  $B/fir_fpga_sim --xclbin sim.xclbin --cus 2 --mem --size-mb $1 --chunk-mb $2 --streams $3 --verify > $B/host.log; rc=$?
  $B/fir_cpu --size-mb $1 --repeat 1 > $B/cpu.log
  hf=$(kv $B/host.log out_fnv1a64); hc=$(kv $B/cpu.log out_fnv1a64)
  [ $rc = 0 ] && [ "$hf" = "$hc" ] && [ "$(kv $B/host.log verified)" = 1 ]
  check $? "mem size=$1 chunk=$2 streams=$3: fpga $hf == cpu $hc"
done
for k in project platform impl ok t_total_s t_compute_s t_xclbin_s t_alloc_s t_read_s t_h2d_s t_kernel_s t_d2h_s t_write_s n_cu n_workers streams gsps bytes samples taps; do
  grep -q "^RESULT $k=" $B/host.log || check 1 "host key $k"
done
grep -q '^RESULT_JSON {' $B/host.log && grep -q '^MARK start' $B/host.log && grep -q '^MARK end' $B/host.log; check $? "host RESULT/RESULT_JSON/MARK format"
$B/fir_fpga_sim --size-mb 1 > /dev/null 2>&1; [ $? = 1 ]; check $? "host without --xclbin exits 1"

step "4. real XRT header syntax check"
g++ -std=c++17 -fsyntax-only -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver $INC host/fir_fpga.cpp
check $? "host/fir_fpga.cpp against real XRT headers"

step "5. sandbox throughput (CPU, 1 thread, in-cache and DRAM sizes; not a the server reference)"
for sz in 16 512; do
  $B/fir_cpu --size-mb $sz --repeat 5 --threads 1 | grep -E "^RESULT (impl|size_mb|gsps)=" | tr '\n' ' '; echo
done

echo
[ $FAIL = 0 ] && echo "07_FIR sim_test: ALL PASS" || echo "07_FIR sim_test: FAILURES"
exit $FAIL
