#!/usr/bin/env bash
# =============================================================================
#  tune_v2.sh - builds every v2 xclbin and AUTOMATICALLY picks the fastest design that
#  still builds, meets its self-check on the card and fits the U50.
#
#   03 MC Heston, 04 Portfolio : design-space search over "lanes per CU"
#       start value -> go up one lane at a time while (build OK) and (--verify OK on the
#       card) and (measured throughput improves by > TUNE_MIN_GAIN_PCT); if the start value
#       fails, go down until one works. The winner is copied to build/ and every later
#       build (build_v2.sh) uses the same lane count (v2_projects/TUNED.conf).
#   01, 02, 05, 06 : built once in their configured (already optimal) form, then checked
#       on the card. More CUs/lanes cannot speed these up (PCIe or host bound, see READMEs).
#
#  Everything is RESUMABLE: after Ctrl-C, a crash or a power failure just start the same
#  command again. Finished builds/tests are never repeated; an interrupted Vivado run is
#  restarted from its beginning (Vivado cannot resume mid-run).
#
#  ./tune_v2.sh                   all projects (days; that is fine)
#  ./tune_v2.sh --only 03,04      selected projects
#  ./tune_v2.sh --ablation        also the CU-ablation builds used in the paper (05 4 CUs, 06 2 CUs)
#  ./tune_v2.sh --no-card         decide from the reports only (achieved clock x lanes)
#  ./tune_v2.sh --status          print what has been decided so far
#  ./tune_v2.sh --retry-failed    forget failed builds/tests (after fixing a tool or path problem)
#
#  Results: tune_state/SUMMARY.md and tune_state/tune_summary.csv (a table for the paper)
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SUITE_DIR
source "$SUITE_DIR/config.sh"
V2="$V2_ROOT"
ST="$SUITE_DIR/tune_state"; mkdir -p "$ST"
ONLY=""; CARD=1; ABL=0; STATUS=0; RETRY=0
while [ $# -gt 0 ]; do
    case $1 in
        --only) ONLY=$2; shift ;;
        --no-card) CARD=0 ;;
        --ablation) ABL=1 ;;
        --status) STATUS=1 ;;
        --retry-failed) RETRY=1 ;;
        -h|--help) sed -n 2,27p "$0"; exit 0 ;;
        *) echo "unknown option $1"; exit 1 ;;
    esac; shift
done
selected() { [ -z "$ONLY" ] && return 0; local n=${1%%_*}; [[ ",$ONLY," == *",$n,"* ]]; }
say() { echo "[$(date '+%F %T')] $*" | tee -a "$ST/tune.log"; }
kv() { grep -s "^$2=" "$1" | tail -1 | cut -d= -f2-; }

summary() {
    python3 - "$ST" <<'PY'
import glob, os, sys
st = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(st, "*", "*.result"))):
    d = dict(l.strip().split("=", 1) for l in open(f) if "=" in l)
    d["project"] = os.path.basename(os.path.dirname(f))
    d["variant"] = os.path.basename(f)[:-7]
    sel = os.path.join(os.path.dirname(f), "SELECTED")
    d["selected"] = "yes" if os.path.exists(sel) and open(sel).read().split()[0] == d["variant"] else ""
    rows.append(d)
cols = ["project", "variant", "built", "ok", "metric", "score", "requested_clock_mhz", "achieved_kernel_clock_mhz", "hls_target_mhz",
        "hls_fmax_est_mhz", "WNS_kernel_clocks_min", "util_LUT_pct", "util_REG_pct", "util_DSP_pct",
        "util_BRAM_pct", "util_URAM_pct", "decision", "selected"]
with open(os.path.join(st, "tune_summary.csv"), "w") as fo:
    fo.write(",".join(cols) + "\n")
    for d in rows:
        fo.write(",".join(str(d.get(c, "")) for c in cols) + "\n")
with open(os.path.join(st, "SUMMARY.md"), "w") as fo:
    fo.write("# Automatic FPGA design selection (tune_v2.sh)\n\n| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n")
    for d in rows:
        fo.write("| " + " | ".join(str(d.get(c, "")) for c in cols) + " |\n")
