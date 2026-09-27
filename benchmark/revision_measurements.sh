#!/usr/bin/env bash
# =============================================================================
#  revision_measurements.sh - extra measurements for the JSA revision.
#
#  Copy this file into the benchmark suite folder (the folder that contains
#  config.sh, common/, mc_steady.sh, energy_long.sh ...) and run it there.
#  It reuses the same harness (common/lib.sh -> run_measured.py, power monitor,
#  MARK markers, cool-downs), so the new runs are directly comparable with the paper.
#
#  Steps (run all, or one at a time; every step can be restarted - finished runs are skipped):
#    sysinfo      1 min   memory channels, locked-memory limit, PCIe link, card rails, idle processes
#    idle         2 min   60 s of pure idle power (settled idle floors)
#    stream       5 min   memory bandwidth of socket 0 (and of both sockets)
#    mc_telemetry 35 min  MC Heston at the "slow state" sizes, 10 reps, with turbostat
#                         (per-core MHz, busy %) and a process census during every run
#    reps10       60 min  10 repetitions of the headline configurations (portfolio, MC 67M x 128)
#                         with the same telemetry
#    native       35 min  MC Heston and portfolio CPU built WITH fused multiply-add (-ffp-contract=fast)
#                         vs. the paper build, interleaved; FMA instruction count; price differences
#    decode        2 min  FFmpeg decode-only speed of the 4K input with 1-24 threads
#    video        75 min  both video pipelines: 5 CPU worker/thread settings + FPGA reference in the
#                         same session, decode-only speed
#    pf_ft        10 min  portfolio with the full-truncation variance scheme (less biased prices) on
#                         FPGA and CPU: same speed? outputs still identical?
#    cores        70 min  core-equivalents: socket 0 with 1, 2, 4, 8, 12, 16, 20, 24 physical cores on
#                         portfolio, MC Heston, AES and convolution, plus FPGA reference runs
#                         (how many CPU cores does the card replace, in time and in energy?)
#
#  Usage (on the server, in the suite folder):
#    sudo -v                                   # turbostat needs root; the script keeps sudo alive
#    taskset -c 95 ./revision_measurements.sh all      # harness + power monitor off socket 0
#    taskset -c 95 ./revision_measurements.sh stream   # or one step
#  Progress:   tail -f results_revision/revision.log
#  At the end: upload results_revision_<date>.tar.gz (created automatically by 'all' or 'pack').
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_revision"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR" "$RESULTS_DIR/sysinfo" "$RESULTS_DIR/telemetry" "$RESULTS_DIR/stream" "$RESULTS_DIR/video_decode"
exec > >(tee -a "$RESULTS_DIR/revision.log") 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
[ -f "$STATE_DIR/idle_temps.txt" ] || cp "$SUITE_DIR/results/.state/idle_temps.txt" "$STATE_DIR/" 2>/dev/null

