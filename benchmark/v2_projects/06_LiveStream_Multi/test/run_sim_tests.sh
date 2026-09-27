#!/usr/bin/env bash
# Sandbox verification for 06 LiveStream Multi (v2). No Vitis, no card.
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
same_dir() { local a=$1 b=$2 r; for r in 240p 360p 480p 720p 1080p; do cmp -s "$a/$r.raw" "$b/$r.raw" || return 1; done; }
ulimit -s unlimited 2>/dev/null || ulimit -s 65536

echo "== build"
run build_tb_2023_1 $CXX -std=c++17 -O2 -w -D__SDSVHLS__ -DLS_RESIZE_HAS_URAM_ARG=0 $HLS_INC -I$SHIM/Vitis_Libraries/vision/L1/include \
    -Icommon -Ikernel test/tb_kernel.cpp kernel/lm_ladder.cpp -o $T/tb_2023_1 $OCV
if [ -d "$SHIM/vision_main/vision/L1/include" ]; then
  run build_tb_main $CXX -std=c++17 -O2 -w -D__SDSVHLS__ -DLS_RESIZE_HAS_URAM_ARG=1 $HLS_INC -I$SHIM/vision_main/vision/L1/include \
      -Icommon -Ikernel test/tb_kernel.cpp kernel/lm_ladder.cpp -o $T/tb_main $OCV
fi
run build_cpu make -s cpu BUILD=$BUILD
run build_host_sim $CXX -std=c++17 -O2 -w -pthread -D__SDSVHLS__ -I$SHIM/xrt_sim $HLS_INC -I$SHIM/Vitis_Libraries/vision/L1/include \
    -I$V2C -Icommon -Ikernel host/lm_fpga.cpp test/sim_register.cpp kernel/lm_ladder.cpp -o $T/lm_fpga_sim $OCV
run host_real_xrt_syntax $CXX -std=c++17 -fsyntax-only -Wall -Wextra -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver -I$V2C \
    -Icommon $(pkg-config --cflags opencv4) host/lm_fpga.cpp

echo "== kernel C-sim (all rungs vs library with MAX_DOWN_SCALE 16)"
run tb_kernel_vitis_vision_2023_1 $T/tb_2023_1 ${FULL:+--full}
if [ -x $T/tb_main ]; then run tb_kernel_vitis_vision_main_uram $T/tb_main; fi
grep -E "vs cv::resize" $T/tb_kernel_vitis_vision_2023_1.log | sed 's/^/   /'

echo "== test videos"
V1=$T/v1080.mp4; V4=$T/v4k.mp4; V7=$T/v720.mp4
[ -s $V1 ] || ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=1920x1080:rate=30 -t 0.2 -c:v libx264 -preset ultrafast -pix_fmt yuv420p $V1
[ -s $V4 ] || ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=3840x2160:rate=30 -t 0.2 -c:v libx264 -preset ultrafast -pix_fmt yuv420p $V4
[ -s $V7 ] || ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=30 -t 0.1 -c:v libx264 -preset ultrafast -pix_fmt yuv420p $V7
[ -s $V1 ] && [ -s $V4 ] && ok "test videos" || bad "test videos (ffmpeg missing?)"
CPU=$BUILD/lm_cpu; HS=$T/lm_fpga_sim

echo "== CPU pipeline"
mkdir -p $T/c1 $T/c2 $T/c3 $T/cf $T/chls $T/c5
run cpu_raw_w1 $CPU --video $V1 --sink raw --out-dir $T/c1 --workers 1 --threads 2 --checksum --verify
run cpu_raw_w3_b3 $CPU --video $V1 --sink raw --out-dir $T/c2 --workers 3 --slots-per-worker 1 --batch 3 --threads 2 --checksum --verify
run cpu_raw_serial $CPU --video $V1 --sink raw --out-dir $T/c3 --workers 2 --batch 4 --rung-parallel 0 --threads 2
same_dir $T/c1 $T/c2 && same_dir $T/c1 $T/c3 && ok "cpu output independent of workers/batch/rung-parallel" || bad "cpu config dependence"
run cpu_ladder_check python3 test/check_ladder.py $V1 $T/c1 0 6
[ "$(key $T/cpu_raw_w3_b3.log jobs)" = 2 ] && ok "cpu batch 3 -> 2 jobs" || bad "cpu jobs=$(key $T/cpu_raw_w3_b3.log jobs)"
[ "$(key $T/cpu_raw_w1.log verified)" = 1 ] && ok "cpu verify" || bad "cpu verify"
for k in project platform impl fps frames jobs t_total_s t_compute_s decode_ms_mean ladder_ms_per_frame sink_ms_per_frame \
         sink_ms_per_frame_1080p out_fnv1a64_240p cv_ipp threads isa rung_parallel batch ok; do
  grep -q "^RESULT $k=" $T/cpu_raw_w1.log || bad "cpu missing RESULT $k"
done
grep -q "^Starting parallel processing" $T/cpu_raw_w1.log && grep -q "^Processing Complete" $T/cpu_raw_w1.log && ok "cpu v1 markers" || bad "cpu markers"
run cpu_sink_file $CPU --video $V1 --sink file --out-dir $T/cf --threads 2
nbad=0; for r in 240p 360p 480p 720p 1080p; do
  nf=$(ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames,width -of csv=p=0 $T/cf/$r.mp4 2>/dev/null)
  echo "   $r.mp4: width,frames = $nf"; [ "${nf#*,}" = 6 ] || nbad=1
