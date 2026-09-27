#!/usr/bin/env bash
# Records the hardware/software facts the paper needs (Table I) + build metadata.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
S="$RESULTS_DIR/system"; mkdir -p "$S"/{xclbin_info,link_cfg,timing_reports,util_reports,build_flags}
section "Collecting system information -> $S"

{ echo "date: $(date -Is)"; echo "host: $(hostname)"; uname -a; } > "$S/host.txt"
lscpu > "$S/lscpu.txt" 2>&1
numactl -H > "$S/numactl.txt" 2>&1
free -g > "$S/memory.txt" 2>&1
cat /proc/meminfo >> "$S/memory.txt" 2>/dev/null
lsblk -o NAME,MODEL,ROTA,SIZE,TYPE,TRAN,MOUNTPOINT > "$S/storage.txt" 2>&1
for d in "$AES_ROOT" "$CONV_ROOT" "$LS_ROOT" /dev/shm; do df -hT "$d" >> "$S/storage.txt" 2>&1; done
cpupower frequency-info > "$S/cpufreq.txt" 2>&1 || cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor > "$S/cpufreq.txt" 2>&1
grep . /sys/devices/system/cpu/cpu{0,1,23,48}/cpufreq/scaling_governor >> "$S/cpufreq.txt" 2>/dev/null
cat /sys/devices/system/cpu/intel_pstate/no_turbo >> "$S/cpufreq.txt" 2>/dev/null
cat /sys/devices/system/cpu/smt/control > "$S/smt.txt" 2>/dev/null
for d in /sys/class/powercap/intel-rapl*; do
    [ -f "$d/name" ] && echo "$d name=$(cat "$d/name") pl1_uw=$(cat "$d/constraint_0_power_limit_uw" 2>/dev/null) pl2_uw=$(cat "$d/constraint_1_power_limit_uw" 2>/dev/null) readable=$([ -r "$d/energy_uj" ] && echo yes || echo no)"
done > "$S/rapl_domains.txt"
python3 "$COMMON/power_monitor.py" --probe > "$S/sensor_probe.txt" 2>&1

# ---- Alveo U50 ----
xbutil examine > "$S/xbutil_examine.txt" 2>&1
BDF=$(u50_bdf)
echo "user_bdf=$BDF" > "$S/u50_pci.txt"
lspci -D -d 10ee: -vv >> "$S/u50_pci.txt" 2>&1
for b in $(lspci -D -d 10ee: | awk '{print $1}'); do
    echo "$b numa_node=$(cat /sys/bus/pci/devices/$b/numa_node 2>/dev/null) link=$(cat /sys/bus/pci/devices/$b/current_link_speed 2>/dev/null) x$(cat /sys/bus/pci/devices/$b/current_link_width 2>/dev/null)"
done > "$S/u50_numa_node.txt"
[ -n "$BDF" ] && for r in platform electrical thermal memory dynamic-regions; do
    xbutil examine -d "$BDF" -r "$r" > "$S/xbutil_$r.txt" 2>&1
done
{ xbutil --version; echo; xrt-smi --version 2>/dev/null; } > "$S/xrt_version.txt" 2>&1

# ---- toolchain / libraries ----
{
  echo "gcc: $(gcc --version | head -1)"
  echo "opencv: $(pkg-config --modversion opencv4 2>/dev/null)"
  echo "openssl: $(openssl version 2>/dev/null)"
  echo "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"
  echo "python: $(python3 --version 2>&1)"
  python3 -c "import numpy, pandas, matplotlib; print('numpy', numpy.__version__, 'pandas', pandas.__version__, 'matplotlib', matplotlib.__version__)" 2>&1
  echo "vitis/vivado on PATH: $(command -v vitis) $(command -v vivado)"
} > "$S/toolchain.txt"
{ ss -ltnp 2>/dev/null | grep -E ':1935|:8888|:8090'; echo "(1935 = RTMP server used by the live-stream encoders)"; } > "$S/network_ports.txt"
ffprobe -v error -show_format -show_streams "$LS_VIDEO" > "$S/video_5mp4.txt" 2>&1

# ---- every xclbin: achieved clock, memory map, kernels ----
declare -A XCL=( [aes_enc]="$AES_ENC_XCLBIN" [aes_dec]="$AES_DEC_XCLBIN"
                 [conv_bw]="${CONV_XCLBIN[bw]}" [conv_8bit]="${CONV_XCLBIN[8bit]}" [conv_16bit]="${CONV_XCLBIN[16bit]}"
                 [mc_heston]="$MC_XCLBIN" [portfolio]="$PF_XCLBIN" [live_single]="$LS_XCLBIN" [live_multi]="$LM_XCLBIN"
                 [v2_aes]="$AES2_XCLBIN" [v2_conv]="$CONV2_XCLBIN" [v2_mc_heston]="$MC2_XCLBIN" [v2_portfolio]="$PF2_XCLBIN"
                 [v2_live_single]="$LS2_XCLBIN" [v2_live_multi]="$LM2_XCLBIN"
                 [v2_live_single_4cu]="$LS2_XCLBIN_ABLATION" [v2_live_multi_2cu]="$LM2_XCLBIN_ABLATION" )
