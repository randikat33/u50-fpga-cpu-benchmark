#!/usr/bin/env bash
# v2 06 - 4K live stream -> 5-rung ABR ladder + 5 encoders. Same pipeline code on both platforms.
#   groups: pipe (decode -> ladder -> 5 encoders) | ro (ladder stage only; FPGA batch sizes,
#           CPU thread configurations) | ablation (2-CU xclbin, if built) | cold
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=06_LiveStream_Multi; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
unset OMP_PROC_BIND OMP_PLACES
O="$DATA_DIR/lm_out"

check() {
    v2_need "v2 LiveStream multi" "$LM2_FPGA_BIN" "$LM2_CPU_BIN" "$LM2_XCLBIN" || return 1
    need "$LM2_VIDEO" "4K video"
}

one() {   # one <group> <config> <platdir> <rep> <xclbin> <args...>
    local grp=$1 cfg=$2 plat=$3 rep=$4 x=$5; shift 5
    local o="$OUT/$grp/$cfg/$plat/$rep" v=()
    [[ $rep == rep1 ]] && v=(--verify)
    warm_file "$LM2_VIDEO"
    rm -rf "$O"; mkdir -p "$O"
    if [[ $plat == fpga* ]]; then
        measure_v2 "$o" --label "$P $grp $cfg $plat $rep" --xclbin "$x" --xrt-ini "$(ini_for "$rep")" \
            --timeout "$LM2_TIMEOUT_S" -- "$LM2_FPGA_BIN" --xclbin "$x" --video "$LM2_VIDEO" "$@" "${v[@]}"
    else
        measure_v2 "$o" --label "$P $grp $cfg $plat $rep" --timeout "$LM2_TIMEOUT_S" -- \
            "$LM2_CPU_BIN" --video "$LM2_VIDEO" --threads 24 "$@" "${v[@]}"
    fi
    rm -rf "$O"
}

run() {
    local rep b t w c
    local SINK=(--sink "$LM2_SINK") FR=()
    [ "$LM2_SINK" != null ] && SINK+=(--out-dir "$O")
    [ "$LM2_FRAMES" -gt 0 ] 2>/dev/null && FR=(--frames "$LM2_FRAMES")
    section "$P v2: pipeline, 5 outputs (sink $LM2_SINK)"
    for rep in $(rep_list); do
        one pipe all5 fpga "$rep" "$LM2_XCLBIN" "${SINK[@]}" "${FR[@]}"
        [[ $rep == profiled* ]] && continue
        one pipe all5 cpu "$rep" "" "${SINK[@]}" "${FR[@]}"
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    section "$P v2: ladder stage only"
    for rep in $(rep_list); do
        for b in "${LM2_BATCHES[@]}"; do
            # One HBM pseudo-channel holds 256 MB.  A 4K frame is 24.9 MB, so
            # slots x batch x 24.9 MB must stay below it: 3 slots x batch 4 = 299 MB fails
            # ("failed to allocate userptr bo"), 2 slots x batch 4 = 199 MB fits.
            spw=(); [ "$b" -ge 4 ] && spw=(--slots-per-worker 2)
            one ro all5 "fpga_b$b" "$rep" "$LM2_XCLBIN" --resize-only --iters "$LM2_RO_ITERS" --batch "$b" "${spw[@]}"
        done
        [[ $rep == profiled* ]] && continue
        for t in "${LM2_CPU_TUNE[@]}"; do
            w=${t%%:*}; c=${t##*:}
            one ro all5 "cpu_w${w}c${c}" "$rep" "" --resize-only --iters "$LM2_RO_ITERS" --workers "$w" --cv-threads "$c"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    if [ -f "$LM2_XCLBIN_ABLATION" ]; then
        section "$P v2: CU-scaling ablation (2 CUs)"
        for rep in $(seq -f "rep%g" 1 "$REPS"); do
            one ablation all5 fpga_2cu "$rep" "$LM2_XCLBIN_ABLATION" --resize-only --iters "$LM2_RO_ITERS"
            one ablation_pipe all5 fpga_2cu "$rep" "$LM2_XCLBIN_ABLATION" "${SINK[@]}" "${FR[@]}"
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    fi
    section "$P v2: cold probes"
    local k
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/all5/fpga/cold$k/DONE" ] && continue
        switch_bitstream_v2 "$LM2_XCLBIN"
        one cold all5 fpga "cold$k" "$LM2_XCLBIN" "${SINK[@]}" "${FR[@]}"
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P v2: build the programs first (./build_v2.sh)"; exit 1; }; run ;;
esac
