#!/usr/bin/env bash
# Pre-build the 06 ablation (2 CUs) at 225 MHz - the same clock as the main 06 design,
# so the 1-CU vs 2-CU comparison is not confounded by a clock difference.
set -u
cd "$(dirname "$0")"
SUITE_DIR="$(pwd)"; export SUITE_DIR
source ./config.sh
for f in "$VITIS_SETTINGS" "$XRT_SETUP"; do [ -f "$f" ] && source "$f" >/dev/null 2>&1; done
exec 9>.prebuild_abl06.lock; flock -n 9 || { echo "already running"; exit 0; }
echo "[$(date '+%F %T')] building 06 ablation, 2 CUs at 225 MHz"
nice -n 19 ionice -c3 make -C v2_projects/06_LiveStream_Multi xclbin fpga_reports \
    BUILD=build_2cu NCU=2 FREQ_HZ=225000000 VISION_ROOT="$VISION_ROOT" JOBS=2 VIVADO_THREADS=8
echo "[$(date '+%F %T')] finished rc=$?"
