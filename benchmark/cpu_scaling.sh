#!/usr/bin/env bash
# =============================================================================
#  cpu_scaling.sh - supplementary measurement: how strong is the CPU if it may use
#  the WHOLE server instead of one socket?
#
#  The main campaign pins every CPU baseline to socket 0 (24 physical cores, SMT off),
#  the NUMA node the U50 hangs off.  A reviewer will ask what happens with hyper-threading
#  and with the second socket.  This measures the best CPU implementation of each kernel
#  in four configurations, with the same harness, markers and power monitor:
#
#     s0_c24   socket 0, 24 physical cores          (replicates the campaign baseline)
#     s0_t48   socket 0, 48 hardware threads (SMT)
#     s01_c48  both sockets, 48 physical cores
#     s01_t96  both sockets, 96 hardware threads (SMT)
#
#  power.csv records pkg0/dram0 AND pkg1/dram1, so energy can be computed both for one
#  socket and for the whole server afterwards (analysis/analyze_scaling.py).
#
#  ./cpu_scaling.sh            run everything (about 2 h, resumable like the campaign)
#  Results: results_scaling/
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_scaling"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR"
exec > >(tee -a "$RESULTS_DIR/scaling.log") 2>&1

# idle temperatures for the adaptive cool-down (reuse the full campaign's baseline)
[ -f "$STATE_DIR/idle_temps.txt" ] || cp "$SUITE_DIR/results/.state/idle_temps.txt" "$STATE_DIR/" 2>/dev/null

# ---- core sets from the kernel's NUMA description (no assumptions about numbering)
N0=$(cat /sys/devices/system/node/node0/cpulist)     # e.g. 0-23,48-71
N1=$(cat /sys/devices/system/node/node1/cpulist)     # e.g. 24-47,72-95
P0=${N0%%,*}; P1=${N1%%,*}                            # physical-core ranges
count() { python3 -c "
import sys
n=0
for part in sys.argv[1].split(','):
    a,_,b=part.partition('-'); n+= (int(b)-int(a)+1) if b else 1
print(n)" "$1"; }

declare -A CORES=( [s0_c24]="$P0"         [s0_t48]="$N0"
                   [s01_c48]="$P0,$P1"    [s01_t96]="$N0,$N1" )
declare -A NODE=(  [s0_c24]=0 [s0_t48]=0 [s01_c48]=0,1 [s01_t96]=0,1 )
declare -A PLACES=([s0_c24]=cores [s0_t48]=threads [s01_c48]=cores [s01_t96]=threads)
CONFIGS=(s0_c24 s0_t48 s01_c48 s01_t96)

section "CPU scaling: socket 0 = $N0, socket 1 = $N1"
for c in "${CONFIGS[@]}"; do info "$c: cores ${CORES[$c]} (threads $(count "${CORES[$c]}"), node ${NODE[$c]}, OMP_PLACES=${PLACES[$c]})"; done

# ---- one measured run
one() {   # one <workload> <config> <rep> <cmd...>
    local w=$1 c=$2 rep=$3; shift 3
    local o="$RAW_DIR/$w/$c/$rep"
    export OMP_NUM_THREADS; OMP_NUM_THREADS=$(count "${CORES[$c]}")
    export OMP_PLACES=${PLACES[$c]} OMP_PROC_BIND=close OMP_DYNAMIC=false
    measure_v2 "$o" --cores "${CORES[$c]}" --node "${NODE[$c]}" --timeout 3600 \
        --label "$w $c $rep" -- "$@"
}
T() { count "${CORES[$1]}"; }

W="$RESULTS_DIR/work"; mkdir -p "$W"
for rep in $(seq -f "rep%g" 1 "$REPS"); do
    for c in "${CONFIGS[@]}"; do
        one mc_4194304x64   "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
            --paths 4194304 --steps 64 --runs 3 --warmup 0
        one mc_67108864x128 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
            --paths 67108864 --steps 128 --runs 3 --warmup 0
        one pf_131072       "$c" "$rep" "$PF2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_$c.bin"
        one aes_noio_1024MB "$c" "$rep" "$AES2_CPU_BIN" --impl vaes --threads "$(T "$c")" \
            --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 1024
        one conv_rgb8_8K    "$c" "$rep" "$CONV2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
            --repeat 3 --variant rgb8 --synthetic 7680x4320
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
done
rm -rf "$W"
touch "$RESULTS_DIR/DONE"
info "CPU scaling finished -> $RESULTS_DIR  (zip the whole folder incl. power.csv; it is small)"
