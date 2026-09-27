#!/usr/bin/env bash
# mc_steady.sh - MC Heston size sweep with the CPU at its SUSTAINED clock.
# Every CPU run first does >= 3 s of untimed warm-up passes (firmware clock ramp ~1.5 s),
# then 10 timed passes. FPGA: 2 warm-up + 10 timed. 11 sizes x (FPGA, CPU 24 c, CPU 96 t) x 5 reps.
# ./mc_steady.sh   (~40-50 min, resumable)   Results: results_mc_steady/
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_mc_steady"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR"
exec > >(tee -a "$RESULTS_DIR/mc_steady.log") 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
[ -f "$STATE_DIR/idle_temps.txt" ] || cp "$SUITE_DIR/results/.state/idle_temps.txt" "$STATE_DIR/" 2>/dev/null

N0=$(cat /sys/devices/system/node/node0/cpulist); N1=$(cat /sys/devices/system/node/node1/cpulist)
P0=${N0%%,*}
count() { python3 -c "
import sys
n=0
for part in sys.argv[1].split(','):
    a,_,b=part.partition('-'); n+= (int(b)-int(a)+1) if b else 1
print(n)" "$1"; }
declare -A CORES=( [s0_c24]="$P0" [s01_t96]="$N0,$N1" )
declare -A NODE=(  [s0_c24]=0 [s01_t96]=0,1 )
declare -A PLACES=([s0_c24]=cores [s01_t96]=threads)
CONFIGS=(s0_c24 s01_t96)
INI="$COMMON/xrt_timing.ini"
WARM=(--runs 10 --warmup 2)
declare -A T_S0=( [131072x32]=0.0038 [262144x32]=0.0075 [524288x32]=0.0151 [1048576x32]=0.0300
                  [524288x64]=0.0292 [2097152x32]=0.0604 [524288x128]=0.0573 [524288x256]=0.1141
                  [4194304x64]=0.1955 [16777216x64]=0.7816 [67108864x128]=6.1642 )
declare -A T_96=( [131072x32]=0.0016 [262144x32]=0.0030 [524288x32]=0.0059 [1048576x32]=0.0116
                  [524288x64]=0.0113 [2097152x32]=0.0231 [524288x128]=0.0221 [524288x256]=0.0438
                  [4194304x64]=0.0906 [16777216x64]=0.3079 [67108864x128]=2.4301 )
nwarm() { python3 -c "import math,sys; print(max(2, math.ceil(3.0/float(sys.argv[1]))))" "$1"; }

section "MC steady-clock sweep: ${#MC2_CONFIGS[@]} sizes x (FPGA + ${CONFIGS[*]}) x $REPS reps"
for rep in $(seq -f "rep%g" 1 "$REPS"); do
    for c in "${MC2_CONFIGS[@]}"; do
        paths=${c%%:*}; steps=${c##*:}; cfg="${paths}x${steps}"
        export OMP_NUM_THREADS=24 OMP_PLACES=cores OMP_PROC_BIND=close OMP_DYNAMIC=false
        measure_v2 "$RAW_DIR/$cfg/fpga/$rep" --xclbin "$MC2_XCLBIN" --xrt-ini "$INI" --timeout 1800 \
            --label "mc $cfg fpga $rep" -- \
            "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" --paths "$paths" --steps "$steps" "${WARM[@]}"
        for k in "${CONFIGS[@]}"; do
            OMP_NUM_THREADS=$(count "${CORES[$k]}"); export OMP_PLACES=${PLACES[$k]}
            if [ "$k" = s0_c24 ]; then tp=${T_S0[$cfg]:-1}; else tp=${T_96[$cfg]:-1}; fi
            measure_v2 "$RAW_DIR/$cfg/$k/$rep" --cores "${CORES[$k]}" --node "${NODE[$k]}" --timeout 1800 \
                --label "mc $cfg $k $rep" -- \
                "$MC2_CPU_BIN" --impl avx512 --threads "$OMP_NUM_THREADS" --paths "$paths" --steps "$steps" \
                --runs 10 --warmup "$(nwarm "$tp")"
        done
    done
    cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
done
touch "$RESULTS_DIR/DONE"
info "MC steady-clock sweep finished -> $RESULTS_DIR  (zip the whole folder; it is small)"
