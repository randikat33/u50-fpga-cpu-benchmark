#!/usr/bin/env bash
# run_sim_tests.sh - sandbox verification of 09_MC_Asian_Sweep (make sim_test). Exit 0 = all pass.
set -u -o pipefail
cd "$(dirname "$0")/.."
SHIM=${SHIM:-/home/USER/shim}
V2_ROOT=${V2_ROOT:-$(cd .. && pwd)}
LANES=${LANES:-16}
B=build/sim
mkdir -p $B
MC=$V2_ROOT/03_MC_Heston
HLS_INC="-I$SHIM/include -I$SHIM/HLS_arbitrary_Precision_Types/include"
INC="-I$V2_ROOT/common -I$MC/common -I$MC/cpu -Icommon -Ikernel -Icpu"
CSIM="g++ -std=c++17 -O2 -w -march=native -ffp-contract=off $HLS_INC $INC"
CPUF="-std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off -Wall -Wno-unknown-pragmas $INC"
FAIL=0
step() { echo; echo "=== $*"; }
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
kv() { grep -m1 "^RESULT $2=" "$1" | cut -d= -f2; }

step "1. kernel C-sim == scalar reference == AVX-512 (bit-identical), LANES=$LANES,1,5"
for L in $LANES 1 5; do
  $CSIM -DAS_LANES=$L test/tb_kernel.cpp kernel/mc_asian.cpp -o $B/tb_kernel_$L || { check 1 "build tb_kernel L=$L"; continue; }
  $B/tb_kernel_$L | tee $B/tb_kernel_$L.log | tail -n 3
  check ${PIPESTATUS[0]} "tb_kernel LANES=$L"
done

step "2. CPU baseline (production flags): avx512 x1/x2/x4 == scalar, thread-count invariance, --verify"
g++ $CPUF cpu/asian_cpu.cpp -o $B/asian_cpu; check $? "build asian_cpu"
for cfg in "1 1" "33 3" "1000 16" "4097 5" "20001 64"; do
  set -- $cfg
  H=()
  for run in "--impl avx512 --interleave 2 --threads 2" "--impl avx512 --interleave 1 --threads 1" \
             "--impl avx512 --interleave 4 --threads 3" "--impl scalar --threads 2"; do
    $B/asian_cpu --paths $1 --steps $2 --seed 777 $run --verify > $B/cpu.log; rc=$?
    H+=("$(kv $B/cpu.log mom_hash)")
    [ $rc = 0 ] || { check 1 "asian_cpu $run paths=$1 steps=$2 rc=$rc"; }
  done
  [ "${H[0]}" = "${H[1]}" ] && [ "${H[0]}" = "${H[2]}" ] && [ "${H[0]}" = "${H[3]}" ]; check $? "paths=$1 steps=$2: ${H[*]}"
done
$B/asian_cpu --paths 3000 --steps 8 --put --sigma 0.4 --verify > $B/cpu_put.log; check $? "put, sigma 0.4 --verify"
$B/asian_cpu --paths 3000 --steps 70000 > /dev/null 2>&1; [ $? != 0 ]; check $? "rejects --steps > 65535"

step "3. FPGA host end-to-end via xrt_sim (kernel C-sim as device)"
g++ -std=c++17 -O2 -w -march=native -ffp-contract=off -DXRT_SIM -DAS_LANES=$LANES -I$SHIM/xrt_sim $HLS_INC $INC \
    host/asian_fpga.cpp test/sim_register.cpp kernel/mc_asian.cpp -o $B/asian_fpga_sim -pthread; check $? "build host (xrt_sim)"
