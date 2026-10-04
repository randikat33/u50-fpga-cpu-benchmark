#!/usr/bin/env bash
# =============================================================================
#  round7_sweep.sh - review round 7:
#   (1) near-break-even test of the time-energy model: the MC Asian kernel rebuilt with L = 16 lanes
#       whose number of ACTIVE lanes is chosen at run time (v2_projects/09_MC_Asian_Sweep). The card's
#       rate grows with the active lanes, the CPU's does not, so the verdicts cross over inside the sweep.
#       The prediction is written and hashed BEFORE the sweep design ever runs on the card.
#   (2) CPU-native portfolio baseline (04_Portfolio/cpu/pf_native.cpp) against the paper's portfolio card
#       and the paper's algorithm-matched CPU code (no prediction involved).
#
#  Copy this file and the round7/ folder into the suite folder, v2_projects/09_MC_Asian_Sweep into
#  v2_projects/, and 04_Portfolio/cpu/pf_native.cpp + 04_Portfolio/scripts/pf_native_check.py. Then
#  (normally through r7_queue.sh, which runs all of this unattended):
#    ./round7_sweep.sh check      #  5 min  builds, C-sim with active lanes, CPU checks
#    ./round7_sweep.sh pfnative   #  2 h    portfolio: card vs algorithm-matched CPU vs CPU-native CPU
#    ./round7_sweep.sh build      #  8-20 h sweep xclbin (tries 300 MHz, then 250 MHz if timing fails)
#    ./round7_sweep.sh design     #  1 min  clock, CUs, II, P_viv from the build reports
#    ./round7_sweep.sh cpu        # 20 min  CPU-only runs used by the prediction
#    ./round7_sweep.sh predict    #  1 min  predictions.md/.json + SHA-256
#        -> e-mail predictions.md and the SHA-256 lines to your supervisor NOW, then: ./r7_queue.sh published
#    ./round7_sweep.sh measure    #  2 h    sweep on the card + fresh CPU runs, interleaved
#    ./round7_sweep.sh pack       #  1 min  results_round7_<date>.tar.gz to upload
#  Options: REPS=5  SWEEP_LANES_SET=1,2,...,16 (all active-lane counts)
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_round7"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR" "$RESULTS_DIR/logs" "$RESULTS_DIR/predictions" "$RESULTS_DIR/work"
exec > >(tee -a "$RESULTS_DIR/round7.log") 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
[ -f "$STATE_DIR/idle_temps.txt" ] || cp "$SUITE_DIR/results/.state/idle_temps.txt" "$STATE_DIR/" 2>/dev/null

R7="$SUITE_DIR/round7"
SWP="$V2_ROOT/09_MC_Asian_Sweep"
PFD="$V2_ROOT/04_Portfolio"
[ -f "$R7/build.conf" ] && source "$R7/build.conf"
SWEEP_LANES=${SWEEP_LANES:-16}
SWEEP_FREQS=${SWEEP_FREQS:-"300000000 250000000"}
SWEEP_STRATEGY=${SWEEP_STRATEGY:-Congestion_SpreadLogic_high}
SWEEP_SLR=${SWEEP_SLR:-1}
SWEEP_LANES_SET=${SWEEP_LANES_SET:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}
REPS=${REPS:-5}
SWP_XCLBIN="$SWP/build/mc_asian.xclbin"
SWP_CPU="$SWP/build/asian_cpu"; SWP_FPGA="$SWP/build/asian_fpga"
PF_NAT="$PFD/build/pf_native"
INI="$COMMON/xrt_timing.ini"
ASN_PATHS=${ASN_PATHS:-268435456}; ASN_STEPS=${ASN_STEPS:-128}
declare -A ASN_CPU_RUNS=( [s0_c24]=${ASN_RUNS_S0:-1} [s01_t96]=${ASN_RUNS_S01:-2} )
SWEEP_PATHS_PER_LANE=${SWEEP_PATHS_PER_LANE:-33554432}     # card paths = active lanes x 2^25 (~10 s per run)
SWEEP_RUNS=${SWEEP_RUNS:-2}
SWEEP_VERIFY_PATHS=${SWEEP_VERIFY_PATHS:-2000000}                  # card == CPU check before the sweep