for k in "${!XCL[@]}"; do
    x=${XCL[$k]}
    [ -f "$x" ] || { echo "missing: $x" > "$S/xclbin_info/$k.txt"; continue; }
    { echo "path: $x"; md5sum "$x"; ls -l "$x"; xclbinutil --info --input "$x"; } > "$S/xclbin_info/$k.txt" 2>&1
    # search only inside this xclbin's own Vitis project (the folder that contains "build")
    proj=$(dirname "$x")
    while [ "$proj" != "/" ] && [ "$(basename "$proj")" != "build" ]; do proj=$(dirname "$proj"); done
    [ "$proj" = "/" ] && proj=$(dirname "$x") || proj=$(dirname "$proj")
    roots=("$proj")
    case $(basename "$proj") in FPGA_system_project*) ;; *)
        for sib in "$(dirname "$proj")"/FPGA_system_project*; do [ -d "$sib" ] && roots+=("$sib"); done ;; esac
    timeout 90 find "${roots[@]}" -maxdepth 9 -size -20M \( -name "*-link.cfg" -o -name "*.link_summary" \
        -o -name "hw_bb_locked_timing_summary_routed.rpt" -o -name "*full_util_routed.rpt" \
        -o -name "*kernel_util_routed.rpt" \) 2>/dev/null | grep -E "link|/reports/" | head -12 |
    while read -r f; do
        case $f in
            *timing*)            cp "$f" "$S/timing_reports/${k}__$(basename "$f")" ;;
            *util_routed.rpt)    cp "$f" "$S/util_reports/${k}__$(basename "$f")" ;;
            *)                   cp "$f" "$S/link_cfg/${k}__$(basename "$f")" ;;
        esac
    done
done

# ---- CPU build flags of every CPU binary (compile flags matter for fairness) ----
for d in "$AES_ROOT/cpu_only" "$CONV_ROOT/cpu_host_bw" "$CONV_ROOT/CPU_host" "$CONV_ROOT/CPU_host_2" \
         "$MC_ROOT/cpu_only" "$PF_ROOT/cpu_only" "$LS_CPU_DIR" "$LM_CPU_DIR" \
         "$AES_ROOT/FPGA_Host" "$MC_ROOT/host_heston_mc_p6" "$PF_ROOT/host_heston_mc_p6" \
         "$LS_ROOT/Live_Stream_Host" "$LM_ROOT/Live_Stream_Host"; do
    [ -d "$d" ] || continue
    tag=$(echo "${d#/home/USER/Desktop/}" | tr '/' '_')
    timeout 30 find "$d" -maxdepth 3 \( -name Makefile -o -name CMakeLists.txt -o -name "build*.sh" -o -name "compile*.sh" -o -name flags.make -o -name CMakeCache.txt \) 2>/dev/null |
    while read -r f; do
        { echo "### $f"; grep -E "CMAKE_CXX_FLAGS|CXXFLAGS|CXX_FLAGS|-O[0-3s]|march|mavx|fopenmp|g\+\+" "$f" | head -40; } >> "$S/build_flags/$tag.txt"
    done
done
# ---- v2: exact build commands, HLS/timing/utilisation reports, source checksums ----
mkdir -p "$S/v2_builds"
for d in "$V2_ROOT"/0*/; do
    [ -d "$d" ] || continue
    n=$(basename "$d")
    make -C "$d" -n -B cpu host 2>/dev/null | grep -E "g\+\+|c\+\+" > "$S/v2_builds/${n}_compile_cmds.txt"
    [ -d "$d/build/reports" ] && { mkdir -p "$S/v2_builds/${n}_reports"; cp "$d"/build/reports/* "$S/v2_builds/${n}_reports/" 2>/dev/null; }
    ls -l "$d"/build/*.xclbin > "$S/v2_builds/${n}_xclbins.txt" 2>/dev/null
done
( cd "$V2_ROOT" 2>/dev/null && find . -type f \( -name "*.cpp" -o -name "*.hpp" -o -name "*.h" -o -name "*.cfg" -o -name Makefile -o -name "*.mk" -o -name "*.tcl" \) \
    -not -path "*/build/*" | sort | xargs md5sum ) > "$S/v2_builds/source_md5.txt" 2>/dev/null
cp "$SUITE_DIR/config.sh" "$S/config_used.sh"
# automatic design selection (tune_v2.sh): every variant tried, with its reports and the decision
if [ -d "$SUITE_DIR/tune_state" ]; then
    mkdir -p "$S/v2_builds/tuning"
    cp "$SUITE_DIR"/tune_state/{SUMMARY.md,tune_summary.csv,tune.log} "$S/v2_builds/tuning/" 2>/dev/null
    cp "$SUITE_DIR"/v2_projects/TUNED.conf "$S/v2_builds/tuning/" 2>/dev/null
    for d in "$SUITE_DIR"/tune_state/*/; do
        n=$(basename "$d"); mkdir -p "$S/v2_builds/tuning/$n"
        cp "$d"*.result "$d"*.test.log "$d"SELECTED "$S/v2_builds/tuning/$n/" 2>/dev/null
        for r in "$d"*_reports; do [ -d "$r" ] && cp -r "$r" "$S/v2_builds/tuning/$n/"; done
    done
fi
[ -f "$SUITE_DIR/config.local.sh" ] && cp "$SUITE_DIR/config.local.sh" "$S/config_local_used.sh"
info "system information collected"
