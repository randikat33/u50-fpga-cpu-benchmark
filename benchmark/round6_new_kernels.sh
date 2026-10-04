#!/usr/bin/env bash
# =============================================================================
#  round6_new_kernels.sh - out-of-sample test of the time-energy model with two NEW kernels
#  (07_FIR: int16 FIR filter, streaming; 08_MC_Asian: Monte Carlo Asian option, polling).
#
#  The model predicts the card's speedup S and energy advantage A from the routed design, one
#  PCIe number and CPU-only runs. Here the prediction is written and committed BEFORE the new
#  kernels ever run on the card, and only then are they measured.
#
#  Copy this file and the round6/ folder into the suite folder (the one with config.sh, common/,
#  round5_measurements.sh), and the two project folders into v2_projects/. Then:
#
#    taskset -c 95 ./round6_new_kernels.sh check      #  5 min  tools, CPU builds, C-sim, verify on this CPU
#    taskset -c 95 ./round6_new_kernels.sh build      # 12-20 h two xclbins (HLS + Vivado), resumable
#    taskset -c 95 ./round6_new_kernels.sh design     #  1 min  clock, CUs, II, P_viv from the build reports
#    sudo -v
#    taskset -c 95 ./round6_new_kernels.sh cputune    # 15 min  pick the faster CPU variant (CPU only)
#    taskset -c 95 ./round6_new_kernels.sh cpu        # 40 min  CPU-only runs used by the prediction
#    taskset -c 95 ./round6_new_kernels.sh predict    #  1 min  predictions.md/.json + SHA-256 (+ git commit)
#        -> push/send the predictions NOW (see the guide), before the next step
#    taskset -c 95 ./round6_new_kernels.sh measure    # 90 min  card runs + fresh CPU runs, interleaved
#    taskset -c 95 ./round6_new_kernels.sh mcnative   # 30 min  paper's MC Heston card vs a CPU-native MC baseline
#    taskset -c 95 ./round6_new_kernels.sh pack       #  1 min  results_round6_<date>.tar.gz to upload
#
#    all      = check build design cputune cpu predict   (stops after the prediction)
#  The card is not used by check, build, design, cputune, cpu or predict.
#  Progress: tail -f results_round6/round6.log      Options: REPS=5  ASIAN_LANES=32
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; export SUITE_DIR
export RESULTS_DIR="$SUITE_DIR/results_round6"
export STATE_DIR="$RESULTS_DIR/.state"
source "$SUITE_DIR/common/lib.sh"
mkdir -p "$STATE_DIR" "$RESULTS_DIR/logs" "$RESULTS_DIR/predictions" "$RESULTS_DIR/wall"
exec > >(tee -a "$RESULTS_DIR/round6.log") 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
[ -f "$STATE_DIR/idle_temps.txt" ] || cp "$SUITE_DIR/results/.state/idle_temps.txt" "$STATE_DIR/" 2>/dev/null

R6="$SUITE_DIR/round6"
FIR="$V2_ROOT/07_FIR"
ASN="$V2_ROOT/08_MC_Asian"
# MC Asian build settings; round6/build.conf (optional) overrides them, e.g. after a failed build:
#   ASIAN_LANES=24  ASIAN_FREQ_HZ=250000000  ASIAN_STRATEGY=Congestion_SpreadLogic_high  ASIAN_SLR=1
[ -f "$SUITE_DIR/round6/build.conf" ] && source "$SUITE_DIR/round6/build.conf"
ASIAN_LANES=${ASIAN_LANES:-32}
ASIAN_FREQ_HZ=${ASIAN_FREQ_HZ:-300000000}
ASIAN_STRATEGY=${ASIAN_STRATEGY:-Performance_Explore}
ASIAN_SLR=${ASIAN_SLR:-}
REPS=${REPS:-5}
FIR_XCLBIN="$FIR/build/fir.xclbin"
ASN_XCLBIN="$ASN/build/mc_asian.xclbin"
FIR_CPU="$FIR/build/fir_cpu";   FIR_FPGA="$FIR/build/fir_fpga"
ASN_CPU="$ASN/build/asian_cpu"; ASN_FPGA="$ASN/build/asian_fpga"
INI="$COMMON/xrt_timing.ini"

