#!/bin/bash
cd "$(dirname "$0")"
L=$(ls -t tune_state/*/*.build.log   head -1)
echo "build : $L"
echo "now   : $(date '+%T')"
echo
grep -E "Finished [0-9]+.. of 6 tasks Starting bitstream Run vpl: FINISHED" "$L" | tail -6
echo
echo "last lines:"; tail -3 "$L"   sed 's/^/  /'
echo
echo "results so far:"; grep "built=" tune_state/tune.log   tail -5