N0=$(cat /sys/devices/system/node/node0/cpulist); N1=$(cat /sys/devices/system/node/node1/cpulist)
P0=${N0%%,*}; P1=${N1%%,*}
count() { python3 -c "
import sys
n=0
for part in sys.argv[1].split(','):
    a,_,b=part.partition('-'); n+= (int(b)-int(a)+1) if b else 1
print(n)" "$1"; }
declare -A CORES=( [s0_c1]="${P0%%-*}" [s0_c24]="$P0" [s0_t48]="$N0" [s01_c48]="$P0,$P1" [s01_t96]="$N0,$N1" )
declare -A NODE=(  [s0_c1]=0 [s0_c24]=0 [s0_t48]=0 [s01_c48]=0,1 [s01_t96]=0,1 )
declare -A PLACES=([s0_c1]=cores [s0_c24]=cores [s0_t48]=threads [s01_c48]=cores [s01_t96]=threads)
T() { count "${CORES[$1]}"; }
INI="$COMMON/xrt_timing.ini"
REPS10=${REPS10:-10}

# ---------------------------------------------------------------------------------------------
# telemetry: turbostat (per-core frequency, busy %, package power) + process census, started
# before a run and stopped after it. Written NEXT TO the run folder (the harness recreates it).
# ---------------------------------------------------------------------------------------------
HAVE_TS=0
TS_OPTS="--quiet --interval 0.5"
if command -v turbostat >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    HAVE_TS=1
    ( while true; do sudo -n -v 2>/dev/null; sleep 60; done ) &    # keep the sudo timestamp alive
    SUDO_KEEPALIVE=$!
    trap 'kill $SUDO_KEEPALIVE 2>/dev/null' EXIT
    # a wall-clock column lets the analysis cut turbostat to the MARK window; older turbostat lacks it
    if sudo -n turbostat --quiet --enable Time_Of_Day_Seconds --num_iterations 1 --interval 0.2 >/dev/null 2>&1; then
        TS_OPTS="$TS_OPTS --enable Time_Of_Day_Seconds"
    fi
fi
tele_start() {   # tele_start <dir>
    local d=$1; mkdir -p "$d"
    TELE_PIDS=()
    if [ $HAVE_TS = 1 ]; then
        date +%s.%N > "$d/turbostat_start.txt"       # fallback time base if there is no time column
        # shellcheck disable=SC2086
        sudo -n turbostat $TS_OPTS --out "$d/turbostat.txt" >/dev/null 2>&1 &
        TELE_PIDS+=($!)
    fi
    ( while true; do
          echo "## $(date +%s.%N)"
          ps -eLo pid,tid,psr,pcpu,stat,comm --no-headers | awk '$4 > 2.0'
          for f in /sys/devices/system/cpu/intel_uncore_frequency/*/current_freq_khz; do
              [ -r "$f" ] && echo "UNCORE $f $(cat "$f")"
          done
          sleep 0.5
      done ) > "$d/census.txt" 2>&1 &
    TELE_PIDS+=($!)
    cat /proc/loadavg > "$d/loadavg_before.txt"
}
tele_stop() {
    local p
    for p in "${TELE_PIDS[@]}"; do kill "$p" 2>/dev/null; done
    [ $HAVE_TS = 1 ] && sudo -n pkill -f "turbostat --quiet --interval 0.5" 2>/dev/null
    wait "${TELE_PIDS[@]}" 2>/dev/null   # never a bare 'wait': it would also wait for the log tee
}
# cpu_run <group> <workload> <config> <rep> <cmd...>   (CPU run with telemetry)
cpu_run() {
    local g=$1 w=$2 c=$3 rep=$4; shift 4
    local o="$RAW_DIR/$g/$w/$c/$rep"
    [ -f "$o/DONE" ] && { echo "    (already done: $g/$w/$c/$rep)"; return 0; }
    export OMP_NUM_THREADS; OMP_NUM_THREADS=$(T "$c")
    export OMP_PLACES=${PLACES[$c]} OMP_PROC_BIND=close OMP_DYNAMIC=false
    tele_start "$RESULTS_DIR/telemetry/$g/$w/$c/$rep"
    measure_v2 "$o" --cores "${CORES[$c]}" --node "${NODE[$c]}" --timeout 3600 --label "$g $w $c $rep" -- "$@"
    tele_stop
}
# fpga_run <group> <workload> <rep> <xclbin> <cmd...>
fpga_run() {
    local g=$1 w=$2 rep=$3 x=$4; shift 4
    local o="$RAW_DIR/$g/$w/fpga/$rep"
    [ -f "$o/DONE" ] && { echo "    (already done: $g/$w/fpga/$rep)"; return 0; }
    export OMP_NUM_THREADS=24 OMP_PLACES=cores OMP_PROC_BIND=close OMP_DYNAMIC=false
    tele_start "$RESULTS_DIR/telemetry/$g/$w/fpga/$rep"
    measure_v2 "$o" --xclbin "$x" --xrt-ini "$INI" --timeout 3600 --label "$g $w fpga $rep" -- "$@"
    tele_stop
}
nwarm() { python3 -c "import math,sys; print(max(2, math.ceil(3.0/float(sys.argv[1]))))" "$1"; }

# core-count configurations s0_k<k>: the first k physical cores of socket 0 (one thread per core)
CORE_COUNTS=(${CORE_COUNTS:-1 2 4 8 12 16 20 24})
firstk() { python3 -c "
import sys
cpus = []
for part in sys.argv[1].split(','):
    a, _, b = part.partition('-'); cpus += list(range(int(a), int(b) + 1)) if b else [int(a)]
print(','.join(map(str, cpus[:int(sys.argv[2])])))" "$P0" "$1"; }
for k in "${CORE_COUNTS[@]}"; do CORES[s0_k$k]=$(firstk "$k"); NODE[s0_k$k]=0; PLACES[s0_k$k]=cores; done

# ============================================================================================
step_sysinfo() {
    section "sysinfo"
    local d="$RESULTS_DIR/sysinfo"
    { echo "ulimit -l (locked memory, KiB): $(ulimit -l)"; echo "ulimit -a:"; ulimit -a; } > "$d/ulimits.txt"
    sudo -n dmidecode -t memory > "$d/dmidecode_memory.txt" 2>&1 || echo "run 'sudo -v' first for dmidecode" > "$d/dmidecode_memory.txt"
    sudo -n dmidecode -t bios -t system > "$d/dmidecode_bios.txt" 2>&1
    numactl -H > "$d/numactl.txt" 2>&1
    lscpu > "$d/lscpu.txt" 2>&1
    lspci -d 10ee: -vv 2>/dev/null | grep -E "^[0-9a-f]|LnkCap|LnkSta" > "$d/pcie_link.txt"
    cat /sys/devices/system/cpu/cpuidle/current_driver > "$d/cpuidle_driver.txt" 2>&1
    ls /sys/devices/system/cpu/intel_uncore_frequency/ > "$d/uncore_sysfs.txt" 2>&1
    { command -v turbostat && turbostat --version; } > "$d/turbostat_version.txt" 2>&1
    echo "turbostat usable with sudo -n: $HAVE_TS" >> "$d/turbostat_version.txt"
    local bdf; bdf=$(u50_bdf)
    [ -n "$bdf" ] && xbutil examine -d "$bdf" -r electrical > "$d/card_electrical_idle.txt" 2>&1
    ps -eo pid,user,psr,pcpu,pmem,etime,comm --sort=-pcpu | head -60 > "$d/processes_idle.txt"
    systemctl list-units --type=service --state=running --no-pager > "$d/services.txt" 2>&1
    uptime > "$d/uptime.txt"
    info "sysinfo -> $d"
}

# ============================================================================================
step_idle() {
    section "idle power (60 s, nothing running)"
    measure_v2 "$RAW_DIR/idle/rep1" --timeout 120 --label "idle 60 s" -- \
        bash -c 'echo "MARK start $(date +%s.%N)"; sleep 60; echo "MARK end $(date +%s.%N)"'
}

# ============================================================================================
step_stream() {
    section "memory bandwidth (STREAM-like copy/triad/xor, best of 10)"
    local d="$RESULTS_DIR/stream" src="$RESULTS_DIR/stream/bw_test.c"
    cat > "$src" <<'EOF'
/* bw_test.c - STREAM-like bandwidth test. Arrays: 3 x 1 GiB of 8-byte words, parallel first touch.
   Counts bytes as STREAM does (copy/xor: 2 words per element, triad: 3). Best of 10 trials. */
#include <omp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define N (1UL << 27)
static double now(void) { return omp_get_wtime(); }
int main(void) {
    uint64_t *a = aligned_alloc(64, N * 8), *b = aligned_alloc(64, N * 8), *c = aligned_alloc(64, N * 8);
    #pragma omp parallel for schedule(static)
    for (size_t i = 0; i < N; i++) { a[i] = i; b[i] = 2 * i; c[i] = 0; }
    double best[3] = {1e9, 1e9, 1e9};
    for (int t = 0; t < 10; t++) {
        double t0 = now();
        #pragma omp parallel for schedule(static)
        for (size_t i = 0; i < N; i++) c[i] = a[i];
        double t1 = now();
        #pragma omp parallel for schedule(static)
        for (size_t i = 0; i < N; i++) c[i] = a[i] + 3 * b[i];   /* integer triad: no denormals */
        double t2 = now();
        #pragma omp parallel for schedule(static)
        for (size_t i = 0; i < N; i++) c[i] = a[i] ^ 0x5bd1e995u;
        double t3 = now();
        if (t1 - t0 < best[0]) best[0] = t1 - t0;
        if (t2 - t1 < best[1]) best[1] = t2 - t1;
        if (t3 - t2 < best[2]) best[2] = t3 - t2;
    }
    printf("threads=%d copy_GBps=%.2f triad_GBps=%.2f xor_GBps=%.2f checksum=%lu\n", omp_get_max_threads(),
           2.0 * N * 8 / best[0] / 1e9, 3.0 * N * 8 / best[1] / 1e9, 2.0 * N * 8 / best[2] / 1e9,
           (unsigned long)(c[N / 3] + c[N / 5]));
    return 0;
}
EOF
    gcc -O3 -march=native -fopenmp "$src" -o "$d/bw_test" || { err "gcc failed"; return 1; }
    local cfg
    for cfg in s0_c1 s0_c24 s0_t48 s01_t96; do
        for r in 1 2 3; do
            OMP_NUM_THREADS=$(T "$cfg") OMP_PLACES=${PLACES[$cfg]} OMP_PROC_BIND=close \
                numactl --cpunodebind="${NODE[$cfg]}" --membind="${NODE[$cfg]}" taskset -c "${CORES[$cfg]}" "$d/bw_test" \
                | sed "s/^/config=$cfg rep=$r /" | tee -a "$d/bw_results.txt"
        done
    done
    info "bandwidth -> $d/bw_results.txt"
}

# ============================================================================================
step_mc_telemetry() {
    section "MC Heston slow-state sizes with turbostat + process census ($REPS10 reps, CPU socket 0)"
    [ $HAVE_TS = 1 ] || warn "turbostat not usable (install linux-tools-\$(uname -r) and run 'sudo -v' first); continuing with the process census only"
    declare -A TP=( [4194304x64]=0.1955 [524288x256]=0.1141 [2097152x32]=0.0604 [67108864x128]=6.1642 )
    local rep c paths steps
    for rep in $(seq -f "rep%g" 1 "$REPS10"); do
        for c in 4194304x64 524288x256 2097152x32 67108864x128; do
            paths=${c%%x*}; steps=${c##*x}
            cpu_run mc_telemetry "$c" s0_c24 "$rep" "$MC2_CPU_BIN" --impl avx512 --threads 24 \
                --paths "$paths" --steps "$steps" --runs 10 --warmup "$(nwarm "${TP[$c]}")"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

# ============================================================================================
step_reps10() {
    section "headline configurations, $REPS10 repetitions (FPGA + 3 CPU configurations)"
    local rep c W="$RESULTS_DIR/work"; mkdir -p "$W"
    # one workload at a time, so the bitstream is loaded once per workload (as in the campaign)
    for rep in $(seq -f "rep%g" 1 "$REPS10"); do
        fpga_run reps10 pf_131072 "$rep" "$PF2_XCLBIN" "$PF2_FPGA_BIN" --xclbin "$PF2_XCLBIN" \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_fpga.bin"
        for c in s0_c24 s0_t48 s01_t96; do
            cpu_run reps10 pf_131072 "$c" "$rep" "$PF2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_$c.bin"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    for rep in $(seq -f "rep%g" 1 "$REPS10"); do
        fpga_run reps10 mc_67108864x128 "$rep" "$MC2_XCLBIN" "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" \
            --paths 67108864 --steps 128 --runs 3 --warmup 0
        for c in s0_c24 s0_t48 s01_t96; do
            cpu_run reps10 mc_67108864x128 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                --paths 67108864 --steps 128 --runs 3 --warmup 0
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    rm -rf "$W"
}

# ============================================================================================
step_native() {
    section "CPU with fused multiply-add (-ffp-contract=fast) vs. the paper build: MC Heston and portfolio"
    local pdir="$V2_ROOT/03_MC_Heston" qdir="$V2_ROOT/04_Portfolio" nb="$RESULTS_DIR/bin/mc_cpu_fma"
    local pb="$RESULTS_DIR/bin/pf_cpu_fma" W="$RESULTS_DIR/work"
    mkdir -p "$RESULTS_DIR/bin" "$W"
    if [ ! -x "$nb" ]; then
        g++ -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=fast \
            -Wall -Wno-unknown-pragmas -I"$V2_ROOT/common" -I"$pdir/common" -I"$pdir/cpu" \
            "$pdir/cpu/mc_cpu.cpp" -o "$nb" || { err "build of mc_cpu_fma failed"; return 1; }
    fi
    if [ ! -x "$pb" ]; then     # same flags as the portfolio Makefile (LANES=8, LOG_IL=7), only -ffp-contract differs
        g++ -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=fast -Wall \
            -DPF_LANES=8 -DPF_LOG_IL=7 -DPF_IL_PRAGMA=128 -I"$qdir/common" -I"$qdir/kernel" -I"$qdir/cpu" \
            -I"$V2_ROOT/common" "$qdir/cpu/pf_cpu.cpp" -o "$pb" || { err "build of pf_cpu_fma failed"; return 1; }
    fi
    {   # did the compiler actually emit FMA instructions?
        for b in "$MC2_CPU_BIN" "$nb" "$PF2_CPU_BIN" "$pb"; do
            echo "$b vfmadd/vfmsub/vfnmadd instructions: $(objdump -d "$b" | grep -cE 'vf(n?m(add|sub))')"
        done
    } | tee "$RESULTS_DIR/native_fma_instructions.txt"
    local rep c paths steps
    declare -A TP=( [4194304x64]=0.1955 [67108864x128]=6.1642 )
    [ -f "$RESULTS_DIR/native_pf_compare.csv" ] || echo "rep,n_trades,n_different,max_abs_diff,median_abs_diff,max_diff_in_stderr,median_diff_in_stderr" \
        > "$RESULTS_DIR/native_pf_compare.csv"
    for rep in $(seq -f "rep%g" 1 5); do
        for c in 4194304x64 67108864x128; do
            paths=${c%%x*}; steps=${c##*x}
            cpu_run native "$c" s0_c24_paper "$rep" "$MC2_CPU_BIN" --impl avx512 --threads 24 \
                --paths "$paths" --steps "$steps" --runs 10 --warmup "$(nwarm "${TP[$c]}")"
            cpu_run native "$c" s0_c24_fma "$rep" "$nb" --impl avx512 --threads 24 \
                --paths "$paths" --steps "$steps" --runs 10 --warmup "$(nwarm "${TP[$c]}")"
        done
        cpu_run native pf_131072 s0_c24_paper "$rep" "$PF2_CPU_BIN" --impl avx512 --threads 24 \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_paper.bin"
        cpu_run native pf_131072 s0_c24_fma "$rep" "$pb" --impl avx512 --threads 24 \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_fma.bin"
        # price differences FMA vs paper build (the paper build equals the FPGA bit for bit)
        [ -f "$W/pf_paper.bin" ] && [ -f "$W/pf_fma.bin" ] && python3 - "$W/pf_paper.bin" "$W/pf_fma.bin" "$rep" \
            >> "$RESULTS_DIR/native_pf_compare.csv" <<'PY'
import sys, array, statistics
def rd(f):                                   # uint32 n, then n x (float32 price, float32 std_err)
    b = open(f, 'rb').read(); n = int.from_bytes(b[:4], 'little')
    v = array.array('f'); v.frombytes(b[4:4 + 8 * n]); return v[0::2], v[1::2]
(pa, sa), (pb, _) = rd(sys.argv[1]), rd(sys.argv[2])
d = [abs(x - y) for x, y in zip(pa, pb)]; z = [di / max(se, 1e-12) for di, se in zip(d, sa)]
print(f"{sys.argv[3]},{len(d)},{sum(x > 0 for x in d)},{max(d):.6g},{statistics.median(d):.6g},{max(z):.4g},{statistics.median(z):.4g}")
PY
        rm -f "$W"/pf_*.bin
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}
# the two native configs use socket-0 pinning
CORES[s0_c24_paper]=$P0; NODE[s0_c24_paper]=0; PLACES[s0_c24_paper]=cores
CORES[s0_c24_fma]=$P0;   NODE[s0_c24_fma]=0;   PLACES[s0_c24_fma]=cores

# ============================================================================================
step_decode() {
    section "video: decode-only speed of the 4K input (FFmpeg, 1-24 threads, socket 0)"
    local d="$RESULTS_DIR/video_decode" n r
    mkdir -p "$d"; : > "$d/ffmpeg_decode.txt"
    warm_file "$LS2_VIDEO"
    ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 \
        "$LS2_VIDEO" > "$d/frames.txt" 2>&1
    for n in 1 2 4 8 24; do
        for r in 1 2 3; do
            numactl --cpunodebind=0 --membind=0 taskset -c "$P0" \
                ffmpeg -hide_banner -nostdin -nostats -benchmark -threads "$n" -i "$LS2_VIDEO" -f null - 2>&1 \
                | grep -E "bench:" | sed "s/^/threads=$n rep=$r /" | tee -a "$d/ffmpeg_decode.txt"
        done
    done
}

# ============================================================================================
step_video() {
    section "video: CPU pipeline settings and FPGA references (socket 0, cores 0-23 as in the paper)"
    step_decode
    local O="$RESULTS_DIR/ls_out"; mkdir -p "$O"
    local q wc w cth rep o
    for rep in $(seq -f "rep%g" 1 5); do
        # ---- one output (1080p and 240p): FPGA reference, then CPU settings workers:opencv-threads
        for q in 4 0; do
            o="$RAW_DIR/video/q$q/fpga/$rep"
            if [ ! -f "$o/DONE" ]; then
                warm_file "$LS2_VIDEO"
                tele_start "$RESULTS_DIR/telemetry/video/q$q/fpga/$rep"
                measure_v2 "$o" --xclbin "$LS2_XCLBIN" --xrt-ini "$INI" --timeout "$LS2_TIMEOUT_S" \
                    --label "video q$q fpga $rep" -- "$LS2_FPGA_BIN" --xclbin "$LS2_XCLBIN" --video "$LS2_VIDEO" \
                    --quality "$q" --sink file --out "$O/out.mp4"
                tele_stop; rm -f "$O/out.mp4"
            fi
            for wc in "4:24" "1:24" "8:3" "24:1" "8:1"; do
                w=${wc%%:*}; cth=${wc##*:}
                o="$RAW_DIR/video/q$q/cpu_w${w}c${cth}/$rep"
                [ -f "$o/DONE" ] && continue
                unset OMP_PROC_BIND OMP_PLACES
                warm_file "$LS2_VIDEO"
                tele_start "$RESULTS_DIR/telemetry/video/q$q/cpu_w${w}c${cth}/$rep"
                measure_v2 "$o" --timeout "$LS2_TIMEOUT_S" --label "video q$q w$w c$cth $rep" -- \
                    "$LS2_CPU_BIN" --video "$LS2_VIDEO" --quality "$q" --threads 24 --workers "$w" --cv-threads "$cth" \
                    --sink file --out "$O/out.mp4"
                tele_stop
                rm -f "$O/out.mp4"
            done
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
        # ---- five outputs (ladder): FPGA reference, then the same CPU settings
        local LO="$RESULTS_DIR/lm_out" SINK=(--sink "$LM2_SINK")
        [ "$LM2_SINK" != null ] && SINK+=(--out-dir "$LO")
        o="$RAW_DIR/video/all5/fpga/$rep"
        if [ ! -f "$o/DONE" ]; then
            rm -rf "$LO"; mkdir -p "$LO"; warm_file "$LM2_VIDEO"
            tele_start "$RESULTS_DIR/telemetry/video/all5/fpga/$rep"
            measure_v2 "$o" --xclbin "$LM2_XCLBIN" --xrt-ini "$INI" --timeout "$LM2_TIMEOUT_S" \
                --label "video all5 fpga $rep" -- "$LM2_FPGA_BIN" --xclbin "$LM2_XCLBIN" --video "$LM2_VIDEO" "${SINK[@]}"
            tele_stop; rm -rf "$LO"
        fi
        for wc in "4:24" "1:24" "8:3" "24:1" "8:1"; do
            w=${wc%%:*}; cth=${wc##*:}
            o="$RAW_DIR/video/all5/cpu_w${w}c${cth}/$rep"
            [ -f "$o/DONE" ] && continue
            unset OMP_PROC_BIND OMP_PLACES
            rm -rf "$LO"; mkdir -p "$LO"; warm_file "$LM2_VIDEO"
            tele_start "$RESULTS_DIR/telemetry/video/all5/cpu_w${w}c${cth}/$rep"
            measure_v2 "$o" --timeout "$LM2_TIMEOUT_S" --label "video all5 w$w c$cth $rep" -- \
                "$LM2_CPU_BIN" --video "$LM2_VIDEO" --threads 24 --workers "$w" --cv-threads "$cth" "${SINK[@]}"
            tele_stop; rm -rf "$LO"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

# ============================================================================================
step_pf_ft() {
    section "portfolio with full truncation (--scheme ft), FPGA + CPU socket 0, 5 reps"
    local rep W="$RESULTS_DIR/work"; mkdir -p "$W"
    for rep in $(seq -f "rep%g" 1 5); do
        fpga_run pf_ft pf_131072_ft "$rep" "$PF2_XCLBIN" "$PF2_FPGA_BIN" --xclbin "$PF2_XCLBIN" \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --scheme ft --out "$W/pf_ft_fpga.bin"
        cpu_run pf_ft pf_131072_ft s0_c24 "$rep" "$PF2_CPU_BIN" --impl avx512 --threads 24 \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --scheme ft --out "$W/pf_ft_cpu.bin"
        cmp -s "$W/pf_ft_fpga.bin" "$W/pf_ft_cpu.bin" && echo "    ft outputs identical (FPGA = CPU)" \
            | tee -a "$RESULTS_DIR/pf_ft_check.txt" || echo "    ft outputs DIFFER" | tee -a "$RESULTS_DIR/pf_ft_check.txt"
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    rm -rf "$W"
}

# ============================================================================================
step_cores() {
    section "core-equivalents: socket 0 with ${CORE_COUNTS[*]} physical cores + FPGA references"
    local rep k c t n W="$RESULTS_DIR/work"; mkdir -p "$W"
    local R=${CORES_REPS:-3}
    for rep in $(seq -f "rep%g" 1 "$R"); do
        # FPGA references: exactly the commands of the paper's campaign / long-window runs
        fpga_run cores pf_131072 "$rep" "$PF2_XCLBIN" "$PF2_FPGA_BIN" --xclbin "$PF2_XCLBIN" \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_fpga.bin"
        fpga_run cores mc_4194304x64 "$rep" "$MC2_XCLBIN" "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" \
            --paths 4194304 --steps 64 --runs 40 --warmup 0
        fpga_run cores aes_noio "$rep" "$AES2_XCLBIN" "$AES2_FPGA_BIN" --xclbin "$AES2_XCLBIN" \
            --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 16384
        fpga_run cores conv_rgb8_8K "$rep" "$CONV2_XCLBIN" "$CONV2_FPGA_BIN" --xclbin "$CONV2_XCLBIN" \
            --repeat 200 --variant rgb8 --synthetic 7680x4320
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        # CPU: every core count; windows of about 4 s or more, >= 3 s sustained warm-up for MC
        for k in "${CORE_COUNTS[@]}"; do
            c=s0_k$k
            t=$(python3 -c "print(0.1955*24/$k)")                               # MC pass time estimate (s)
            n=$(python3 -c "import math; print(max(3, math.ceil(4.0/$t)))")
            cpu_run cores mc_4194304x64 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$k" \
                --paths 4194304 --steps 64 --runs "$n" --warmup "$(nwarm "$t")"
            cpu_run cores aes_noio "$c" "$rep" "$AES2_CPU_BIN" --impl vaes --threads "$k" \
                --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 1024 --repeat 40
            n=$(python3 -c "print(max(20, round(200*$k/24)))")
            cpu_run cores conv_rgb8_8K "$c" "$rep" "$CONV2_CPU_BIN" --impl avx512 --threads "$k" \
                --repeat "$n" --variant rgb8 --synthetic 7680x4320
            # portfolio: 1 core takes about 11 min, so 1 and 2 cores are run in the first repetition only
            if [ "$k" -gt 2 ] || [ "$rep" = rep1 ]; then
                cpu_run cores pf_131072 "$c" "$rep" "$PF2_CPU_BIN" --impl avx512 --threads "$k" \
                    --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_$c.bin"
                cmp -s "$W/pf_fpga.bin" "$W/pf_$c.bin" || echo "    WARNING: portfolio output differs ($c $rep)"
            fi
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    rm -rf "$W"
}

# ============================================================================================
step_pack() {
    local f="$SUITE_DIR/results_revision_$(date +%Y%m%d_%H%M).tar.gz"
    tar czf "$f" -C "$SUITE_DIR" results_revision
    info "packed -> $f  ($(du -h "$f" | cut -f1)). Please upload this file."
}

case "${1:-all}" in
    sysinfo) step_sysinfo ;;
    idle) step_idle ;;
    stream) step_stream ;;
    mc_telemetry) step_mc_telemetry ;;
    reps10) step_reps10 ;;
    native) step_native ;;
    video) step_video ;;
    decode) step_decode ;;
    pf_ft) step_pf_ft ;;
    cores) step_cores ;;
    pack) step_pack ;;
    all) step_sysinfo; step_idle; step_stream; step_mc_telemetry; step_reps10; step_native; step_video; step_pf_ft; step_cores; step_pack ;;
    *) echo "usage: $0 all|sysinfo|idle|stream|mc_telemetry|reps10|native|video|decode|pf_ft|cores|pack"; exit 1 ;;
esac
touch "$RESULTS_DIR/DONE_${1:-all}"
info "step '${1:-all}' finished"
