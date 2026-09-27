#!/usr/bin/env bash
# Pre-build the 05 ablation (4 CUs) - work the campaign must do anyway, in its own
# directory (build_4cu), so it cannot collide with the 05 build in build/.
set -u
cd "$(dirname "$0")"
SUITE_DIR="$(pwd)"; export SUITE_DIR
source ./config.sh
for f in "$VITIS_SETTINGS" "$XRT_SETUP"; do [ -f "$f" ] && source "$f" >/dev/null 2>&1; done
exec 9>.prebuild_abl05.lock; flock -n 9 || { echo "already running"; exit 0; }
echo "[$(date '+%F %T')] building 05 ablation, 4 CUs"
nice -n 19 ionice -c3 make -C v2_projects/05_LiveStream_Single xclbin fpga_reports \
    BUILD=build_4cu NCU=4 VISION_ROOT="$VISION_ROOT" JOBS=2 VIVADO_THREADS=8
echo "[$(date '+%F %T')] finished rc=$?"
