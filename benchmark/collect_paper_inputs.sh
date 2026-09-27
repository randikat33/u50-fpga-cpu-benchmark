#!/usr/bin/env bash
# =============================================================================
#  collect_paper_inputs.sh  -  run on the server (SERVER) to collect ONLY what the
#  JSA paper needs, in the exact layout that paper/scripts/paper_numbers.py reads.
#
#  It copies (never moves or changes) files from the benchmark suite into one zip:
#     inputs/        -> drop into paper/inputs/  (raw runs, tables, supplementary sweeps)
#     tables/        all analysis tables of the campaign (results/v2/tables, results/v1/tables)
#     reports/       routed timing / utilisation reports, xclbin info, tuning summary
#     code/          suite scripts, harness, analysis, v2 sources (no builds, no bitstreams)
#     system_info/   OS, CPU, memory, compiler, XRT, card and firmware details (Table 1)
#     CHECK.txt      what was found / missing, with run counts
#
#  Usage:
#     cd ~/Desktop/Thesis_and_jounal_data && bash collect_paper_inputs.sh
#     bash collect_paper_inputs.sh --suite /path/to/suite --out ~/Desktop
#  Options:  --max-mb N  (skip single files larger than N MB, default 20)
#            --no-code   (skip code/)      --no-sysinfo (skip system_info/)
# =============================================================================
set -uo pipefail
SUITE=""; OUT=""; MAX_MB=20; CODE=1; SYSINFO=1
while [ $# -gt 0 ]; do
    case "$1" in
        --suite) SUITE="$2"; shift 2 ;;  --out) OUT="$2"; shift 2 ;;
        --max-mb) MAX_MB="$2"; shift 2 ;; --no-code) CODE=0; shift ;;
        --no-sysinfo) SYSINFO=0; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "Unknown option $1"; exit 1 ;;
    esac
done
say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; echo "WARNING: $*" >> "$CHECK"; }

# ---------------------------------------------------------------- find the suite
is_suite() { [ -f "$1/config.sh" ] && [ -d "$1/v2_projects" ] && [ -d "$1/results" ]; }
if [ -z "$SUITE" ]; then
    for c in "$PWD" "$(cd "$(dirname "$0")" && pwd)" "$HOME/Desktop/Thesis_and_jounal_data"; do
        is_suite "$c" && { SUITE="$c"; break; }; done
fi
if [ -z "$SUITE" ]; then
    while IFS= read -r c; do d="$(dirname "$c")"; is_suite "$d" && { SUITE="$d"; break; }; done \
        < <(find "$HOME" -maxdepth 5 -name config.sh -not -path '*/.*' 2>/dev/null)
fi
[ -n "$SUITE" ] || { echo "Suite not found. Use --suite /path/to/Thesis_and_jounal_data"; exit 1; }
SUITE="$(cd "$SUITE" && pwd)"; OUT="${OUT:-$SUITE}"
NAME="JSA_server_inputs_$(date +%Y%m%d_%H%M)"
TMP="$(mktemp -d)"; S="$TMP/$NAME"; mkdir -p "$S"; trap 'rm -rf "$TMP"' EXIT
CHECK="$S/CHECK.txt"; : > "$CHECK"
LIMIT=$((MAX_MB * 1024 * 1024)); BIG="$S/EXCLUDED_LARGE_FILES.txt"; : > "$BIG"
say "Suite: $SUITE"
echo "Suite: $SUITE   collected: $(date '+%Y-%m-%d %H:%M')" >> "$CHECK"

