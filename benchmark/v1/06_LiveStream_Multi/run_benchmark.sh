#!/usr/bin/env bash
# 06 - Live 4K stream -> FIVE output resolutions at once (broadcast + 5 resize kernels): FPGA vs CPU
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=06_LiveStream_Multi; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
unset OMP_PROC_BIND OMP_PLACES   # as in the original live-stream scripts (OpenCV/TBB threading)

check() {
    local ok=0
    need "$LM_FPGA_BIN" "FPGA host" || ok=1
    need "$LM_XCLBIN"   "xclbin"    || ok=1
    need "$LM_CPU_BIN"  "CPU binary" || ok=1
    need "$LM_VIDEO"    "4K video"  || ok=1
    return $ok
}

# Processing window (used for FPS and energy):
#   FPGA: "Starting 3-deep pipelined processing" -> "Shutdown signal received"
#   CPU : "Starting parallel processing"          -> "Processing Complete"
# Everything after that (encoder drain, XRT context release ~30 s) is recorded
# separately as teardown and is NOT charged to the processing window.
one_run() {
    local plat=$1 rep=$2 grp=$3
    local o="$OUT/$grp/all5/$plat/$rep"
    [ -f "$o/DONE" ] && { echo "    (already done: ${o#"$RAW_DIR"/})"; return 0; }
    wait_port_free 8888
    warm_file "$LM_VIDEO"
    if [ "$plat" = fpga ]; then
        measure "$o" --label "$P fpga $rep" --cwd "$LM_FPGA_DIR" --xclbin "$LM_XCLBIN" \
            --xrt-ini "$(ini_for "$rep")" --timeout "$LM_TIMEOUT_S" \
            --start-marker "Starting .*pipelined processing" --end-marker "Shutdown signal received" -- \
            "$LM_FPGA_BIN" "$LM_XCLBIN" "$LM_VIDEO"
    else
        measure "$o" --label "$P cpu $rep" --cwd "$LM_CPU_DIR" --timeout "$LM_TIMEOUT_S" \
            --start-marker "Starting parallel processing" --end-marker "Processing Complete" -- \
            "$LM_CPU_BIN" "$LM_VIDEO"
    fi
}

run() {
    section "$P: all five outputs"
    for rep in $(rep_list); do
        one_run fpga "$rep" main
        [[ $rep == profiled* ]] && continue
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        one_run cpu "$rep" main
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    section "$P: cold (bitstream-swap) probes"
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/all5/fpga/cold$k/DONE" ] && continue
        switch_bitstream "$LM_XCLBIN"
        one_run fpga "cold$k" cold
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P: fix config.sh first"; exit 1; }; run ;;
esac
