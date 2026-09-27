#!/usr/bin/env bash
# =============================================================================
#  build_v2.sh - builds the v2 programs (CPU baselines + FPGA hosts) and, on request,
#  the v2 xclbins. Everything is built inside v2_projects/<NN>/build/.
#
#  ./build_v2.sh                 CPU + host programs of all 6 projects (~2 min)
#  ./build_v2.sh --xclbin        also HLS + v++ link for every project (6-12 h, one after
#                                another; run it in tmux, it resumes where it stopped)
#  ./build_v2.sh --xclbin --only 03,04     selected projects only
#  ./build_v2.sh --ablation      also the optional CU-ablation xclbins (05: 4 CUs, 06: 2 CUs)
#  ./build_v2.sh --deps          only check/print the packages that are needed
#
#  Before: source /tools/Xilinx/Vitis/2023.1/settings64.sh ; source /opt/xilinx/xrt/setup.sh
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2="$SUITE_DIR/v2_projects"
LOG="$SUITE_DIR/build_logs"; mkdir -p "$LOG"
PROJECTS=(01_AES 02_Convolution 03_MC_Heston 04_Portfolio 05_LiveStream_Single 06_LiveStream_Multi)
DO_XCLBIN=0; DO_ABL=0; ONLY=""; DEPS_ONLY=0
while [ $# -gt 0 ]; do
    case $1 in
        --xclbin) DO_XCLBIN=1 ;;
        --ablation) DO_ABL=1 ;;
        --only) ONLY=$2; shift ;;
        --deps) DEPS_ONLY=1 ;;
        -h|--help) sed -n 2,15p "$0"; exit 0 ;;
        *) echo "unknown option $1"; exit 1 ;;
    esac; shift
done
selected() { [ -z "$ONLY" ] && return 0; local n=${1%%_*}; [[ ",$ONLY," == *",$n,"* ]]; }
# lane counts chosen by tune_v2.sh (if it has run) are used for every build
tuned() { grep -s "^$1 " "$V2/TUNED.conf" | cut -d' ' -f2-; }
VISION_ROOT=$(SUITE_DIR="$SUITE_DIR" bash -c 'source "$SUITE_DIR/config.sh" >/dev/null 2>&1; echo "$VISION_ROOT"')

echo "== dependencies"
miss=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "  [ok]      $1"; else echo "  [MISSING] $1   -> $3"; miss=1; fi; }
chk "g++ (C++17)"            "g++ --version"                          "sudo apt install build-essential"
chk "OpenMP"                 "echo 'int main(){}' | g++ -fopenmp -x c++ - -o /tmp/omp_chk" "sudo apt install libgomp1"
chk "OpenCV 4 (pkg-config)"  "pkg-config --exists opencv4"            "sudo apt install libopencv-dev"
chk "OpenSSL libcrypto"      "echo 'int main(){}' | g++ -x c++ - -lcrypto -o /tmp/ssl_chk" "sudo apt install libssl-dev"
chk "libpng"                 "echo 'int main(){}' | g++ -x c++ - -lpng -o /tmp/png_chk" "sudo apt install libpng-dev"
chk "ffmpeg (encoders)"      "command -v ffmpeg"                      "sudo apt install ffmpeg"
chk "XRT (XILINX_XRT)"       "test -f \${XILINX_XRT:-/opt/xilinx/xrt}/include/xrt/xrt_kernel.h" "source /opt/xilinx/xrt/setup.sh"
chk "Vitis Vision library"   "test -f $VISION_ROOT/L1/include/imgproc/xf_resize.hpp" "set VISION_ROOT=... in config.local.sh"
if [ $DO_XCLBIN = 1 ]; then
    chk "vitis_hls"          "command -v vitis_hls"                   "source /tools/Xilinx/Vitis/2023.1/settings64.sh"
    chk "v++"                "command -v v++"                         "source /tools/Xilinx/Vitis/2023.1/settings64.sh"
fi
chk "kernel/link-config static checks" "python3 $SUITE_DIR/common/check_kernels.py" "see: python3 common/check_kernels.py"
[ $DEPS_ONLY = 1 ] && exit $miss
[ $miss = 0 ] || echo "  (continuing: projects whose dependencies are missing will fail below)"

rc=0
echo; echo "== CPU baselines and FPGA hosts"
for p in "${PROJECTS[@]}"; do
    selected "$p" || continue
    for t in cpu host; do
        # shellcheck disable=SC2046
        if make -C "$V2/$p" "$t" VISION_ROOT="$VISION_ROOT" $(tuned "$p") > "$LOG/${p}_${t}.log" 2>&1; then
            echo "  [ok]      $p $t"
        else
            echo "  [FAILED]  $p $t -> see build_logs/${p}_${t}.log"; tail -4 "$LOG/${p}_${t}.log" | sed 's/^/            /'; rc=1
        fi
    done
done

if [ $DO_XCLBIN = 1 ]; then
    echo; echo "== xclbins (HLS + Vivado implementation; long)"
    for p in "${PROJECTS[@]}"; do
        selected "$p" || continue
        start=$(date +%s)
        echo "  $p: building xclbin (log: build_logs/${p}_xclbin.log) ..."
        # shellcheck disable=SC2046
        if make -C "$V2/$p" xclbin fpga_reports VISION_ROOT="$VISION_ROOT" $(tuned "$p") > "$LOG/${p}_xclbin.log" 2>&1; then
            echo "  [ok]      $p xclbin in $(( ($(date +%s)-start)/60 )) min: $(ls "$V2/$p"/build/*.xclbin | tr '\n' ' ')"
        else
            echo "  [FAILED]  $p xclbin -> see build_logs/${p}_xclbin.log"; grep -m5 -E "^ERROR|CRITICAL" "$LOG/${p}_xclbin.log" | sed 's/^/            /'; rc=1
        fi
    done
fi
if [ $DO_ABL = 1 ]; then
    echo; echo "== ablation xclbins"
    selected 05 && { make -C "$V2/05_LiveStream_Single" xclbin fpga_reports NCU=4 BUILD=build_4cu VISION_ROOT="$VISION_ROOT" > "$LOG/05_xclbin_4cu.log" 2>&1 \
        && echo "  [ok]      05 4-CU" || { echo "  [FAILED]  05 4-CU (build_logs/05_xclbin_4cu.log)"; rc=1; }; }
    selected 06 && { make -C "$V2/06_LiveStream_Multi" xclbin fpga_reports NCU=2 BUILD=build_2cu VISION_ROOT="$VISION_ROOT" > "$LOG/06_xclbin_2cu.log" 2>&1 \
        && echo "  [ok]      06 2-CU" || { echo "  [FAILED]  06 2-CU (build_logs/06_xclbin_2cu.log)"; rc=1; }; }
fi
echo
[ $rc = 0 ] && echo "build_v2: all requested builds OK" || echo "build_v2: some builds FAILED (see above)"
exit $rc
