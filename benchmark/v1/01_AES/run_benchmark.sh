#!/usr/bin/env bash
# 01 - AES-256 CTR : FPGA (Alveo U50) vs CPU (24 cores) + OpenSSL AES-NI baselines
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=01_AES; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"
D="$AES_DATA_DIR"

check() {
    local ok=0
    need "$AES_FPGA_BIN" "FPGA host" || ok=1
    need "$AES_ENC_XCLBIN" "encrypt xclbin" || ok=1
    need "$AES_DEC_XCLBIN" "decrypt xclbin" || ok=1
    need "$AES_CPU_BIN" "CPU binary" || ok=1
    command -v openssl >/dev/null && echo "  [ok]      openssl: $(openssl version)" || echo "  [info]    openssl not found (AES-NI baseline skipped)"
    local need_gb=$(( ( $(IFS=+; echo "$((${AES_SIZES_MB[*]}))") * 4 ) / 1024 + 2 ))
    local free_gb; free_gb=$(df -BG --output=avail "$(dirname "$D")" | tail -1 | tr -dc 0-9)
    echo "  [info]    disk free near test data: ${free_gb} GB (needs ~${need_gb} GB)"
    [ "${free_gb:-0}" -ge "$need_gb" ] || { echo "  [MISSING] not enough disk space"; ok=1; }
    return $ok
}