# copy_tree <src> <dst> [find filters...]: copy files keeping relative paths; skip big files
copy_tree() {
    local src="$1" dst="$2"; shift 2
    [ -d "$src" ] || return 1
    local list="$TMP/l.$RANDOM"
    ( cd "$src" && find . \( -type d \( -name work -o -name _x -o -name .Xil -o -name .ipcache \
          -o -name __pycache__ -o -name .git -o -name 'build*' -o -name bin \) \) -prune -o \
          -type f ! -name '*.xclbin' ! -name '*.xo' ! -name '*.o' ! -name '*.dcp' ! -name '*.zip' \
          ! -name '*.mp4' ! -name '*.bak' "$@" -printf '%s\t%p\0' ) |
    while IFS=$'\t' read -r -d '' sz p; do
        if [ "$sz" -gt "$LIMIT" ]; then printf '%s MB\t%s/%s\n' $((sz/1048576)) "$src" "${p#./}" >> "$BIG"
        else printf '%s\0' "$p"; fi
    done > "$list"
    mkdir -p "$dst"
    tar -C "$src" --null -T "$list" -cf - 2>/dev/null | tar -C "$dst" -xf -
    rm -f "$list"
}
newest() { find "$SUITE/results" -name "$1" -not -path '*results_quick*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-; }

# ---------------------------------------------------------------- 1. inputs/ (what paper_numbers.py reads)
say "1/5 inputs/ (layout of paper/inputs)"
I="$S/inputs"; mkdir -p "$I"
for t in runs_v2.csv summary_v2.csv accuracy_v2.csv fpga_impl_v2.csv fpga_tuning_v2.csv v1_vs_v2.csv; do
    f="$(newest "$t")"
    if [ -n "$f" ]; then cp "$f" "$I/"; echo "found  $t  <- ${f#$SUITE/}" >> "$CHECK"
    else warn "$t not found under results/ (run: ./run_all.sh --analyze)"; fi
done
# every campaign run: meta.json + power.csv + stdout.log  (v2, and v1 for reviewer questions)
for v in v2 v1; do
    [ -d "$SUITE/results/$v/raw" ] || { [ "$v" = v2 ] && warn "results/v2/raw missing"; continue; }
    copy_tree "$SUITE/results/$v/raw" "$I/campaign_raw_small/$v/raw" \( -name meta.json -o -name power.csv -o -name stdout.log \)
    n=$(find "$I/campaign_raw_small/$v/raw" -name meta.json | wc -l)
    echo "runs   results/$v/raw: $n runs (meta.json)" >> "$CHECK"
done
# the 06 two-CU ablation, which paper_numbers.py also reads from inputs/ablation*
for g in ablation ablation_pipe; do
    src="$SUITE/results/v2/raw/06_LiveStream_Multi/$g"
    if [ -d "$src" ]; then copy_tree "$src" "$I/$g" \( -name meta.json -o -name power.csv -o -name stdout.log \)
        echo "found  inputs/$g ($(find "$I/$g" -name stdout.log | wc -l) runs)" >> "$CHECK"
    else warn "06 $g runs not found in results/v2/raw/06_LiveStream_Multi/$g (the paper's 2-CU ablation)"; fi
done
# supplementary sweeps (whole folders, minus scratch data)
for r in results_scaling results_energy results_mc_steady results_mc_warm; do
    if [ -d "$SUITE/$r" ]; then
        copy_tree "$SUITE/$r" "$I/$r"
        [ -f "$SUITE/$r/DONE" ] || warn "$r has no DONE file (sweep may be incomplete)"
        echo "found  $r ($(find "$I/$r" -name stdout.log | wc -l) runs)" >> "$CHECK"
    else warn "$r missing"; fi
done

# ---------------------------------------------------------------- 2. tables/ + 3. reports/
say "2/5 tables/ and reports/"
for v in v2 v1; do [ -d "$SUITE/results/$v/tables" ] && copy_tree "$SUITE/results/$v/tables" "$S/tables/$v"; done
[ -d "$SUITE/results/system" ] && copy_tree "$SUITE/results/system" "$S/reports/system"
if [ -d "$SUITE/tune_state" ]; then
    copy_tree "$SUITE/tune_state" "$S/reports/tune_state" \( -name '*.md' -o -name '*.csv' -o -name '*.log' \
        -o -name '*.result' -o -name SELECTED -o -name '*.rpt' -o -name '*.txt' -o -name '*.json' \)
fi
# routed reports that still sit in the v2 build folders
( cd "$SUITE/v2_projects" && find . -path '*/build*/reports/*' -type f -size -"${MAX_MB}"M -print0 2>/dev/null ) |
    tar -C "$SUITE/v2_projects" --null -T - -cf - 2>/dev/null | { mkdir -p "$S/reports/v2_build_reports"; tar -C "$S/reports/v2_build_reports" -xf -; }
for f in "$SUITE"/build_logs "$SUITE"/campaign_state; do [ -d "$f" ] && copy_tree "$f" "$S/reports/$(basename "$f")" -size -2M; done

# ---------------------------------------------------------------- 4. code/
if [ "$CODE" = 1 ]; then
    say "3/5 code/ (scripts and sources, no builds or bitstreams)"
    mkdir -p "$S/code"
    for f in "$SUITE"/*.sh "$SUITE"/*.md; do [ -f "$f" ] && cp "$f" "$S/code/"; done
    for d in common analysis patches v1 v2 v2_projects; do [ -d "$SUITE/$d" ] && copy_tree "$SUITE/$d" "$S/code/$d"; done
fi

# ---------------------------------------------------------------- 5. system_info/
if [ "$SYSINFO" = 1 ]; then
    say "4/5 system_info/ (fills the [placeholders] in Table 1)"
    Y="$S/system_info"; mkdir -p "$Y"
    r() { local f="$1"; shift; { echo "\$ $*"; timeout 60 "$@" 2>&1; } > "$Y/$f" 2>/dev/null || true; }
    r os.txt cat /etc/os-release;  r kernel.txt uname -srmv;  r lscpu.txt lscpu
    r numa.txt numactl --hardware; r memory.txt free -h;       r gcc.txt gcc --version
    r python.txt python3 --version; r openssl.txt openssl version
    r cpufreq.txt bash -c 'ls /sys/devices/system/cpu/cpu0/cpufreq 2>&1; cat /sys/devices/system/cpu/intel_pstate/status 2>&1; cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>&1'
    r cstates.txt bash -c 'for s in /sys/devices/system/cpu/cpu0/cpuidle/state*; do echo "$(cat $s/name) disable=$(cat $s/disable)"; done'
    r dimms.txt bash -c 'sudo -n dmidecode -t memory 2>&1 | grep -E "^\s+(Size|Speed|Type|Configured Memory Speed|Locator):" | grep -v "No Module"'
    r bios.txt bash -c 'sudo -n dmidecode -t bios -t system 2>&1 | grep -E "Vendor|Version|Release Date|Manufacturer|Product Name"'
    r pcie_u50.txt bash -c 'lspci -d 10ee: -vv 2>&1 | grep -E "^[0-9a-f]|LnkCap:|LnkSta:"'
    r xrt.txt bash -c 'cat /opt/xilinx/xrt/version.json 2>/dev/null; xbutil --version 2>&1 | head -20'
    r xbutil_examine.txt xbutil examine
    r xbutil_card.txt bash -c 'd=$(xbutil examine 2>/dev/null | grep -oE "\[[0-9a-fA-F:.]+\]" | head -1 | tr -d "[]"); [ -n "$d" ] && xbutil examine -d "$d" -r platform electrical thermal'
    r tools.txt bash -c 'ls -d /tools/Xilinx/Vitis/* /tools/Xilinx/Vivado/* 2>/dev/null; pkg-config --modversion opencv4 2>/dev/null; ffmpeg -version 2>/dev/null | head -1; x264 --version 2>/dev/null | head -1'
    [ -s "$Y/dimms.txt" ] && grep -q "sudo" "$Y/dimms.txt" && echo "note   dimms/bios need sudo: run  sudo dmidecode -t memory  once and paste the Size/Speed lines" >> "$CHECK"
fi

# ---------------------------------------------------------------- manifest + zip
say "5/5 zip"
( cd "$S" && find . -type f ! -name MANIFEST.tsv -printf '%s\t%P\n' | sort -k2 ) > "$S/MANIFEST.tsv"
[ -s "$BIG" ] || rm -f "$BIG"
{ echo; echo "files: $(wc -l < "$S/MANIFEST.tsv")"; [ -f "$BIG" ] && echo "skipped (>${MAX_MB} MB): $(wc -l < "$BIG") file(s), see EXCLUDED_LARGE_FILES.txt"; } >> "$CHECK"
ZIP="$(cd "$OUT" && pwd)/$NAME.zip"
( cd "$TMP" && zip -r -q -9 "$ZIP" "$NAME" )
echo; cat "$CHECK"; echo
say "Done: $ZIP ($(du -h "$ZIP" | cut -f1))"
grep -q WARNING "$CHECK" && echo "    Some items are missing - see the WARNING lines above (the zip is still usable)."
echo "    Upload this zip together with JSA_workspace.zip to the new Claude session."
