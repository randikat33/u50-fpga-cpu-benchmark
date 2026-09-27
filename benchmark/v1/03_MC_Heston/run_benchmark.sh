#!/usr/bin/env bash
# 03 - Monte-Carlo Heston option pricing, single compute unit: FPGA vs CPU
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=03_MC_Heston; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"

check() {
    local ok=0
    need "$MC_FPGA_BIN" "FPGA host" || ok=1
    need "$MC_XCLBIN"   "xclbin"    || ok=1
    need "$MC_CPU_BIN"  "CPU binary" || ok=1
    return $ok
}

# NOTE on accounting: the FPGA host always does 1 warm-up + --runs timed executions
# inside one process. The analysis uses: E2E(single job) = XCLBIN load + H2D + ONE
# kernel run, and also keeps the process wall time. The CPU binary does one job.
one_run() {
    local plat=$1 paths=$2 steps=$3 rep=$4 grp=$5
    local o="$OUT/$grp/p${paths}_s${steps}/$plat/$rep"
    if [ "$plat" = fpga ]; then
        measure "$o" --label "$P ${paths}x${steps} fpga $rep" --xclbin "$MC_XCLBIN" \
            --xrt-ini "$(ini_for "$rep")" --timeout 1800 -- \
            "$MC_FPGA_BIN" --paths "$paths" --steps "$steps" --runs "$MC_FPGA_INPROC_RUNS" --xclbin "$MC_XCLBIN"
    else
        measure "$o" --label "$P ${paths}x${steps} cpu $rep" --timeout 1800 -- \
            "$MC_CPU_BIN" --paths "$paths" --steps "$steps"
    fi
}

run() {
    local cfg paths steps
    for cfg in "${MC_CONFIGS[@]}"; do
        paths=${cfg%%:*}; steps=${cfg##*:}
        section "$P: ${paths} paths x ${steps} steps"
        for rep in $(rep_list); do
            one_run fpga "$paths" "$steps" "$rep" main
            [[ $rep == profiled* ]] && continue
            one_run cpu "$paths" "$steps" "$rep" main
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    done
    section "$P: cold (bitstream-swap) probes"
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/p131072_s32/fpga/cold$k/DONE" ] && continue
        switch_bitstream "$MC_XCLBIN"
        one_run fpga 131072 32 "cold$k" cold
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P: fix config.sh first"; exit 1; }; run ;;
esac
