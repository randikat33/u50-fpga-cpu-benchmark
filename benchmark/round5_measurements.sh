#!/usr/bin/env bash
# =============================================================================
#  round5_measurements.sh - measurements for review round 5 (Tier 1 of the plan).
#
#  Copy this file into the benchmark suite folder (the one with config.sh, common/, energy_long.sh,
#  revision_measurements.sh ...) and run it there. It uses the same harness as the paper
#  (common/lib.sh -> run_measured.py, 10 Hz power monitor, MARK markers, cool-downs).
#
#  Steps (every step can be restarted; finished runs are skipped):
#    check          1 min   what this server can do: ipmitool (wall power), perf events, turbostat, xbutil
#    hygiene        1 min   stop and mask the desktop file indexer (tracker) and log running services
#    session <ID>  ~1.5 h   one measurement session: system snapshot, PCIe link + DMA bandwidth,
#                           60 s idle, then the headline rows (portfolio, MC 67M x 128, MC 4.2M x 64
#                           long window, AES long window + 1 GiB, convolution long window), FPGA and CPU
#                           interleaved, 5 repetitions, with WALL POWER logged the whole time.
#                           Run it on three different days:  session s1 / session s2 / session s3
#    wall          ~1.5 h   wall power with long windows (>= 30 s per run; the BMC gives ~1 reading/s):
#                           MC 4.2M x 64, AES, convolution, portfolio; FPGA, 1 socket, whole server
#    overlap       ~1 h     better FPGA hosts: AES with 1-4 buffer sets per CU (--streams, patched copy of the
#                           host, output verified), convolution with smaller strips (--strip-mb 64..8)
#    mcperf        ~1.5 h   MC Heston slow-core test: 4 sizes x 10 reps, per-core perf counters
#                           (IPC, cache and TLB misses) + turbostat, with the paper's static OpenMP
#                           schedule AND a dynamic-schedule build (same output, checked)
#    videoperf     ~30 min  240p and 1080p video pipelines, FPGA and tuned CPU (8 workers x 1 thread),
#                           per-thread perf counters of the decode thread (IPC, LLC misses)
#    pack           1 min   results_round5_<date>.tar.gz to upload
#    all                    check hygiene session s1 wall overlap mcperf videoperf pack
#
#  Usage (on the server, in the suite folder):
#    sudo -v                                            # ipmitool, perf and turbostat need root
#    taskset -c 95 ./round5_measurements.sh check       # first: shows what works
#    taskset -c 95 ./round5_measurements.sh all         # day 1
#    taskset -c 95 ./round5_measurements.sh session s2  # day 2
#    taskset -c 95 ./round5_measurements.sh session s3  # day 3, then: ./round5_measurements.sh pack
#  Progress:   tail -f results_round5/round5.log
#  Options:    REPS=5 (session reps)  REPS_MC=10 (mcperf reps)  SESSION_WORKLOADS="pf mc67 mclw aes conv"
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_round5"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR" "$RESULTS_DIR/sessions" "$RESULTS_DIR/wall" "$RESULTS_DIR/perf" "$RESULTS_DIR/telemetry" "$RESULTS_DIR/bin"
exec > >(tee -a "$RESULTS_DIR/round5.log") 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
[ -f "$STATE_DIR/idle_temps.txt" ] || cp "$SUITE_DIR/results/.state/idle_temps.txt" "$STATE_DIR/" 2>/dev/null

