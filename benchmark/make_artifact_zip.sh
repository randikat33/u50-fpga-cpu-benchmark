#!/usr/bin/env bash
# =============================================================================
#  make_artifact_zip.sh - collect everything needed to reproduce the JSA paper
#  (code, raw measurements, build reports, system information) into ONE zip,
#  ready for GitHub + Zenodo.  It never changes your original files: everything
#  is copied to a temporary staging folder first and cleaned there.
#
#  Usage (run on the server, from anywhere):
#     bash make_artifact_zip.sh                 # find the suite automatically, build the zip
#     bash make_artifact_zip.sh --dry-run       # only show what would be included
#
#  Options:
#     --suite DIR      suite folder (the one with config.sh and v2_projects/)
#     --out DIR        where to write the zip (default: your home folder)
#     --max-mb N       skip single files larger than N MB (default 50; 0 = no limit)
#     --with-xclbin    also include the built FPGA bitstreams (*.xclbin, ~20-80 MB each)
#     --no-v1          do not include the original thesis (v1) source code
#     --paper PATH     also include the paper package (a folder or a .zip)
#     --no-scrub       keep host names, user names and IP addresses unchanged
#     --dry-run        list what would be packed, with sizes, and stop
# =============================================================================
set -uo pipefail

SUITE=""; OUT="$HOME"; MAX_MB=50; WITH_XCLBIN=0; WITH_V1=1; PAPER=""; SCRUB=1; DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --suite) SUITE="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --max-mb) MAX_MB="$2"; shift 2 ;;
        --with-xclbin) WITH_XCLBIN=1; shift ;;
        --no-v1) WITH_V1=0; shift ;;
        --paper) PAPER="$2"; shift 2 ;;
        --no-scrub) SCRUB=0; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
    esac
done

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*"; exit 1; }
for t in find tar zip sha256sum sed grep awk; do command -v "$t" >/dev/null || die "missing tool: $t"; done

# ---------------------------------------------------------------- 1. find the suite folder
is_suite() { [ -f "$1/config.sh" ] && [ -d "$1/v2_projects" ]; }
if [ -z "$SUITE" ]; then
    here="$(cd "$(dirname "$0")" && pwd)"
    if is_suite "$here"; then SUITE="$here"
    elif is_suite "$PWD"; then SUITE="$PWD"
    else
        say "Searching your home folder for the benchmark suite (config.sh + v2_projects/) ..."
        while IFS= read -r c; do d="$(dirname "$c")"; is_suite "$d" && { SUITE="$d"; break; }; done \
            < <(find "$HOME" -maxdepth 6 -name config.sh -not -path '*/.*' 2>/dev/null)
    fi
fi
[ -n "$SUITE" ] && is_suite "$SUITE" || die "Could not find the suite. Run with --suite /path/to/Thesis_and_jounal_data"
SUITE="$(cd "$SUITE" && pwd)"
say "Suite folder: $SUITE"

STAMP="$(date +%Y%m%d)"
NAME="JSA_artifact_${STAMP}"
STAGE_ROOT="$(mktemp -d)"; STAGE="$STAGE_ROOT/$NAME"; mkdir -p "$STAGE"
trap 'rm -rf "$STAGE_ROOT"' EXIT
LIMIT_BYTES=$(( MAX_MB * 1024 * 1024 ))
BIG_LIST="$STAGE_ROOT/excluded_large.txt"; : > "$BIG_LIST"

# Folders that are never useful to share (build products, caches, secrets)
PRUNE_DIRS=( .git .svn __pycache__ .ipynb_checkpoints _x .Xil .ipcache .run .cache .ssh .gnupg
             Emulation-SW Emulation-HW Hardware sw_emu hw_emu ip_cache .hbs .jobs node_modules )
# File patterns that are never copied
SKIP_FILES=( '*.o' '*.a' '*.so' '*.dcp' '*.bit' '*.xo' '*.xsa' '*.pdi' '*.wdb' '*.wcfg' '*.pb' '*.jou'
             'core' 'core.*' '*.pem' '*.key' 'id_rsa*' 'id_ed25519*' '.env' '.netrc' '.git-credentials'
             '*.zip' '*.mp4' '*.mkv' '*.mov' '*.avi' '*.h264' '*.h265' '*.yuv' '*.y4m' '*.swp' '*~' )
[ "$WITH_XCLBIN" = 1 ] || SKIP_FILES+=( '*.xclbin' )

