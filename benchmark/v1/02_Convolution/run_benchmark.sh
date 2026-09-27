#!/usr/bin/env bash
# 02 - 2D convolution on an 8K image (B&W, 8-bit RGB, 16-bit RGB): FPGA vs CPU
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../common/lib.sh"
P=02_Convolution; OUT="$RAW_DIR/$P"; mkdir -p "$OUT"

check() {
    local ok=0 v
    for v in "${CONV_VARIANTS[@]}"; do
        need "${CONV_FPGA_BIN[$v]}" "FPGA host ($v)" || ok=1
        need "${CONV_XCLBIN[$v]}"   "xclbin ($v)"    || ok=1
        need "${CONV_CPU_BIN[$v]}"  "CPU binary ($v)" || ok=1
    done
    need "$CONV_ROOT/gray_8bit_8k.png" "B&W image" || ok=1
    need "$CONV_ROOT/rgb_8bit_8k.png"  "8-bit image" || ok=1
    need "$CONV_ROOT/rgb_16bit_8k.png" "16-bit image" || ok=1
    return $ok
}

# The hosts have the image/xclbin paths compiled in and write PNGs to the current dir,
# so each run gets its own empty working directory (outputs deleted afterwards, a
# checksum of them is kept for the correctness check).
one_run() {
    local plat=$1 v=$2 rep=$3 grp=$4
    local o="$OUT/$grp/$v/$plat/$rep" w="$RESULTS_DIR/.work/$P"
    rm -rf "$w"; mkdir -p "$w"
    case $v in bw) warm_file "$CONV_ROOT/gray_8bit_8k.png";; 8bit) warm_file "$CONV_ROOT/rgb_8bit_8k.png";; *) warm_file "$CONV_ROOT/rgb_16bit_8k.png";; esac
    if [ "$plat" = fpga ]; then
        measure "$o" --label "$P $v fpga $rep" --cwd "$w" --xclbin "${CONV_XCLBIN[$v]}" \
            --xrt-ini "$(ini_for "$rep")" --timeout 600 -- "${CONV_FPGA_BIN[$v]}"
    else
        measure "$o" --label "$P $v cpu $rep" --cwd "$w" --timeout 600 -- "${CONV_CPU_BIN[$v]}" 1
    fi
    if [ -d "$o" ] && [ ! -f "$o/outputs.md5" ]; then
        ( cd "$w" && for f in *.png; do [ -f "$f" ] && echo "$(stat -c %s "$f") $(md5sum "$f")"; done ) > "$o/outputs.md5"
        # keep one small down-scaled copy of each output for the PSNR/visual check
        python3 - "$w" "$o" <<'PY' 2>/dev/null || true
import sys, glob, os
try:
    import cv2
except ImportError:
    sys.exit(0)
w, o = sys.argv[1:]
for f in glob.glob(os.path.join(w, "*.png")):
    im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
    if im is None: continue
    cy, cx = im.shape[0] // 2, im.shape[1] // 2
    cv2.imwrite(os.path.join(o, "crop_" + os.path.basename(f)), im[cy-256:cy+256, cx-256:cx+256])
PY
    fi
    rm -rf "$w"
}

run() {
    local v
    for v in "${CONV_VARIANTS[@]}"; do
        section "$P: $v"
        for rep in $(rep_list); do
            one_run fpga "$v" "$rep" main
            [[ $rep == profiled* ]] && continue
            one_run cpu "$v" "$rep" main
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
        section "$P: $v cold (bitstream-swap) probes"
        for k in $(seq 1 "$COLD_PROBES"); do
            [ -f "$OUT/cold/$v/fpga/cold$k/DONE" ] && continue
            switch_bitstream "${CONV_XCLBIN[$v]}"
            one_run fpga "$v" "cold$k" cold
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    done
    project_done "$P"
}

case "${1:-run}" in
    --check) check ;;
    *) check || { err "$P: fix config.sh first"; exit 1; }; run ;;
esac