prepare() {
    section "$P: preparing exact-size test files in $D"
    mkdir -p "$D/out"
    for s in "${AES_SIZES_MB[@]}"; do
        local f="$D/test_${s}MB.bin" want=$((s*1048576))
        if [ ! -f "$f" ] || [ "$(stat -c %s "$f")" != "$want" ]; then
            info "creating $f ($s MB)"; head -c "$want" /dev/urandom > "$f"
        fi
        md5sum "$f" | cut -d' ' -f1 > "$f.md5"
        # reference ciphertexts used as decrypt inputs (one per platform, not timed)
        [ -s "$D/test_${s}MB.cpu.enc" ] || "$AES_CPU_BIN" encrypt "$f" "$D/test_${s}MB.cpu.enc" "$AES_KEY" > "$STATE_DIR/aes_prep.log" 2>&1
        [ -s "$D/test_${s}MB.fpga.enc" ] || ( cd "$STATE_DIR" && "$AES_FPGA_BIN" encrypt "$f" "$D/test_${s}MB.fpga.enc" "$AES_KEY" "$AES_ENC_XCLBIN" >> "$STATE_DIR/aes_prep.log" 2>&1 )
    done
    rm -f "$STATE_DIR"/summary.csv "$STATE_DIR"/*trace*.csv "$STATE_DIR"/power_profile_*.csv "$STATE_DIR"/*.run_summary
    realpath "$AES_ENC_XCLBIN" > "$STATE_DIR/last_xclbin.txt"
}

# verify_dec <dir> <size>  : decrypted file must be byte-identical to the original
verify_dec() {
    local got; got=$(find "$1" -type f | head -1)
    local want; want=$(cat "$D/test_${2}MB.bin.md5")
    if [ -n "$got" ] && [ "$(md5sum "$got" | cut -d' ' -f1)" = "$want" ]; then echo PASS; else echo FAIL; fi
}

# one_run <plat fpga|cpu> <mode enc|dec> <size> <rep> <group> [datadir]
one_run() {
    local plat=$1 mode=$2 s=$3 rep=$4 grp=$5 dd=${6:-$D}
    local o="$OUT/$grp/${mode}_${s}MB/$plat/$rep" in out x args
    if [ "$mode" = enc ]; then
        in="$dd/test_${s}MB.bin"; out="$dd/out/${plat}_${s}MB.enc"; x="$AES_ENC_XCLBIN"
        args=(encrypt "$in" "$out" "$AES_KEY")
    else
        in="$dd/test_${s}MB.${plat}.enc"; out="$dd/out/dec_${plat}_${s}MB"; x="$AES_DEC_XCLBIN"
        rm -rf "$out"; mkdir -p "$out"
        args=(decrypt "$in" "$out" "$AES_KEY")
    fi
    warm_file "$in"
    if [ "$plat" = fpga ]; then
        measure "$o" --label "$P $grp $mode ${s}MB fpga $rep" --xclbin "$x" --xrt-ini "$(ini_for "$rep")" \
            --timeout 900 -- "$AES_FPGA_BIN" "${args[@]}" "$x"
    else
        measure "$o" --label "$P $grp $mode ${s}MB cpu $rep" --timeout 900 -- "$AES_CPU_BIN" "${args[@]}"
    fi
    if [ "$mode" = dec ] && [ -d "$o" ] && [ ! -f "$o/verify.txt" ]; then verify_dec "$out" "$s" > "$o/verify.txt"; fi
    rm -rf "$dd/out/${plat}_${s}MB.enc" "$dd/out/dec_${plat}_${s}MB"; sync
}

run() {
    prepare
    # ---- A) main experiment: warm (bitstream already on card) -------------
    for mode in enc dec; do
        section "$P: $mode  (profiled run first, then $REPS timed reps, FPGA/CPU interleaved)"
        for s in "${AES_SIZES_MB[@]}"; do
            for rep in $(rep_list); do
                one_run fpga "$mode" "$s" "$rep" main
                [[ $rep == profiled* ]] && continue
                one_run cpu "$mode" "$s" "$rep" main
                cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
            done
        done
        # ---- B) cold probes: a different bitstream is loaded right before --
        section "$P: $mode cold (bitstream-swap) probes"
        for s in "${AES_COLD_SIZES_MB[@]}"; do
            for k in $(seq 1 "$COLD_PROBES"); do
                local o="$OUT/cold/${mode}_${s}MB/fpga/cold$k"
                [ -f "$o/DONE" ] && continue
                if [ "$mode" = enc ]; then switch_bitstream "$AES_ENC_XCLBIN"; else switch_bitstream "$AES_DEC_XCLBIN"; fi
                one_run fpga "$mode" "$s" "cold$k" cold
                cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
            done
        done
    done

    # ---- C) same runs with input/output on RAM disk (removes SSD from E2E) --
    local maxs=0; for s in "${AES_TMPFS_SIZES_MB[@]}"; do [ "$s" -gt "$maxs" ] && maxs=$s; done
    local shm_free; shm_free=$(df -BM --output=avail /dev/shm | tail -1 | tr -dc 0-9)
    if [ "${shm_free:-0}" -gt $(( maxs * 6 )) ]; then
        section "$P: tmpfs (RAM disk) runs"
        local T=/dev/shm/thesis_aes; mkdir -p "$T/out"
        for s in "${AES_TMPFS_SIZES_MB[@]}"; do
            cp "$D/test_${s}MB.bin" "$D/test_${s}MB.cpu.enc" "$D/test_${s}MB.fpga.enc" "$D/test_${s}MB.bin.md5" "$T/"
        done
        for mode in enc dec; do          # one block per bitstream -> timed runs are all "cached"
            for s in "${AES_TMPFS_SIZES_MB[@]}"; do
                one_run fpga "$mode" "$s" prime1 tmpfs "$T"     # untimed: loads the bitstream
                for rep in $(seq -f "rep%g" 1 "$REPS"); do
                    one_run fpga "$mode" "$s" "$rep" tmpfs "$T"
                    one_run cpu  "$mode" "$s" "$rep" tmpfs "$T"
                done
            done
        done
        rm -rf "$T"
    else
        warn "$P: /dev/shm too small (${shm_free} MB) - tmpfs runs skipped"
    fi

    # ---- D) OpenSSL baselines (industry-standard CPU AES, with/without AES-NI) --
    if [ "$AES_OPENSSL_BASELINE" = 1 ] && command -v openssl >/dev/null; then
        section "$P: OpenSSL baselines"
        local iv=000102030405060708090a0b0c0d0e0f
        local NOAESNI="OPENSSL_ia32cap=~0x200000200000000"
        for s in "${AES_SIZES_MB[@]}"; do
            for rep in $(seq -f "rep%g" 1 "$REPS"); do
                warm_file "$D/test_${s}MB.bin"
                measure "$OUT/openssl/enc_${s}MB/openssl_aesni/$rep" --label "openssl aesni ${s}MB" -- \
                    openssl enc -aes-256-ctr -K "$AES_KEY" -iv $iv -in "$D/test_${s}MB.bin" -out "$D/out/ossl_${s}MB.enc"
                rm -f "$D/out/ossl_${s}MB.enc"
                measure "$OUT/openssl/enc_${s}MB/openssl_noaesni/$rep" --label "openssl no-aesni ${s}MB" --env "$NOAESNI" -- \
                    openssl enc -aes-256-ctr -K "$AES_KEY" -iv $iv -in "$D/test_${s}MB.bin" -out "$D/out/ossl_${s}MB.enc"
                rm -f "$D/out/ossl_${s}MB.enc"; sync
            done
        done
        for rep in $(seq -f "rep%g" 1 "$REPS"); do
            measure "$OUT/openssl/speed/aesni_24proc/$rep" --label "openssl speed aesni" -- \
                openssl speed -elapsed -seconds 3 -bytes 16384 -multi 24 -evp aes-256-ctr
            measure "$OUT/openssl/speed/noaesni_24proc/$rep" --label "openssl speed no-aesni" --env "$NOAESNI" -- \
                openssl speed -elapsed -seconds 3 -bytes 16384 -multi 24 -evp aes-256-ctr
            measure "$OUT/openssl/speed/aesni_1proc/$rep" --label "openssl speed aesni 1" -- \
                openssl speed -elapsed -seconds 3 -bytes 16384 -evp aes-256-ctr
        done
    fi
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P: fix config.sh first"; exit 1; }; run ;;
esac