for cfg in "2001 7 1" "777 64 1" "150 3 1" "30001 64 1"; do
  set -- $cfg
  $B/asian_fpga_sim --xclbin sim.xclbin --paths $1 --steps $2 --cus $3 --runs 2 --verify > $B/host.log; rc=$?
  $B/asian_cpu --paths $1 --steps $2 > $B/cpu.log
  hf=$(kv $B/host.log mom_hash); hc=$(kv $B/cpu.log mom_hash)
  [ $rc = 0 ] && [ "$hf" = "$hc" ] && [ "$(kv $B/host.log price)" = "$(kv $B/cpu.log price)" ]
  check $? "host paths=$1 steps=$2 cus=$3: fpga $hf == cpu $hc, price $(kv $B/host.log price)"
done
for act in 1 3 7 $LANES; do
  $B/asian_fpga_sim --xclbin sim.xclbin --paths 20001 --steps 9 --active-lanes $act --runs 2 --verify > $B/host_a.log; rc=$?
  $B/asian_cpu --paths 20001 --steps 9 > $B/cpu.log
  [ $rc = 0 ] && [ "$(kv $B/host_a.log mom_hash)" = "$(kv $B/cpu.log mom_hash)" ] && [ "$(kv $B/host_a.log active_lanes)" = "$act" ]
  check $? "host --active-lanes $act: hash $(kv $B/host_a.log mom_hash) == cpu, impl $(kv $B/host_a.log impl)"
done
$B/asian_fpga_sim --xclbin sim.xclbin --paths 100 --steps 3 --active-lanes $((LANES + 1)) > /dev/null 2>&1; [ $? = 1 ]
check $? "host rejects --active-lanes > lanes"
$B/asian_fpga_sim --xclbin sim.xclbin --paths 5000 --steps 3 --put --verify --verify-pairs 100 --active-lanes 2 > $B/host.log2
check $? "host put, subset verify with --active-lanes 2 (flags passed to the verify call)"
$B/asian_fpga_sim --xclbin sim.xclbin --paths 5000 --steps 3 --put --verify --verify-pairs 100 > $B/host.log
check $? "host put, subset verify (extra kernel call)"
for k in project platform impl active_lanes ok t_total_s t_compute_s t_xclbin_s t_alloc_s t_read_s t_h2d_s t_kernel_s t_d2h_s t_write_s n_cu lanes msteps_per_s price stderr mom_hash; do
  grep -q "^RESULT $k=" $B/host.log || { check 1 "host key $k"; }
done
[ "$(kv $B/host.log lanes)" = "$LANES" ]; check $? "host reports lanes=$LANES from the kernel info word"
grep -q '^RESULT_JSON {' $B/host.log && grep -q '^MARK start' $B/host.log && grep -q '^MARK end' $B/host.log; check $? "host RESULT/RESULT_JSON/MARK format"
$B/asian_fpga_sim --paths 10 > /dev/null 2>&1; [ $? = 1 ]; check $? "host without --xclbin exits 1"

step "4. independent double-precision reference (mt19937_64 + Box-Muller), |diff| <= 3 SE"
g++ -std=c++17 -O2 -march=native -ffp-contract=off $INC test/ref_double.cpp -o $B/ref_double
$B/ref_double 200000 64 0.2 0; check $? "call, M=64, sigma 0.2 (+ geometric closed form)"
$B/ref_double 100000 16 0.4 1; check $? "put, M=16, sigma 0.4 (+ geometric closed form)"

step "5. real XRT header syntax check"
g++ -std=c++17 -fsyntax-only -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver $INC host/asian_fpga.cpp
check $? "host/asian_fpga.cpp against real XRT headers"

step "6. sandbox throughput (CPU, 1 thread; the C-simulated kernel is not a speed reference)"
for nb in 1 2 4; do
  $B/asian_cpu --paths 524288 --steps 64 --runs 3 --threads 1 --interleave $nb | grep -E "^RESULT (impl|ns_per_path_step_core)=" | tr '\n' ' '; echo
done

echo
[ $FAIL = 0 ] && echo "09_MC_Asian_Sweep sim_test: ALL PASS" || echo "09_MC_Asian_Sweep sim_test: FAILURES"
exit $FAIL
