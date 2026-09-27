#!/usr/bin/env bash
# 05 - Live 4K stream -> ONE output resolution (240p..1080p), 6 CUs: FPGA vs CPU
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=05_LiveStream_Single; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
unset OMP_PROC_BIND OMP_PLACES   # as in the original live-stream scripts (OpenCV/TBB threading)
QNAME=(240p 360p 480p 720p 1080p)

check() {
    local ok=0
    need "$LS_FPGA_BIN" "FPGA host" || ok=1
    need "$LS_XCLBIN"   "xclbin"    || ok=1
    need "$LS_CPU_BIN"  "CPU binary" || ok=1
    need "$LS_VIDEO"    "4K video"  || ok=1
    if [ "$LS_CPU_DIRECT" = 1 ]; then
        need "$LS_CPU_BENCH_BIN" "fair CPU build (optional, patches/build_patched.sh)" || echo "            -> cpu_direct runs will be skipped"
    fi
    return $ok
}

# The FPGA host is a server (web UI on :8888): it is stopped 8 s after it reports
# "Consumer finished". Processing time = "Starting pipeline" -> "Consumer finished".
one_run() {
    local plat=$1 q=$2 rep=$3 grp=$4
    local o="$OUT/$grp/${QNAME[$q]}/$plat/$rep"
    [ -f "$o/DONE" ] && { echo "    (already done: ${o#"$RAW_DIR"/})"; return 0; }
    wait_port_free 8888
    warm_file "$LS_VIDEO"
    local common=(--start-marker "Starting pipeline" --end-marker "Consumer finished" --env "STREAM_QUALITY=$q")
    case $plat in
      fpga)
        measure "$o" --label "$P ${QNAME[$q]} fpga $rep" --cwd "$LS_FPGA_DIR" --xclbin "$LS_XCLBIN" \
            --xrt-ini "$(ini_for "$rep")" --timeout "$LS_TIMEOUT_S" --kill-after-end 8 "${common[@]}" -- \
            "$LS_FPGA_BIN" "$LS_XCLBIN" "$LS_VIDEO" "$q" ;;
      cpu)          # original CPU build (keeps FPGA-style pack/unpack, as in the thesis)
        measure "$o" --label "$P ${QNAME[$q]} cpu $rep" --cwd "$LS_CPU_DIR" \
            --timeout "$LS_TIMEOUT_S" --kill-after-end 30 "${common[@]}" -- "$LS_CPU_BIN" "$LS_VIDEO" ;;
      cpu_direct)   # patched build: decode -> resize -> encode, no artificial pack/unpack
        measure "$o" --label "$P ${QNAME[$q]} cpu_direct $rep" --cwd "$LS_CPU_DIR" \
            --timeout "$LS_TIMEOUT_S" --kill-after-end 30 "${common[@]}" --env CPU_PIPELINE_MODE=direct -- \
            "$LS_CPU_BENCH_BIN" "$LS_VIDEO" ;;
    esac
}

run() {
    local q plats=(cpu)
    [ "$LS_CPU_DIRECT" = 1 ] && [ -x "$LS_CPU_BENCH_BIN" ] && plats+=(cpu_direct)
    for q in "${LS_QUALITIES[@]}"; do
        section "$P: ${QNAME[$q]}  (CPU variants: ${plats[*]})"
        for rep in $(rep_list); do
            one_run fpga "$q" "$rep" main
            [[ $rep == profiled* ]] && continue
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
            for p in "${plats[@]}"; do
                one_run "$p" "$q" "$rep" main
                cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
            done
        done
    done
    section "$P: cold (bitstream-swap) probes (1080p)"
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/1080p/fpga/cold$k/DONE" ] && continue
        switch_bitstream "$LS_XCLBIN"
        one_run fpga 4 "cold$k" cold
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P: fix config.sh first"; exit 1; }; run ;;
esac