# workload sizes (windows of several seconds on each side)
FIR_CPU_MB=${FIR_CPU_MB:-2048}                    # 1 G samples in DRAM, filtered --repeat times
declare -A FIR_CPU_REPEAT=( [s0_c24]=${FIR_REPEAT_S0:-64} [s01_t96]=${FIR_REPEAT_S01:-128} )
FIR_FPGA_MB=${FIR_FPGA_MB:-65536}                 # 32 G samples streamed through the card (no-io, as AES)
ASN_PATHS=${ASN_PATHS:-268435456}; ASN_STEPS=${ASN_STEPS:-128}   # 2^28 paths x 128 monitoring dates
ASN_TUNE_PATHS=${ASN_TUNE_PATHS:-67108864}
declare -A ASN_CPU_RUNS=( [s0_c24]=${ASN_RUNS_S0:-1} [s01_t96]=${ASN_RUNS_S01:-2} )
ASN_FPGA_RUNS=${ASN_FPGA_RUNS:-4}
CHECK_FIR_MB=${CHECK_FIR_MB:-4096}; CHECK_ASN_PATHS=${CHECK_ASN_PATHS:-2000000}

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

HAVE_IPMI=0
sudo -n true 2>/dev/null && command -v ipmitool >/dev/null 2>&1 && \
    sudo -n ipmitool dcmi power reading 2>/dev/null | grep -qi "Instantaneous" && HAVE_IPMI=1
WALL_PID=""
wall_start() {   # wall power ~1 Hz while the step runs (optional)
    [ $HAVE_IPMI = 1 ] || return 0
    local f=$1
    [ -f "$f" ] || echo "t_unix,wall_w" > "$f"
    ( while true; do
        w=$(sudo -n ipmitool dcmi power reading 2>/dev/null | awk '/Instantaneous/{print $(NF-1)}')
        [ -n "$w" ] && echo "$(date +%s.%N),$w" >> "$f"; sleep 0.9
      done ) &
    WALL_PID=$!
}
wall_stop() { [ -n "$WALL_PID" ] && kill "$WALL_PID" 2>/dev/null; WALL_PID=""; }
trap wall_stop EXIT

# cpu_run <group> <workload> <config> <rep> <cmd...>
cpu_run() {
    local g=$1 w=$2 c=$3 rep=$4; shift 4
    export OMP_NUM_THREADS; OMP_NUM_THREADS=$(T "$c")
    export OMP_PLACES=${PLACES[$c]} OMP_PROC_BIND=close OMP_DYNAMIC=false
    measure_v2 "$RAW_DIR/$g/$w/$c/$rep" --cores "${CORES[$c]}" --node "${NODE[$c]}" --timeout 3600 \
        --label "$g $w $c $rep" -- "$@"
}
# fpga_run <group> <workload> <rep> <xclbin> <cmd...>
fpga_run() {
    local g=$1 w=$2 rep=$3 x=$4; shift 4
    export OMP_NUM_THREADS=24 OMP_PLACES=cores OMP_PROC_BIND=close OMP_DYNAMIC=false
    measure_v2 "$RAW_DIR/$g/$w/fpga/$rep" --xclbin "$x" --xrt-ini "$INI" --timeout 3600 --label "$g $w fpga $rep" -- "$@"
}
tuned() { grep -s "^$1=" "$RESULTS_DIR/cpu_tuning.txt" | cut -d= -f2; }
fir_cpu_cmd() {   # fir_cpu_cmd <config>
    echo "$FIR_CPU --size-mb $FIR_CPU_MB --repeat ${FIR_CPU_REPEAT[$1]} --isa $(tuned fir_isa)"
}
asn_cpu_cmd() {
    echo "$ASN_CPU --paths $ASN_PATHS --steps $ASN_STEPS --runs ${ASN_CPU_RUNS[$1]} --warmup 1 --interleave $(tuned asian_interleave) --verify"
}