print(open(os.path.join(st, "SUMMARY.md")).read())
PY
}
[ $STATUS = 1 ] && { summary; exit 0; }

exec 9>"$ST/.lock"
flock -n 9 || { echo "tune_v2.sh is already running (lock $ST/.lock)"; exit 1; }
if [ $RETRY = 1 ]; then
    for f in "$ST"/*/*.result; do
        [ -f "$f" ] || continue
        if grep -q "^ok=0" "$f"; then
            p=$(basename "$(dirname "$f")")
            [ -f "$ST/$p/SELECTED" ] && continue
            echo "retry: forgetting $f"; rm -f "$f" "${f%.result}.attempts"
        fi
    done
fi

for f in "$VITIS_SETTINGS" "$XRT_SETUP"; do [ -f "$f" ] && source "$f" >/dev/null 2>&1; done
command -v v++ >/dev/null || { echo "v++ not found: check VITIS_SETTINGS in config.sh"; exit 1; }
PIN=(numactl "--cpunodebind=$NUMA_NODE" "--membind=$NUMA_NODE" taskset -c "$CPU_CORES")
command -v numactl >/dev/null || PIN=()

wait_for_card() {   # after a reboot the card may need a few minutes
    [ $CARD = 1 ] || return 0
    local i
    for i in $(seq 1 60); do
        xbutil examine 2>/dev/null | grep -qiE "u50|xilinx_u50" && return 0
        sleep 10
    done
    say "WARNING: card not visible after 10 min - continuing, tests will fail and be retried next start"
}

# ---------------------------------------------------------------------------
# build <project> <build-dir-name> <make vars...>   (resumable through make)
# If Vivado cannot close timing at the project's clock, the SAME design is rebuilt at the next
# lower clock from TUNE_FREQS_HZ (this is a normal outcome and is reported in the paper).
# Sets BUILD_FREQ_HZ to the clock of the successful build.
build() {
    local p=$1 bdir=$2; shift 2
    local log="$ST/$p/${bdir//\//_}.build.log"
    mkdir -p "$ST/$p"
    local def f freqs=()
    def=$(make -s -C "$V2/$p" print-FREQ_HZ 2>/dev/null); def=${def:-300000000}
    # start at the highest clock that already closed timing for this project (saves failed runs)
    [ -s "$ST/$p/FREQ_OK" ] && def=$(cat "$ST/$p/FREQ_OK")
    freqs=("$def")
    for f in $TUNE_FREQS_HZ; do [ "$f" -lt "$def" ] && freqs+=("$f"); done
    for f in "${freqs[@]}"; do
        BUILD_FREQ_HZ=$f
        say "$p: building $bdir ($* FREQ_HZ=$f) - log $log"
        # low priority: the machine (SSH, desktop) stays usable while Vivado runs
        if nice -n 15 ionice -c3 make -C "$V2/$p" xclbin fpga_reports BUILD="$bdir" \
                VISION_ROOT="$VISION_ROOT" FREQ_HZ="$f" "$@" >> "$log" 2>&1; then
            echo "$f" > "$ST/$p/FREQ_OK"
            return 0
        fi
        if tail -400 "$log" | grep -q "did not meet timing"; then
            say "$p: $bdir did not meet timing at $((f / 1000000)) MHz"
            rm -rf "$V2/$p/$bdir/link"          # a fresh link run is needed for a new clock
            continue
        fi
        say "$p: build $bdir FAILED (see $log)"
        return 1
    done
    say "$p: build $bdir FAILED - timing not met at any clock down to $((BUILD_FREQ_HZ / 1000000)) MHz"
    return 1
}

# card_test <project> <xclbin> <host-binary> <log> -> runs the self-checking benchmark once
card_test() {
    local p=$1 x=$2 h=$3 log=$4
    local args
    case $p in
        01_AES)               args=(--mode enc --no-io --size-mb 1024 --verify) ;;
        02_Convolution)       args=(--variant rgb8 --synthetic 7680x4320 --repeat 3 --verify) ;;
        03_MC_Heston)         args=(--paths 4194304 --steps 64 --runs 3 --warmup 1 --verify) ;;
        04_Portfolio)         args=(--portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --verify sample) ;;
        05_LiveStream_Single) args=(--video "$LS2_VIDEO" --quality 4 --resize-only --iters 300 --verify) ;;
        06_LiveStream_Multi)  args=(--video "$LM2_VIDEO" --resize-only --iters 300 --verify) ;;
    esac
    ( cd "$ST/$p" && timeout 3600 "${PIN[@]}" "$h" --xclbin "$x" "${args[@]}" ) > "$log" 2>&1
}
metric_of() {
    case $1 in
        01_AES) echo throughput_gbps ;; 02_Convolution) echo mpix_per_s ;;
        03_MC_Heston|04_Portfolio) echo msteps_per_s ;; *) echo fps ;;
    esac
}

# evaluate <project> <variant-name> <build-dir> <xclbin> <host> <lanes|-> <make vars...>
# writes $ST/<project>/<variant>.result once (atomic); returns 0 if the variant is usable
evaluate() {
    local p=$1 v=$2 bdir=$3 x=$4 h=$5 lanes=$6; shift 6
    local res="$ST/$p/$v.result" tmp
    mkdir -p "$ST/$p"
    if [ -f "$res" ]; then [ "$(kv "$res" ok)" = 1 ]; return; fi
    tmp="$res.tmp"; : > "$tmp"
    local built=0 BUILD_FREQ_HZ=0
    build "$p" "$bdir" "$@" && [ -f "$V2/$p/$x" ] && built=1
    echo "built=$built" >> "$tmp"
    echo "requested_clock_mhz=$((BUILD_FREQ_HZ / 1000000))" >> "$tmp"
    if [ $CARD = 1 ]; then echo "metric=$(metric_of "$p")"; else echo "metric=clock_MHz_x_lanes(no card)"; fi >> "$tmp"
    [ -d "$V2/$p/$bdir/reports" ] && python3 "$SUITE_DIR/common/tune_eval.py" reports "$V2/$p/$bdir" >> "$tmp"
    [ -d "$V2/$p/$bdir/reports" ] && { mkdir -p "$ST/$p/${v}_reports"; cp -a "$V2/$p/$bdir/reports/." "$ST/$p/${v}_reports/"; }
    local ok=0 score=""
    if [ $built = 1 ]; then
        if [ $CARD = 1 ]; then
            if [ ! -x "$V2/$p/$h" ]; then
                make -C "$V2/$p" host VISION_ROOT="$VISION_ROOT" "$@" BUILD="$(dirname "$h")" >> "$ST/$p/$v.host.log" 2>&1
            fi
            wait_for_card
            if card_test "$p" "$V2/$p/$x" "$V2/$p/$h" "$ST/$p/$v.test.log"; then :; fi
            eval "$(python3 "$SUITE_DIR/common/tune_eval.py" host "$ST/$p/$v.test.log" "$(metric_of "$p")" | sed 's/^/T_/')"
            ok=${T_ok:-0}; score=${T_value:-}
            if [ "$ok" != 1 ] && ! grep -q "^RESULT " "$ST/$p/$v.test.log" 2>/dev/null; then
                # the program never ran (card missing, driver problem, crash): no verdict yet,
                # retried at the next start; after 3 attempts the variant counts as failed
                local att=$(( $(cat "$ST/$p/$v.attempts" 2>/dev/null || echo 0) + 1 ))
                echo $att > "$ST/$p/$v.attempts"
                cp -f "$ST/$p/$v.test.log" "$ST/$p/$v.test.attempt$att.log" 2>/dev/null
                if [ $att -lt 3 ]; then
                    say "$p/$v: card test did not run (attempt $att, see $ST/$p/$v.test.log) - will retry at next start"
                    rm -f "$tmp"; return 2
                fi
                say "$p/$v: card test failed 3 times without a result - variant rejected"
            fi
        else
            ok=1
            local clk; clk=$(kv "$tmp" achieved_kernel_clock_mhz); [ -z "$clk" ] && clk=$(kv "$tmp" hls_fmax_est_mhz)
            [ "$lanes" != "-" ] && score=$(python3 -c "print(${clk:-0}*$lanes)") || score=$clk
        fi
    fi
    { echo "ok=$ok"; echo "score=$score"; echo "lanes=$lanes"; echo "finished=$(date -Is)"; } >> "$tmp"
    mv "$tmp" "$res"
    say "$p/$v: built=$built ok=$ok score=$score $(metric_of "$p") (clock $(kv "$res" achieved_kernel_clock_mhz) MHz, max util $(kv "$res" util_max_pct)%)"
    # free disk: the full Vivado run of a variant is only kept if requested
    if [ "$TUNE_KEEP_VIVADO_DIRS" != 1 ] && [ "$bdir" != build ]; then rm -rf "$V2/$p/$bdir/link/_x" "$V2/$p/$bdir/link/.ipcache"; fi
    [ "$ok" = 1 ]
}

decide() { echo "decision=$2" >> "$ST/$1"; }

# ---------------------------------------------------------------------------
# lane search for 03 / 04
tune_lanes() {
    local p=$1 xname=$2 hostname=$3 range=$4
    local start lo hi; read -r start lo hi <<< "$range"
    local host_of          # MC host is lane-independent; Portfolio host is built per variant
    if [ -f "$ST/$p/SELECTED" ]; then say "$p: already tuned -> $(cat "$ST/$p/SELECTED")"; return 0; fi
    ev() {   # ev <lanes>
        local n=$1 b="build_tune/L$1"
        if [ "$p" = 04_Portfolio ]; then host_of="$b/$hostname"; else host_of="build/$hostname"; fi
        [ "$p" = 03_MC_Heston ] && [ ! -x "$V2/$p/build/$hostname" ] && make -C "$V2/$p" host >> "$ST/$p/host.log" 2>&1
        evaluate "$p" "L$n" "$b" "$b/$xname" "$host_of" "$n" LANES="$n"
    }
    mkdir -p "$ST/$p"
    local best="" bestscore=0 n rc
    ev "$start"; rc=$?
    [ $rc = 2 ] && return 1
    if [ $rc = 0 ]; then
        best=$start; bestscore=$(kv "$ST/$p/L$start.result" score)
        n=$((start + 1))
        while [ "$n" -le "$hi" ]; do
            ev "$n"; rc=$?
            [ $rc = 2 ] && return 1
            if [ $rc != 0 ]; then decide "$p/L$n.result" "stop: does not build or fails the self-check"; break; fi
            local s; s=$(kv "$ST/$p/L$n.result" score)
            if python3 -c "import sys; sys.exit(0 if float('$s') > float('$bestscore') * (1 + $TUNE_MIN_GAIN_PCT / 100.0) else 1)"; then
                decide "$p/L$n.result" "better than L$best"
                best=$n; bestscore=$s; n=$((n + 1))
            else
                decide "$p/L$n.result" "stop: not faster than L$best (clock or routing limit reached)"
                break
            fi
        done
    else
        n=$((start - 1))
        while [ "$n" -ge "$lo" ]; do
            ev "$n"; rc=$?
            [ $rc = 2 ] && return 1
            if [ $rc = 0 ]; then best=$n; decide "$p/L$n.result" "first working size below the start value"; break; fi
            n=$((n - 1))
        done
    fi
    [ -n "$best" ] || { say "$p: NO working variant between $lo and $hi lanes - needs attention (see $ST/$p)"; return 1; }
    apply_tuned "$p" "$best" "$xname"
}

apply_tuned() {
    local p=$1 n=$2 xname=$3
    say "$p: selected L$n -> copying to build/ and rebuilding host + CPU with LANES=$n"
    mkdir -p "$V2/$p/build"
    cp -a "$V2/$p/build_tune/L$n/." "$V2/$p/build/"
    # keep TUNED.conf consistent (one line per project)
    touch "$V2/TUNED.conf"
    grep -v "^$p " "$V2/TUNED.conf" > "$V2/TUNED.conf.tmp"; echo "$p LANES=$n" >> "$V2/TUNED.conf.tmp"
    mv "$V2/TUNED.conf.tmp" "$V2/TUNED.conf"
    rm -f "$V2/$p"/build/{mc_cpu,mc_fpga,pf_cpu,pf_fpga,pf_gen}
    make -C "$V2/$p" cpu host LANES="$n" >> "$ST/$p/final_programs.log" 2>&1 || say "$p: WARNING rebuilding programs failed (see $ST/$p/final_programs.log)"
    echo "L$n" > "$ST/$p/SELECTED"
    decide "$p/L$n.result" "SELECTED"
}

# fixed designs: build in build/ and check on the card
check_fixed() {
    local p=$1 xname=$2 hostname=$3
    if [ -f "$ST/$p/SELECTED" ]; then say "$p: already built and checked"; return 0; fi
    mkdir -p "$ST/$p"
    [ -x "$V2/$p/build/$hostname" ] || make -C "$V2/$p" cpu host VISION_ROOT="$VISION_ROOT" >> "$ST/$p/programs.log" 2>&1
    local rc
    evaluate "$p" default build "build/$xname" "build/$hostname" - ; rc=$?
    [ $rc = 2 ] && return 1
    if [ $rc = 0 ]; then
        echo "default" > "$ST/$p/SELECTED"; decide "$p/default.result" "SELECTED (configured design)"
    else
        say "$p: the configured design failed its build or card self-check - see $ST/$p and the project README section 8"
    fi
}

ablation() {
    local p=$1 bdir=$2 xname=$3; shift 3
    local res="$ST/$p/ablation_${bdir}.result"
    [ -f "$res" ] && return 0
    if build "$p" "$bdir" "$@"; then
        { echo "built=1"; python3 "$SUITE_DIR/common/tune_eval.py" reports "$V2/$p/$bdir"; } > "$res.tmp"
        mv "$res.tmp" "$res"; say "$p: ablation $bdir built"
    else
        say "$p: ablation $bdir failed (optional)"
    fi
}

# ---------------------------------------------------------------------------
say "tune_v2.sh started (card tests: $CARD)"
if [ ! -f "$PF2_PORTFOLIO" ] && selected 04_Portfolio; then
    make -C "$V2/04_Portfolio" cpu >> "$ST/pf_gen.log" 2>&1
    mkdir -p "$(dirname "$PF2_PORTFOLIO")"
    "$V2/04_Portfolio/build/pf_gen" --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" >> "$ST/pf_gen.log" 2>&1
fi
selected 01 && check_fixed 01_AES aes.xclbin aes_fpga
selected 02 && check_fixed 02_Convolution conv3.xclbin conv_fpga
selected 03 && tune_lanes 03_MC_Heston mc_heston.xclbin mc_fpga "$TUNE_MC_LANES"
selected 04 && tune_lanes 04_Portfolio pf.xclbin pf_fpga "$TUNE_PF_LANES"
selected 05 && check_fixed 05_LiveStream_Single ls_single.xclbin ls_fpga
selected 06 && check_fixed 06_LiveStream_Multi ls_multi.xclbin lm_fpga
if [ $ABL = 1 ]; then
    selected 05 && ablation 05_LiveStream_Single build_4cu ls_single_4cu.xclbin NCU=4
    selected 06 && ablation 06_LiveStream_Multi build_2cu ls_multi_2cu.xclbin NCU=2
fi
summary > /dev/null
say "tune_v2.sh finished - summary: $ST/SUMMARY.md"
missing=0
for p in 01_AES 02_Convolution 03_MC_Heston 04_Portfolio 05_LiveStream_Single 06_LiveStream_Multi; do
    selected "$p" || continue
    [ -f "$ST/$p/SELECTED" ] || { echo "  NOT READY: $p"; missing=1; }
done
exit $missing
