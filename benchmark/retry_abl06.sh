#!/usr/bin/env bash
# 06 ablation, 2 CUs: 225 MHz missed by 0.055 ns. Retry with congestion spreading and
# post-route phys-opt at the same clock as the main design; fall back to 200 MHz.
set -u
cd "$(dirname "$0")"
SUITE_DIR="$(pwd)"; export SUITE_DIR
source ./config.sh
for f in "$VITIS_SETTINGS" "$XRT_SETUP"; do [ -f "$f" ] && source "$f" >/dev/null 2>&1; done
PHYS="--vivado.prop run.impl_1.STEPS.PHYS_OPT_DESIGN.IS_ENABLED=true \
--vivado.prop run.impl_1.STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE=AggressiveExplore \
--vivado.prop run.impl_1.STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED=true \
--vivado.prop run.impl_1.STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE=AggressiveExplore \
--vivado.prop run.impl_1.STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE=AggressiveExplore"
for spec in "225000000 Congestion_SpreadLogic_high" "200000000 Performance_Explore"; do
    set -- $spec
    echo "[$(date '+%F %T')] 06 ablation 2CU: $1 Hz, $2"
    rm -rf v2_projects/06_LiveStream_Multi/build_2cu/link
    if make -C v2_projects/06_LiveStream_Multi xclbin fpga_reports BUILD=build_2cu NCU=2 \
            FREQ_HZ="$1" STRATEGY="$2" VPP_EXTRA="$PHYS" \
            VISION_ROOT="$VISION_ROOT" JOBS=6 VIVADO_THREADS=32; then
        echo "[$(date '+%F %T')] SUCCESS at $1 Hz"; exit 0
    fi
    echo "[$(date '+%F %T')] failed at $1 Hz"
done
echo "[$(date '+%F %T')] 06 ablation did not close at 225 or 200 MHz"
