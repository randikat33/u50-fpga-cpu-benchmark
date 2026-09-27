#!/usr/bin/env bash
cd "$(dirname "$0")"
printf '%s    load:%s\n' "$(date '+%F %T')" "$(cut -d' ' -f1-3 /proc/loadavg)"
free -g | sed -n 2p | awk '{printf "memory: used %s GB, available %s GB\n", $3, $7}'
echo
echo "== campaign stages done"
for s in deps programs tune check quick pause full analyze; do
    [ -f "campaign_state/$s.done" ] && printf "   %-9s %s\n" "$s" "$(cat campaign_state/$s.done)"
done
flock -n campaign_state/.lock true 2>/dev/null && echo "   campaign NOT running" || echo "   campaign running"
echo
echo "== campaign build in progress"
P=$(ls -t tune_state/*/*.build.log 2>/dev/null | head -1)
if [ -n "$P" ]; then
    echo "   ${P#tune_state/}"
    grep -E "kernel clock requested|of 6 tasks|\] Phase" "$P" | tail -3 | sed 's/^/   /'
fi
echo
echo "== 06 pre-build (separate)"
grep -E "of 6 tasks|\] Phase|building|build finished|handed|slack:" build_06_parallel.log 2>/dev/null \
    | tail -3 | sed 's/^/   /'
echo
echo "== designs selected"
grep -h "ok=1" tune_state/tune.log 2>/dev/null | tail -5 | sed 's/^/   /'
echo
echo "== timing misses (all projects)"
grep -h "slack:" tune_state/*/*.build.log build_06_parallel.log 2>/dev/null \
    | sed 's/.*slack:/   slack:/' | tail -6
