#!/usr/bin/env bash
# v2 05 - 4K live stream -> one output quality. Same pipeline code on both platforms.
#   groups: pipe (decode -> resize -> encoder sink, the live-service view)
#           ro   (resize stage only; CPU in several thread configurations)
#           ablation (4-CU xclbin, if built) | cold (bitstream load included)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=05_LiveStream_Single; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
unset OMP_PROC_BIND OMP_PLACES   # OpenCV thread pool manages its own threads (as in v1)
QN=(240p 360p 480p 720p 1080p)
O="$DATA_DIR/ls_out"

check() {
    v2_need "v2 LiveStream single" "$LS2_FPGA_BIN" "$LS2_CPU_BIN" "$LS2_XCLBIN" || return 1
    need "$LS2_VIDEO" "4K video"
}

sink_args() {   # prints the sink options for the pipeline group
    case $LS2_SINK in
        file) echo "--sink file --out $O/out.mp4" ;;
        rtmp) echo "--sink rtmp" ;;
        *)    echo "--sink null" ;;
    esac
}

one() {   # one <group> <q> <platdir> <rep> <xclbin> <args...>
    local grp=$1 q=$2 plat=$3 rep=$4 x=$5; shift 5
    local o="$OUT/$grp/${QN[$q]}/$plat/$rep" v=()
    [[ $rep == rep1 ]] && v=(--verify)
    warm_file "$LS2_VIDEO"
    if [[ $plat == fpga* ]]; then
        measure_v2 "$o" --label "$P $grp ${QN[$q]} $plat $rep" --xclbin "$x" --xrt-ini "$(ini_for "$rep")" \
            --timeout "$LS2_TIMEOUT_S" -- "$LS2_FPGA_BIN" --xclbin "$x" --video "$LS2_VIDEO" --quality "$q" "$@" "${v[@]}"
    else
        measure_v2 "$o" --label "$P $grp ${QN[$q]} $plat $rep" --timeout "$LS2_TIMEOUT_S" -- \
            "$LS2_CPU_BIN" --video "$LS2_VIDEO" --quality "$q" --threads 24 "$@" "${v[@]}"
    fi
    rm -f "$O"/out.mp4
}

run() {
    mkdir -p "$O"
    local q rep t w c
    # shellcheck disable=SC2207
    local SINK=($(sink_args)) FR=()
    [ "$LS2_FRAMES" -gt 0 ] 2>/dev/null && FR=(--frames "$LS2_FRAMES")
    for q in "${LS2_QUALITIES[@]}"; do
        section "$P v2: ${QN[$q]} pipeline (sink $LS2_SINK)"
        for rep in $(rep_list); do
            one pipe "$q" fpga "$rep" "$LS2_XCLBIN" "${SINK[@]}" "${FR[@]}"
            [[ $rep == profiled* ]] && continue
            one pipe "$q" cpu "$rep" "" "${SINK[@]}" "${FR[@]}"
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
        section "$P v2: ${QN[$q]} resize stage only"
        for rep in $(rep_list); do
            one ro "$q" fpga "$rep" "$LS2_XCLBIN" --resize-only --iters "$LS2_RO_ITERS"
            [[ $rep == profiled* ]] && continue
            for t in "${LS2_CPU_TUNE[@]}"; do
                w=${t%%:*}; c=${t##*:}
                one ro "$q" "cpu_w${w}c${c}" "$rep" "" --resize-only --iters "$LS2_RO_ITERS" --workers "$w" --cv-threads "$c"
            done
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    done
    if [ -f "$LS2_XCLBIN_ABLATION" ]; then
        section "$P v2: CU-scaling ablation (4 CUs, 1080p)"
        for rep in $(seq -f "rep%g" 1 "$REPS"); do
            one ablation 4 fpga_4cu "$rep" "$LS2_XCLBIN_ABLATION" --resize-only --iters "$LS2_RO_ITERS"
            one ablation_pipe 4 fpga_4cu "$rep" "$LS2_XCLBIN_ABLATION" "${SINK[@]}" "${FR[@]}"
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    fi
    section "$P v2: cold probes (1080p pipeline)"
    local k
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/1080p/fpga/cold$k/DONE" ] && continue
        switch_bitstream_v2 "$LS2_XCLBIN"
        one cold 4 fpga "cold$k" "$LS2_XCLBIN" "${SINK[@]}" "${FR[@]}"
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P v2: build the programs first (./build_v2.sh)"; exit 1; }; run ;;
esac
