#!/bin/bash
# run_sim_tests.sh - sandbox verification for 02_Convolution (called by `make sim_test`).
set -u
SHIM=${SHIM:-/home/USER/shim}; BUILD=${BUILD:-build}; V2_ROOT=${V2_ROOT:-$(cd .. && pwd)}
T=$BUILD/simtest; rm -rf "$T"; mkdir -p "$T"
CPU=$BUILD/conv_cpu; HOST=$BUILD/sim/conv_fpga_sim; MK=$BUILD/sim/mkimg
FAILS=0; PASSES=0
pass() { PASSES=$((PASSES+1)); echo "  PASS $*"; }
fail() { FAILS=$((FAILS+1)); echo "  FAIL $*"; }
key() { grep -o "^RESULT $1=[^ ]*" "$2" | head -1 | cut -d= -f2; }
cks() { echo "$(key cks_sharpen $1)/$(key cks_edge $1)/$(key cks_blur $1)"; }

echo "== 1. kernel C-simulation vs reference (all variants, edge sizes, strips, ~1 MP)"
if "$BUILD/sim/tb_kernel" > "$T/tb.log" 2>&1; then pass "$(tail -1 "$T/tb.log")"; else fail "tb_kernel"; tail -20 "$T/tb.log"; fi

echo "== 2. CPU implementations vs reference (--verify) and cross-impl checksums"
for v in rgb16 rgb8 gray8; do
  for sz in 3x3 2x2 1x7 37x19 257x131 1003x517; do
    ref=""
    for im in ref avx512 opencv; do
      for th in 1 2; do
        f="$T/cpu_${v}_${sz}_${im}_$th.log"
        $CPU --impl $im --variant $v --synthetic $sz --threads $th --verify > "$f" 2>&1
        c=$(cks "$f")
        if [ "$(key ok "$f")" != 1 ]; then fail "cpu $im $v $sz t$th (ok!=1)"; continue; fi
        [ -z "$ref" ] && ref=$c
        [ "$c" == "$ref" ] || fail "cpu $im $v $sz t$th checksum $c != $ref"
      done
    done
    echo "$ref" > "$T/cks_${v}_${sz}"
  done
  pass "cpu ref/avx512/opencv $v (6 sizes x 2 thread counts, verify + identical checksums)"
done

echo "== 3. host end-to-end via xrt_sim"
for v in rgb16 rgb8 gray8; do
  for sz in 3x3 2x2 1x7 37x19 257x131 1003x517; do
    for opt in "" "--strip-rows 1" "--strip-rows 7 --cus 2" "--strip-mb 0.05 --cus 1 --d2h-threads 1" "--max-bo-mb 0.02 --cus 2"; do
      f="$T/host_${v}_${sz}_$(echo $opt | tr -c 'a-z0-9' _).log"
      $HOST --xclbin dummy.xclbin --variant $v --synthetic $sz --verify --repeat 2 $opt > "$f" 2>&1
      if [ "$(key ok "$f")" != 1 ] || [ "$(cks "$f")" != "$(cat "$T/cks_${v}_${sz}")" ]; then
        # an image row larger than the BO cap is an expected, reported error
        if grep -q "does not fit in --max-bo-mb" "$f"; then continue; fi
        fail "host $v $sz [$opt]"; tail -3 "$f"
      fi
    done
  done
  pass "host $v (6 sizes x 5 strip/CU configs, verify + checksum == CPU)"
done
# strips/CU accounting
f="$T/host_strip_count.log"
$HOST --xclbin x --variant rgb8 --synthetic 257x131 --strip-rows 10 --cus 2 > "$f"
[ "$(key n_strips "$f")" == 13 ] && [ "$(key n_cu "$f")" == 2 ] && pass "strip plan (13 strips on 2 CUs)" || fail "strip plan"
# error handling
$HOST --xclbin x --variant rgb8 --synthetic 9000x4 > "$T/err1.log" 2>&1; [ $? -ne 0 ] && grep -q "ok=0" "$T/err1.log" && pass "width > 8192 rejected" || fail "width check"
$HOST --variant rgb8 --synthetic 9x4 > "$T/err2.log" 2>&1; [ $? -ne 0 ] && pass "missing --xclbin rejected" || fail "xclbin check"

