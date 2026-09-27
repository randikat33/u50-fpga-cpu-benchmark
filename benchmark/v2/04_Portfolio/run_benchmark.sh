#!/usr/bin/env bash
# v2 04 - two-stage Heston portfolio: FPGA (lanes x CUs) vs AVX-512 / scalar CPU
#   stage 1: all trades x 256 paths; stage 2: top-10,000 trades x PATHS2 paths (sweep)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=04_Portfolio; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
W="$DATA_DIR/pf_out"

check() {
    v2_need "v2 Portfolio" "$PF2_FPGA_BIN" "$PF2_CPU_BIN" "$PF2_GEN_BIN" "$PF2_XCLBIN" || return 1
    [ -f "$PF2_PORTFOLIO" ] && [ -f "$PF2_MARKET" ] && echo "  [ok]      portfolio/market: $PF2_PORTFOLIO" \
        || echo "  [info]    portfolio.bin/market.bin missing -> will be generated with pf_gen (v1 defaults)"
    return 0
}

one() {   # one <group> <paths2> <platdir> <rep>
    local grp=$1 p2=$2 plat=$3 rep=$4
    local cfg="p2_${p2}" o v=() res
    o="$OUT/$grp/$cfg/$plat/$rep"
    [[ $rep == rep1 ]] && v=(--verify sample)
    res="$W/${cfg}_${plat}_${rep}.bin"
    if [ "$plat" = fpga ]; then
        measure_v2 "$o" --label "$P $grp $cfg fpga $rep" --xclbin "$PF2_XCLBIN" --xrt-ini "$(ini_for "$rep")" --timeout 3600 -- \
            "$PF2_FPGA_BIN" --xclbin "$PF2_XCLBIN" --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 "$p2" --out "$res" "${v[@]}"
    else
        measure_v2 "$o" --label "$P $grp $cfg $plat $rep" --timeout 3600 -- \
            "$PF2_CPU_BIN" --impl "${plat#cpu_}" --threads 24 --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 "$p2" --out "$res" "${v[@]}"
    fi
    # keep rep1 result files next to the run (accuracy tables); delete the others
    if [ -f "$res" ]; then
        if [ "$rep" = rep1 ] && [ -d "$o" ]; then mv "$res" "$o/results_final.bin"; else rm -f "$res"; fi
    fi
}

run() {
    mkdir -p "$W"
    if [ ! -f "$PF2_PORTFOLIO" ] || [ ! -f "$PF2_MARKET" ]; then
        info "generating portfolio with pf_gen (v1 defaults)"
        mkdir -p "$(dirname "$PF2_PORTFOLIO")"
        "$PF2_GEN_BIN" --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" || { err "pf_gen failed"; return 1; }
    fi
    warm_file "$PF2_PORTFOLIO"; warm_file "$PF2_MARKET"
    local p2 rep impl
    for p2 in "${PF2_PATHS2[@]}"; do
        section "$P v2: stage-2 paths = $p2"
        for rep in $(rep_list); do
            one main "$p2" fpga "$rep"
            [[ $rep == profiled* ]] && continue
            for impl in "${PF2_CPU_IMPLS[@]}"; do
                if [ "$impl" = scalar ] && [[ " ${PF2_SCALAR_PATHS2[*]} " != *" $p2 "* ]]; then continue; fi
                one main "$p2" "cpu_$impl" "$rep"
            done
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    done
    section "$P v2: cold probes"
    local k
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/p2_${PF2_COLD_PATHS2}/fpga/cold$k/DONE" ] && continue
        switch_bitstream_v2 "$PF2_XCLBIN"
        one cold "$PF2_COLD_PATHS2" fpga "cold$k"
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P v2: build the programs first (./build_v2.sh)"; exit 1; }; run ;;
esac
