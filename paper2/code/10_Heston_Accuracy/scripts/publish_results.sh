#!/bin/bash
# publish_results.sh - rerun both analyses and the figures, then copy everything paper 2 reports into the GitHub repo.
# It only copies; it does not commit or push. Usage (from 10_Heston_Accuracy):  bash scripts/publish_results.sh
set -e
P=$(cd "$(dirname "$0")/.." && pwd)
REPO=${REPO:-$HOME/Desktop/Thesis_and_jounal_data/u50-fpga-cpu-benchmark}
D=$REPO/paper2/results
C=$REPO/paper2/code/10_Heston_Accuracy
[ -d "$REPO/.git" ] || { echo "repo not found: $REPO"; exit 1; }
cd "$P"

echo "== 1. analyses"
python3 scripts/analyse.py   > results/analyse.log 2>&1
python3 scripts/analyse_s.py > results/analyse_s.log 2>&1
sha256sum results/analysis/analysis.md results/analysis_s/analysis_s.md

echo "== 2. figures"
if python3 -c "import matplotlib" 2>/dev/null; then
  python3 scripts/make_figures.py
else
  echo "matplotlib is not installed here: keeping the figures shipped in the zip"
fi

echo "== 3. copy into the repo"
mkdir -p "$D/main" "$D/secondary" "$D/figures" "$C"
cp results/phase5_summary_s*.csv results/phase5_s1.log results/phase5_s2.log \
   results/phase5_s1_events.log results/phase5_s2_events.log "$D/main/"
cp -r results/analysis "$D/main/"
[ -f build/card_check.log ] && cp build/card_check.log "$D/main/"
tar czf "$D/main/phase5_runs.tar.gz" --exclude=.state -C results phase5

cp results/phase5s_summary_s*.csv results/phase5s_s1.log results/phase5s_s2.log \
   results/phase5s_s1_events.log results/phase5s_s2_events.log "$D/secondary/"
cp -r results/analysis_s "$D/secondary/"
[ -f build/card_check_s.log ] && cp build/card_check_s.log "$D/secondary/"
tar czf "$D/secondary/phase5s_runs.tar.gz" --exclude=.state -C results phase5s

cp results/figures/* "$D/figures/"
cp scripts/RESULTS_README.md "$D/README.md"

echo "== 4. source snapshot (no builds, no results)"
tar cf - --exclude=./build --exclude=./results --exclude=./results_calls_v03 --exclude=__pycache__ \
   --exclude='*.xclbin' --exclude='*.o' --exclude='*.log' . | tar xf - -C "$C"

echo "== 5. sizes"
du -sh "$D/main" "$D/secondary" "$D/figures" "$C"
big=$(find "$D" "$C" -type f -size +50M)
[ -z "$big" ] && echo "no file above 50 MB" || { echo "TOO LARGE FOR GITHUB:"; echo "$big"; exit 1; }
echo "PUBLISH COPY DONE"
