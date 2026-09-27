#!/usr/bin/env bash
# =============================================================================
#  run_all.sh - the complete benchmark campaign for the journal paper.
#
#   Phase A  v2 programs (A-grade FPGA + A-grade CPU), full sweeps     -> results/v2
#   Phase B  v1 programs (thesis versions), key points only (option B) -> results/v1
#   Phase C  tables, figures, v1-vs-v2 comparison, REPORT, one zip to upload
#
#  ./run_all.sh --check            check paths, builds and sensors (run this first)
#  ./run_all.sh --quick            smoke test: 1 repetition, fewer sweep points (~1.5 h)
#  ./run_all.sh                    full campaign (5 repetitions, ~14-18 h, run in tmux)
#  ./run_all.sh --only 01,04       selected projects only
#  ./run_all.sh --v2-only | --v1-only
#  ./run_all.sh --reps 7           other number of repetitions
#  ./run_all.sh --analyze          only rebuild tables/figures/zip from results/
#
#  Every run is resumable: stop with Ctrl-C and start the same command again.
#  Build the v2 programs first: ./build_v2.sh  (and ./build_v2.sh --xclbin)
# =============================================================================
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SUITE_DIR
PROJECTS=(01_AES 02_Convolution 03_MC_Heston 04_Portfolio 05_LiveStream_Single 06_LiveStream_Multi)
MODE=full; ONLY=""; DO_V1=1; DO_V2=1
while [ $# -gt 0 ]; do
    case $1 in
        --check)   MODE=check ;;
        --quick)   export REPS=1 PROFILED_REPS=1 COLD_PROBES=1 QUICK=1 ;;
        --analyze) MODE=analyze ;;
        --only)    ONLY=$2; shift ;;
        --reps)    export REPS=$2; shift ;;
        --v2-only) DO_V1=0 ;;
        --v1-only) DO_V2=0 ;;
        -h|--help) sed -n 2,19p "$0"; exit 0 ;;
        *) echo "unknown option $1"; exit 1 ;;
    esac; shift
done
RESULTS_ROOT="$SUITE_DIR/results"; [ "${QUICK:-0}" = 1 ] && RESULTS_ROOT="$SUITE_DIR/results_quick"
export STATE_DIR="$RESULTS_ROOT/.state"
export RESULTS_DIR="$RESULTS_ROOT"
source "$SUITE_DIR/common/lib.sh"
if [ "${QUICK:-0}" = 1 ]; then COOLDOWN_REP_MIN=5; COOLDOWN_REP_MAX=30; COOLDOWN_PROJECT_MIN=30; COOLDOWN_PROJECT_MAX=120; fi
export COOLDOWN_REP_MIN COOLDOWN_REP_MAX
mkdir -p "$RESULTS_ROOT"
exec > >(tee -a "$RESULTS_ROOT/run_all.log") 2>&1

selected() { [ -z "$ONLY" ] && return 0; local n=${1%%_*}; [[ ",$ONLY," == *",$n,"* ]]; }
v2script() { echo "$SUITE_DIR/v2/$1/run_benchmark.sh"; }
v1script() { echo "$SUITE_DIR/v1/$1/run_benchmark.sh"; }
run_v2() { RESULTS_DIR="$RESULTS_ROOT/v2" bash "$(v2script "$1")" "${@:2}"; }
run_v1() { V1_SUBSET=1 RESULTS_DIR="$RESULTS_ROOT/v1" bash "$(v1script "$1")" "${@:2}"; }

# ---------------------------------------------------------------------------
preflight() {
    section "Pre-flight check"
    local bad=0 t p
    for t in python3 numactl taskset xbutil xclbinutil ss ffmpeg zip; do
        command -v $t >/dev/null && echo "  [ok]      $t" || { echo "  [MISSING] $t"; case $t in python3|numactl|ffmpeg) bad=1 ;; esac; }
    done
    python3 -c "import numpy, pandas, matplotlib" 2>/dev/null && echo "  [ok]      python: numpy pandas matplotlib" \
        || { echo "  [MISSING] python packages -> pip3 install --user numpy pandas matplotlib"; bad=1; }
    local node; node=$(for b in $(lspci -D -d 10ee: 2>/dev/null | awk '{print $1}'); do cat /sys/bus/pci/devices/$b/numa_node; done | sort -u | head -1)
    echo "  [info]    Alveo U50 NUMA node: ${node:-unknown} (benchmarks pinned to node $NUMA_NODE)"
    [ -n "$node" ] && [ "$node" != "$NUMA_NODE" ] && [ "$node" != "-1" ] && warn "card is on NUMA node $node - set NUMA_NODE=$node and matching CPU_CORES in config.local.sh"
    echo "  [info]    SMT (hyper-threading): $(cat /sys/devices/system/cpu/smt/control 2>/dev/null) - using physical cores $CPU_CORES"
    python3 "$COMMON/power_monitor.py" --probe | sed 's/^/  [sensor]  /'
    if [ $DO_V2 = 1 ]; then
        echo "  ===== v2 programs"
        for p in "${PROJECTS[@]}"; do
            selected "$p" || continue
            echo "  --- $p (v2)"
            run_v2 "$p" --check || echo "  -> $p v2 will be SKIPPED"
        done
    fi
    if [ $DO_V1 = 1 ]; then
        echo "  ===== v1 programs (key points only)"
        for p in "${PROJECTS[@]}"; do
            selected "$p" || continue
            echo "  --- $p (v1)"
            run_v1 "$p" --check || echo "  -> $p v1 will be SKIPPED"
        done
    fi
    return $bad
}