# collect <src_dir> <dest_subdir> [extra find args...]
# Copies files below src_dir into STAGE/dest_subdir, keeping relative paths, applying the
# prune/skip rules and the size limit. Extra find arguments narrow the selection further.
collect() {
    local src="$1" dst="$2"; shift 2
    [ -d "$src" ] || return 0
    local list="$STAGE_ROOT/list.$$.$RANDOM" expr=() p
    for p in "${PRUNE_DIRS[@]}"; do expr+=( -name "$p" -o ); done
    local skip=() s
    for s in "${SKIP_FILES[@]}"; do skip+=( ! -name "$s" ); done
    ( cd "$src" && find . \( -type d \( "${expr[@]}" -false \) \) -prune -o \
        -type f "${skip[@]}" "$@" -printf '%s\t%p\0' ) 2>/dev/null |
    while IFS=$'\t' read -r -d '' size path; do
        if [ "$MAX_MB" -gt 0 ] && [ "$size" -gt "$LIMIT_BYTES" ]; then
            printf '%s\t%s/%s\n' "$(awk -v b="$size" 'BEGIN{printf "%.1f MB", b/1048576}')" "$src" "${path#./}" >> "$BIG_LIST"
        else
            printf '%s\0' "$path"
        fi
    done > "$list"
    local n; n=$(tr -cd '\0' < "$list" | wc -c)
    if [ "$DRY" = 1 ]; then
        local bytes; bytes=$( ( cd "$src" && tr '\0' '\n' < "$list" | while IFS= read -r f; do stat -c %s "$f" 2>/dev/null; done ) | awk '{s+=$1} END{print s+0}')
        printf '    %-45s %7d files  %9.1f MB   (%s)\n' "$dst" "$n" "$(awk -v b="$bytes" 'BEGIN{print b/1048576}')" "$src"
    elif [ "$n" -gt 0 ]; then
        mkdir -p "$STAGE/$dst"
        tar -C "$src" --null -T "$list" -cf - 2>/dev/null | tar -C "$STAGE/$dst" -xf -
        printf '    %-45s %7d files\n' "$dst" "$n"
    fi
    rm -f "$list"
}

[ "$DRY" = 1 ] && say "DRY RUN - nothing will be written" || say "Collecting files (originals are not modified)"

# ---------------------------------------------------------------- 2. code
# Suite scripts, harness, analysis, patches, v1 wrappers, v2 project sources (no build outputs,
# no results, no scratch data). Build folders are handled separately (reports only).
collect "$SUITE" "code" \
    ! -path './results*' ! -path './data/*' ! -path './tune_state/*' ! -path './patches/bin/*' \
    ! -path './v2_projects/*/build*/*' ! -path './*/work/*'

# ---------------------------------------------------------------- 3. measurements
for r in "$SUITE"/results "$SUITE"/results_*; do
    [ -d "$r" ] || continue
    [ "$(basename "$r")" = results_quick ] && continue          # smoke test, not used in the paper
    collect "$r" "results/$(basename "$r")" ! -path '*/work/*'
done

# ---------------------------------------------------------------- 4. build + tuning reports
collect "$SUITE/v2_projects" "build_reports/v2_projects" \( -path '*/build*/reports/*' -o -name '*.xclbin.info' -o -name '*_timing*.rpt' -o -name '*util*.rpt' \)
[ "$WITH_XCLBIN" = 1 ] && collect "$SUITE/v2_projects" "build_reports/xclbin" -path '*/build*/*' -name '*.xclbin'
collect "$SUITE/tune_state" "build_reports/tune_state" ! -path '*/build*/*'

