#!/usr/bin/env bash
# v2 03 - Monte Carlo Heston basket option: FPGA (lanes + interleaving) vs AVX-512 / scalar CPU
#   Same RNG, same normal transform, same recurrence -> identical prices (mom_hash).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=03_MC_Heston; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"

check() { v2_need "v2 MC" "$MC2_FPGA_BIN" "$MC2_CPU_BIN" "$MC2_XCLBIN"; }

one() {   # one <group> <paths> <steps> <platdir> <rep>
    local grp=$1 paths=$2 steps=$3 plat=$4 rep=$5
    local cfg="${paths}x${steps}" o v=()
    o="$OUT/$grp/$cfg/$plat/$rep"
    [[ $rep == rep1 ]] && v=(--verify)
    if [ "$plat" = fpga ]; then
        measure_v2 "$o" --label "$P $grp $cfg fpga $rep" --xclbin "$MC2_XCLBIN" --xrt-ini "$(ini_for "$rep")" --timeout 1800 -- \
            "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" --paths "$paths" --steps "$steps" --runs 3 --warmup 0 "${v[@]}"
    else
        measure_v2 "$o" --label "$P $grp $cfg $plat $rep" --timeout 1800 -- \
            "$MC2_CPU_BIN" --impl "${plat#cpu_}" --threads 24 --paths "$paths" --steps "$steps" --runs 3 --warmup 0 "${v[@]}"
    fi
}

run() {
    local c paths steps rep impl
    for c in "${MC2_CONFIGS[@]}"; do
        paths=${c%%:*}; steps=${c##*:}
        section "$P v2: ${paths} paths x ${steps} steps"
        for rep in $(rep_list); do
            one main "$paths" "$steps" fpga "$rep"
            [[ $rep == profiled* ]] && continue
            for impl in "${MC2_CPU_IMPLS[@]}"; do
                if [ "$impl" = scalar ] && [ $((paths * steps)) -gt "$MC2_SCALAR_MAX_STEPS" ]; then continue; fi
                one main "$paths" "$steps" "cpu_$impl" "$rep"
            done
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    done
    section "$P v2: cold probes"
    local k
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/${MC2_COLD_CONFIG%%:*}x${MC2_COLD_CONFIG##*:}/fpga/cold$k/DONE" ] && continue
        switch_bitstream_v2 "$MC2_XCLBIN"
        one cold "${MC2_COLD_CONFIG%%:*}" "${MC2_COLD_CONFIG##*:}" fpga "cold$k"
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P v2: build the programs first (./build_v2.sh)"; exit 1; }; run ;;
esac
