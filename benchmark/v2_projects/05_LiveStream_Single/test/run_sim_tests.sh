#!/usr/bin/env bash
# Sandbox verification for 05 LiveStream Single (v2). No Vitis, no card.
set -u
cd "$(dirname "$0")/.."
SHIM=${SHIM:-/home/USER/shim}; BUILD=${BUILD:-build}; CXX=${CXX:-g++}
V2C=$(cd .. && pwd)/common
T=$BUILD/simtest; mkdir -p "$T"
OCV=$(pkg-config --cflags --libs opencv4)
HLS_INC="-I$SHIM/include -I$SHIM/HLS_arbitrary_Precision_Types/include"
PASS=0; FAIL=0
ok()  { echo "PASS: $*"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL+1)); }
run() { local name=$1; shift; if timeout "${TMO:-900}" "$@" > "$T/$name.log" 2>&1; then ok "$name"; else bad "$name (see $T/$name.log)"; tail -5 "$T/$name.log"; fi; }
key() { grep -m1 "^RESULT $2=" "$1" | cut -d= -f2-; }
ulimit -s unlimited 2>/dev/null || ulimit -s 65536

echo "== build"
run build_tb_2023_1 $CXX -std=c++17 -O2 -w -D__SDSVHLS__ -DLS_RESIZE_HAS_URAM_ARG=0 $HLS_INC -I$SHIM/Vitis_Libraries/vision/L1/include \
    -Icommon -Ikernel test/tb_kernel.cpp kernel/ls_resize.cpp -o $T/tb_2023_1 $OCV
if [ -d "$SHIM/vision_main/vision/L1/include" ]; then
  run build_tb_main $CXX -std=c++17 -O2 -w -D__SDSVHLS__ -DLS_RESIZE_HAS_URAM_ARG=1 $HLS_INC -I$SHIM/vision_main/vision/L1/include \
      -Icommon -Ikernel test/tb_kernel.cpp kernel/ls_resize.cpp -o $T/tb_main $OCV
fi
run build_cpu make -s cpu BUILD=$BUILD
run build_host_sim $CXX -std=c++17 -O2 -w -pthread -I$SHIM/xrt_sim $HLS_INC -I$SHIM/Vitis_Libraries/vision/L1/include -I$V2C -Icommon -Ikernel \
    host/ls_fpga.cpp test/sim_register.cpp kernel/ls_resize.cpp -o $T/ls_fpga_sim $OCV
run host_real_xrt_syntax $CXX -std=c++17 -fsyntax-only -Wall -Wextra -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver -I$V2C -Icommon \
    $(pkg-config --cflags opencv4) host/ls_fpga.cpp

echo "== kernel C-sim"
run tb_kernel_vitis_vision_2023_1 $T/tb_2023_1
[ -x $T/tb_main ] && run tb_kernel_vitis_vision_main_uram $T/tb_main
[ "${FULL:-0}" = 1 ] && run tb_kernel_full_5_qualities $T/tb_2023_1 --full

echo "== test videos"
V1=$T/v1080.mp4; V4=$T/v4k.mp4
[ -s $V1 ] || ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=1920x1080:rate=30 -f lavfi -i sine=frequency=440 \
    -t 0.4 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest $V1
