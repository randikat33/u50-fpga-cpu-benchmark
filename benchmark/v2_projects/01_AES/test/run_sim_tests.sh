#!/usr/bin/env bash
# run_sim_tests.sh - sandbox verification for AES v2 (no Vitis, no card).
#  1. kernel C-sim + all CPU impls vs NIST / textbook reference (tb_aes)
#  2. CPU binary (production flags): files of edge sizes, all impls, cmp with each other,
#     with the OpenSSL CLI, decrypt round trip, --verify, counter carry
#  3. FPGA host against xrt_sim: 2 CUs, 1 MiB chunks (multi-chunk + tail), file and --no-io
#     modes, enc/dec round trip, carry IV, output == CPU output
#  4. host syntax check against the real XRT headers
#  5. quick CPU throughput (--no-io 256 MiB, informational)
set -u
cd "$(dirname "$0")/.."
SHIM="${SHIM:-/home/USER/shim}"
BUILD="${BUILD:-build}"
CXX="${CXX:-g++}"
T="$BUILD/simtest"
mkdir -p "$T"
pass=0; fail=0
ok()   { echo "PASS: $*"; pass=$((pass+1)); }
bad()  { echo "FAIL: $*"; fail=$((fail+1)); }
chk()  { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
res()  { grep -o "^RESULT $2=[^ ]*" "$1" | head -1 | cut -d= -f2; }
HLS_INC="-I$SHIM/include -I$SHIM/HLS_arbitrary_Precision_Types/include"
INC="-I../common -Icommon -Icpu -Ikernel"

# ---- VAES availability (the sandbox hides the CPUID bit) ----
ISA=""
if $CXX -O2 -march=native -mvaes test/vaes_probe.cpp -o "$T/vaes_probe" 2>/dev/null && "$T/vaes_probe"; then
    ISA="-mvaes -mvpclmulqdq"; export AES_FORCE_VAES=1
    echo "INFO: AVX-512 VAES executes on this CPU (CPUID bit: $(grep -qw vaes /proc/cpuinfo && echo set || echo hidden))"
fi

# ---- 1. C-sim testbench ----
echo "== 1. kernel C-sim + CPU impls vs reference"
if $CXX -std=c++17 -O2 -march=native $ISA -w $HLS_INC $INC test/tb_aes.cpp kernel/aes256ctr.cpp -o "$T/tb_aes" -lcrypto; then
    "$T/tb_aes" | tail -5 | tee "$T/tb.log"
    if grep -q "0 failed" "$T/tb.log"; then ok "tb_aes ($(grep -o '[0-9]* passed' "$T/tb.log"))"; else bad "tb_aes"; fi
else bad "tb_aes build"; fi

# ---- 2. CPU binary ----
echo "== 2. CPU baseline binary"
make -s cpu BUILD="$BUILD" CPU_ISA="$ISA" >/dev/null && ok "make cpu (production flags)" || bad "make cpu"
CPU="$BUILD/aes_cpu"
KEY=000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
IV=f0e0d0c0b0a090807060504030201000
IVC=0011223344556677ffffffffffffffff        # carry across the 64-bit boundary on block 1
mkfile() { head -c "$2" /dev/urandom > "$1"; }
sizes="1 63 64 65 4097 3145735 2621443"      # 2621443 = 2.5 MiB + 3: not a multiple of 1 MiB chunks
for s in $sizes; do mkfile "$T/in_$s.bin" "$s"; done
for s in $sizes; do
    for iv in $IV $IVC; do
        tag="size=$s iv=${iv:16}"
        openssl enc -aes-256-ctr -K $KEY -iv $iv -nosalt -in "$T/in_$s.bin" -out "$T/ossl_$s.bin" 2>/dev/null
        allok=1
        for impl in vaes aesni ttable openssl; do
            "$CPU" --impl $impl --mode enc --in "$T/in_$s.bin" --out "$T/c_${impl}_$s.bin" --key $KEY --iv $iv \
                   --threads 2 --chunk-mb 1 --seg-kb 256 --verify > "$T/c_${impl}_$s.log" 2>&1 || allok=0
            [ "$(res "$T/c_${impl}_$s.log" ok)" = 1 ] || allok=0
            cmp -s "$T/c_${impl}_$s.bin" "$T/ossl_$s.bin" || allok=0
        done
        [ $allok = 1 ] && ok "cpu 4 impls == openssl CLI, --verify ok [$tag]" || bad "cpu impls [$tag]"
    done
done
"$CPU" --impl vaes --mode dec --in "$T/c_vaes_3145735.bin" --out "$T/rt.bin" --key $KEY --iv $IVC --threads 2 > /dev/null 2>&1
chk "cpu dec(enc(x)) == x (3 MiB)" cmp -s "$T/rt.bin" "$T/in_3145735.bin"
for impl in vaes aesni ttable openssl; do
    "$CPU" --impl $impl --mode enc --no-io --size-mb 3 --chunk-mb 1 --threads 2 --verify --checksum > "$T/n_$impl.log" 2>&1
done
h=$(res "$T/n_vaes.log" out_fnv1a64); same=1
for impl in aesni ttable openssl; do [ "$(res "$T/n_$impl.log" out_fnv1a64)" = "$h" ] || same=0; done
for impl in vaes aesni ttable openssl; do [ "$(res "$T/n_$impl.log" ok)" = 1 ] || same=0; done
[ $same = 1 ] && ok "cpu --no-io: 4 impls same checksum, --verify ok" || bad "cpu --no-io checksums"
"$CPU" --impl ttable --mode enc --in "$T/in_64.bin" --key 00 > "$T/badkey.log" 2>&1
[ "$(res "$T/badkey.log" ok)" = 0 ] && ok "cpu rejects malformed key" || bad "cpu malformed key"
if objdump -d --no-show-raw-insn -C "$CPU" | awk '/ctr_ttable/,/^$/' | grep -qiE "aesenc|vaes"; then
    bad "ttable path contains AES instructions"; else ok "ttable path has no AES instructions (objdump)"; fi

# ---- 3. host via xrt_sim ----
echo "== 3. FPGA host against xrt_sim (2 CUs, 1 MiB chunks)"
HOST="$T/aes_fpga_sim"
if $CXX -std=c++17 -O2 -march=native -w -pthread -I"$SHIM/xrt_sim" $HLS_INC $INC \
        host/aes_fpga.cpp test/sim_register.cpp kernel/aes256ctr.cpp -o "$HOST"; then
    ok "host build against xrt_sim"
else bad "host build against xrt_sim"; fi
H() { "$HOST" --xclbin sim.xclbin --cus 2 --chunk-mb 1 --key $KEY "$@"; }
for s in 1 65 2621443 3145735; do
    for iv in $IV $IVC; do
        tag="size=$s iv=${iv:16}"
        H --mode enc --iv $iv --in "$T/in_$s.bin" --out "$T/f_$s.bin" --verify --checksum > "$T/f_$s.log" 2>&1
        o=$(res "$T/f_$s.log" ok); k=$(res "$T/f_$s.log" kat_ok)
        # the CPU reference for this iv
        "$CPU" --impl openssl --mode enc --in "$T/in_$s.bin" --out "$T/r_$s.bin" --key $KEY --iv $iv >/dev/null 2>&1
        if [ "$o" = 1 ] && [ "$k" = 1 ] && cmp -s "$T/f_$s.bin" "$T/r_$s.bin"; then
            ok "host(xrt_sim) == cpu, --verify ok, KAT ok [$tag n_cu=$(res "$T/f_$s.log" n_cu) chunks=$(res "$T/f_$s.log" nchunks)]"
        else bad "host(xrt_sim) [$tag]"; fi
    done
done
H --mode dec --iv $IVC --in "$T/f_3145735.bin" --out "$T/fd.bin" > "$T/fd.log" 2>&1
chk "host dec(enc(x)) == x (3 MiB+7, 4 chunks, carry IV)" cmp -s "$T/fd.bin" "$T/in_3145735.bin"
H --mode enc --iv $IV --no-io --size-mb 5 --verify > "$T/fn.log" 2>&1
if [ "$(res "$T/fn.log" ok)" = 1 ] && [ "$(res "$T/fn.log" verified)" = 1 ]; then ok "host --no-io 5 MiB (5 chunks, 2 CUs) verified"; else bad "host --no-io"; fi
missing=""
for k in project platform impl ok mode t_xclbin_s t_alloc_s t_read_s t_h2d_s t_kernel_s t_d2h_s t_write_s \
         t_compute_s t_accel_window_s t_total_s n_cu bytes chunk_mb throughput_gbps e2e_gbps; do
    [ -n "$(res "$T/f_3145735.log" $k)" ] || missing="$missing $k"
done
[ -z "$missing" ] && ok "host report has all mandatory keys" || bad "host report keys missing:$missing"
H --mode enc --in "$T/in_64.bin" --out "$T/x.bin" --chunk-mb 2048 > "$T/big.log" 2>&1
[ "$(res "$T/big.log" ok)" = 0 ] && ok "host rejects chunk > 1 GiB" || bad "host chunk limit"
"$HOST" --mode enc --in "$T/in_64.bin" > "$T/nox.log" 2>&1
[ "$(res "$T/nox.log" ok)" = 0 ] && ok "host requires --xclbin" || bad "host --xclbin check"
grep -q "^MARK start" "$T/f_3145735.log" && grep -q "^MARK end" "$T/f_3145735.log" && grep -q "^RESULT_JSON {" "$T/f_3145735.log" \
    && ok "MARK start/end + RESULT_JSON present" || bad "MARK/RESULT_JSON"

# ---- 4. real XRT syntax ----
echo "== 4. real XRT header syntax check"
chk "host compiles against real XRT 2.16 headers" $CXX -std=c++17 -fsyntax-only -Wall -Wextra \
    -I"$SHIM/XRT/src/runtime_src/core/include" -I"$SHIM/xrtver" $INC host/aes_fpga.cpp

# ---- 5. quick throughput ----
echo "== 5. CPU throughput, --no-io 256 MiB, $(nproc) threads (sandbox, not the server)"
for impl in vaes aesni ttable openssl; do
    "$CPU" --impl $impl --mode enc --no-io --size-mb 256 > "$T/tp_$impl.log" 2>&1
    printf "THROUGHPUT impl=%-8s threads=%s gbps=%s ok=%s\n" $impl "$(res "$T/tp_$impl.log" threads)" \
        "$(res "$T/tp_$impl.log" throughput_gbps)" "$(res "$T/tp_$impl.log" ok)"
done
H --mode enc --no-io --size-mb 8 > "$T/tp_host.log" 2>&1
printf "THROUGHPUT host(xrt_sim, C-sim kernel) 8 MiB gbps=%s (meaningless for the card)\n" "$(res "$T/tp_host.log" throughput_gbps)"

echo "SUMMARY: $pass passed, $fail failed"
[ $fail = 0 ]
