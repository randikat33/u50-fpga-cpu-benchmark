#!/usr/bin/env bash
# v2 01 - AES-256-CTR: one FPGA kernel (enc = dec) vs 4 CPU implementations
#   groups: enc (file sweep, SSD) | dec (spot checks) | noio (compute only) |
#           tmpfs (files on /dev/shm) | cold (bitstream load included)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=01_AES; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
D="$AES_DATA_DIR"; O="$DATA_DIR/aes_out"

check() {
    v2_need "v2 AES" "$AES2_FPGA_BIN" "$AES2_CPU_BIN" "$AES2_XCLBIN"
}

prepare() {
    mkdir -p "$D" "$O"
    local s f want
    for s in $(printf '%s\n' "${AES2_SIZES_MB[@]}" "${AES2_DEC_SIZES_MB[@]}" "${AES2_TMPFS_SIZES_MB[@]}" "$AES2_COLD_SIZE_MB" | sort -nu); do
        f="$D/test_${s}MB.bin"; want=$((s*1048576))
        if [ ! -f "$f" ] || [ "$(stat -c %s "$f")" != "$want" ]; then info "creating $f"; head -c "$want" /dev/urandom > "$f"; fi
        # decrypt input = CPU (VAES) ciphertext of the same file (untimed)
        [ -s "$D/test_${s}MB.v2.enc" ] || "$AES2_CPU_BIN" --impl vaes --mode enc --in "$f" --out "$D/test_${s}MB.v2.enc" \
            --key "$AES2_KEY" --iv "$AES2_IV" > "$STATE_DIR/aes2_prep.log" 2>&1
    done
}

# one <group> <config> <platdir> <rep> <args...>   (platdir: fpga | cpu_<impl>)
one() {
    local grp=$1 cfg=$2 plat=$3 rep=$4; shift 4
    local o="$OUT/$grp/$cfg/$plat/$rep" v=()
    [[ $rep == rep1 ]] && v=(--verify)
    if [ "$plat" = fpga ]; then
        measure_v2 "$o" --label "$P $grp $cfg fpga $rep" --xclbin "$AES2_XCLBIN" --xrt-ini "$(ini_for "$rep")" --timeout 900 -- \
            "$AES2_FPGA_BIN" --xclbin "$AES2_XCLBIN" --key "$AES2_KEY" --iv "$AES2_IV" "$@" "${v[@]}"
    else
        measure_v2 "$o" --label "$P $grp $cfg $plat $rep" --timeout 900 -- \
            "$AES2_CPU_BIN" --impl "${plat#cpu_}" --threads 24 --key "$AES2_KEY" --iv "$AES2_IV" "$@" "${v[@]}"
    fi
}

# round <group> <config> <rep> <args...> : FPGA then every CPU impl, then cool down
round() {
    local grp=$1 cfg=$2 rep=$3; shift 3
    local impl
    one "$grp" "$cfg" fpga "$rep" "$@"
    [[ $rep == profiled* ]] && return 0
    for impl in "${AES2_CPU_IMPLS[@]}"; do one "$grp" "$cfg" "cpu_$impl" "$rep" "$@"; done
    cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
}

FARGS=()
fargs() {   # fargs <mode> <size> <input dir> <output dir>  -> sets FARGS
    local in="$3/test_${2}MB.bin"; [ "$1" = dec ] && in="$3/test_${2}MB.v2.enc"
    warm_file "$in"
    FARGS=(--mode "$1" --in "$in" --out "$4/out_${1}_${2}MB.bin")
}

run() {
    prepare
    local s rep
    section "$P v2: encrypt sweep (SSD files)"
    for s in "${AES2_SIZES_MB[@]}"; do
        for rep in $(rep_list); do
            fargs enc "$s" "$D" "$O"; round enc "${s}MB" "$rep" "${FARGS[@]}"
            rm -f "$O"/out_*; sync
        done
    done
    section "$P v2: decrypt spot checks"
    for s in "${AES2_DEC_SIZES_MB[@]}"; do
        for rep in $(rep_list); do
            fargs dec "$s" "$D" "$O"; round dec "${s}MB" "$rep" "${FARGS[@]}"
            rm -f "$O"/out_*; sync
        done
    done
    section "$P v2: compute only (no file I/O)"
    for s in "${AES2_NOIO_SIZES_MB[@]}"; do
        for rep in $(rep_list); do round noio "${s}MB" "$rep" --mode enc --no-io --size-mb "$s"; done
        # single-core VAES reference (per-core normalisation)
        for rep in $(seq -f "rep%g" 1 "$REPS"); do
            measure_v2 "$OUT/noio/${s}MB/cpu_vaes_1t/$rep" --label "$P noio ${s}MB vaes 1 thread $rep" --cores 0 -- \
                "$AES2_CPU_BIN" --impl vaes --threads 1 --mode enc --no-io --size-mb "$s"
        done
    done
    local shm_free; shm_free=$(df -BM --output=avail /dev/shm | tail -1 | tr -dc 0-9)
    if [ "${shm_free:-0}" -gt 3000 ]; then
        section "$P v2: files on /dev/shm"
        local T=/dev/shm/thesis_aes2; mkdir -p "$T"
        for s in "${AES2_TMPFS_SIZES_MB[@]}"; do
            cp "$D/test_${s}MB.bin" "$T/"
            for rep in $(seq -f "rep%g" 1 "$REPS"); do
                fargs enc "$s" "$T" "$T"; round tmpfs "${s}MB" "$rep" "${FARGS[@]}"
                rm -f "$T"/out_*
            done
            rm -f "$T/test_${s}MB.bin"
        done
        rm -rf "$T"
    else
        warn "$P: /dev/shm too small (${shm_free} MB) - tmpfs runs skipped"
    fi
    section "$P v2: cold probes (bitstream load included)"
    local k
    for k in $(seq 1 "$COLD_PROBES"); do
        [ -f "$OUT/cold/${AES2_COLD_SIZE_MB}MB/fpga/cold$k/DONE" ] && continue
        switch_bitstream_v2 "$AES2_XCLBIN"
        fargs enc "$AES2_COLD_SIZE_MB" "$D" "$O"; one cold "${AES2_COLD_SIZE_MB}MB" fpga "cold$k" "${FARGS[@]}"
        rm -f "$O"/out_*
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P v2: build the programs first (./build_v2.sh)"; exit 1; }; run ;;
esac