NODE_SYS=${NODE_SYS:-/sys/devices/system/node}
N0=$(cat "$NODE_SYS/node0/cpulist"); N1=$(cat "$NODE_SYS/node1/cpulist")
P0=${N0%%,*}; P1=${N1%%,*}
count() { python3 -c "
import sys
n=0
for part in sys.argv[1].split(','):
    a,_,b=part.partition('-'); n+= (int(b)-int(a)+1) if b else 1
print(n)" "$1"; }
declare -A CORES=( [s0_c24]="$P0" [s0_t48]="$N0" [s01_t96]="$N0,$N1" )
declare -A NODE=(  [s0_c24]=0 [s0_t48]=0 [s01_t96]=0,1 )
declare -A PLACES=([s0_c24]=cores [s0_t48]=threads [s01_t96]=threads)
T() { count "${CORES[$1]}"; }
INI="$COMMON/xrt_timing.ini"
REPS=${REPS:-5}
REPS_MC=${REPS_MC:-10}
SESSION_WORKLOADS=${SESSION_WORKLOADS:-"pf mc67 mclw aes conv"}

# ---------------------------------------------------------------------------------------------
# root helpers: keep the sudo timestamp alive (ipmitool, perf, turbostat)
# ---------------------------------------------------------------------------------------------
HAVE_SUDO=0
if sudo -n true 2>/dev/null; then
    HAVE_SUDO=1
    ( while true; do sudo -n -v 2>/dev/null; sleep 60; done ) &
    SUDO_KEEPALIVE=$!
fi
BG_PIDS=()
cleanup() {
    local p
    for p in "${BG_PIDS[@]}"; do kill "$p" 2>/dev/null; done
    [ -n "${SUDO_KEEPALIVE:-}" ] && kill "$SUDO_KEEPALIVE" 2>/dev/null
}
trap cleanup EXIT

HAVE_IPMI=0; HAVE_PERF=0; HAVE_TS=0
if [ $HAVE_SUDO = 1 ]; then
    command -v ipmitool >/dev/null 2>&1 && sudo -n ipmitool dcmi power reading 2>/dev/null | grep -qi "Instantaneous" && HAVE_IPMI=1
    command -v perf >/dev/null 2>&1 && sudo -n perf stat -e instructions -x, -- true >/dev/null 2>&1 && HAVE_PERF=1
    command -v turbostat >/dev/null 2>&1 && HAVE_TS=1
fi

# perf events: keep only the ones this CPU and kernel support
PERF_WANT="cycles instructions cache-references cache-misses LLC-loads LLC-load-misses dTLB-load-misses \
l2_rqsts.miss mem_load_retired.l3_miss mem_load_l3_miss_retired.remote_dram mem_load_l3_miss_retired.local_dram"
PERF_EVENTS=""
if [ $HAVE_PERF = 1 ]; then
    for ev in $PERF_WANT; do
        out=$(sudo -n perf stat -e "$ev" -x, -- sleep 0.01 2>&1)
        echo "$out" | grep -q "not supported\|event syntax error\|<not counted>\|Invalid" || PERF_EVENTS="$PERF_EVENTS,$ev"
    done
    PERF_EVENTS=${PERF_EVENTS#,}
fi

# ---------------------------------------------------------------------------------------------
# wall power: ipmitool dcmi at ~1 Hz, "t_unix,watts" (the BMC updates about once per second)
# ---------------------------------------------------------------------------------------------
wall_start() {   # wall_start <file>
    [ $HAVE_IPMI = 1 ] || return 0
    local f=$1
    [ -f "$f" ] || echo "t_unix,wall_w" > "$f"
    ( while true; do
          w=$(sudo -n ipmitool dcmi power reading 2>/dev/null | awk '/Instantaneous/ {print $(NF-1)}')
          [ -n "$w" ] && echo "$(date +%s.%N),$w" >> "$f"
          sleep 0.9
      done ) &
    WALL_PID=$!
    BG_PIDS+=("$WALL_PID")
}
wall_stop() { [ -n "${WALL_PID:-}" ] && kill "$WALL_PID" 2>/dev/null; WALL_PID=""; }

# ---------------------------------------------------------------------------------------------
# telemetry around one run: turbostat + per-core perf (cores given) + process census
# ---------------------------------------------------------------------------------------------
tele_start() {   # tele_start <dir> <cpu-list-for-perf or "">
    local d=$1 cl=${2:-}; mkdir -p "$d"
    TELE_PIDS=()
    if [ $HAVE_TS = 1 ]; then
        date +%s.%N > "$d/turbostat_start.txt"
        sudo -n turbostat --quiet --interval 0.5 --enable Time_Of_Day_Seconds --out "$d/turbostat.txt" >/dev/null 2>&1 &
        TELE_PIDS+=($!)
    fi
    if [ $HAVE_PERF = 1 ] && [ -n "$cl" ]; then
        date +%s.%N > "$d/perf_start.txt"
        sudo -n perf stat -a -A -C "$cl" -I 500 -x, -e "$PERF_EVENTS" -o "$d/perf_percore.csv" >/dev/null 2>&1 &
        TELE_PIDS+=($!)
    fi
    ( while true; do
          echo "## $(date +%s.%N)"
          ps -eLo pid,tid,psr,pcpu,stat,comm --no-headers | awk '$4 > 2.0'
          sleep 0.5
      done ) > "$d/census.txt" 2>&1 &
    TELE_PIDS+=($!)
}
tele_stop() {
    local p
    [ $HAVE_TS = 1 ] && sudo -n pkill -INT -f "turbostat --quiet --interval 0.5" 2>/dev/null
    [ $HAVE_PERF = 1 ] && sudo -n pkill -INT -f "perf stat -a -A" 2>/dev/null
    sleep 0.3
    for p in "${TELE_PIDS[@]}"; do kill "$p" 2>/dev/null; done
    wait "${TELE_PIDS[@]}" 2>/dev/null
}
# per-thread perf of the process called <comm> (waits for it to start), until it exits
thread_perf_start() {   # thread_perf_start <file> <comm>
    [ $HAVE_PERF = 1 ] || return 0
    local f=$1 comm=$2
    ( for i in $(seq 1 200); do
          pid=$(pgrep -n -x "$comm") && break
          sleep 0.1
      done
      [ -n "${pid:-}" ] && sudo -n perf stat --per-thread -p "$pid" -x, -e "$PERF_EVENTS" -o "$f" >/dev/null 2>&1
    ) &
    TP_PID=$!
}
thread_perf_stop() { [ -n "${TP_PID:-}" ] && { sleep 1; kill "$TP_PID" 2>/dev/null; wait "$TP_PID" 2>/dev/null; }; TP_PID=""; }

# cpu_run <group> <workload> <config> <rep> <cmd...>
cpu_run() {
    local g=$1 w=$2 c=$3 rep=$4; shift 4
    local o="$RAW_DIR/$g/$w/$c/$rep"
    [ -f "$o/DONE" ] && { echo "    (already done: $g/$w/$c/$rep)"; return 0; }
    export OMP_NUM_THREADS; OMP_NUM_THREADS=$(T "$c")
    export OMP_PLACES=${PLACES[$c]} OMP_PROC_BIND=close OMP_DYNAMIC=false
    tele_start "$RESULTS_DIR/telemetry/$g/$w/$c/$rep" "${PERF_CORES:-}"
    measure_v2 "$o" --cores "${CORES[$c]}" --node "${NODE[$c]}" --timeout 3600 --label "$g $w $c $rep" -- "$@"
    tele_stop
}
# fpga_run <group> <workload> <rep> <xclbin> <cmd...>
fpga_run() {
    local g=$1 w=$2 rep=$3 x=$4; shift 4
    local o="$RAW_DIR/$g/$w/fpga/$rep"
    [ -f "$o/DONE" ] && { echo "    (already done: $g/$w/fpga/$rep)"; return 0; }
    export OMP_NUM_THREADS=24 OMP_PLACES=cores OMP_PROC_BIND=close OMP_DYNAMIC=false
    tele_start "$RESULTS_DIR/telemetry/$g/$w/fpga/$rep" ""
    measure_v2 "$o" --xclbin "$x" --xrt-ini "$INI" --timeout 3600 --label "$g $w fpga $rep" -- "$@"
    tele_stop
}
nwarm() { python3 -c "import math,sys; print(max(2, math.ceil(3.0/float(sys.argv[1]))))" "$1"; }

# ============================================================================================
step_check() {
    section "what this server can do"
    local f="$RESULTS_DIR/capabilities.txt"
    {
        echo "date: $(date -Is)"
        echo "sudo without password (after 'sudo -v'): $HAVE_SUDO"
        echo "wall power via ipmitool dcmi:            $HAVE_IPMI"
        [ $HAVE_IPMI = 1 ] && sudo -n ipmitool dcmi power reading 2>&1 | sed 's/^/    /'
        echo "perf usable:                             $HAVE_PERF"
        echo "perf events supported:                   ${PERF_EVENTS:-none}"
        echo "perf_event_paranoid:                     $(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null)"
        echo "turbostat:                               $HAVE_TS"
        echo "ulimit -l (KiB):                         $(ulimit -l)"
        echo "U50 PCIe link:"
        { sudo -n lspci -d 10ee: -vv 2>/dev/null || lspci -d 10ee: -vv 2>/dev/null; } | grep -E "^[0-9a-f]|LnkCap:|LnkSta:" | sed 's/^/    /'
        echo "tracker (file indexer) running:          $(pgrep -c -f tracker-miner 2>/dev/null) process(es)"
    } | tee "$f"
    [ $HAVE_IPMI = 1 ] || warn "no wall power: install ipmitool (sudo apt install ipmitool) and check that 'sudo ipmitool dcmi power reading' works"
    [ $HAVE_PERF = 1 ] || warn "no perf: sudo apt install linux-tools-\$(uname -r) linux-tools-common"
    info "capabilities -> $f"
}

# ============================================================================================
step_hygiene() {
    section "stop the desktop file indexer and log running services"
    local d="$RESULTS_DIR/hygiene"; mkdir -p "$d"
    ps -eo pid,user,psr,pcpu,etime,comm --sort=-pcpu | head -40 > "$d/processes_before.txt"
    local u
    for u in tracker-miner-fs-3 tracker-extract-3 tracker-miner-rss-3 tracker-writeback-3; do
        systemctl --user mask --now "$u" >/dev/null 2>&1 && echo "masked $u"
    done
    pkill -f tracker-miner 2>/dev/null; pkill -f tracker-extract 2>/dev/null
    sleep 2
    ps -eo pid,user,psr,pcpu,etime,comm --sort=-pcpu | head -40 > "$d/processes_after.txt"
    systemctl list-units --type=service --state=running --no-pager > "$d/services.txt" 2>&1
    systemctl --user list-units --state=running --no-pager > "$d/user_services.txt" 2>&1
    echo "tracker processes left: $(pgrep -c -f tracker-miner 2>/dev/null)" | tee "$d/tracker_left.txt"
    info "(undo later with: systemctl --user unmask tracker-miner-fs-3 tracker-extract-3)"
}

# ============================================================================================
session_snapshot() {   # session_snapshot <dir>
    local d=$1; mkdir -p "$d"
    date -Is > "$d/date.txt"
    uptime > "$d/uptime.txt"
    cat /proc/cmdline > "$d/cmdline.txt"
    grep -i huge /proc/meminfo > "$d/hugepages.txt"
    { echo "ulimit -l: $(ulimit -l)"; } > "$d/ulimit.txt"
    { sudo -n lspci -d 10ee: -vv 2>/dev/null || lspci -d 10ee: -vv; } > "$d/lspci_u50.txt" 2>&1   # LnkCap/LnkSta need root
    grep -E "LnkCap:|LnkSta:" "$d/lspci_u50.txt" > "$d/pcie_link.txt"
    { ls /sys/kernel/iommu_groups 2>/dev/null | wc -l; dmesg 2>/dev/null | grep -iE "DMAR|IOMMU" | tail -5; } > "$d/iommu.txt" 2>&1
    sudo -n dmesg 2>/dev/null | grep -iE "AER|pcieport|xclmgmt|xocl" | tail -40 > "$d/dmesg_pcie.txt"
    numastat > "$d/numastat.txt" 2>&1
    sensors > "$d/sensors.txt" 2>&1
    local bdf; bdf=$(u50_bdf)
    if [ -n "$bdf" ]; then
        xbutil examine -d "$bdf" -r platform electrical thermal > "$d/xbutil_examine.txt" 2>&1
        # DMA bandwidth test of the platform (card-to-host and host-to-card, several sizes)
        timeout 600 xbutil validate -d "$bdf" -r dma --verbose > "$d/xbutil_validate_dma.txt" 2>&1
    fi
    ps -eo pid,user,psr,pcpu,etime,comm --sort=-pcpu | head -40 > "$d/processes.txt"
    pgrep -a -f tracker > "$d/tracker.txt" 2>&1
}

step_session() {
    local id=${1:-}
    [ -n "$id" ] || { err "usage: $0 session <ID>   (for example s1, s2, s3)"; return 1; }
    section "session $id: snapshot, idle, headline rows with wall power ($REPS reps)"
    local sd="$RESULTS_DIR/sessions/$id" G="session_$id" W="$RESULTS_DIR/work"
    mkdir -p "$sd" "$W"
    session_snapshot "$sd"
    wall_start "$RESULTS_DIR/wall/$G.csv"
    measure_v2 "$RAW_DIR/$G/idle/rep1" --timeout 120 --label "$G idle 60 s" -- \
        bash -c 'echo "MARK start $(date +%s.%N)"; sleep 60; echo "MARK end $(date +%s.%N)"'
    local rep c wl
    for wl in $SESSION_WORKLOADS; do
        for rep in $(seq -f "rep%g" 1 "$REPS"); do
            case $wl in
            pf)
                fpga_run "$G" pf_131072 "$rep" "$PF2_XCLBIN" "$PF2_FPGA_BIN" --xclbin "$PF2_XCLBIN" \
                    --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_fpga.bin"
                for c in s0_c24 s0_t48 s01_t96; do
                    cpu_run "$G" pf_131072 "$c" "$rep" "$PF2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                        --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_$c.bin"
                    [ -f "$W/pf_fpga.bin" ] && ! cmp -s "$W/pf_fpga.bin" "$W/pf_$c.bin" && warn "portfolio output differs ($c $rep)"
                done ;;
            mc67)
                fpga_run "$G" mc_67108864x128 "$rep" "$MC2_XCLBIN" "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" \
                    --paths 67108864 --steps 128 --runs 3 --warmup 0
                for c in s0_c24 s0_t48 s01_t96; do
                    cpu_run "$G" mc_67108864x128 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                        --paths 67108864 --steps 128 --runs 3 --warmup 0
                done ;;
            mclw)
                fpga_run "$G" mc_4194304x64 "$rep" "$MC2_XCLBIN" "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" \
                    --paths 4194304 --steps 64 --runs 40 --warmup 0
                for c in s0_c24 s01_t96; do
                    cpu_run "$G" mc_4194304x64 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                        --paths 4194304 --steps 64 --runs 40 --warmup 0
                done ;;
            aes)
                fpga_run "$G" aes_noio "$rep" "$AES2_XCLBIN" "$AES2_FPGA_BIN" --xclbin "$AES2_XCLBIN" \
                    --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 16384
                fpga_run "$G" aes_noio_1024MB "$rep" "$AES2_XCLBIN" "$AES2_FPGA_BIN" --xclbin "$AES2_XCLBIN" \
                    --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 1024
                for c in s0_c24 s01_t96; do
                    cpu_run "$G" aes_noio "$c" "$rep" "$AES2_CPU_BIN" --impl vaes --threads "$(T "$c")" \
                        --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 1024 --repeat 60
                done ;;
            conv)
                fpga_run "$G" conv_rgb8_8K "$rep" "$CONV2_XCLBIN" "$CONV2_FPGA_BIN" --xclbin "$CONV2_XCLBIN" \
                    --repeat 200 --variant rgb8 --synthetic 7680x4320
                for c in s0_c24 s01_t96; do
                    cpu_run "$G" conv_rgb8_8K "$c" "$rep" "$CONV2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                        --repeat 200 --variant rgb8 --synthetic 7680x4320
                done ;;
            *) warn "unknown session workload $wl" ;;
            esac
            cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        done
    done
    measure_v2 "$RAW_DIR/$G/idle/rep2" --timeout 120 --label "$G idle 60 s (end)" -- \
        bash -c 'echo "MARK start $(date +%s.%N)"; sleep 60; echo "MARK end $(date +%s.%N)"'
    wall_stop
    session_snapshot "$sd/end"
    rm -rf "$W"
    touch "$RESULTS_DIR/DONE_session_$id"
}