# ============================================================================================
step_check() {
    section "check: files, tools, CPU builds and bit-exactness on this CPU"
    local ok=1 f
    for f in "$FIR/Makefile" "$ASN/Makefile" "$V2_ROOT/03_MC_Heston/common/mc_model.hpp" "$R6/predict_round6.py" \
             "$R6/design_facts.py" "$R6/model_constants.json" "$R6/runlib.py" "$COMMON/run_measured.py"; do
        [ -f "$f" ] && echo "  [ok]      $f" || { echo "  [MISSING] $f"; ok=0; }
    done
    [ $ok = 1 ] || { err "copy the round6/ folder and v2_projects/07_FIR, 08_MC_Asian first (see the guide)"; return 1; }
    [ -f "$VITIS_SETTINGS" ] && echo "  [ok]      Vitis: $VITIS_SETTINGS" || warn "Vitis settings not found: $VITIS_SETTINGS (needed by build)"
    { sudo -n lspci -d 10ee: -vv 2>/dev/null | grep -E "LnkCap:|LnkSta:" | sed 's/^/  U50 PCIe /'; } || true
    # memory configuration (DIMMs per channel decide the CPU's memory bandwidth; reported in the paper)
    if sudo -n true 2>/dev/null; then
        sudo -n dmidecode -t memory 2>/dev/null | grep -E "^\s+(Size|Locator|Speed|Configured Memory Speed|Rank|Part Number):" \
            > "$RESULTS_DIR/memory_dimms.txt" 2>&1
        echo "  DIMMs populated: $(grep -cE 'Size: [0-9]+ [GM]B' "$RESULTS_DIR/memory_dimms.txt") (details: results_round6/memory_dimms.txt)"
    else
        warn "no sudo: DIMM list not recorded (run 'sudo -v' before check to record it)"
    fi
    { lscpu | grep -E "Model name|Socket|Core\(s\)|Thread|NUMA node[01]"; grep -o 'avx512_vnni' /proc/cpuinfo | head -1; } | sed 's/^/  /'
    for p in "$FIR" "$ASN"; do
        make -C "$p" cpu host LANES="$ASIAN_LANES" > "$RESULTS_DIR/logs/$(basename "$p")_cpu_host.log" 2>&1 \
            && echo "  [ok]      $(basename "$p"): cpu + host built" \
            || { err "$(basename "$p"): build failed, see results_round6/logs/"; ok=0; }
    done
    [ $ok = 1 ] || return 1
    local L="$RESULTS_DIR/logs/check_verify.log"; : > "$L"
    info "bit-exactness on this CPU (scalar == AVX-512 VNNI == AVX-512 BW; thread-count invariance)"
    local h=() isa th
    for isa in vnni bw; do for th in 1 24; do
        OMP_NUM_THREADS=$th "$FIR_CPU" --size-mb 64 --repeat 1 --isa "$isa" --threads "$th" --verify >> "$L" 2>&1 || ok=0
        h+=("$(grep 'out_fnv1a64=' "$L" | tail -1 | cut -d= -f2)")
    done; done
    "$FIR_CPU" --size-mb 64 --repeat 1 --impl scalar >> "$L" 2>&1; h+=("$(grep 'out_fnv1a64=' "$L" | tail -1 | cut -d= -f2)")
    [ "$(printf '%s\n' "${h[@]}" | sort -u | wc -l)" = 1 ] && echo "  [ok]      FIR outputs identical: ${h[0]}" \
        || { err "FIR outputs differ: ${h[*]}"; ok=0; }
    h=()
    local nb
    for nb in 1 2 4; do
        "$ASN_CPU" --paths 200000 --steps 64 --interleave $nb --verify >> "$L" 2>&1 || ok=0
        h+=("$(grep 'mom_hash=' "$L" | tail -1 | cut -d= -f2)")
    done
    "$ASN_CPU" --paths 200000 --steps 64 --impl scalar >> "$L" 2>&1; h+=("$(grep 'mom_hash=' "$L" | tail -1 | cut -d= -f2)")
    [ "$(printf '%s\n' "${h[@]}" | sort -u | wc -l)" = 1 ] && echo "  [ok]      MC Asian results identical: ${h[0]}" \
        || { err "MC Asian results differ: ${h[*]}"; ok=0; }
    # kernel C-simulation with the Vitis HLS headers (optional: needs Vitis)
    local HI=""
    [ -f "$VITIS_SETTINGS" ] && HI=$(bash -c "source '$VITIS_SETTINGS' >/dev/null 2>&1; echo \$XILINX_HLS")
    if [ -n "$HI" ] && [ -f "$HI/include/ap_int.h" ]; then
        g++ -std=c++17 -O2 -w -march=native -ffp-contract=off -I"$HI/include" -I"$V2_ROOT/common" \
            -I"$V2_ROOT/03_MC_Heston/common" -I"$V2_ROOT/03_MC_Heston/cpu" -I"$ASN/common" -I"$ASN/cpu" -I"$ASN/kernel" \
            -DAS_LANES="$ASIAN_LANES" "$ASN/test/tb_kernel.cpp" "$ASN/kernel/mc_asian.cpp" -o "$RESULTS_DIR/logs/tb_asian" \
            && { "$RESULTS_DIR/logs/tb_asian" > "$RESULTS_DIR/logs/tb_asian.log" 2>&1; grep -E "^tb_kernel:" "$RESULTS_DIR/logs/tb_asian.log" | sed 's/^/  /'; \
                 grep -q "ALL PASS" "$RESULTS_DIR/logs/tb_asian.log"; } || { warn "MC Asian C-sim failed (see logs/tb_asian.log)"; ok=0; }
        g++ -std=c++17 -O2 -w -march=native -I"$HI/include" -I"$V2_ROOT/common" -I"$FIR/common" -I"$FIR/kernel" \
            "$FIR/test/tb_fir.cpp" "$FIR/kernel/fir_v2.cpp" -o "$RESULTS_DIR/logs/tb_fir" \
            && { "$RESULTS_DIR/logs/tb_fir" > "$RESULTS_DIR/logs/tb_fir.log" 2>&1; grep -E "^tb_fir:" "$RESULTS_DIR/logs/tb_fir.log" | sed 's/^/  /'; \
                 grep -q "ALL PASS" "$RESULTS_DIR/logs/tb_fir.log"; } || { warn "FIR C-sim failed (see logs/tb_fir.log)"; ok=0; }
    else
        warn "Vitis HLS headers not found: kernel C-sim skipped (it passed in the sandbox)"
    fi
    [ $ok = 1 ] && info "check passed" || { err "check failed (see $L)"; return 1; }
}