echo "== 4. file I/O: PNG and raw inputs, PNG/raw outputs, host == CPU byte-identical files"
for v in rgb16 rgb8 gray8; do
  $MK --variant $v --size 301x77 --out "$T/in_$v.png" && $MK --variant $v --size 301x77 --out "$T/in_$v.raw" || fail "mkimg $v"
  $CPU --impl avx512 --variant $v --in "$T/in_$v.png" --out-prefix "$T/cpu_$v" --verify > "$T/io_cpu_$v.log" 2>&1
  $HOST --xclbin x --variant $v --in "$T/in_$v.png" --out-prefix "$T/fpga_$v" --verify --strip-rows 9 > "$T/io_fpga_$v.log" 2>&1
  $HOST --xclbin x --variant $v --in "$T/in_$v.raw" --width 301 --height 77 --out-prefix "$T/fpgaraw_$v" --out-format raw > "$T/io_fpgaraw_$v.log" 2>&1
  $CPU --impl opencv --variant $v --synthetic 301x77 > "$T/io_syn_$v.log" 2>&1
  c1=$(cks "$T/io_cpu_$v.log"); c2=$(cks "$T/io_fpga_$v.log"); c3=$(cks "$T/io_fpgaraw_$v.log"); c4=$(cks "$T/io_syn_$v.log")
  same=1
  for n in sharpen edge blur; do cmp -s "$T/cpu_${v}_$n.png" "$T/fpga_${v}_$n.png" || same=0; done
  if [ "$c1" == "$c2" ] && [ "$c2" == "$c3" ] && [ "$c3" == "$c4" ] && [ $same == 1 ] && \
     [ "$(key ok "$T/io_cpu_$v.log")" == 1 ] && [ "$(key ok "$T/io_fpga_$v.log")" == 1 ]; then
    pass "io $v (png in == raw in == synthetic; host and CPU PNG outputs byte-identical)"
  else fail "io $v ($c1 $c2 $c3 $c4 same=$same)"; fi
done
# wrong bit depth is rejected
$HOST --xclbin x --variant rgb16 --in "$T/in_rgb8.png" > "$T/err3.log" 2>&1; [ $? -ne 0 ] && pass "8-bit PNG rejected by rgb16" || fail "depth check"
# cross-check against OpenCV's own codec (channel order, endianness, alpha stripping)
if python3 -c "import cv2, numpy" 2>/dev/null; then
  if python3 - "$T" "$CPU" <<'PY'
import sys, subprocess, numpy as np, cv2
T, CPU = sys.argv[1], sys.argv[2]
ok = True
# our PNG outputs decode in OpenCV to exactly the raw buffers
for v, dt, ch in (("rgb16", np.uint16, 3), ("rgb8", np.uint8, 3), ("gray8", np.uint8, 1)):
    subprocess.run([CPU, "--variant", v, "--in", f"{T}/in_{v}.png", "--out-prefix", f"{T}/cvraw_{v}", "--out-format", "raw"], check=True, capture_output=True)
    for n in ("sharpen", "edge", "blur"):
        a = cv2.imread(f"{T}/cpu_{v}_{n}.png", cv2.IMREAD_UNCHANGED)
        b = np.fromfile(f"{T}/cvraw_{v}_{n}.raw", dtype=dt).reshape(a.shape)
        ok &= a.dtype == dt and np.array_equal(a, b)
