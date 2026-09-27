#!/usr/bin/env bash
# =============================================================================
#  mc_warm.sh - MC Heston size sweep with BOTH sides warmed up.
#
#  Why: the campaign ran MC with --runs 3 --warmup 0, so the CPU's first-pass costs (thread
#  start-up, first touch of memory, cold caches) sat inside its 3-run median. That made the
#  CPU look ~15% slower at small and mid sizes (campaign 2.2-2.4x; warm 40-run check: 2.0x).
#  Here both programs do 2 untimed warm-up passes (outside the MARK window) and 10 timed
#  passes; t_compute_s = median of the 10.
#
#  Sizes: all 11 campaign sizes. Platforms: FPGA (selected design), CPU AVX-512 on socket 0
#  (24 cores = campaign baseline) and on the whole server (96 threads). 5 reps.
#
#  ./mc_warm.sh            (~30-45 min, resumable)          Results: results_mc_warm/
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_mc_warm"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR"
exec > >(tee -a "$RESULTS_DIR/mc_warm.log") 2>&1
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

section "MC warm sweep: ${#MC2_CONFIGS[@]} sizes x (FPGA + ${CONFIGS[*]}) x $REPS reps"
for rep in $(seq -f "rep%g" 1 "$REPS"); do
    for c in "${MC2_CONFIGS[@]}"; do
        paths=${c%%:*}; steps=${c##*:}; cfg="${paths}x${steps}"
        export OMP_NUM_THREADS=24 OMP_PLACES=cores OMP_PROC_BIND=close OMP_DYNAMIC=false
        measure_v2 "$RAW_DIR/$cfg/fpga/$rep" --xclbin "$MC2_XCLBIN" --xrt-ini "$INI" --timeout 1800 \
            --label "mc $cfg fpga $rep" -- \
            "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" --paths "$paths" --steps "$steps" "${WARM[@]}"
        for k in "${CONFIGS[@]}"; do
            OMP_NUM_THREADS=$(count "${CORES[$k]}"); export OMP_PLACES=${PLACES[$k]}
            measure_v2 "$RAW_DIR/$cfg/$k/$rep" --cores "${CORES[$k]}" --node "${NODE[$k]}" --timeout 1800 \
                --label "mc $cfg $k $rep" -- \
                "$MC2_CPU_BIN" --impl avx512 --threads "$OMP_NUM_THREADS" --paths "$paths" --steps "$steps" "${WARM[@]}"
        done
    done
    cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
done
touch "$RESULTS_DIR/DONE"
info "MC warm sweep finished -> $RESULTS_DIR  (zip the whole folder; it is small)"