# ============================================================================================
step_build() {
    section "build: two xclbins (HLS + Vivado; 6-10 h each, one after the other; resumable)"
    [ -f "$VITIS_SETTINGS" ] || { err "Vitis not found ($VITIS_SETTINGS)"; return 1; }
    # shellcheck disable=SC1090
    source "$VITIS_SETTINGS" >/dev/null 2>&1
    local p x start args sig
    for p in "$FIR" "$ASN"; do
        if [ "$p" = "$FIR" ]; then
            x=$FIR_XCLBIN; args=()
        else
            x=$ASN_XCLBIN
            args=(LANES="$ASIAN_LANES" FREQ_HZ="$ASIAN_FREQ_HZ" STRATEGY="$ASIAN_STRATEGY")
            [ -n "$ASIAN_SLR" ] && args+=(LINK_CFG="$ASN/cfg/link_slr$ASIAN_SLR.cfg")
            sig="lanes=$ASIAN_LANES freq=$ASIAN_FREQ_HZ strategy=$ASIAN_STRATEGY slr=${ASIAN_SLR:-any}"
            # new settings -> start HLS and Vivado from scratch (make would reuse the old HLS result)
            if [ ! -f "$x" ] && [ "$(cat "$ASN/build/.buildcfg" 2>/dev/null)" != "$sig" ]; then
                info "08_MC_Asian: new build settings ($sig): removing the previous HLS/link results"
                rm -rf "$ASN/build/hls" "$ASN/build/xo" "$ASN/build/link" "$ASN"/build/partial_*
                mkdir -p "$ASN/build"; echo "$sig" > "$ASN/build/.buildcfg"
            fi
        fi
        if [ -f "$x" ]; then info "$(basename "$p"): $x exists"; else
            start=$(date +%s)
            info "$(basename "$p"): building ${args[*]} (log results_round6/logs/$(basename "$p")_xclbin.log)"
            make -C "$p" xclbin "${args[@]}" > "$RESULTS_DIR/logs/$(basename "$p")_xclbin.log" 2>&1 \
                || { err "$(basename "$p") xclbin failed:"; grep -m8 -E "^ERROR|CRITICAL" "$RESULTS_DIR/logs/$(basename "$p")_xclbin.log"
                     grep -A8 "List of system clocks with timing failure" "$RESULTS_DIR/logs/$(basename "$p")_xclbin.log" | head -10
                     return 1; }
            info "$(basename "$p"): xclbin in $(( ($(date +%s) - start) / 60 )) min"
        fi
        make -C "$p" fpga_reports "${args[@]}" > "$RESULTS_DIR/logs/$(basename "$p")_reports.log" 2>&1
        xclbinutil --info --input "$x" > "$p/build/reports/xclbin_info.txt" 2>&1
    done
}

