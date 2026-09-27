#!/usr/bin/env bash
# Shared helpers for every project script. Source it, don't run it.

SUITE_DIR="${SUITE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../config.sh
source "$SUITE_DIR/config.sh"
RESULTS_DIR="${RESULTS_DIR:-$SUITE_DIR/results}"
RAW_DIR="$RESULTS_DIR/raw"
STATE_DIR="${STATE_DIR:-$RESULTS_DIR/.state}"
COMMON="$SUITE_DIR/common"
mkdir -p "$RAW_DIR" "$STATE_DIR"

# Identical thread settings for every CPU baseline (24 physical cores, socket 0)
export OMP_NUM_THREADS=24 OMP_DYNAMIC=false OMP_PROC_BIND=close OMP_PLACES=cores

C_G='\033[0;32m'; C_Y='\033[1;33m'; C_R='\033[0;31m'; C_B='\033[0;34m'; C_0='\033[0m'
info()    { echo -e "${C_G}[$(date +%H:%M:%S)]${C_0} $*"; }
warn()    { echo -e "${C_Y}[$(date +%H:%M:%S)] WARN:${C_0} $*"; }
err()     { echo -e "${C_R}[$(date +%H:%M:%S)] ERROR:${C_0} $*" >&2; }
section() { echo -e "\n${C_B}==== $* ====${C_0}"; }

need() {  # need <path> <description>  -> returns 1 if missing
    if [ -e "$1" ]; then echo "  [ok]      $2: $1"; return 0; fi
    echo "  [MISSING] $2: $1"; return 1
}

# Read current temperatures "fpga cpu"
temps_now() { python3 "$COMMON/power_monitor.py" --temps 2>/dev/null || echo "nan nan"; }

# cooldown <min_s> <max_s> : wait min, then until FPGA/CPU temps are near the idle baseline
cooldown() {
    local min=$1 max=$2 waited=0
    local base_f base_c
    read -r base_f base_c < "$STATE_DIR/idle_temps.txt" 2>/dev/null || { base_f=nan; base_c=nan; }
    sleep "$min"; waited=$min
    while [ "$waited" -lt "$max" ]; do
        read -r f c <<< "$(temps_now)"
        if python3 - "$f" "$c" "$base_f" "$base_c" "$COOLDOWN_FPGA_DELTA_C" "$COOLDOWN_CPU_DELTA_C" <<'PY'
import sys, math
f,c,bf,bc,df,dc=[float(x) for x in sys.argv[1:]]
okf = math.isnan(f) or math.isnan(bf) or f <= bf+df
okc = math.isnan(c) or math.isnan(bc) or c <= bc+dc
sys.exit(0 if (okf and okc) else 1)
PY
        then break; fi
        sleep 10; waited=$((waited+10))
    done
    echo "    cooled ${waited}s (FPGA $(temps_now | cut -d' ' -f1) C, idle ${base_f} C)"
}

# measure <outdir> [run_measured options] -- <command...>
# Skips runs that already finished (DONE file) so the suite can be resumed.
measure() {
    local out=$1; shift
    if [ -f "$out/DONE" ]; then echo "    (already done: ${out#"$RAW_DIR"/})"; return 0; fi
    rm -rf "$out"; mkdir -p "$out"
    echo "  > ${out#"$RAW_DIR"/}"
    python3 "$COMMON/run_measured.py" --out "$out" --cores "$CPU_CORES" --node "$NUMA_NODE" \
        --interval "$POWER_INTERVAL" --idle "$IDLE_BASELINE_S" --state-dir "$STATE_DIR" "$@" \
        || { warn "run failed: $out (see $out/stdout.log)"; return 1; }
    sync
}

ini_for() {  # ini_for <rep-name>
    if [[ "$1" == profiled* ]]; then echo "$COMMON/xrt_profile.ini"; else echo "$COMMON/xrt_timing.ini"; fi
}

# rep names: profiled runs first (FPGA only), then rep1..repN
rep_list() { local r; for r in $(seq 1 "$PROFILED_REPS"); do echo "profiled$r"; done; seq -f "rep%g" 1 "$REPS"; }

# ---------------------------------------------------------------------------
# switch_bitstream <xclbin-we-want-to-be-COLD-for>
# Loads a DIFFERENT bitstream on the card so the next run pays the full swap cost.
# Tries `xbutil program`; falls back to a tiny AES job with the other AES xclbin.
# ---------------------------------------------------------------------------
u50_bdf() { xbutil examine 2>/dev/null | grep -oE '\[[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]\]' | head -1 | tr -d '[]'; }

