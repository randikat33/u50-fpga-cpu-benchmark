#!/usr/bin/env bash
# v2 02 - 3x3 convolution (sharpen/edge/blur), 3 variants: FPGA vs AVX-512 vs OpenCV
#   groups: image (the thesis 8K PNGs, end-to-end incl. PNG decode/encode)
#           synth (synthetic size sweep, compute only, no output files)
#           cold  (bitstream load included)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=02_Convolution; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
O="$DATA_DIR/conv_out"

check() {
    v2_need "v2 Conv" "$CONV2_FPGA_BIN" "$CONV2_CPU_BIN" "$CONV2_XCLBIN" || return 1
    local v; for v in "${CONV2_VARIANTS[@]}"; do need "${CONV2_IMAGE[$v]}" "8K image ($v)" || echo "            -> the image group of $v is skipped"; done
    return 0
}

one() {   # one <group> <config> <platdir> <rep> <args...>
    local grp=$1 cfg=$2 plat=$3 rep=$4; shift 4
    local o="$OUT/$grp/$cfg/$plat/$rep" v=()
    [[ $rep == rep1 ]] && v=(--verify)
    if [ "$plat" = fpga ]; then
        measure_v2 "$o" --label "$P $grp $cfg fpga $rep" --xclbin "$CONV2_XCLBIN" --xrt-ini "$(ini_for "$rep")" --timeout 1200 -- \
            "$CONV2_FPGA_BIN" --xclbin "$CONV2_XCLBIN" --repeat "$CONV2_REPEAT" "$@" "${v[@]}"
    else
        measure_v2 "$o" --label "$P $grp $cfg $plat $rep" --timeout 1200 -- \
            "$CONV2_CPU_BIN" --impl "${plat#cpu_}" --threads 24 --repeat "$CONV2_REPEAT" "$@" "${v[@]}"
    fi
}

round() {   # round <group> <config> <rep> <args...>
    local grp=$1 cfg=$2 rep=$3; shift 3
    local impl
    one "$grp" "$cfg" fpga "$rep" "$@"
    [[ $rep == profiled* ]] && return 0
    for impl in "${CONV2_CPU_IMPLS[@]}"; do one "$grp" "$cfg" "cpu_$impl" "$rep" "$@"; done
    cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
}

run() {
    mkdir -p "$O"
    local v s rep img
    for v in "${CONV2_VARIANTS[@]}"; do
        img=${CONV2_IMAGE[$v]}
        if [ -f "$img" ]; then
            section "$P v2: $v, 8K image, end-to-end (PNG in and out)"
            for rep in $(rep_list); do
                warm_file "$img"
                round image "img_$v" "$rep" --variant "$v" --in "$img" --out-prefix "$O/${v}" --repeat 1
                rm -f "$O"/*; sync
            done
        fi
        section "$P v2: $v, synthetic size sweep (compute only)"
        for s in "${CONV2_SYNTH[@]}"; do
            for rep in $(rep_list); do round synth "${v}_$s" "$rep" --variant "$v" --synthetic "$s"; done
        done
    done
    section "$P v2: cold probes"
    local k cv=${CONV2_COLD%%:*} cs=${CONV2_COLD##*:}
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/${cv}_${cs}/fpga/cold$k/DONE" ] && continue
        switch_bitstream_v2 "$CONV2_XCLBIN"
        one cold "${cv}_${cs}" fpga "cold$k" --variant "$cv" --synthetic "$cs" --repeat 1
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P v2: build the programs first (./build_v2.sh)"; exit 1; }; run ;;
esac