[ -s $V4 ] || ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=3840x2160:rate=30 -t 0.2 -c:v libx264 -preset ultrafast -pix_fmt yuv420p $V4
[ -s $V1 ] && [ -s $V4 ] && ok "test videos" || bad "test videos (ffmpeg missing?)"
N1=$(python3 -c "import cv2;c=cv2.VideoCapture('$V1');n=0
while c.read()[0]: n+=1
print(n)")
echo "   1080p test video: $N1 frames"

CPU=$BUILD/ls_cpu; HS=$T/ls_fpga_sim
echo "== CPU pipeline"
for q in 0 1 2 3; do
  W=$(python3 -c "print([432,640,856,1280][$q])"); H=$(python3 -c "print([240,360,480,720][$q])")
  run cpu_q${q}_w1 $CPU --video $V1 --quality $q --sink raw --out $T/cpu_q${q}_w1.raw --workers 1 --threads 2 --checksum --verify
  run cpu_q${q}_w3 $CPU --video $V1 --quality $q --sink raw --out $T/cpu_q${q}_w3.raw --workers 3 --slots-per-worker 1 --threads 2 --checksum --verify
  if cmp -s $T/cpu_q${q}_w1.raw $T/cpu_q${q}_w3.raw; then ok "cpu q$q output independent of worker count"; else bad "cpu q$q worker-count dependence"; fi
  run cpu_q${q}_frames python3 test/check_frames.py $V1 $T/cpu_q${q}_w1.raw $W $H 0 $N1
done
[ "$(key $T/cpu_q1_w1.log ok)" = 1 ] && [ "$(key $T/cpu_q1_w1.log verified)" = 1 ] && ok "cpu RESULT ok/verified" || bad "cpu RESULT keys"
for k in project platform impl fps frames t_total_s t_compute_s decode_ms_mean resize_ms_mean sink_ms_mean cv_ipp threads isa; do
  grep -q "^RESULT $k=" $T/cpu_q1_w1.log || bad "cpu missing RESULT $k"
done
grep -q "^Starting pipeline" $T/cpu_q1_w1.log && grep -q "^Consumer finished" $T/cpu_q1_w1.log && grep -q "^MARK start" $T/cpu_q1_w1.log \
    && ok "cpu markers" || bad "cpu markers"
run cpu_sink_file $CPU --video $V1 --quality 3 --sink file --out $T/cpu_q3.mp4 --threads 2
NF=$(ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 $T/cpu_q3.mp4 2>/dev/null)
[ "$NF" = "$N1" ] && ok "cpu encoded file has $NF frames" || bad "cpu encoded file frames=$NF expected $N1"
run cpu_frames_limit $CPU --video $V1 --quality 0 --frames 5 --sink raw --out $T/cpu_f5.raw --threads 2
run cpu_frames_limit_check python3 test/check_frames.py $V1 $T/cpu_f5.raw 432 240 0 5
run cpu_resize_only_4k $CPU --video $V4 --quality 4 --resize-only --iters 40 --workers 2 --threads 2 --verify
echo "   CPU 4K->1080p resize-only: $(key $T/cpu_resize_only_4k.log fps) fps, $(key $T/cpu_resize_only_4k.log resize_ms_mean) ms/frame (2 cores)"
run cpu_pipeline_4k_null $CPU --video $V4 --quality 4 --threads 2
echo "   CPU 4K->1080p pipeline (sink null): $(key $T/cpu_pipeline_4k_null.log fps) fps, decode $(key $T/cpu_pipeline_4k_null.log decode_ms_mean) ms"

echo "== FPGA host via xrt_sim (kernel C-model)"
export XRT_SIM_NUM_CUS=2
run host_q1_probe_2cu $HS --xclbin sim.xclbin --video $V1 --quality 1 --sink raw --out $T/fpga_q1.raw --checksum --verify
[ "$(key $T/host_q1_probe_2cu.log n_cu)" = 2 ] && ok "host found 2 CUs by probing" || bad "host CU probing: $(key $T/host_q1_probe_2cu.log n_cu)"
[ "$(key $T/host_q1_probe_2cu.log verified)" = 1 ] && ok "host verify vs cv::resize (max diff $(key $T/host_q1_probe_2cu.log verify_max_absdiff))" || bad "host verify"
run host_q1_frames python3 test/check_frames.py $V1 $T/fpga_q1.raw 640 360 2 $N1
w0=$(key $T/host_q1_probe_2cu.log frames_worker0); w1=$(key $T/host_q1_probe_2cu.log frames_worker1)
[ "${w0:-0}" -gt 0 ] && [ "${w1:-0}" -gt 0 ] && ok "both CUs used ($w0/$w1 frames)" || bad "CU usage $w0/$w1"
run host_q1_1cu $HS --xclbin sim.xclbin --cus 1 --slots-per-worker 3 --video $V1 --quality 1 --sink raw --out $T/fpga_q1_1cu.raw --checksum
cmp -s $T/fpga_q1.raw $T/fpga_q1_1cu.raw && ok "host output independent of CU/slot count" || bad "host CU-count dependence"
run host_q0_limit $HS --xclbin sim.xclbin --video $V1 --quality 0 --frames 5 --sink raw --out $T/fpga_q0.raw
run host_q0_limit_check python3 test/check_frames.py $V1 $T/fpga_q0.raw 432 240 2 5
run host_resize_only $HS --xclbin sim.xclbin --video $V1 --quality 2 --resize-only --iters 6 --verify
for k in project platform impl fps frames n_cu t_xclbin_s t_alloc_s t_total_s t_compute_s h2d_ms_mean kernel_ms_mean d2h_ms_mean ok; do
  grep -q "^RESULT $k=" $T/host_resize_only.log || bad "host missing RESULT $k"
done
grep -q "^RESULT_JSON {" $T/host_resize_only.log && ok "RESULT_JSON present" || bad "RESULT_JSON"
XRT_SIM_NUM_CUS=1 run host_4k_1frame $HS --xclbin sim.xclbin --video $V4 --quality 4 --frames 1 --sink raw --out $T/fpga_4k.raw --verify
unset XRT_SIM_NUM_CUS

echo "== error handling"
if $HS --video $V1 > $T/err1.log 2>&1; then bad "missing --xclbin accepted"; else ok "missing --xclbin rejected"; fi
if $CPU --video $V1 --quality 7 > $T/err2.log 2>&1; then bad "bad quality accepted"; else ok "bad --quality rejected"; fi
if $CPU --video /nonexistent.mp4 > $T/err3.log 2>&1; then bad "missing video accepted"; else ok "missing video rejected"; fi
grep -q "^RESULT ok=0" $T/err3.log && ok "error run prints ok=0" || bad "error run RESULT"

echo "SUMMARY: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ]