NODE_SYS=${NODE_SYS:-/sys/devices/system/node}
N0=$(cat "$NODE_SYS/node0/cpulist"); N1=$(cat "$NODE_SYS/node1/cpulist")
P0=${N0%%,*}
count() { python3 -c "
import sys
n=0
for part in sys.argv[1].split(','):
    a,_,b=part.partition('-'); n+= (int(b)-int(a)+1) if b else 1
print(n)" "$1"; }
declare -A CORES=( [s0_c24]="$P0" [s01_t96]="$N0,$N1" )
declare -A NODE=(  [s0_c24]=0 [s01_t96]=0,1 )
declare -A PLACES=([s0_c24]=cores [s01_t96]=threads)
T() { count "${CORES[$1]}"; }

cpu_run() {   # cpu_run <group> <workload> <config> <rep> <cmd...>
    local g=$1 w=$2 c=$3 rep=$4; shift 4
    export OMP_NUM_THREADS; OMP_NUM_THREADS=$(T "$c")
    export OMP_PLACES=${PLACES[$c]} OMP_PROC_BIND=close OMP_DYNAMIC=false
    measure_v2 "$RAW_DIR/$g/$w/$c/$rep" --cores "${CORES[$c]}" --node "${NODE[$c]}" --timeout 3600 \
        --label "$g $w $c $rep" -- "$@"
}
fpga_run() {  # fpga_run <group> <workload> <rep> <xclbin> <cmd...>
    local g=$1 w=$2 rep=$3 x=$4; shift 4
    export OMP_NUM_THREADS=24 OMP_PLACES=cores OMP_PROC_BIND=close OMP_DYNAMIC=false
    measure_v2 "$RAW_DIR/$g/$w/fpga/$rep" --xclbin "$x" --xrt-ini "$INI" --timeout 3600 --label "$g $w fpga $rep" -- "$@"
}
interleave() {
    local f
    for f in "$RESULTS_DIR/cpu_tuning.txt" "$SUITE_DIR/results_round6/cpu_tuning.txt"; do
        [ -f "$f" ] && grep -q "^asian_interleave=" "$f" && { grep "^asian_interleave=" "$f" | cut -d= -f2; return; }
    done
    echo 4
}
asn_cpu_cmd() { echo "$SWP_CPU --paths $ASN_PATHS --steps $ASN_STEPS --runs ${ASN_CPU_RUNS[$1]} --warmup 1 --interleave $(interleave) --verify"; }
lanes_in_order() {   # lanes_in_order <rep number>: the sweep rotated per repetition (drift is not tied to one l)
    python3 -c "
import sys
l=[int(x) for x in sys.argv[1].split(',')]; k=int(sys.argv[2])-1
k%=len(l); print(' '.join(map(str,l[k:]+l[:k])))" "$SWEEP_LANES_SET" "$1"
}
build_pf_native() {
    mkdir -p "$PFD/build"
    g++ -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -ffp-contract=fast -Wall \
        -DPF_LANES=8 -DPF_LOG_IL=7 -DPF_IL_PRAGMA=128 -I"$PFD/common" -I"$PFD/kernel" -I"$PFD/cpu" -I"$V2_ROOT/common" \
        "$PFD/cpu/pf_native.cpp" -o "$PF_NAT" > "$RESULTS_DIR/logs/pf_native_build.log" 2>&1
}

# ============================================================================================
step_check() {
    section "check: files, builds, C-sim with active lanes, CPU checks"
    local ok=1 f
    for f in "$SWP/Makefile" "$SWP/kernel/mc_asian.cpp" "$PFD/cpu/pf_native.cpp" "$PFD/scripts/pf_native_check.py" \
             "$R7/predict_round7.py" "$R7/design_facts7.py" "$R7/design_facts.py" "$R7/runlib.py" \
             "$R7/model_constants.json" "$COMMON/run_measured.py"; do
        [ -f "$f" ] && echo "  [ok]      $f" || { echo "  [MISSING] $f"; ok=0; }
    done
    [ $ok = 1 ] || { err "copy round7/ and v2_projects/09_MC_Asian_Sweep and the 04_Portfolio files first"; return 1; }
    for v in PF2_XCLBIN PF2_FPGA_BIN PF2_CPU_BIN PF2_PORTFOLIO PF2_MARKET; do
        [ -e "${!v:-/nonexistent}" ] && echo "  [ok]      $v=${!v}" || { err "$v not found (${!v:-unset})"; ok=0; }
    done
    make -C "$SWP" cpu host LANES="$SWEEP_LANES" > "$RESULTS_DIR/logs/09_cpu_host.log" 2>&1 \
        && echo "  [ok]      09_MC_Asian_Sweep: cpu + host built" || { err "09_MC_Asian_Sweep build failed (logs/09_cpu_host.log)"; ok=0; }
    build_pf_native && echo "  [ok]      pf_native built" || { err "pf_native build failed (logs/pf_native_build.log)"; ok=0; }
    [ $ok = 1 ] || return 1
    "$PF_NAT" --threads 1 --portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --help >/dev/null 2>&1
    local L="$RESULTS_DIR/logs/check_verify.log"; : > "$L"
    # MC Asian CPU: interleave 1/2/4 and scalar identical (same code as round 6)
    local h=() nb
    for nb in 1 2 4; do
        "$SWP_CPU" --paths 200000 --steps 64 --interleave $nb --verify >> "$L" 2>&1 || ok=0
        h+=("$(grep 'mom_hash=' "$L" | tail -1 | cut -d= -f2)")
    done
    [ "$(printf '%s\n' "${h[@]}" | sort -u | wc -l)" = 1 ] && echo "  [ok]      MC Asian CPU results identical: ${h[0]}" \
        || { err "MC Asian CPU results differ: ${h[*]}"; ok=0; }
    # portfolio CPU-native: statistically equal to the algorithm-matched engine on a 20,000-trade portfolio
    local G="$PFD/build/pf_gen" W="$RESULTS_DIR/work"
    [ -x "$G" ] || make -C "$PFD" build/pf_gen > /dev/null 2>&1
    if [ -x "$G" ]; then
        "$G" --trades 20000 --underlyings 1024 --portfolio "$W/pf20k.bin" --market "$W/mk20k.bin" > /dev/null 2>&1
        local A=(--portfolio "$W/pf20k.bin" --market "$W/mk20k.bin" --paths2 16384 --topk 400)
        "$PF2_CPU_BIN" --impl avx512 "${A[@]}" --out "$W/r20k_bx.bin" >> "$L" 2>&1 && "$PF_NAT" "${A[@]}" --out "$W/r20k_nat.bin" >> "$L" 2>&1 \
            && python3 "$PFD/scripts/pf_native_check.py" "$W/pf20k.bin" "$W/r20k_bx.bin" "$W/r20k_nat.bin" | tee -a "$L" | sed 's/^/  /' \
            && grep -q "pf_native_check: PASS" "$L" || { err "pf_native check failed (see $L)"; ok=0; }
        grep -E "^RESULT (msteps_per_s|impl)=" "$L" | tail -4 | sed 's/^/  /'
    else
        warn "pf_gen not found: the small pf_native check is skipped (pfnative checks on the full portfolio)"
    fi
    # kernel C-simulation with active lanes (needs the Vitis HLS headers)
    local HI=""
    [ -f "$VITIS_SETTINGS" ] && HI=$(bash -c "source '$VITIS_SETTINGS' >/dev/null 2>&1; echo \$XILINX_HLS")
    if [ -n "$HI" ] && [ -f "$HI/include/ap_int.h" ]; then
        g++ -std=c++17 -O2 -w -march=native -ffp-contract=off -I"$HI/include" -I"$V2_ROOT/common" \
            -I"$V2_ROOT/03_MC_Heston/common" -I"$V2_ROOT/03_MC_Heston/cpu" -I"$SWP/common" -I"$SWP/cpu" -I"$SWP/kernel" \
            -DAS_LANES="$SWEEP_LANES" "$SWP/test/tb_kernel.cpp" "$SWP/kernel/mc_asian.cpp" -o "$RESULTS_DIR/logs/tb_sweep" \
            && { "$RESULTS_DIR/logs/tb_sweep" > "$RESULTS_DIR/logs/tb_sweep.log" 2>&1; grep -E "^tb_kernel:" "$RESULTS_DIR/logs/tb_sweep.log" | sed 's/^/  /'; \
                 grep -q "ALL PASS" "$RESULTS_DIR/logs/tb_sweep.log"; } || { warn "sweep kernel C-sim failed (logs/tb_sweep.log)"; ok=0; }
    else
        warn "Vitis HLS headers not found: kernel C-sim skipped (it passed in the sandbox)"
    fi
    [ $ok = 1 ] && info "check passed" || { err "check failed (see $L)"; return 1; }
}

# ============================================================================================
step_pfnative() {
    section "portfolio: paper's card vs algorithm-matched CPU vs CPU-native CPU ($REPS reps)"
    build_pf_native || { err "pf_native build failed"; return 1; }
    "$PF_NAT" --help 2>/dev/null | grep -q "Box-Muller" || { err "pf_native is not the round-7 version"; return 1; }
    local W="$RESULTS_DIR/work" A=(--portfolio "$PF2_PORTFOLIO" --market "$PF2_MARKET" --paths2 131072)
    if [ ! -f "$STATE_DIR/pfnative_checked" ]; then
        info "full-portfolio check: CPU-native prices agree with the algorithm-matched engine within 3 SE"
        OMP_NUM_THREADS=24 taskset -c "$P0" "$PF2_CPU_BIN" --impl avx512 --threads 24 "${A[@]}" --out "$W/pf_bx.bin" \
            > "$RESULTS_DIR/logs/pf_native_full_bx.log" 2>&1
        OMP_NUM_THREADS=24 taskset -c "$P0" "$PF_NAT" --threads 24 "${A[@]}" --out "$W/pf_nat.bin" \
            > "$RESULTS_DIR/logs/pf_native_full_nat.log" 2>&1
        python3 "$PFD/scripts/pf_native_check.py" "$PF2_PORTFOLIO" "$W/pf_bx.bin" "$W/pf_nat.bin" \
            | tee "$RESULTS_DIR/logs/pf_native_check.log" | sed 's/^/  /'
        grep -q "pf_native_check: PASS" "$RESULTS_DIR/logs/pf_native_check.log" \
            || { err "CPU-native portfolio prices do not agree (logs/pf_native_check.log)"; return 1; }
        touch "$STATE_DIR/pfnative_checked"
    fi
    [ -f "$PF2_XCLBIN" ] || { err "paper portfolio xclbin not found: $PF2_XCLBIN"; return 1; }
    local rep c
    for rep in $(seq -f "rep%g" 1 "$REPS"); do
        fpga_run pfnative pf_131072 "$rep" "$PF2_XCLBIN" "$PF2_FPGA_BIN" --xclbin "$PF2_XCLBIN" "${A[@]}" --out "$W/pf_fpga.bin"
        for c in s0_c24 s01_t96; do
            cpu_run pfnative pf_131072 "$c" "$rep" "$PF2_CPU_BIN" --impl avx512 --threads "$(T "$c")" "${A[@]}" --out "$W/pf_$c.bin"
            cpu_run pfnative pf_131072_native "$c" "$rep" "$PF_NAT" --threads "$(T "$c")" "${A[@]}" --out "$W/pfn_$c.bin"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

# ============================================================================================
step_build() {
    section "build: lane-sweep xclbin ($SWEEP_LANES lanes; clocks tried: $SWEEP_FREQS; resumable)"
    [ -f "$VITIS_SETTINGS" ] || { err "Vitis not found ($VITIS_SETTINGS)"; return 1; }
    # shellcheck disable=SC1090
    source "$VITIS_SETTINGS" >/dev/null 2>&1
    if [ -f "$SWP_XCLBIN" ]; then info "$SWP_XCLBIN exists ($(cat "$SWP/build/.buildcfg" 2>/dev/null))"; else
        local fr sig args start built=0
        mkdir -p "$SWP/build"
        for fr in $SWEEP_FREQS; do
            [ -f "$SWP/build/.failed_$fr" ] && { info "skipping $((fr / 1000000)) MHz (missed timing before)"; continue; }
            sig="lanes=$SWEEP_LANES freq=$fr strategy=$SWEEP_STRATEGY slr=${SWEEP_SLR:-any}"
            args=(LANES="$SWEEP_LANES" FREQ_HZ="$fr" STRATEGY="$SWEEP_STRATEGY")
            [ -n "$SWEEP_SLR" ] && args+=(LINK_CFG="$SWP/cfg/link_slr$SWEEP_SLR.cfg")
            if [ "$(cat "$SWP/build/.buildcfg" 2>/dev/null)" != "$sig" ]; then
                info "new build settings ($sig): removing previous HLS/link results"
                rm -rf "$SWP/build/hls" "$SWP/build/xo" "$SWP/build/link" "$SWP"/build/partial_*
                echo "$sig" > "$SWP/build/.buildcfg"
            fi
            start=$(date +%s)
            info "building $sig (log results_round7/logs/09_xclbin.log)"
            if make -C "$SWP" xclbin "${args[@]}" > "$RESULTS_DIR/logs/09_xclbin.log" 2>&1; then
                info "xclbin in $(( ($(date +%s) - start) / 60 )) min at $((fr / 1000000)) MHz"; built=1; break
            fi
            if grep -q "did not meet timing" "$RESULTS_DIR/logs/09_xclbin.log"; then
                warn "missed timing at $((fr / 1000000)) MHz; trying the next clock"
                cp "$RESULTS_DIR/logs/09_xclbin.log" "$RESULTS_DIR/logs/09_xclbin_${fr}_failed.log"
                touch "$SWP/build/.failed_$fr"
                continue
            fi
            err "xclbin failed (not a timing failure):"; grep -m8 -E "^ERROR|CRITICAL" "$RESULTS_DIR/logs/09_xclbin.log"; return 1
        done
        [ $built = 1 ] || { err "no clock in '$SWEEP_FREQS' met timing; send results_round7/logs/09_xclbin*.log"; return 1; }
    fi
    local sig; sig=$(cat "$SWP/build/.buildcfg")
    local fr=${sig#*freq=}; fr=${fr%% *}
    local args=(LANES="$SWEEP_LANES" FREQ_HZ="$fr" STRATEGY="$SWEEP_STRATEGY")
    [ -n "$SWEEP_SLR" ] && args+=(LINK_CFG="$SWP/cfg/link_slr$SWEEP_SLR.cfg")
    make -C "$SWP" fpga_reports "${args[@]}" > "$RESULTS_DIR/logs/09_reports.log" 2>&1
    xclbinutil --info --input "$SWP_XCLBIN" > "$SWP/build/reports/xclbin_info.txt" 2>&1
}

step_design() {
    section "design facts (no card): clock, CUs, lanes, II, pipeline depth, Vivado power"
    python3 "$R7/design_facts7.py" --sweep "$SWP" --lanes "$SWEEP_LANES" --out "$RESULTS_DIR/design.json" \
        || { err "design facts failed"; return 1; }
    mkdir -p "$RESULTS_DIR/design_reports"
    cp -r "$SWP/build/reports" "$RESULTS_DIR/design_reports/09_MC_Asian_Sweep" 2>/dev/null
    cp "$SWP"/build/hls/*/hls.log "$RESULTS_DIR/design_reports/09_MC_Asian_Sweep/" 2>/dev/null
    cp "$SWP/build/.buildcfg" "$RESULTS_DIR/design_reports/buildcfg.txt" 2>/dev/null
}

step_cpu() {
    section "CPU-only runs used by the prediction ($REPS reps, 1 socket and whole server; interleave $(interleave))"
    local rep c
    echo "asian_interleave=$(interleave)" > "$RESULTS_DIR/cpu_tuning.txt"
    for rep in $(seq -f "rep%g" 1 "$REPS"); do
        for c in s0_c24 s01_t96; do
            # shellcheck disable=SC2046
            cpu_run pred asian "$c" "$rep" $(asn_cpu_cmd "$c")
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

step_predict() {
    section "PREDICTION (before the sweep design ever runs on the card)"
    [ -f "$RESULTS_DIR/design.json" ] || { err "run design first"; return 1; }
    if ls "$RAW_DIR"/meas/*/fpga/*/DONE >/dev/null 2>&1 || [ -f "$STATE_DIR/card_touched" ]; then
        err "the sweep design already ran on the card: a prediction now would not be blind"; return 1
    fi
    local P="$RESULTS_DIR/predictions"
    if [ -f "$P/predictions.json" ]; then
        warn "predictions already exist ($P/predictions.json); not overwritten"; cat "$P/predictions.md"; return 0
    fi
    python3 "$R7/predict_round7.py" --raw "$RAW_DIR" --design "$RESULTS_DIR/design.json" \
        --lanes "$SWEEP_LANES_SET" --out "$P" || return 1
    cp "$RESULTS_DIR/design.json" "$RESULTS_DIR/cpu_tuning.txt" "$P/"
    echo
    info "NEXT: e-mail $P/predictions.md and these two lines to your supervisor BEFORE 'measure':"
    cat "$P"/predictions.json.sha256 "$P"/predictions.md.sha256 | sed 's/^/        /'
    info "      then run: ./r7_queue.sh published"
    touch "$STATE_DIR/predicted"
}

step_measure() {
    section "MEASURE: active-lane sweep on the card + fresh CPU runs, interleaved ($REPS reps)"
    local P="$RESULTS_DIR/predictions"
    [ -f "$P/predictions.json" ] || { err "no prediction: run predict first"; return 1; }
    (cd "$P" && sha256sum -c predictions.json.sha256 predictions.md.sha256) \
        || { err "the prediction files changed after they were made"; return 1; }
    date -Is > "$RESULTS_DIR/measure_started.txt"
    touch "$STATE_DIR/card_touched"
    info "prediction $(cut -c1-16 "$P/predictions.json.sha256") ... is fixed; measuring"
    local l ok=1
    "$SWP_CPU" --paths $SWEEP_VERIFY_PATHS --steps $ASN_STEPS > "$RESULTS_DIR/logs/sweep_cpu_hash.log" 2>&1
    local hc; hc=$(grep mom_hash= "$RESULTS_DIR/logs/sweep_cpu_hash.log" | cut -d= -f2)
    for l in 1 3 7 "$SWEEP_LANES"; do
        "$SWP_FPGA" --xclbin "$SWP_XCLBIN" --paths $SWEEP_VERIFY_PATHS --steps $ASN_STEPS --active-lanes "$l" --verify \
            > "$RESULTS_DIR/logs/sweep_card_verify_l$l.log" 2>&1
        if [ "$(grep mom_hash= "$RESULTS_DIR/logs/sweep_card_verify_l$l.log" | cut -d= -f2)" = "$hc" ] && \
           grep -q "^RESULT ok=1" "$RESULTS_DIR/logs/sweep_card_verify_l$l.log"; then
            info "sweep design, $l active lanes: card == CPU (bit-identical), $(grep -m1 'msteps_per_s_kernel=' "$RESULTS_DIR/logs/sweep_card_verify_l$l.log" | cut -d= -f2) M path-steps/s"
        else err "sweep design with $l active lanes: card result differs from the CPU (logs/sweep_card_verify_l$l.log)"; ok=0; fi
    done
    [ $ok = 1 ] || return 1
    local rep r c
    for r in $(seq 1 "$REPS"); do
        rep="rep$r"
        for l in $(lanes_in_order "$r"); do
            fpga_run meas "asian_l$l" "$rep" "$SWP_XCLBIN" "$SWP_FPGA" --xclbin "$SWP_XCLBIN" \
                --paths $((l * SWEEP_PATHS_PER_LANE)) --steps $ASN_STEPS --runs $SWEEP_RUNS --warmup 1 --active-lanes "$l"
        done
        for c in s0_c24 s01_t96; do
            # shellcheck disable=SC2046
            cpu_run meas asian "$c" "$rep" $(asn_cpu_cmd "$c")
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    date -Is > "$RESULTS_DIR/measure_finished.txt"
}

step_pack() {
    local f="$SUITE_DIR/results_round7_$(date +%Y%m%d_%H%M).tar.gz"
    tar czf "$f" -C "$SUITE_DIR" --exclude='results_round7/work' results_round7
    info "packed -> $f  ($(du -h "$f" | cut -f1)). Please upload this file."
}

case "${1:-}" in
    check) step_check ;;
    pfnative) step_pfnative ;;
    build) step_build ;;
    design) step_design ;;
    cpu) step_cpu ;;
    predict) step_predict ;;
    measure) step_measure ;;
    pack) step_pack ;;
    *) echo "usage: $0 check|pfnative|build|design|cpu|predict|measure|pack"; exit 1 ;;
esac
rc=$?
[ $rc = 0 ] && touch "$RESULTS_DIR/.state/DONE_$1" && info "step '$1' finished" || err "step '$1' stopped (rc=$rc)"
exit $rc
