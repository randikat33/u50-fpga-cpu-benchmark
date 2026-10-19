#!/usr/bin/env bash
# build_xclbins.sh - phase 3, step 2 on frodo: HLS + full Vitis build of both kernels, one after the
# other (QE first, then FT), with a resource check after HLS so that no 10-hour link starts with a
# design that cannot fit. Resumable: run it again and make skips what is already built.
#   nohup nice -n 19 bash test/build_xclbins.sh > /dev/null 2>&1 &
# Log: build/build_xclbins.log.  Variables: LANES_QE (7), LANES_FT (14), JOBS (4), VIVADO_THREADS (8),
# FREQS (kernel clocks tried in turn when Vivado cannot close timing, as paper 1's tune_v2.sh).
set -u -o pipefail
cd "$(dirname "$0")/.."
VITIS_SETTINGS=${VITIS_SETTINGS:-/tools/Xilinx/Vitis/2023.1/settings64.sh}
XRT_SETUP=${XRT_SETUP:-/opt/xilinx/xrt/setup.sh}
LANES_QE=${LANES_QE:-7}
LANES_FT=${LANES_FT:-14}
export JOBS=${JOBS:-4} VIVADO_THREADS=${VIVADO_THREADS:-8}
FREQS=${FREQS:-"300000000 275000000 250000000 225000000"}
mkdir -p build
LOG=build/build_xclbins.log
exec > >(tee -a "$LOG") 2>&1
echo "=== build_xclbins started $(date -u +%FT%TZ)  (QE lanes/CU $LANES_QE, FT lanes/CU $LANES_FT, 2 CUs each)"
[ -f "$VITIS_SETTINGS" ] && source "$VITIS_SETTINGS" >/dev/null 2>&1
[ -f "$XRT_SETUP" ] && source "$XRT_SETUP" >/dev/null 2>&1
command -v v++ > /dev/null || { echo "v++ not found: set VITIS_SETTINGS"; exit 1; }

# budget for the whole card (2 CUs): DSP <= 75 % of 5952; LUT after implementation <= 60 % of the
# user budget (743,637), taking routed LUT = 0.51 x HLS estimate (paper 1, 08_MC_Asian: 112,072 / 221,611)
budget_ok() {   # $1 = scheme
  local rpt; rpt=$(find build/$1/hls -path "*syn/report*" -name "hq_$1_csynth.rpt" | head -1)
  [ -n "$rpt" ] || { echo "  no HLS report found"; return 1; }
  local line; line=$(grep -m1 "^|Total" "$rpt")
  local dsp lut; dsp=$(echo "$line" | awk -F'|' '{gsub(/ /,"",$4); print $4}'); lut=$(echo "$line" | awk -F'|' '{gsub(/ /,"",$6); print $6}')
  local dsp2=$((2 * dsp)) lut2=$((2 * lut * 51 / 100))
  echo "  HLS estimate per CU: DSP $dsp, LUT $lut  ->  card (2 CUs): DSP $dsp2 of 5952 ($((100 * dsp2 / 5952))%), LUT after implementation ~$lut2 of 743637 ($((100 * lut2 / 743637))%)"
  [ $dsp2 -le 4464 ] && [ $lut2 -le 446182 ]
}

for s in qe ft; do
  L=$([ $s = qe ] && echo $LANES_QE || echo $LANES_FT)
  if [ -f build/$s/hq_$s.xclbin ]; then echo "=== $s: build/$s/hq_$s.xclbin exists, skipped"; continue; fi
  echo "=== $s: HLS ($(date -u +%T))"
  make -s csynth SCHEME=$s LANES=$L > build/csynth_$s.log 2>&1 || { echo "HLS failed for $s: see build/csynth_$s.log"; exit 1; }
  if ! budget_ok $s; then
    echo "=== $s: the design is larger than the budget. Stopped before the long build. Send me build/build_xclbins.log."
    exit 2
  fi
  built=0
  for f in $FREQS; do
    if [ -f build/$s/failed_timing_$f ]; then echo "=== $s: $((f / 1000000)) MHz failed timing before, skipped"; continue; fi
    echo "=== $s: Vitis link at $((f / 1000000)) MHz (synthesis, place, route): 8-20 h ($(date -u +%T))"
    t0=$(date +%s)
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
  [ $built = 1 ] || { echo "=== $s: timing not met at any clock in FREQS. Send me build/build_xclbins.log."; exit 3; }
  echo "=== $s: built at $(( $(cat build/$s/FREQ_HZ) / 1000000 )) MHz in $(( ($(date +%s) - t0) / 3600 )) h $(( ($(date +%s) - t0) % 3600 / 60 )) min"
  make -s fpga_reports SCHEME=$s > /dev/null 2>&1
  grep -h -E "Frequency|freq" build/$s/reports/xclbin_info.txt 2>/dev/null | head -6 | sed 's/^/  /'
  grep -h -A3 "Design Timing Summary" build/$s/reports/*timing_summary_routed.rpt 2>/dev/null | head -8 | sed 's/^/  /'
done
echo "=== ALL XCLBINS BUILT $(date -u +%FT%TZ)"
ls -la build/qe/hq_qe.xclbin build/ft/hq_ft.xclbin