step_design() {
    section "design facts (no card): clock, CUs, lanes, II, pipeline depth, Vivado power"
    python3 "$R6/design_facts.py" --fir "$FIR" --asian "$ASN" --asian-lanes "$ASIAN_LANES" \
        --out "$RESULTS_DIR/design.json" || { err "design facts failed"; return 1; }
    mkdir -p "$RESULTS_DIR/design_reports"
    cp -r "$FIR/build/reports" "$RESULTS_DIR/design_reports/07_FIR" 2>/dev/null
    cp -r "$ASN/build/reports" "$RESULTS_DIR/design_reports/08_MC_Asian" 2>/dev/null
    cp "$FIR"/build/hls/*/hls.log "$RESULTS_DIR/design_reports/07_FIR/" 2>/dev/null
    cp "$ASN"/build/hls/*/hls.log "$RESULTS_DIR/design_reports/08_MC_Asian/" 2>/dev/null
}

# ============================================================================================
step_cputune() {
    section "CPU tuning (CPU only): FIR vpdpwssd (VNNI) vs vpmaddwd (BW); MC Asian 1/2/4 interleaved blocks"
    local rep v
    for rep in rep1 rep2 rep3; do
        for v in vnni bw; do
            cpu_run tune "fir_$v" s0_c24 "$rep" "$FIR_CPU" --size-mb $FIR_CPU_MB --repeat ${FIR_TUNE_REPEAT:-16} --isa $v
        done
        for v in 1 2 4; do
            cpu_run tune "asian_x$v" s0_c24 "$rep" "$ASN_CPU" --paths $ASN_TUNE_PATHS --steps $ASN_STEPS --runs 1 --warmup 1 --interleave $v
        done
    done
    python3 - "$RAW_DIR/tune" "$RESULTS_DIR/cpu_tuning.txt" <<'PY'
import glob, os, re, statistics, sys
raw, out = sys.argv[1:3]
def t(d):
    s = open(os.path.join(d, "stdout.log"), errors="replace").read()
    m = re.search(r"^RESULT t_compute_s=(\S+)", s, re.M)
    return float(m.group(1)) if m and "RESULT ok=1" in s else None
best = {}
for wl in sorted(os.listdir(raw)):
    ts = [x for x in (t(d) for d in glob.glob(os.path.join(raw, wl, "s0_c24", "rep*"))) if x]
    if ts:
        best[wl] = statistics.median(ts)
        print(f"  {wl:10s} median t_compute {best[wl]:.3f} s over {len(ts)} runs")
fir = min((w for w in best if w.startswith("fir_")), key=best.get)
asn = min((w for w in best if w.startswith("asian_")), key=best.get)
open(out, "w").write(f"fir_isa={fir[4:]}\nasian_interleave={asn[7:]}\n")
print(open(out).read())
PY
}