done
[ $nbad = 0 ] && ok "cpu 5 encoded files with 6 frames" || bad "cpu encoded files"
run cpu_sink_hls $CPU --video $V1 --sink hls --out-dir $T/chls --threads 2
[ "$(ls $T/chls/*/stream.m3u8 2>/dev/null | wc -l)" = 5 ] && ok "cpu hls sink: 5 playlists" || bad "cpu hls playlists"
run cpu_frames_limit $CPU --video $V1 --sink raw --out-dir $T/c5 --frames 5 --batch 2 --threads 2
run cpu_frames_limit_check python3 test/check_ladder.py $V1 $T/c5 0 5
run cpu_resize_only_4k $CPU --video $V4 --resize-only --iters 30 --threads 2 --verify
echo "   CPU 4K ladder resize-only: $(key $T/cpu_resize_only_4k.log fps) fps ($(key $T/cpu_resize_only_4k.log ladder_ms_per_frame) ms/frame, 2 cores)"
run cpu_pipeline_4k $CPU --video $V4 --threads 2
echo "   CPU 4K pipeline (sink null): $(key $T/cpu_pipeline_4k.log fps) fps"

echo "== FPGA host via xrt_sim (kernel C-model)"
mkdir -p $T/f1 $T/f2
XRT_SIM_NUM_CUS=1 run host_1cu_b2_partial $HS --xclbin sim.xclbin --video $V1 --frames 5 --batch 2 --sink raw --out-dir $T/f1 --checksum --verify
[ "$(key $T/host_1cu_b2_partial.log jobs)" = 3 ] && [ "$(key $T/host_1cu_b2_partial.log frames)" = 5 ] && ok "host 5 frames in 3 jobs (last one partial)" || bad "host jobs/frames"
[ "$(key $T/host_1cu_b2_partial.log verified)" = 1 ] && ok "host verify (max diff $(key $T/host_1cu_b2_partial.log verify_max_absdiff))" || bad "host verify"
run host_ladder_check python3 test/check_ladder.py $V1 $T/f1 2 5
XRT_SIM_NUM_CUS=2 run host_2cu_b1 $HS --xclbin sim.xclbin --video $V1 --frames 5 --slots-per-worker 2 --sink raw --out-dir $T/f2
[ "$(key $T/host_2cu_b1.log n_cu)" = 2 ] && ok "host probed 2 CUs" || bad "host CU probe"
w0=$(key $T/host_2cu_b1.log jobs_worker0); w1=$(key $T/host_2cu_b1.log jobs_worker1)
[ "${w0:-0}" -gt 0 ] && [ "${w1:-0}" -gt 0 ] && ok "both CUs used ($w0/$w1 jobs)" || bad "CU usage $w0/$w1"
same_dir $T/f1 $T/f2 && ok "host output independent of CU count and batch" || bad "host config dependence"
grep -q "^Starting 3-deep pipelined processing" $T/host_2cu_b1.log && grep -q "^Shutdown signal received" $T/host_2cu_b1.log \
    && ok "host v1 markers" || bad "host markers"
XRT_SIM_NUM_CUS=1 run host_resize_only $HS --xclbin sim.xclbin --video $V1 --resize-only --batch 2 --slots-per-worker 1 --iters 3 --verify
[ "$(key $T/host_resize_only.log frames)" = 4 ] && ok "resize-only rounds up to whole batches" || bad "resize-only frames=$(key $T/host_resize_only.log frames)"
for k in project platform impl fps frames n_cu t_xclbin_s t_alloc_s t_total_s t_compute_s h2d_ms_per_frame kernel_ms_per_frame \
         d2h_ms_per_frame bo_in_bytes bo_out_bytes out_fnv1a64 verified ok; do
  grep -q "^RESULT $k=" $T/host_resize_only.log || bad "host missing RESULT $k"
done
grep -q "^RESULT_JSON {" $T/host_resize_only.log && ok "RESULT_JSON present" || bad "RESULT_JSON"
if [ "${FULL:-0}" = 1 ]; then
  XRT_SIM_NUM_CUS=1 run host_4k_1frame $HS --xclbin sim.xclbin --video $V4 --frames 1 --verify
fi

echo "== error handling"
if $HS --video $V1 > $T/e1.log 2>&1; then bad "missing --xclbin accepted"; else ok "missing --xclbin rejected"; fi
if $CPU --video $V1 --batch 9 > $T/e2.log 2>&1; then bad "batch 9 accepted"; else ok "--batch 9 rejected"; fi
if $CPU --video $V7 > $T/e3.log 2>&1; then bad "720p input accepted"; else ok "input smaller than 1080p rejected"; fi
if $CPU --video $V1 --sink raw > $T/e4.log 2>&1; then bad "raw without out-dir accepted"; else ok "--sink raw without --out-dir rejected"; fi
grep -q "^RESULT ok=0" $T/e3.log && ok "error run prints ok=0" || bad "error RESULT"

echo "SUMMARY: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ]
