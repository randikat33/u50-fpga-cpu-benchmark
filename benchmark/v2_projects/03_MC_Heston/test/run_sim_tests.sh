#!/usr/bin/env bash
# run_sim_tests.sh - sandbox verification of 03_MC_Heston (make sim_test). Exit 0 = all pass.
set -u -o pipefail
cd "$(dirname "$0")/.."
SHIM=${SHIM:-/home/USER/shim}
V2_ROOT=${V2_ROOT:-$(cd .. && pwd)}
LANES=${LANES:-6}
B=build/sim
mkdir -p $B
HLS_INC="-I$SHIM/include -I$SHIM/HLS_arbitrary_Precision_Types/include"
INC="-I$V2_ROOT/common -Icommon -Ikernel -Icpu"
CSIM="g++ -std=c++17 -O2 -w -march=native -ffp-contract=off $HLS_INC $INC"
CPUF="-std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off -Wall -Wno-unknown-pragmas $INC"
FAIL=0
step() { echo; echo "=== $*"; }
check() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
kv() { grep -m1 "^RESULT $2=" "$1" | cut -d= -f2; }

step "1. ROM tables reproducible from scripts/gen_tables.cpp"
g++ -O2 -std=c++17 scripts/gen_tables.cpp -o $B/gen_tables && $B/gen_tables > $B/mc_tables.h 2> $B/gen_tables.log
cat $B/gen_tables.log
cmp -s $B/mc_tables.h common/mc_tables.h; check $? "common/mc_tables.h is up to date"

step "2. kernel C-sim == scalar reference == AVX-512 (bit-identical), LANES=$LANES,1,3"
for L in $LANES 1 3; do
  $CSIM -DMC_LANES=$L test/tb_kernel.cpp kernel/mc_heston_v2.cpp -o $B/tb_kernel_$L || { check 1 "build tb_kernel L=$L"; continue; }
  $B/tb_kernel_$L | tee $B/tb_kernel_$L.log | tail -n 3
  check ${PIPESTATUS[0]} "tb_kernel LANES=$L"
done

step "3. normal generator quality (4e6 samples) and exp accuracy"
g++ -std=c++17 -O2 -march=native -ffp-contract=off $INC test/icdf_test.cpp -o $B/icdf_test && $B/icdf_test 4000000 | tee $B/icdf_test.log
check ${PIPESTATUS[0]} "icdf_test"

step "4. CPU baseline (production flags): avx512 == scalar, thread-count invariance, --verify"
g++ $CPUF cpu/mc_cpu.cpp -o $B/mc_cpu; check $? "build mc_cpu"
for cfg in "1 1" "33 3" "1000 16" "4097 5" "20001 32"; do
  set -- $cfg
  H=()
  for run in "--impl avx512 --threads 2" "--impl avx512 --threads 1" "--impl scalar --threads 2"; do
    $B/mc_cpu --paths $1 --steps $2 --seed 777 $run --verify > $B/cpu.log; rc=$?
    H+=("$(kv $B/cpu.log mom_hash)")
    [ $rc = 0 ] || { check 1 "mc_cpu $run paths=$1 steps=$2 rc=$rc"; }
  done
  [ "${H[0]}" = "${H[1]}" ] && [ "${H[0]}" = "${H[2]}" ]; check $? "paths=$1 steps=$2: ${H[*]}"
done
$B/mc_cpu --paths 3000 --steps 8 --put --corr 0.4 --verify > $B/cpu_put.log; check $? "put + correlated basket --verify"
$B/mc_cpu --paths 3000 --steps 70000 > /dev/null 2>&1; [ $? != 0 ]; check $? "rejects --steps > 65535"
$B/mc_cpu --help | head -2

step "5. FPGA host end-to-end via xrt_sim (kernel C-sim as device)"
g++ -std=c++17 -O2 -w -march=native -ffp-contract=off -DXRT_SIM -DMC_LANES=$LANES -I$SHIM/xrt_sim $HLS_INC $INC \
    host/mc_fpga.cpp test/sim_register.cpp kernel/mc_heston_v2.cpp -o $B/mc_fpga_sim -pthread; check $? "build host (xrt_sim)"
for cfg in "2001 7 2" "777 16 2" "150 3 1" "3001 4 3"; do
  set -- $cfg
  $B/mc_fpga_sim --xclbin sim.xclbin --paths $1 --steps $2 --cus $3 --runs 2 --verify > $B/host.log; rc=$?
  $B/mc_cpu --paths $1 --steps $2 > $B/cpu.log
  hf=$(kv $B/host.log mom_hash); hc=$(kv $B/cpu.log mom_hash)
  [ $rc = 0 ] && [ "$hf" = "$hc" ] && [ "$(kv $B/host.log price)" = "$(kv $B/cpu.log price)" ]
  check $? "host paths=$1 steps=$2 cus=$3: fpga $hf == cpu $hc, price $(kv $B/host.log price)"
done
$B/mc_fpga_sim --xclbin sim.xclbin --paths 5000 --steps 3 --cus 2 --put --verify --verify-pairs 100 > $B/host.log
check $? "host put, subset verify (extra kernel call)"
for k in project platform impl ok t_total_s t_compute_s t_xclbin_s t_alloc_s t_read_s t_h2d_s t_kernel_s t_d2h_s t_write_s n_cu msteps_per_s price stderr; do
  grep -q "^RESULT $k=" $B/host.log || { check 1 "host key $k"; }
done
grep -q '^RESULT_JSON {' $B/host.log && grep -q '^MARK start' $B/host.log && grep -q '^MARK end' $B/host.log; check $? "host RESULT/RESULT_JSON/MARK format"
$B/mc_fpga_sim --paths 10 > /dev/null 2>&1; [ $? = 1 ]; check $? "host without --xclbin exits 1"

step "6. independent double-precision reference (mt19937_64 + Box-Muller), |diff| <= 3 SE"
g++ -std=c++17 -O2 -march=native -ffp-contract=off $INC test/ref_double.cpp -o $B/ref_double
$B/ref_double 200000 32 0.0 0; check $? "v1 market, call, M=32"
$B/ref_double 60000 8 0.5 1;   check $? "correlated basket, put, M=8"

step "7. real XRT header syntax check"
g++ -std=c++17 -fsyntax-only -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver $INC host/mc_fpga.cpp
check $? "host/mc_fpga.cpp against real XRT headers"

step "8. sandbox throughput (CPU; the C-simulated kernel is not a speed reference)"
$B/mc_cpu --paths 524288 --steps 32 --runs 3 --threads 1 | grep -E "^RESULT (impl|threads|t_compute_s|msteps_per_s|ns_per_path_step_core|price|stderr)="
$B/mc_cpu --paths 524288 --steps 32 --runs 3 | grep -E "^RESULT (threads|msteps_per_s|ns_per_path_step_core)="
$B/mc_cpu --paths 131072 --steps 32 --runs 1 --impl scalar --threads 1 | grep -E "^RESULT (impl|msteps_per_s|ns_per_path_step_core)="
$B/mc_fpga_sim --xclbin sim.xclbin --paths 20000 --steps 16 --cus 2 | grep -E "^RESULT (impl|t_kernel_s|msteps_per_s)="

echo
if [ $FAIL = 0 ]; then echo "sim_test: ALL PASS"; else echo "sim_test: FAILURES"; fi
exit $FAIL
