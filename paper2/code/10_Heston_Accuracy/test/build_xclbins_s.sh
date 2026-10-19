#!/usr/bin/env bash
# build_xclbins_s.sh - secondary cases (protocol v0.5) on frodo: HLS + full Vitis build of the three new kernels,
# one after the other, with the same resource check and lower-clock fallback as test/build_xclbins.sh.
#   nohup nice -n 19 bash test/build_xclbins_s.sh > /dev/null 2>&1 &          (all three: asian, bqe, bft)
#   nohup nice -n 19 bash test/build_xclbins_s.sh bft > /dev/null 2>&1 &      (only some, e.g. in a second shell)
# Log: build/build_xclbins_s.log. Resumable: a built xclbin is skipped.
# Clocks tried in turn: the basket kernels have the main kernels' lanes, which did not close timing above
# 225 MHz, so they start at 225 MHz; the Asian kernel is new and gets one try at 250 MHz.
#   FREQS_B (default "225000000 200000000"), FREQS_A (default "250000000 225000000 200000000")
#   LANES_BFT (14), LANES_BQE (7), LANES_ASIAN (14); JOBS (4), VIVADO_THREADS (8)
set -u -o pipefail
cd "$(dirname "$0")/.."
VITIS_SETTINGS=${VITIS_SETTINGS:-/tools/Xilinx/Vitis/2023.1/settings64.sh}
XRT_SETUP=${XRT_SETUP:-/opt/xilinx/xrt/setup.sh}
export JOBS=${JOBS:-4} VIVADO_THREADS=${VIVADO_THREADS:-8}
FREQS_B=${FREQS_B:-"225000000 200000000"}
FREQS_A=${FREQS_A:-"250000000 225000000 200000000"}
SCHEMES=${*:-"asian bqe bft"}
mkdir -p build
LOG=build/build_xclbins_s.log
exec > >(tee -a "$LOG") 2>&1
echo "=== build_xclbins_s started $(date -u +%FT%TZ): $SCHEMES"
[ -f "$VITIS_SETTINGS" ] && source "$VITIS_SETTINGS" >/dev/null 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
command -v v++ > /dev/null || { echo "v++ not found: set VITIS_SETTINGS"; exit 1; }

top() { case $1 in bft) echo hb_ft;; bqe) echo hb_qe;; asian) echo ha_asian;; esac; }
lanes() { case $1 in bft) echo ${LANES_BFT:-14};; bqe) echo ${LANES_BQE:-7};; asian) echo ${LANES_ASIAN:-14};; esac; }
freqs() { [ $1 = asian ] && echo $FREQS_A || echo $FREQS_B; }

budget_ok() {   # $1 = scheme; same budget as test/build_xclbins.sh
  local t; t=$(top $1)
  local rpt; rpt=$(find build/$1/hls -path "*syn/report*" -name "${t}_csynth.rpt" | head -1)
  [ -n "$rpt" ] || rpt=$(find build/$1/hls -path "*syn/report*" -name "csynth.rpt" | head -1)
  [ -n "$rpt" ] || { echo "  no HLS report found"; return 1; }
  local line; line=$(grep -m1 "^|Total" "$rpt")
  local dsp lut; dsp=$(echo "$line" | awk -F'|' '{gsub(/ /,"",$4); print $4}'); lut=$(echo "$line" | awk -F'|' '{gsub(/ /,"",$6); print $6}')
  local dsp2=$((2 * dsp)) lut2=$((2 * lut * 51 / 100))
  echo "  HLS estimate per CU: DSP $dsp, LUT $lut  ->  card (2 CUs): DSP $dsp2 of 5952 ($((100 * dsp2 / 5952))%), LUT after implementation ~$lut2 of 743637 ($((100 * lut2 / 743637))%)"
  [ $dsp2 -le 4464 ] && [ $lut2 -le 446182 ]
}

for s in $SCHEMES; do
  t=$(top $s); L=$(lanes $s)
  [ -n "$t" ] || { echo "unknown scheme $s (asian, bqe, bft)"; exit 1; }
  if [ -f build/$s/$t.xclbin ]; then echo "=== $s: build/$s/$t.xclbin exists, skipped"; continue; fi
  echo "=== $s ($t, $L lanes per CU, 2 CUs): HLS ($(date -u +%T))"
  make -s csynth SCHEME=$s LANES=$L > build/csynth_$s.log 2>&1 || { echo "HLS failed for $s: see build/csynth_$s.log"; exit 1; }
  if ! budget_ok $s; then
    echo "=== $s: the design is larger than the budget. Stopped before the long build. Send me build/build_xclbins_s.log."
    exit 2
  fi
  built=0
  t0=$(date +%s)
  for f in $(freqs $s); do
    if [ -f build/$s/failed_timing_$f ]; then echo "=== $s: $((f / 1000000)) MHz failed timing before, skipped"; continue; fi
    echo "=== $s: Vitis link at $((f / 1000000)) MHz (synthesis, place, route): 4-20 h ($(date -u +%T))"
    if make xclbin SCHEME=$s LANES=$L FREQ_HZ=$f > build/xclbin_$s.log 2>&1; then built=1; echo "$f" > build/$s/FREQ_HZ; break; fi
    if tail -n 400 build/xclbin_$s.log | grep -q "did not meet timing"; then
      wns=$(grep -h -A6 "Design Timing Summary" $(ls -t $(find build/$s/link -name "*timing_summary*routed*.rpt") 2>/dev/null | head -1) 2>/dev/null | tail -1 | awk '{print $1}')
      echo "=== $s: did not meet timing at $((f / 1000000)) MHz (WNS ${wns:-?} ns); trying the next lower clock"
      touch build/$s/failed_timing_$f
      cp build/xclbin_$s.log build/xclbin_${s}_failed_$f.log
      continue
    fi
    echo "xclbin build failed for $s (not a timing failure): see build/xclbin_$s.log (last lines below)"; tail -n 30 build/xclbin_$s.log; exit 1
  done
  [ $built = 1 ] || { echo "=== $s: timing not met at any clock. Send me build/build_xclbins_s.log."; exit 3; }
  echo "=== $s: built at $(( $(cat build/$s/FREQ_HZ) / 1000000 )) MHz in $(( ($(date +%s) - t0) / 3600 )) h $(( ($(date +%s) - t0) % 3600 / 60 )) min"
  make -s fpga_reports SCHEME=$s > /dev/null 2>&1
  grep -h -A3 "Design Timing Summary" build/$s/reports/*timing_summary_routed.rpt 2>/dev/null | head -8 | sed 's/^/  /'
done
echo "=== ALL REQUESTED XCLBINS BUILT $(date -u +%FT%TZ)"
ls -la build/asian/ha_asian.xclbin build/bqe/hb_qe.xclbin build/bft/hb_ft.xclbin 2>/dev/null
