#!/usr/bin/env bash
# 04 - Two-stage portfolio risk (Heston MC, 6 CUs): FPGA vs CPU
#      Stage 1: all trades x 256 paths ; Stage 2: top-10,000 trades x 131,072 paths
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=04_Portfolio; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"

check() {
    local ok=0
    need "$PF_FPGA_BIN"  "FPGA host"  || ok=1
    need "$PF_XCLBIN"    "xclbin"     || ok=1
    need "$PF_CPU_BIN"   "CPU binary" || ok=1
    need "$PF_PORTFOLIO" "portfolio.bin (run gen_portfolio once)" || ok=1
    need "$PF_ROOT/gen_portfolio/market.bin" "market.bin" || ok=1
    return $ok
}

# Both programs read portfolio.bin/market.bin from fixed paths and write result
# files into the current directory. We keep result files of every run for the
# FPGA-vs-CPU accuracy comparison (they are ~8 MB each).
one_run() {
    local plat=$1 rep=$2 grp=$3
    local o="$OUT/$grp/full/$plat/$rep" w="$RESULTS_DIR/.work/$P"
    rm -rf "$w"; mkdir -p "$w"
    warm_file "$PF_PORTFOLIO"; warm_file "$PF_ROOT/gen_portfolio/market.bin"
    if [ "$plat" = fpga ]; then
        measure "$o" --label "$P fpga $rep" --cwd "$w" --xclbin "$PF_XCLBIN" \
            --xrt-ini "$(ini_for "$rep")" --timeout 3600 -- "$PF_FPGA_BIN"
    else
        measure "$o" --label "$P cpu $rep" --cwd "$w" --timeout 7200 -- "$PF_CPU_BIN"
    fi
    if [ -d "$o" ]; then
        mkdir -p "$o/results"
        # keep only the final results of rep1 (enough for the accuracy check)
        if [ "$rep" = rep1 ]; then cp "$w"/results_final*.bin "$o/results/" 2>/dev/null; fi
        ( cd "$w" && md5sum ./*.bin 2>/dev/null ) > "$o/results/md5.txt"
    fi
    rm -rf "$w"
}

run() {
    section "$P: full two-stage run"
    for rep in $(rep_list); do
        one_run fpga "$rep" main
        [[ $rep == profiled* ]] && continue
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        one_run cpu "$rep" main
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    section "$P: cold (bitstream-swap) probes"
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/full/fpga/cold$k/DONE" ] && continue
        switch_bitstream "$PF_XCLBIN"
        one_run fpga "cold$k" cold
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P: fix config.sh first"; exit 1; }; run ;;
esac