switch_bitstream() {
    local target other
    target=$(realpath "$1")
    other=$(realpath "$AES_DEC_XCLBIN")
    [ "$target" = "$other" ] && other=$(realpath "$AES_ENC_XCLBIN")
    local bdf; bdf=$(u50_bdf)
    if [ -n "$bdf" ] && xbutil program --device "$bdf" --user "$other" >> "$STATE_DIR/switch.log" 2>&1; then
        echo "$other" > "$STATE_DIR/last_xclbin.txt"; return 0
    fi
    # fallback: 1 MB AES job
    local tmp="$STATE_DIR/switch_tmp"; mkdir -p "$tmp"
    [ -f "$tmp/s.bin" ] || head -c 1048576 /dev/urandom > "$tmp/s.bin"
    (
    cd "$tmp" || exit 1
    if [ "$other" = "$(realpath "$AES_ENC_XCLBIN")" ]; then
        "$AES_FPGA_BIN" encrypt "$tmp/s.bin" "$tmp/s.fpga.enc" "$AES_KEY" "$other" >> "$STATE_DIR/switch.log" 2>&1
    else
        [ -f "$tmp/s.cpu.enc" ] || "$AES_CPU_BIN" encrypt "$tmp/s.bin" "$tmp/s.cpu.enc" "$AES_KEY" >> "$STATE_DIR/switch.log" 2>&1
        mkdir -p "$tmp/dec"
        "$AES_FPGA_BIN" decrypt "$tmp/s.cpu.enc" "$tmp/dec" "$AES_KEY" "$other" >> "$STATE_DIR/switch.log" 2>&1
    fi
    rm -f summary.csv ./*.run_summary ./*trace*.csv power_profile_*.csv
    )
    echo "$other" > "$STATE_DIR/last_xclbin.txt"
}

warm_file() { cat "$1" > /dev/null 2>&1 || true; }   # put input in page cache (same for CPU and FPGA)

# project_done <name>: create a marker so run_all.sh can report progress
project_done() {
    local bad
    bad=$(find "$RAW_DIR/$1" -name meta.json -printf '%h\n' 2>/dev/null | while read -r d; do [ -f "$d/DONE" ] || echo "$d"; done)
    if [ -n "$bad" ]; then
        warn "$1: $(echo "$bad" | wc -l) run(s) failed - project NOT marked complete; start the same command again to retry them:"
        echo "$bad" | sed "s|^$RAW_DIR/|      |" | head -20
        return 0
    fi
    date > "$RAW_DIR/$1/PROJECT_DONE"
}

# wait_port_free <port> : server-style programs (web UI on 8888) must not overlap
wait_port_free() {
    local port=$1 i=0
    while ss -ltn 2>/dev/null | grep -q ":$port "; do
        [ $i -eq 0 ] && echo "    waiting for port $port to be released..."
        sleep 2; i=$((i+2))
        if [ $i -ge 120 ]; then
            warn "port $port still busy - killing leftover benchmark processes"
            pkill -f Live_Stream_Host 2>/dev/null; pkill -f Live_Stream_CPU 2>/dev/null; sleep 3; break
        fi
    done
}

# ===========================================================================
# v2 helpers
# ===========================================================================
# measure_v2 <outdir> [run_measured options] -- <command...>
# v2 programs print "MARK start/end <t>" around the measured section.
measure_v2() {
    local out=$1; shift
    measure "$out" --start-marker "^MARK start" --end-marker "^MARK end" "$@"
}

# v2_rep_list : rep names for v2 (profiled FPGA runs first, then rep1..repN)
v2_rep_list() { rep_list; }

# switch_bitstream_v2 <xclbin-we-want-to-be-COLD-for>
# Loads a different (v2 or v1) bitstream so that the next run pays the full load cost.
switch_bitstream_v2() {
    local target other="" x bdf
    target=$(realpath "$1" 2>/dev/null)
    for x in "$AES2_XCLBIN" "$MC2_XCLBIN" "$PF2_XCLBIN" "$CONV2_XCLBIN" "$AES_ENC_XCLBIN"; do
        [ -f "$x" ] || continue
        [ "$(realpath "$x")" = "$target" ] && continue
        other=$(realpath "$x"); break
    done
    [ -n "$other" ] || { warn "no other xclbin available for a cold probe"; return 1; }
    bdf=$(u50_bdf)
    if [ -n "$bdf" ] && xbutil program --device "$bdf" --user "$other" >> "$STATE_DIR/switch.log" 2>&1; then
        echo "$other" > "$STATE_DIR/last_xclbin.txt"; return 0
    fi
    local tmp="$STATE_DIR/switch_tmp"; mkdir -p "$tmp"
    (
    cd "$tmp" || exit 1
    case $other in
        "$(realpath "$AES2_XCLBIN" 2>/dev/null)") "$AES2_FPGA_BIN" --xclbin "$other" --mode enc --no-io --size-mb 1 ;;
        "$(realpath "$MC2_XCLBIN" 2>/dev/null)")  "$MC2_FPGA_BIN" --xclbin "$other" --paths 1024 --steps 4 ;;
        "$(realpath "$PF2_XCLBIN" 2>/dev/null)")  "$PF2_FPGA_BIN" --xclbin "$other" --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 256 --topk 16 ;;
        "$(realpath "$CONV2_XCLBIN" 2>/dev/null)") "$CONV2_FPGA_BIN" --xclbin "$other" --variant gray8 --synthetic 64x64 ;;
        *) switch_bitstream "$1" ;;
    esac
    rm -f summary.csv ./*.run_summary ./*trace*.csv power_profile_*.csv
    ) >> "$STATE_DIR/switch.log" 2>&1
    echo "$other" > "$STATE_DIR/last_xclbin.txt"
}

# v2_need_bins <label> <path...> : check that v2 binaries exist (built by build_v2.sh)
v2_need() {
    local ok=0 lbl=$1; shift
    local f
    for f in "$@"; do need "$f" "$lbl" || ok=1; done
    [ $ok = 0 ] || echo "            -> run ./build_v2.sh (and the xclbin builds) first"
    return $ok
}