step_cpu() {
    section "CPU-only runs used by the prediction ($REPS reps, 1 socket and whole server)"
    [ -f "$RESULTS_DIR/cpu_tuning.txt" ] || { err "run cputune first"; return 1; }
    local rep c
    for rep in $(seq -f "rep%g" 1 "$REPS"); do
        for c in s0_c24 s01_t96; do
            # shellcheck disable=SC2046
            cpu_run pred fir "$c" "$rep" $(fir_cpu_cmd "$c")
            # shellcheck disable=SC2046
            cpu_run pred asian "$c" "$rep" $(asn_cpu_cmd "$c")
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

step_predict() {
    section "PREDICTION (before any run of the new kernels on the card)"
    [ -f "$RESULTS_DIR/design.json" ] || { err "run design first"; return 1; }
    if ls "$RAW_DIR"/meas/*/fpga/*/DONE >/dev/null 2>&1; then
        err "card runs of the new kernels already exist: a prediction now would not be out-of-sample"; return 1
    fi
    local P="$RESULTS_DIR/predictions"
    if [ -f "$P/predictions.json" ]; then
        warn "predictions already exist ($P/predictions.json); not overwritten"; cat "$P/predictions.md"; return 0
    fi
    python3 "$R6/predict_round6.py" --raw "$RAW_DIR" --design "$RESULTS_DIR/design.json" --out "$P" || return 1
    cp "$RESULTS_DIR/design.json" "$RESULTS_DIR/cpu_tuning.txt" "$P/"
    if git -C "$SUITE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$SUITE_DIR" add -f "$P" && \
        git -C "$SUITE_DIR" commit -q -m "Round 6: model predictions for 07_FIR and 08_MC_Asian, made before any card run" \
            -- "$P" && info "committed: $(git -C "$SUITE_DIR" log -1 --format='%H %cI')"
        git -C "$SUITE_DIR" log -1 --format='%H %cI' > "$P/git_commit.txt" 2>/dev/null
    fi
    echo
    info "NEXT: publish the prediction BEFORE running 'measure' (see ROUND6_RUN_GUIDE.md, step 4):"
    info "      push it to GitHub, or e-mail predictions.md and its SHA-256 to your supervisor."
    touch "$STATE_DIR/predicted"
}

# ============================================================================================
step_measure() {
    section "MEASURE: new kernels on the card + fresh CPU runs, interleaved ($REPS reps)"
    local P="$RESULTS_DIR/predictions"
    [ -f "$P/predictions.json" ] || { err "no prediction: run predict first"; return 1; }
    sha256sum -c "$P/predictions.json.sha256" --status 2>/dev/null || (cd "$P" && sha256sum -c predictions.json.sha256) \
        || { err "predictions.json changed after it was made"; return 1; }
    date -Is > "$RESULTS_DIR/measure_started.txt"
    info "prediction $(cut -c1-16 "$P/predictions.json.sha256") ... is fixed; measuring"
    # correctness on the card first (separate, not part of the measured rows)
    "$FIR_FPGA" --xclbin "$FIR_XCLBIN" --size-mb $CHECK_FIR_MB --verify > "$RESULTS_DIR/logs/fir_card_verify.log" 2>&1 \
        && grep -q "^RESULT ok=1" "$RESULTS_DIR/logs/fir_card_verify.log" && info "FIR on the card: output verified" \
        || { err "FIR card verification failed (see logs/fir_card_verify.log)"; return 1; }
    "$FIR_FPGA" --xclbin "$FIR_XCLBIN" --size-mb 256 --chunk-mb 256 > "$RESULTS_DIR/logs/fir_card_hash.log" 2>&1
    "$FIR_CPU" --size-mb 256 --repeat 1 > "$RESULTS_DIR/logs/fir_cpu_hash.log" 2>&1
    [ "$(grep chunk_out_fnv1a64= "$RESULTS_DIR/logs/fir_card_hash.log" | cut -d= -f2)" = \
      "$(grep out_fnv1a64= "$RESULTS_DIR/logs/fir_cpu_hash.log" | cut -d= -f2)" ] \
        && info "FIR: card output == CPU output (256 MiB block)" || warn "FIR: card/CPU hash differ (see logs)"
    "$ASN_FPGA" --xclbin "$ASN_XCLBIN" --paths $CHECK_ASN_PATHS --steps $ASN_STEPS --verify > "$RESULTS_DIR/logs/asian_card_verify.log" 2>&1
    "$ASN_CPU" --paths $CHECK_ASN_PATHS --steps $ASN_STEPS > "$RESULTS_DIR/logs/asian_cpu_hash.log" 2>&1
    [ "$(grep mom_hash= "$RESULTS_DIR/logs/asian_card_verify.log" | cut -d= -f2)" = \
      "$(grep mom_hash= "$RESULTS_DIR/logs/asian_cpu_hash.log" | cut -d= -f2)" ] && grep -q "^RESULT ok=1" "$RESULTS_DIR/logs/asian_card_verify.log" \
        && info "MC Asian: card result == CPU result (bit-identical)" \
        || { err "MC Asian card verification failed (see logs/asian_card_verify.log)"; return 1; }
    grep -E "^RESULT (lanes|il|msteps_per_s_kernel)=" "$RESULTS_DIR/logs/asian_card_verify.log" | sed 's/^/  /'
    wall_start "$RESULTS_DIR/wall/measure.csv"
    local rep c
    for rep in $(seq -f "rep%g" 1 "$REPS"); do
        fpga_run meas fir "$rep" "$FIR_XCLBIN" "$FIR_FPGA" --xclbin "$FIR_XCLBIN" --size-mb $FIR_FPGA_MB
        for c in s0_c24 s01_t96; do
            # shellcheck disable=SC2046
            cpu_run meas fir "$c" "$rep" $(fir_cpu_cmd "$c")
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
        fpga_run meas asian "$rep" "$ASN_XCLBIN" "$ASN_FPGA" --xclbin "$ASN_XCLBIN" --paths $ASN_PATHS \
            --steps $ASN_STEPS --runs $ASN_FPGA_RUNS --warmup 1 --verify
        for c in s0_c24 s01_t96; do
            # shellcheck disable=SC2046
            cpu_run meas asian "$c" "$rep" $(asn_cpu_cmd "$c")
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
    wall_stop
    date -Is > "$RESULTS_DIR/measure_finished.txt"
}

# ============================================================================================
# mcnative: how conservative is the paper's algorithm-matched MC baseline? The paper's MC Heston card
# (unchanged xclbin) against the bit-exact AVX-512 CPU baseline AND a CPU-native variant (Box-Muller with
# libmvec, fast-math, double moments; prices agree within 3 SE, not bit-identical). Needs no prediction.
step_mcnative() {
    section "MC Heston: card vs bit-exact CPU vs CPU-native CPU ($REPS reps, 67M x 128)"
    local M3="$V2_ROOT/03_MC_Heston" NAT="$V2_ROOT/03_MC_Heston/build/mc_native"
    # built directly (your 03_MC_Heston/Makefile is not replaced); flags as in the Makefile's 'native' target
    mkdir -p "$M3/build"
    g++ -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -ffast-math -Wall -Wno-unknown-pragmas \
        -I"$V2_ROOT/common" -I"$M3/common" -I"$M3/cpu" "$M3/cpu/mc_native.cpp" -o "$NAT" \
        > "$RESULTS_DIR/logs/mc_native_build.log" 2>&1 || { err "mc_native build failed"; return 1; }
    "$NAT" --threads 1 --paths 4096 --steps 4 --runs 1 --warmup 0 2>/dev/null | grep -q "^RESULT isa=avx512_intrinsics" \
        && info "mc_native: explicit AVX-512 build (does not depend on the compiler vectorising libm calls)" \
        || { err "mc_native is the old libmvec version (scalar with gcc 11); copy cpu/mc_native.cpp from round6_fix6.zip"; return 1; }
    # runs of the first (libmvec) version ran scalar on gcc 11 and are not valid: keep them aside, measure again
    if grep -qs "^RESULT isa=avx512_libmvec" "$RAW_DIR"/mcnative/*/*/rep*/stdout.log; then
        local old="$RESULTS_DIR/mcnative_invalid_scalar_$(date +%Y%m%d_%H%M)"
        mv "$RAW_DIR/mcnative" "$old" && warn "old mcnative runs (scalar libm build) moved to $old; all mcnative runs are repeated"
    fi
    bash "$M3/test/native_check.sh" "$NAT" "$MC2_CPU_BIN" > "$RESULTS_DIR/logs/mc_native_check.log" 2>&1 \
        && info "CPU-native prices agree with the bit-exact engine (within 3 SE)" \
        || { err "native_check failed (see logs/mc_native_check.log)"; cat "$RESULTS_DIR/logs/mc_native_check.log"; return 1; }
    [ -f "$MC2_XCLBIN" ] || { err "paper MC xclbin not found: $MC2_XCLBIN"; return 1; }
    local rep c A=(--paths "${MCN_PATHS:-67108864}" --steps "${MCN_STEPS:-128}" --runs "${MCN_RUNS:-3}" --warmup 0)
    for rep in $(seq -f "rep%g" 1 "$REPS"); do
        fpga_run mcnative mc_67108864x128 "$rep" "$MC2_XCLBIN" "$MC2_FPGA_BIN" --xclbin "$MC2_XCLBIN" "${A[@]}"
        for c in s0_c24 s01_t96; do
            cpu_run mcnative mc_67108864x128 "$c" "$rep" "$MC2_CPU_BIN" --impl avx512 --threads "$(T "$c")" "${A[@]}"
            cpu_run mcnative mc_67108864x128_native "$c" "$rep" "$NAT" --threads "$(T "$c")" "${A[@]}"
        done
        cooldown "$COOLDOWN_REP_MIN" "$COOLDOWN_REP_MAX"
    done
}

# ============================================================================================
# membw: achievable DRAM bandwidth (STREAM-style) with this memory configuration, 1 socket and whole server
step_membw() {
    section "memory bandwidth (STREAM-style copy/triad/read; 3 x 2 GiB arrays)"
    local B="$RESULTS_DIR/bin/membw" rep c
    mkdir -p "$RESULTS_DIR/bin"
    g++ -O3 -march=native -fopenmp -ffast-math "$R6/membw.cpp" -o "$B" || { err "membw build failed"; return 1; }
    for rep in rep1 rep2 rep3; do
        for c in s0_c24 s01_t96; do
            cpu_run membw stream "$c" "$rep" "$B" --mib 2048 --reps 10
        done
    done
    grep -h "gbps" "$RAW_DIR"/membw/stream/*/rep*/stdout.log 2>/dev/null | sort | uniq -c | head -20
}

step_pack() {
    local f="$SUITE_DIR/results_round6_$(date +%Y%m%d_%H%M).tar.gz"
    tar czf "$f" -C "$SUITE_DIR" results_round6
    info "packed -> $f  ($(du -h "$f" | cut -f1)). Please upload this file."
}

case "${1:-}" in
    check) step_check ;;
    build) step_build ;;
    design) step_design ;;
    cputune) step_cputune ;;
    cpu) step_cpu ;;
    predict) step_predict ;;
    measure) step_measure ;;
    mcnative) step_mcnative ;;
    membw) step_membw ;;
    pack) step_pack ;;
    all) step_check && step_build && step_design && step_cputune && step_cpu && step_predict ;;
    *) echo "usage: $0 check|build|design|cputune|cpu|predict|measure|mcnative|membw|pack|all"; exit 1 ;;
esac
rc=$?
[ $rc = 0 ] && touch "$RESULTS_DIR/.state/DONE_$1" && info "step '$1' finished" || err "step '$1' stopped (rc=$rc)"
exit $rc