# ============================================================================================
build_mc_dynamic() {
    local pdir="$V2_ROOT/03_MC_Heston" b="$RESULTS_DIR/bin/mc_cpu_dynamic" src="$RESULTS_DIR/bin/mc_cpu_dynamic.cpp"
    [ -x "$b" ] && return 0
    sed 's/#pragma omp for schedule(static)/#pragma omp for schedule(dynamic, 64)/' "$pdir/cpu/mc_cpu.cpp" > "$src"
    grep -q "schedule(dynamic, 64)" "$src" || { err "could not find the static schedule in mc_cpu.cpp"; return 1; }
    # exactly the flags of the paper build (03_MC_Heston/Makefile), only the OpenMP schedule differs
    g++ -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off \
        -Wall -Wno-unknown-pragmas -I"$V2_ROOT/common" -I"$pdir/common" -I"$pdir/cpu" "$src" -o "$b" \
        || { err "build of mc_cpu_dynamic failed"; return 1; }
}

step_mcperf() {
    section "MC Heston slow-core test: static vs dynamic OpenMP schedule, per-core perf ($REPS_MC reps)"
    [ $HAVE_PERF = 1 ] || warn "perf not usable: running without per-core counters (turbostat and census only)"
    build_mc_dynamic || return 1
    CORES[s0_c24_static]=$P0;  NODE[s0_c24_static]=0;  PLACES[s0_c24_static]=cores
    CORES[s0_c24_dynamic]=$P0; NODE[s0_c24_dynamic]=0; PLACES[s0_c24_dynamic]=cores
    declare -A TP=( [4194304x64]=0.1955 [524288x256]=0.1141 [2097152x32]=0.0604 [67108864x128]=6.1642 )
    local rep c paths steps
    export PERF_CORES=$P0
    for rep in $(seq -f "rep%g" 1 "$REPS_MC"); do
        for c in 4194304x64 524288x256 2097152x32 67108864x128; do
            paths=${c%%x*}; steps=${c##*x}
            cpu_run mcperf "$c" s0_c24_static "$rep" "$MC2_CPU_BIN" --impl avx512 --threads 24 \
                --paths "$paths" --steps "$steps" --runs 10 --warmup "$(nwarm "${TP[$c]}")"
            cpu_run mcperf "$c" s0_c24_dynamic "$rep" "$RESULTS_DIR/bin/mc_cpu_dynamic" --impl avx512 --threads 24 \
                --paths "$paths" --steps "$steps" --runs 10 --warmup "$(nwarm "${TP[$c]}")"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    unset PERF_CORES
    # same output with both schedules?
    python3 - "$RAW_DIR/mcperf" <<'PY' | tee "$RESULTS_DIR/mcperf_output_check.txt"
import glob, os, re, sys
bad = 0
for d in sorted(glob.glob(os.path.join(sys.argv[1], "*"))):
    h = {}
    for f in glob.glob(os.path.join(d, "*", "*", "stdout.log")):
        m = re.search(r"^RESULT mom_hash=(\S+)", open(f, errors="replace").read(), re.M)
        if m:
            h.setdefault(m.group(1), []).append(f.split(os.sep)[-3])
    ok = len(h) == 1
    bad += not ok
    print(os.path.basename(d), "identical output with static and dynamic schedule" if ok else f"DIFFERENT: {h}")
print("ALL IDENTICAL" if bad == 0 else f"{bad} size(s) differ")
PY
}

# ============================================================================================
step_videoperf() {
    section "video pipelines: per-thread perf counters of the decode thread (240p, 1080p; FPGA and CPU 8x1)"
    [ $HAVE_PERF = 1 ] || { warn "perf not usable - skipping (install linux-tools)"; return 0; }
    local O="$RESULTS_DIR/ls_out" q rep o
    mkdir -p "$O" "$RESULTS_DIR/perf/video"
    for rep in $(seq -f "rep%g" 1 5); do
        for q in 0 4; do
            o="$RAW_DIR/videoperf/q$q/fpga/$rep"
            if [ ! -f "$o/DONE" ]; then
                warm_file "$LS2_VIDEO"
                tele_start "$RESULTS_DIR/telemetry/videoperf/q$q/fpga/$rep" ""
                thread_perf_start "$RESULTS_DIR/perf/video/q${q}_fpga_$rep.csv" "$(basename "$LS2_FPGA_BIN" | cut -c1-15)"
                measure_v2 "$o" --xclbin "$LS2_XCLBIN" --xrt-ini "$INI" --timeout "$LS2_TIMEOUT_S" \
                    --label "videoperf q$q fpga $rep" -- "$LS2_FPGA_BIN" --xclbin "$LS2_XCLBIN" --video "$LS2_VIDEO" \
                    --quality "$q" --sink file --out "$O/out.mp4"
                thread_perf_stop; tele_stop; rm -f "$O/out.mp4"
            fi
            o="$RAW_DIR/videoperf/q$q/cpu_w8c1/$rep"
            if [ ! -f "$o/DONE" ]; then
                unset OMP_PROC_BIND OMP_PLACES
                warm_file "$LS2_VIDEO"
                tele_start "$RESULTS_DIR/telemetry/videoperf/q$q/cpu_w8c1/$rep" ""
                thread_perf_start "$RESULTS_DIR/perf/video/q${q}_cpu_w8c1_$rep.csv" "$(basename "$LS2_CPU_BIN" | cut -c1-15)"
                measure_v2 "$o" --timeout "$LS2_TIMEOUT_S" --label "videoperf q$q w8 c1 $rep" -- \
                    "$LS2_CPU_BIN" --video "$LS2_VIDEO" --quality "$q" --threads 24 --workers 8 --cv-threads 1 \
                    --sink file --out "$O/out.mp4"
                thread_perf_stop; tele_stop; rm -f "$O/out.mp4"
            fi
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

# ============================================================================================
# overlap: can a better FPGA host close the gap? AES with S workers (buffer sets) per CU, convolution with
# more, smaller strips. FPGA only (the CPU side is unchanged); outputs are verified.
# ============================================================================================
build_aes_streams() {
    local adir="$V2_ROOT/01_AES" b="$RESULTS_DIR/bin/aes_fpga_streams" src="$RESULTS_DIR/bin/aes_fpga_streams.cpp"
    [ -x "$b" ] && return 0
    cp "$adir/host/aes_fpga.cpp" "$src"
    python3 - "$src" <<'PY' || return 1
import sys
p = sys.argv[1]; s = open(p).read()
if '"streams"' in s:
    sys.exit(0)                                     # the host already has --streams
def R(o, n):
    global s
    if s.count(o) != 1:
        sys.exit("patch failed (the AES host differs from the expected version) at: " + o[:70])
    s = s.replace(o, n)
R('const long long chunk_mb = a.i64("chunk-mb", 256);',
  'const long long streams = std::max<long long>(1, a.i64("streams", 1));\n'
  '        long long chunk_default = 256;\n'
  '        while (chunk_default * streams > 256 && chunk_default > 1) chunk_default /= 2;\n'
  '        const long long chunk_mb = a.i64("chunk-mb", chunk_default);')
R("        const size_t ncu = std::min(cus.size(), nchunks);\n        cus.resize(ncu);",
  "        const size_t n_cu_found = cus.size();\n"
  "        const size_t ncu_used = std::min(n_cu_found, nchunks);\n"
  "        cus.resize(ncu_used);\n"
  "        {\n"
  "            std::vector<Cu> slots;\n"
  "            for (long long st = 0; st < streams; ++st)\n"
  "                for (size_t i = 0; i < ncu_used; ++i) { Cu c; c.k = cus[i].k; slots.push_back(std::move(c)); }\n"
  "            cus = std::move(slots);\n"
  "        }\n"
  "        const size_t ncu = std::min(cus.size(), nchunks);\n"
  "        cus.resize(ncu);")
R('        rep.set("n_cu", static_cast<unsigned long long>(ncu));\n'
  '        rep.set("n_cu_found", static_cast<unsigned long long>(cus.size()));',
  '        rep.set("n_cu", static_cast<unsigned long long>(ncu_used));\n'
  '        rep.set("n_cu_found", static_cast<unsigned long long>(n_cu_found));\n'
  '        rep.set("streams", static_cast<unsigned long long>(streams));\n'
  '        rep.set("n_workers", static_cast<unsigned long long>(ncu));')
open(p, "w").write(s)
PY
    g++ -std=c++17 -O3 -march=native -Wall -Wextra -pthread -I"$V2_ROOT/common" -I"$adir/common" -I"$adir/cpu" \
        -I"$adir/kernel" -I"$XILINX_XRT/include" "$src" -o "$b" -L"$XILINX_XRT/lib" -lxrt_coreutil -l:libuuid.so.1 -pthread \
        || { err "build of aes_fpga_streams failed"; return 1; }
}
step_overlap() {
    section "overlapped FPGA hosts: AES --streams 1-4, convolution --strip-mb 64/32/16/8 ($REPS reps)"
    build_aes_streams || return 1
    local rep st sm
    for rep in $(seq -f "rep%g" 1 "$REPS"); do
        for st in 1 2 3 4; do
            fpga_run overlap "aes_noio_streams$st" "$rep" "$AES2_XCLBIN" "$RESULTS_DIR/bin/aes_fpga_streams" \
                --xclbin "$AES2_XCLBIN" --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 16384 \
                --streams "$st"
            [ "$rep" = rep1 ] && fpga_run overlap "aes_check_streams$st" "$rep" "$AES2_XCLBIN" "$RESULTS_DIR/bin/aes_fpga_streams" \
                --xclbin "$AES2_XCLBIN" --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 2048 \
                --streams "$st" --verify          # correctness check only (verification is inside its window)
            fpga_run overlap "aes_noio_1024MB_streams$st" "$rep" "$AES2_XCLBIN" "$RESULTS_DIR/bin/aes_fpga_streams" \
                --xclbin "$AES2_XCLBIN" --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 1024 \
                --streams "$st"
        done
        for sm in 64 32 16 8; do
            fpga_run overlap "conv_rgb8_8K_strip$sm" "$rep" "$CONV2_XCLBIN" "$CONV2_FPGA_BIN" --xclbin "$CONV2_XCLBIN" \
                --repeat 200 --variant rgb8 --synthetic 7680x4320 --strip-mb "$sm" --verify
            fpga_run overlap "conv_rgb8_8K_strip${sm}_h2d2" "$rep" "$CONV2_XCLBIN" "$CONV2_FPGA_BIN" --xclbin "$CONV2_XCLBIN" \
                --repeat 200 --variant rgb8 --synthetic 7680x4320 --strip-mb "$sm" --h2d-threads 2
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

# ============================================================================================
# wall: long windows (>= 30 s) so that the ~1 Hz wall-power readings cover each run with >= 30 samples
# ============================================================================================
make_pf_loop() {   # results_round5/bin/pf_loop.sh N cmd... : N portfolio runs inside ONE measured window
    local f="$RESULTS_DIR/bin/pf_loop.sh"
    cat > "$f" <<'EOF'
#!/bin/bash
n=$1; shift
echo "MARK start $(date +%s.%N)"
for i in $(seq 1 "$n"); do "$@" | sed -u 's/^MARK/INNER_MARK/'; done
echo "MARK end $(date +%s.%N)"
EOF
    chmod +x "$f"
}
step_wall() {
    section "wall power: long windows (>= 30 s), FPGA and CPU (1 socket, whole server), $REPS reps"
    [ $HAVE_IPMI = 1 ] || { warn "ipmitool dcmi not usable - skipping (run './round5_measurements.sh check')"; return 0; }
    local G=wall W="$RESULTS_DIR/work" rep c L="$RESULTS_DIR/bin/pf_loop.sh"
    mkdir -p "$W"; make_pf_loop
    wall_start "$RESULTS_DIR/wall/wall.csv"
    measure_v2 "$RAW_DIR/$G/idle/rep1" --timeout 120 --label "wall idle 60 s" -- \
        bash -c 'echo "MARK start $(date +%s.%N)"; sleep 60; echo "MARK end $(date +%s.%N)"'
    declare -A MCR=( [s0_c24]=160 [s01_t96]=400 )     # MC 4.2M x 64 passes per window (>= 30 s)
    declare -A AESR=( [s0_c24]=360 [s01_t96]=700 )    # AES 1 GiB passes per window
    declare -A CONVR=( [s0_c24]=1700 [s01_t96]=3600 ) # convolution 8K RGB8 frames per window
    declare -A PFN=( [s0_c24]=2 [s01_t96]=3 )         # portfolio runs per window
    for rep in $(seq -f "rep%g" 1 "$REPS"); do
        fpga_run $G mc_4194304x64 "$rep" "$MC2_XCLBIN" "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" \
            --paths 4194304 --steps 64 --runs 300 --warmup 2
        for c in s0_c24 s01_t96; do
            cpu_run $G mc_4194304x64 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                --paths 4194304 --steps 64 --runs "${MCR[$c]}" --warmup 20
        done
        fpga_run $G aes_noio "$rep" "$AES2_XCLBIN" "$AES2_FPGA_BIN" --xclbin "$AES2_XCLBIN" \
            --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 196608
        for c in s0_c24 s01_t96; do
            cpu_run $G aes_noio "$c" "$rep" "$AES2_CPU_BIN" --impl vaes --threads "$(T "$c")" \
                --key "$AES2_KEY" --iv "$AES2_IV" --mode enc --no-io --size-mb 1024 --repeat "${AESR[$c]}"
        done
        fpga_run $G conv_rgb8_8K "$rep" "$CONV2_XCLBIN" "$CONV2_FPGA_BIN" --xclbin "$CONV2_XCLBIN" \
            --repeat 700 --variant rgb8 --synthetic 7680x4320
        for c in s0_c24 s01_t96; do
            cpu_run $G conv_rgb8_8K "$c" "$rep" "$CONV2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                --repeat "${CONVR[$c]}" --variant rgb8 --synthetic 7680x4320
        done
        fpga_run $G pf_131072 "$rep" "$PF2_XCLBIN" "$L" 5 "$PF2_FPGA_BIN" --xclbin "$PF2_XCLBIN" \
            --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_fpga.bin"
        for c in s0_c24 s01_t96; do
            cpu_run $G pf_131072 "$c" "$rep" "$L" "${PFN[$c]}" "$PF2_CPU_BIN" --impl avx512 --threads "$(T "$c")" \
                --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072 --out "$W/pf_$c.bin"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    measure_v2 "$RAW_DIR/$G/idle/rep2" --timeout 120 --label "wall idle 60 s (end)" -- \
        bash -c 'echo "MARK start $(date +%s.%N)"; sleep 60; echo "MARK end $(date +%s.%N)"'
    wall_stop
    rm -rf "$W"
}

# ============================================================================================
step_pack() {
    local f="$SUITE_DIR/results_round5_$(date +%Y%m%d_%H%M).tar.gz"
    tar czf "$f" -C "$SUITE_DIR" results_round5
    info "packed -> $f  ($(du -h "$f" | cut -f1)). Please upload this file."
}

case "${1:-}" in
    check) step_check ;;
    hygiene) step_hygiene ;;
    session) step_session "${2:-}" ;;
    mcperf) step_mcperf ;;
    videoperf) step_videoperf ;;
    wall) step_wall ;;
    overlap) step_overlap ;;
    pack) step_pack ;;
    all) step_check; step_hygiene; step_session s1; step_wall; step_overlap; step_mcperf; step_videoperf; step_pack ;;
    *) echo "usage: $0 check|hygiene|session <ID>|wall|overlap|mcperf|videoperf|pack|all"; exit 1 ;;
esac
touch "$RESULTS_DIR/DONE_${1}${2:+_$2}"
info "step '${1}${2:+ $2}' finished"