# ---------------------------------------------------------------- 5. v1 (thesis) sources
if [ "$WITH_V1" = 1 ]; then
    mapfile -t V1_ROOTS < <( SUITE_DIR="$SUITE" bash -c '
        source "$SUITE_DIR/config.sh" >/dev/null 2>&1
        for v in AES_ROOT CONV_ROOT MC_ROOT PF_ROOT LS_ROOT LM_ROOT; do echo "${!v:-}"; done' | awk 'NF' | sort -u )
    for root in "${V1_ROOTS[@]}"; do
        [ -d "$root" ] || { warn "v1 folder not found, skipped: $root"; continue; }
        collect "$root" "v1_source/$(basename "$root")" \
            ! -path '*/build/*' ! -path '*/Debug/*' ! -path '*/Release/*' ! -path '*/export/*' \
            \( -name '*.cpp' -o -name '*.cc' -o -name '*.c' -o -name '*.h' -o -name '*.hpp' -o -name '*.cl' \
               -o -name '*.cfg' -o -name '*.ini' -o -name '*.tcl' -o -name '*.py' -o -name '*.sh' -o -name '*.mk' \
               -o -name 'Makefile' -o -name 'CMakeLists.txt' -o -name '*.md' -o -name '*.txt' -o -name '*.json' \
               -o -name '*.rpt' -o -name '*.csv' \)
    done
fi

# ---------------------------------------------------------------- 6. paper package (optional)
if [ -n "$PAPER" ]; then
    if [ -d "$PAPER" ]; then collect "$PAPER" "paper"
    elif [ -f "$PAPER" ]; then
        [ "$DRY" = 1 ] && printf '    %-45s (%s)\n' "paper" "$PAPER" || { mkdir -p "$STAGE/paper"; cp "$PAPER" "$STAGE/paper/"; }
    else warn "--paper $PAPER not found, skipped"; fi
fi

if [ "$DRY" = 1 ]; then
    [ -s "$BIG_LIST" ] && { warn "Files larger than $MAX_MB MB that would be skipped:"; sed 's/^/      /' "$BIG_LIST"; }
    say "Dry run finished. Run again without --dry-run to build the zip."
    exit 0
fi

# ---------------------------------------------------------------- 7. system information
say "Recording system information (fills the placeholders in Table 1)"
S="$STAGE/system_info"; mkdir -p "$S"
run() { local f="$1"; shift; { echo "\$ $*"; "$@" 2>&1; } > "$S/$f" 2>/dev/null || true; }
run os-release.txt        cat /etc/os-release
run kernel.txt            uname -srmv
run lscpu.txt             lscpu
run numactl.txt           numactl --hardware
run memory.txt            free -h
run gcc.txt               gcc --version
run gxx.txt               g++ --version
run python.txt            python3 --version
run cmdline.txt           cat /proc/cmdline
run cpufreq_driver.txt    bash -c 'ls /sys/devices/system/cpu/cpu0/cpufreq 2>&1; cat /sys/devices/system/cpu/intel_pstate/status 2>&1'
run turbo.txt             bash -c 'cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>&1; cat /sys/devices/system/cpu/cpufreq/boost 2>&1'
run cstates.txt           bash -c 'for s in /sys/devices/system/cpu/cpu0/cpuidle/state*; do echo "$(cat $s/name) disabled=$(cat $s/disable)"; done'
run dimms.txt             bash -c 'sudo -n dmidecode -t memory 2>&1 | grep -E "Size|Speed|Type:|Locator" | grep -v "No Module"'
run bios.txt              bash -c 'sudo -n dmidecode -t bios -t system 2>&1 | grep -E "Vendor|Version|Release|Product|Manufacturer"'
run pcie_u50.txt          bash -c 'lspci -d 10ee: -vv 2>&1 | grep -E "^[0-9a-f]|LnkCap|LnkSta"'
run xrt_version.txt       bash -c 'cat /opt/xilinx/xrt/version.json 2>/dev/null || xbutil --version'
run xbutil_examine.txt    xbutil examine
run xbutil_device.txt     bash -c 'd=$(xbutil examine 2>/dev/null | grep -oE "\[[0-9a-f:.]+\]" | head -1 | tr -d "[]"); [ -n "$d" ] && xbutil examine -d "$d" -r platform electrical thermal'
run vitis_version.txt     bash -c 'ls -d /tools/Xilinx/Vitis/* /tools/Xilinx/Vivado/* 2>/dev/null'
run opencv_version.txt    bash -c 'pkg-config --modversion opencv4 2>/dev/null || python3 -c "import cv2;print(cv2.__version__)"'
run openssl_version.txt   openssl version
run ffmpeg_version.txt    bash -c 'ffmpeg -version 2>&1 | head -1; x264 --version 2>&1 | head -1'

# ---------------------------------------------------------------- 8. scrub private details
if [ "$SCRUB" = 1 ]; then
    say "Removing host names, user names, serial REDACTED and IP addresses from text files"
    HN="$(hostname -s 2>/dev/null)"; HF="$(hostname -f 2>/dev/null)"; ME="$(id -un)"
    find "$STAGE" -type f -print0 | while IFS= read -r -d '' f; do
        grep -Iq . "$f" 2>/dev/null || continue            # text files only
        sed -i -E \
            -e "s#/home/[A-Za-z0-9._-]+#/home/USER#g" \
            ${HF:+-e "s#\\b${HF//./\\.}\\b#SERVER#g"} \
            ${HN:+-e "s#\\b${HN}\\b#SERVER#g"} \
            -e "s#\\bSERVER\\b#SERVER#gI" \
            ${ME:+-e "s#\\b${ME}\\b#USER#g"} \
            -e 's#\b(10|172|192|131|203)\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b#x.x.x.x#g' \
            -e 's#\b([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b#xx:xx:xx:xx:xx:xx#g' \
            -e 's#((Serial|serial)[ _-]?([Nn]umber|[Nn]o\.?)?[" :=]+)[A-Za-z0-9-]{6,}#\1REDACTED#g' \
            "$f"
    done
    # anything that still looks like a secret is reported (never deleted automatically)
    grep -rIlE 'BEGIN [A-Z ]*PRIVATE KEY|password[[:space:]]*[:=]|passwd[[:space:]]*[:=]|api[_-]?key[[:space:]]*[:=]|secret[[:space:]]*[:=]|token[[:space:]]*[:=]' \
        "$STAGE" 2>/dev/null | sed "s#$STAGE/##" > "$STAGE_ROOT/secrets.txt"
fi

# ---------------------------------------------------------------- 9. manifest + README
say "Writing MANIFEST.tsv and README_ARTIFACT.md"
( cd "$STAGE" && find . -type f ! -name MANIFEST.tsv -printf '%P\0' | sort -z |
  while IFS= read -r -d '' f; do printf '%s\t%s\t%s\n' "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -c1-64)" "$f"; done ) \
  > "$STAGE_ROOT/manifest.tmp"
{ printf 'bytes\tsha256\tpath\n'; cat "$STAGE_ROOT/manifest.tmp"; } > "$STAGE/MANIFEST.tsv"
[ -s "$BIG_LIST" ] && { printf 'size\tpath (not included: larger than %s MB)\n' "$MAX_MB"; sed "s#/home/[A-Za-z0-9._-]*#/home/USER#g" "$BIG_LIST"; } > "$STAGE/EXCLUDED_LARGE_FILES.txt"

cat > "$STAGE/README_ARTIFACT.md" <<EOF
# Artifact: "Optimise both sides, measure the whole system" (Journal of Systems Architecture)

Created $(date -u +%Y-%m-%d) by make_artifact_zip.sh.

| Folder | Contents |
|---|---|
| code/ | benchmark suite: harness (campaign.sh, common/run_measured.py, power_monitor.py), config.sh, build and tuning scripts, analysis scripts, v2 FPGA kernels, host programs and CPU baselines |
| results/ | raw measurements of every run (meta.json, power.csv, stdout.log) and the derived tables and figures, one folder per experiment |
| build_reports/ | routed timing and utilisation reports of the selected FPGA designs and of the tuning search |
| v1_source/ | original thesis (v1) source code, used for the v1-vs-v2 comparison |
| system_info/ | hardware, firmware and software versions of the test server |
| paper/ | the paper package (if included) |

- MANIFEST.tsv lists every file with its size and SHA-256.
- EXCLUDED_LARGE_FILES.txt (if present) lists files left out because of their size (generated input data, videos, bitstreams); they are regenerated by the scripts or available on request.
- Host names, user names, IP and MAC addresses and serial REDACTED were replaced by placeholders.
- Licences: code MIT (or Apache-2.0), data CC-BY 4.0 - edit before publishing.
EOF

# ---------------------------------------------------------------- 10. zip
mkdir -p "$OUT"; ZIP="$(cd "$OUT" && pwd)/$NAME.zip"; rm -f "$ZIP"
say "Creating $ZIP"
( cd "$STAGE_ROOT" && zip -r -q -9 "$ZIP" "$NAME" ) || die "zip failed"

echo
say "Done: $ZIP  ($(du -h "$ZIP" | cut -f1), $(($(wc -l < "$STAGE/MANIFEST.tsv") - 1)) files)"
for d in "$STAGE"/*/; do printf '      %-16s %s\n' "$(basename "$d")" "$(du -sh "$d" | cut -f1)"; done
[ -s "$BIG_LIST" ] && warn "$(wc -l < "$BIG_LIST") file(s) larger than $MAX_MB MB were left out - see EXCLUDED_LARGE_FILES.txt in the zip"
big=$(awk -F'\t' 'NR>1 && $1>100*1024*1024' "$STAGE/MANIFEST.tsv" | wc -l)
[ "$big" -gt 0 ] && warn "$big file(s) are over 100 MB: fine for Zenodo, but GitHub will refuse them"
[ -s "$STAGE_ROOT/secrets.txt" ] && { warn "These files contain words like password/token/key - open and check them before publishing:"; sed 's/^/      /' "$STAGE_ROOT/secrets.txt"; }
echo "    Next: unzip it, look through it once, then upload to GitHub and make a Zenodo release for the DOI."