permissions() {
    section "One-time permissions (sudo password may be asked once)"
    # unattended (campaign.sh / no terminal): never wait for a password; campaign.sh --setup-autoresume
    # makes these settings permanent, so sudo is not needed then
    local sudo=sudo
    if [ ! -t 0 ] || [ "${UNATTENDED:-0}" = 1 ]; then sudo="sudo -n"; fi
    if ! cat /sys/class/powercap/intel-rapl:0/energy_uj >/dev/null 2>&1; then
        $sudo sh -c 'chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj /sys/class/powercap/intel-rapl:*:*/energy_uj' \
            && info "RAPL counters readable" || warn "could not open RAPL counters - CPU energy will be missing"
    else info "RAPL counters already readable"; fi
    if command -v cpupower >/dev/null; then
        $sudo cpupower frequency-set -g performance >/dev/null 2>&1 && info "CPU governor = performance" || warn "could not set governor"
    else
        $sudo sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > $g; done' 2>/dev/null \
            && info "CPU governor = performance" || warn "could not set governor (recorded in system/cpufreq.txt)"
    fi
}

idle_baseline() {
    section "Idle baseline (${IDLE_TEST_S:-60} s, nothing running)"
    local d="$RESULTS_ROOT/idle/rep1"
    measure "$d" --no-pin --idle 0 --label "idle baseline" -- sleep "${IDLE_TEST_S:-60}"
    python3 - "$d/power.csv" > "$STATE_DIR/idle_temps.txt" <<'PY'
import sys, csv, statistics as st
rows = list(csv.DictReader(open(sys.argv[1])))
def med(k):
    v = [float(r[k]) for r in rows if r.get(k)]
    return f"{st.median(v):.1f}" if v else "nan"
print(med("fpga_temp_c"), med("cpu0_temp_c"))
PY
    info "idle temperatures (FPGA CPU): $(cat "$STATE_DIR/idle_temps.txt")"
}

analyze() {
    section "Analysis: tables, figures, report"
    local A="$SUITE_DIR/analysis"
    if [ -d "$RESULTS_ROOT/v1/raw" ]; then
        python3 "$A/analyze.py" --results "$RESULTS_ROOT/v1" || warn "v1 analysis reported errors"
        python3 "$A/make_figures.py" --results "$RESULTS_ROOT/v1" || warn "v1 figures reported errors"
    fi
    if [ -d "$RESULTS_ROOT/v2/raw" ]; then
        python3 "$A/fpga_reports_v2.py" --system "$RESULTS_ROOT/system" --out "$RESULTS_ROOT/v2/tables" \
            || warn "FPGA report tables reported errors"
        [ -f "$SUITE_DIR/tune_state/tune_summary.csv" ] && cp "$SUITE_DIR/tune_state/tune_summary.csv" "$RESULTS_ROOT/v2/tables/fpga_tuning_v2.csv"
        python3 "$A/analyze_v2.py" --results "$RESULTS_ROOT/v2" --v1 "$RESULTS_ROOT/v1" --idle "$RESULTS_ROOT/idle" \
            || warn "v2 analysis reported errors"
        python3 "$A/make_figures_v2.py" --results "$RESULTS_ROOT/v2" || warn "v2 figures reported errors"
    fi
    local zip="$SUITE_DIR/UPLOAD_ME_$(hostname)_$(date +%Y%m%d_%H%M).zip"
    ( cd "$SUITE_DIR" && zip -qr "$zip" "$(basename "$RESULTS_ROOT")" build_logs config.sh $( [ -f config.local.sh ] && echo config.local.sh ) \
        -x "*/work/*" "*/.state/switch_tmp/*" "*/_stale_xrt_files/*" )
    info "DONE. Upload this file:  $zip  ($(du -h "$zip" | cut -f1))"
}

# ---------------------------------------------------------------------------
case $MODE in
  check)   preflight; exit $? ;;
  analyze) analyze; exit 0 ;;
esac

START=$(date +%s)
info "results folder: $RESULTS_ROOT   reps=$REPS profiled=$PROFILED_REPS cold_probes=$COLD_PROBES quick=${QUICK:-0}"
preflight || { err "fix the MISSING items above first"; exit 1; }
permissions
[ -f "$RESULTS_ROOT/system/host.txt" ] || bash "$COMMON/system_info.sh"
[ -f "$RESULTS_ROOT/idle/rep1/DONE" ] || idle_baseline

first=1
phase() {   # phase <v1|v2>
    local ver=$1 p done_file
    for p in "${PROJECTS[@]}"; do
        selected "$p" || continue
        done_file="$RESULTS_ROOT/$ver/raw/$p/PROJECT_DONE"
        if [ -f "$done_file" ]; then info "$p ($ver) already complete - skipping"; continue; fi
        if ! "run_$ver" "$p" --check > /dev/null 2>&1; then warn "$p ($ver) skipped (missing files, see --check)"; continue; fi
        [ $first = 1 ] || { section "Cool-down before $p ($ver)"; cooldown "$COOLDOWN_PROJECT_MIN" "$COOLDOWN_PROJECT_MAX"; }
        first=0
        section "PROJECT $p ($ver)  (started $(date +%H:%M))"
        "run_$ver" "$p"
        info "$p ($ver) finished after $(( ($(date +%s)-START)/60 )) min total"
    done
}
[ $DO_V2 = 1 ] && { section "PHASE A: v2 programs"; phase v2; }
[ $DO_V1 = 1 ] && { section "PHASE B: v1 programs (key points)"; phase v1; }
bash "$COMMON/system_info.sh" > /dev/null 2>&1   # refresh
analyze
info "total time: $(( ($(date +%s)-START)/60 )) min"