# OpenCV-written BGRA 16-bit input (alpha must be stripped, like v1's BGRA2BGR)
rng = np.random.default_rng(1)
img = rng.integers(0, 65536, (45, 67, 4), dtype=np.uint16)
cv2.imwrite(f"{T}/bgra16.png", img)
r = subprocess.run([CPU, "--variant", "rgb16", "--impl", "ref", "--in", f"{T}/bgra16.png", "--out-prefix", f"{T}/bgra", "--out-format", "raw"], capture_output=True, text=True)
bgr = img[:, :, :3].astype(np.int64)
k = lambda y, x: bgr[y, x]
H, W = 45, 67
exp = np.zeros((H, W, 3), np.int64)
c = bgr[1:-1, 1:-1]; n = bgr[:-2, 1:-1]; s = bgr[2:, 1:-1]; w = bgr[1:-1, :-2]; e = bgr[1:-1, 2:]
cr = bgr[:-2, :-2] + bgr[:-2, 2:] + bgr[2:, :-2] + bgr[2:, 2:]
exp[1:-1, 1:-1] = np.clip(5 * c - (n + s + w + e), 0, 65535)
got = np.fromfile(f"{T}/bgra_sharpen.raw", dtype=np.uint16).reshape(H, W, 3)
ok &= np.array_equal(got, exp)
# 8-bit BGR input written by OpenCV, blur via numpy floor division
img8 = rng.integers(0, 256, (33, 50, 3), dtype=np.uint8); cv2.imwrite(f"{T}/bgr8.png", img8)
subprocess.run([CPU, "--variant", "rgb8", "--in", f"{T}/bgr8.png", "--out-prefix", f"{T}/bgr8", "--out-format", "raw"], check=True, capture_output=True)
b = img8.astype(np.int64)
bl = np.zeros_like(b)
bl[1:-1, 1:-1] = (b[:-2, :-2] + 2*b[:-2, 1:-1] + b[:-2, 2:] + 2*b[1:-1, :-2] + 4*b[1:-1, 1:-1] + 2*b[1:-1, 2:] + b[2:, :-2] + 2*b[2:, 1:-1] + b[2:, 2:]) >> 4
ok &= np.array_equal(np.fromfile(f"{T}/bgr8_blur.raw", dtype=np.uint8).reshape(33, 50, 3), bl)
sys.exit(0 if ok else 1)
PY
  then pass "OpenCV/numpy cross-check (PNG codec, BGR order, 16-bit endianness, alpha strip, independent numpy model)"
  else fail "OpenCV/numpy cross-check"; fi
else echo "  SKIP python cv2 not available"; fi

echo "== 5. real XRT header syntax check"
if g++ -std=c++17 -fsyntax-only -Wall -Wextra -I$SHIM/XRT/src/runtime_src/core/include -I$SHIM/xrtver \
      -I$V2_ROOT/common -Icommon host/conv_fpga.cpp > "$T/xrt_syntax.log" 2>&1; then pass "host/conv_fpga.cpp compiles against XRT 2.16 headers"
else fail "real XRT syntax"; cat "$T/xrt_syntax.log"; fi

echo "== 6. sandbox throughput, synthetic 8K (8192x4320), $(nproc) cores - NOT the server numbers"
printf "  %-6s %-8s %10s %10s %10s\n" variant impl t_comp_s mpix/s t_read_s
for v in rgb16 rgb8 gray8; do
  for im in avx512 opencv ref; do
    rep=3; [ $im == ref ] && rep=1
    f="$T/tp_${v}_$im.log"
    $CPU --impl $im --variant $v --synthetic 8192x4320 --repeat $rep > "$f" 2>&1 || fail "8K $im $v"
    printf "  %-6s %-8s %10.4f %10.1f %10.3f\n" $v $im "$(key t_compute_min_s "$f")" "$(key mpix_per_s "$f")" "$(key t_read_s "$f")"
  done
done
f="$T/tp_gray8_host.log"
$HOST --xclbin x --variant gray8 --synthetic 8192x4320 --verify > "$f" 2>&1
[ "$(key ok "$f")" == 1 ] && [ "$(cks "$f")" == "$(cks "$T/tp_gray8_avx512.log")" ] && \
  pass "8K gray8 through xrt_sim + C-sim kernel ($(key n_strips "$f") strips, $(key n_cu "$f") CUs, window $(key t_compute_s "$f") s [C-sim speed, meaningless]) == CPU checksum" || fail "8K gray8 host sim"
f="$T/tp_png.log"
$MK --variant rgb16 --size 8192x4320 --out "$T/8k16.png"
$CPU --impl avx512 --variant rgb16 --in "$T/8k16.png" --out-prefix "$T/8k16o" > "$f" 2>&1
echo "  8K rgb16 PNG I/O (sandbox): t_read_s=$(key t_read_s "$f") t_compute_s=$(key t_compute_s "$f") t_write_s=$(key t_write_s "$f") (3 PNGs, level 1)"
rm -f "$T"/8k16*.png

echo
echo "sim_test: $PASSES passed, $FAILS failed"
[ $FAILS -eq 0 ]
