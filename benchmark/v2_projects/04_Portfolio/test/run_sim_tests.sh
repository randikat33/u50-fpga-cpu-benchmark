#!/usr/bin/env bash
# run_sim_tests.sh - sandbox verification of the v2 portfolio project (called by `make sim_test`)
set -u
B=${BUILD:-build}
SHIM=${SHIM:-/home/USER/shim}
T=$B/simrun
mkdir -p $T
PASS=0; FAIL=0
ok()   { echo "  [PASS] $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
run()  { local name=$1; shift; if "$@" > $T/$name.log 2>&1; then ok "$name"; else bad "$name (see $T/$name.log)"; tail -5 $T/$name.log; fi; }
key()  { grep -o "^RESULT $2=[^ ]*" $1 | head -1 | cut -d= -f2; }
same() { if cmp -s "$2" "$3"; then ok "$1"; else bad "$1 ($2 vs $3)"; fi; }

echo "== 1. RNG / ICDF / exp quality"
run rng_icdf $B/sim/test_rng_icdf
grep -E "^(icdf|fexp|stream|avx512)" $T/rng_icdf.log | sed 's/^/     /'

echo "== 2. kernel C-sim == scalar reference == AVX-512 (bit-identical moments)"
run tb_kernel_L8_IL128 $B/sim/tb_kernel 29
run tb_kernel_L3_IL16 $B/sim/tb_kernel_l3 40
grep -h "^tb_kernel:" $T/tb_kernel_L8_IL128.log $T/tb_kernel_L3_IL16.log | sed 's/^/     /'

echo "== 3. v1 file-format compatibility"
$B/pf_gen --trades 300 --portfolio $T/p300.bin --market $T/m.bin > /dev/null
$B/pf_gen 1 1024 $T/p1.bin $T/m1.bin > /dev/null
V1M=/home/USER/x/67535bca-Financial_Modeling_2/Financial_Modeling_2/market.bin
if [ -f $V1M ]; then same "market.bin identical to v1 generator output" $T/m.bin $V1M; fi
[ "$(stat -c %s $T/p300.bin)" = "$((4 + 300*44))" ] && ok "portfolio size 4+44n" || bad "portfolio size"

W="--portfolio $T/p300.bin --market $T/m.bin --paths1 100 --steps1 8 --topk 23 --paths2 3000 --steps2 5 --seed 99"
echo "== 4. two-stage flow: CPU scalar == CPU AVX-512 (gather / scalar LUT, 1 and 2 threads)"
run cpu_scalar   $B/pf_cpu $W --impl scalar --threads 2 --out $T/f_scalar.bin --out-topk $T/k_scalar.bin --out-stage1 $T/s1_scalar.bin --verify
run cpu_avx_g    $B/pf_cpu $W --impl avx512 --lut gather --threads 2 --out $T/f_avxg.bin --out-topk $T/k_avxg.bin --out-stage1 $T/s1_avxg.bin --verify full
run cpu_avx_s1   $B/pf_cpu $W --impl avx512 --lut scalar --threads 1 --grain 64 --out $T/f_avxs.bin --out-topk $T/k_avxs.bin
same "final results scalar == avx512(gather)" $T/f_scalar.bin $T/f_avxg.bin
same "final results scalar == avx512(scalar lut, 1 thread, grain 64)" $T/f_scalar.bin $T/f_avxs.bin
same "stage-1 results scalar == avx512" $T/s1_scalar.bin $T/s1_avxg.bin
same "top-K selection + refined results identical" $T/k_scalar.bin $T/k_avxg.bin

echo "== 5. host end-to-end via xrt_sim (kernel C-sim) vs CPU"
run host_2cu $B/sim/pf_fpga_sim --xclbin sim.xclbin --cus 2 $W --out $T/f_host2.bin --out-topk $T/k_host2.bin --out-stage1 $T/s1_host2.bin --verify full
[ "$(key $T/host_2cu.log n_cu)" = "2" ] && ok "host used 2 CUs, kernel_calls=$(key $T/host_2cu.log kernel_calls)" || bad "host CU count"
same "host(2 CU) final == CPU" $T/f_host2.bin $T/f_scalar.bin
same "host(2 CU) stage-1 == CPU" $T/s1_host2.bin $T/s1_scalar.bin
same "host(2 CU) top-K selection == CPU" $T/k_host2.bin $T/k_scalar.bin
run host_1cu_chunked $B/sim/pf_fpga_sim --xclbin sim.xclbin --cus 1 --max-bo-mb 0.002 $W --out $T/f_host1c.bin --verify
same "host(1 CU, 47-trade BO chunks, $(key $T/host_1cu_chunked.log kernel_calls) calls) == CPU" $T/f_host1c.bin $T/f_scalar.bin
run host_3cu_ft $B/sim/pf_fpga_sim --xclbin sim.xclbin --cus 3 $W --scheme ft --paths2 700 --out $T/f_host_ft.bin
run cpu_ft      $B/pf_cpu $W --scheme ft --paths2 700 --out $T/f_cpu_ft.bin
same "full truncation: host(3 CU) == CPU" $T/f_host_ft.bin $T/f_cpu_ft.bin
run host_1trade_2cu $B/sim/pf_fpga_sim --xclbin sim.xclbin --cus 2 --portfolio $T/p1.bin --market $T/m1.bin --paths1 5 --steps1 3 --paths2 1000 --steps2 2 --topk 50 --out $T/f_h1.bin --verify full
run cpu_1trade  $B/pf_cpu --portfolio $T/p1.bin --market $T/m1.bin --paths1 5 --steps1 3 --paths2 1000 --steps2 2 --topk 50 --out $T/f_c1.bin
same "1 trade on 2 CUs (one CU idle, lane-split stage 2) == CPU" $T/f_h1.bin $T/f_c1.bin
for f in host_2cu host_1cu_chunked host_1trade_2cu; do
  [ "$(key $T/$f.log verify_mismatch)" = "0" ] && [ "$(key $T/$f.log bad_tags)" = "0" ] && ok "$f: --verify mismatches 0, tags ok" || bad "$f verify"
done

echo "== 6. v1 environment-variable fallback"
PATHS_SMALL=100 PATHS_LARGE=3000 N_STEPS=5 TOP_K=23 SEED=99 run cpu_env $B/pf_cpu --portfolio $T/p300.bin --market $T/m.bin --out $T/f_env.bin
run cpu_cli5 $B/pf_cpu --portfolio $T/p300.bin --market $T/m.bin --paths1 100 --steps1 5 --paths2 3000 --steps2 5 --topk 23 --seed 99 --out $T/f_cli5.bin
same "env vars == CLI" $T/f_env.bin $T/f_cli5.bin
[ "$(key $T/cpu_env.log paths2)" = "3000" ] && ok "env PATHS_LARGE honoured" || bad "env PATHS_LARGE"

echo "== 7. prices vs independent double-precision Box-Muller reference (3 SE)"
$B/pf_gen --trades 2000 --portfolio $T/p2k.bin --market $T/m2.bin > /dev/null
run ref_bm $B/sim/ref_bm --portfolio $T/p2k.bin --market $T/m2.bin --paths 200000 --steps 16
grep "^ref_bm:" $T/ref_bm.log | sed 's/^/     /'

echo "== 8. real-XRT header syntax check"
if make -s xrt_syntax SHIM=$SHIM > $T/xrt_syntax.log 2>&1; then ok "host compiles against real XRT headers"; else bad "xrt syntax"; cat $T/xrt_syntax.log | head; fi

echo "== 9. sandbox throughput (CPU, $(nproc) cores; kernel C-sim speed is not meaningful)"
$B/pf_gen --trades 20000 --portfolio $T/p20k.bin --market $T/m.bin > /dev/null
for impl in avx512 scalar; do
  $B/pf_cpu --portfolio $T/p20k.bin --market $T/m.bin --topk 100 --paths2 16384 --impl $impl > $T/tp_$impl.log 2>&1
  echo "     $impl: $(key $T/tp_$impl.log impl) threads=$(key $T/tp_$impl.log threads) t_compute_s=$(key $T/tp_$impl.log t_compute_s) msteps_per_s=$(key $T/tp_$impl.log msteps_per_s)"
done
$B/pf_cpu --portfolio $T/p20k.bin --market $T/m.bin --topk 100 --paths2 16384 --threads 1 > $T/tp_1t.log 2>&1
echo "     avx512 1 thread: msteps_per_s=$(key $T/tp_1t.log msteps_per_s)"
same "throughput runs identical results across impl (checksum)" <(key $T/tp_avx512.log checksum) <(key $T/tp_scalar.log checksum)

echo
echo "sim_test summary: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ]
