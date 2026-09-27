#!/usr/bin/env bash
# MC Heston 7 lanes: retry after a 0.002 ns miss at 300 MHz.
#   1. 300 MHz + post-route phys-opt + aggressive routing   (recovers 50-300 ps; we need 2)
#   2. 300 MHz + congestion spreading + phys-opt
#   3. 275 MHz  (fallback; still beats L6 if efficiency holds)
set -u
cd "$(dirname "$0")"
exec 9>.mc_l7.lock; flock -n 9 || { echo "mc_l7_retry.sh is already running"; exit 0; }
SUITE_DIR="$(pwd)"; export SUITE_DIR
source ./config.sh
for f in "$VITIS_SETTINGS" "$XRT_SETUP"; do [ -f "$f" ] && source "$f" >/dev/null 2>&1; done
P=v2_projects/03_MC_Heston; ST=tune_state/03_MC_Heston; mkdir -p "$ST"
PHYS="--vivado.prop run.impl_1.STEPS.PHYS_OPT_DESIGN.IS_ENABLED=true \
--vivado.prop run.impl_1.STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE=AggressiveExplore \
--vivado.prop run.impl_1.STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED=true \
--vivado.prop run.impl_1.STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE=AggressiveExplore"
ROUTE="--vivado.prop run.impl_1.STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE=AggressiveExplore"
echo "L6 reference: 2678.12 Msteps/s - L7 must exceed 2704.90 to win"
attempt() {   # attempt <tag> <freq> <strategy> <extra flags>
    local t=$1 f=$2 s=$3 x=$4 b="build_tune/L7_$1"
    echo "[$(date '+%F %T')] L7 $t: ${f}Hz  STRATEGY=$s"
    rm -rf "$P/$b/link"
    if nice -n 15 ionice -c3 make -C "$P" xclbin fpga_reports BUILD="$b" LANES=7 \
            FREQ_HZ="$f" STRATEGY="$s" VPP_EXTRA="$x" JOBS="${MC_JOBS:-3}" VIVADO_THREADS="${MC_THREADS:-16}" >> "$ST/L7_$t.build.log" 2>&1 \
       && [ -f "$P/$b/mc_heston.xclbin" ]; then
        echo "[$(date '+%F %T')] built - testing on the card"
        numactl --cpunodebind="$NUMA_NODE" --membind="$NUMA_NODE" taskset -c "$CPU_CORES" \
            "$P/build/mc_fpga" --xclbin "$P/$b/mc_heston.xclbin" \
            --paths 4194304 --steps 64 --runs 3 --warmup 1 --verify > "$ST/L7_$t.test.log" 2>&1
        grep -E "^RESULT|msteps_per_s|verify" "$ST/L7_$t.test.log" | head -6
        echo "build dir: $P/$b   logs: $ST/L7_$t.*"
        return 0
    fi
    if grep -q "did not meet timing" "$ST/L7_$t.build.log"; then
        echo "  timing miss: $(grep -o 'slack: [-0-9.]* ns' "$ST/L7_$t.build.log" | tail -1)"; return 1
    fi
    echo "  BUILD ERROR - see $ST/L7_$t.build.log"; return 2
}
attempt phys300 300000000 Performance_ExplorePostRoutePhysOpt "$PHYS $ROUTE" && exit 0
[ $? = 2 ] && exit 1
attempt cong300 300000000 Congestion_SpreadLogic_high         "$PHYS $ROUTE" && exit 0
[ $? = 2 ] && exit 1
attempt f275    275000000 Performance_Explore                 "$PHYS"        && exit 0
echo "L7 closed at neither 300 nor 275 MHz - L6 stands"
