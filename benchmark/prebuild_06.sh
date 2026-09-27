#!/usr/bin/env bash
# Pre-build 06_LiveStream_Multi in a private directory, then hand it to the campaign
# only if the campaign has not started 06 itself. No shared directory is ever written.
set -u
cd "$(dirname "$0")"
SUITE_DIR="$(pwd)"; export SUITE_DIR
source ./config.sh
for f in "$VITIS_SETTINGS" "$XRT_SETUP"; do [ -f "$f" ] && source "$f" >/dev/null 2>&1; done
exec 9>.prebuild06.lock; flock -n 9 || { echo "already running"; exit 0; }
P=v2_projects/06_LiveStream_Multi
echo "[$(date '+%F %T')] building $P in build_pre (private)"
nice -n 15 ionice -c3 make -C "$P" xclbin fpga_reports BUILD=build_pre FREQ_HZ="${FREQ:-225000000}" \
    VISION_ROOT="$VISION_ROOT" JOBS=3 VIVADO_THREADS=16
rc=$?
echo "[$(date '+%F %T')] build finished rc=$rc"
[ $rc = 0 ] && [ -f "$P/build_pre/ls_multi.xclbin" ] || { echo "not usable - campaign will build it"; exit 1; }
# hand over only if nothing is touching build/ and the campaign has not started 06
if pgrep -af "06_LiveStream_Multi" | grep -qv prebuild_06; then
    echo "campaign is already working on 06 - keeping build_pre, nothing copied"; exit 0
fi
if [ -f "$P/build/ls_multi.xclbin" ]; then
    echo "campaign already produced build/ls_multi.xclbin - nothing copied"; exit 0
fi
mkdir -p "$P/build"
cp -a "$P/build_pre/." "$P/build/"
echo "[$(date '+%F %T')] handed over to build/ - the campaign will reuse it"
