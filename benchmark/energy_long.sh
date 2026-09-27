#!/usr/bin/env bash
# =============================================================================
#  energy_long.sh - supplementary ENERGY measurement for the short kernels.
#
#  Why: the power monitor samples at 10 Hz. MC 4.2M x 64, AES 1 GB and Conv 8K finish in
#  0.01-0.25 s, so their energy windows contain only 0-3 power samples. Here the same work is
#  repeated inside ONE measured window (>= 2-3 s, 20-90 samples); t_compute_s is still the time
#  of one pass, so energy per unit of work is directly comparable with the campaign.
#
#     MC 4.2M x 64      --runs 40         (FPGA ~4 s, CPU 3.7-9.4 s)
#     Conv RGB8 8K      --repeat 200      (FPGA ~9 s, CPU 1.8-3.7 s)
#     AES-256 noio      FPGA --size-mb 16384 (~4 s; the FPGA host reuses one buffer per CU)
#                       CPU  --size-mb 1024 --repeat 60 (2.6-5 s)  -> compared per GB
#
#  Platforms: FPGA (campaign designs) + the 4 CPU configurations of cpu_scaling.sh
#  (s0_c24, s0_t48, s01_c48, s01_t96). 5 reps, same harness, markers and cool-downs.
#  Needs the rebuilt aes_cpu (with --repeat):  make -C v2_projects/01_AES cpu
#
#  ./energy_long.sh        (~30-40 min, resumable)      Results: results_energy/
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_energy"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR"
exec > >(tee -a "$RESULTS_DIR/energy.log") 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
[ -f "$STATE_DIR/idle_temps.txt" ] || cp "$SUITE_DIR/results/.state/idle_temps.txt" "$STATE_DIR/" 2>/dev/null

"$AES2_CPU_BIN" --help 2>&1 | grep -q -- "--repeat" \
    || { echo "aes_cpu has no --repeat: rebuild it first:  make -C v2_projects/01_AES cpu"; exit 1; }

N0=$(cat /sys/devices/system/node/node0/cpulist); N1=$(cat /sys/devices/system/node/node1/cpulist)
P0=${N0%%,*}; P1=${N1%%,*}
count() { python3 -c "
import sys
n=0
for part in sys.argv[1].split(','):
    a,_,b=part.partition('-'); n+= (int(b)-int(a)+1) if b else 1
print(n)" "$1"; }
declare -A CORES=( [s0_c24]="$P0" [s0_t48]="$N0" [s01_c48]="$P0,$P1" [s01_t96]="$N0,$N1" )
declare -A NODE=(  [s0_c24]=0 [s0_t48]=0 [s01_c48]=0,1 [s01_t96]=0,1 )
declare -A PLACES=([s0_c24]=cores [s0_t48]=threads [s01_c48]=cores [s01_t96]=threads)
CONFIGS=(s0_c24 s0_t48 s01_c48 s01_t96)
INI="$COMMON/xrt_timing.ini"

cpu() {   # cpu <workload> <config> <rep> <cmd...>
    local w=$1 c=$2 rep=$3; shift 3
    export OMP_NUM_THREADS; OMP_NUM_THREADS=$(count "${CORES[$c]}")
    export OMP_PLACES=${PLACES[$c]} OMP_PROC_BIND=close OMP_DYNAMIC=false
    measure_v2 "$RAW_DIR/$w/$c/$rep" --cores "${CORES[$c]}" --node "${NODE[$c]}" --timeout 1800 \
        --label "$w $c $rep" -- "$@"
}
fpga() {  # fpga <workload> <rep> <xclbin> <cmd...>
    local w=$1 rep=$2 x=$3; shift 3
    export OMP_NUM_THREADS=24 OMP_PLACES=cores OMP_PROC_BIND=close OMP_DYNAMIC=false
    measure_v2 "$RAW_DIR/$w/fpga/$rep" --xclbin "$x" --xrt-ini "$INI" --timeout 1800 \
        --label "$w fpga $rep" -- "$@"
}
T() { count "${CORES[$1]}"; }

section "Long-window energy runs (FPGA + 4 CPU configurations, $REPS reps)"
for rep in $(seq -f "rep%g" 1 "$REPS"); do
    fpga mc_4194304x64 "$rep" "$MC2_XCLBIN" "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" \
        --paths 4194304 --steps 64 --runs 40 --warmup 0
    for c in "${CONFIGS[@]}"; do
        cpu mc_4194304x64 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
            --paths 4194304 --steps 64 --runs 40 --warmup 0
    done
    fpga conv_rgb8_8K "$rep" "$CONV2_XCLBIN" "$CONV2_FPGA_BIN" --xclbin "$CONV2_XCLBIN" \
        --repeat 200 --variant rgb8 --synthetic 7680x4320
    for c in "${CONFIGS[@]}"; do
        cpu conv_rgb8_8K "$c" "$rep" "$CONV2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
            --repeat 200 --variant rgb8 --synthetic 7680x4320
    done
    fpga aes_noio "$rep" "$AES2_XCLBIN" "$AES2_FPGA_BIN" --xclbin "$AES2_XCLBIN" \
        --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 16384
    for c in "${CONFIGS[@]}"; do
        cpu aes_noio "$c" "$rep" "$AES2_CPU_BIN" --impl vaes --threads "$(T "$c")" \
            --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 1024 --repeat 60
    done
    cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
done
touch "$RESULTS_DIR/DONE"
info "energy runs finished -> $RESULTS_DIR  (zip the whole folder; it is small)"
