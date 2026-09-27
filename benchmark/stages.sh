#!/usr/bin/env bash
cd "$(dirname "$0")"
now=$(date +%s)
line() {   # line <label> <logfile>
    local n=$1 f=$2 age step
    [ -f "$f" ] || { printf "  %-30s %s\n" "$n" "no log yet"; return; }
    age=$(( (now - $(stat -c %Y "$f")) / 60 ))
    step=$(tail -1 "$f" | tr -d '\r' | sed 's/^\[[0-9: -]*\] //' | cut -c1-46)
    printf "  %-30s %4s min   %s\n" "$n" "$age" "$step"
}
echo "== campaign projects"
for d in tune_state/*/; do
    [ -d "$d" ] || continue
    p=$(basename "$d"); sel=""
    [ -f "$d/SELECTED" ] && sel=" *$(cat "$d/SELECTED")"
    log=$(ls -t "$d"*.build.log 2>/dev/null | head -1)
    if [ -z "$log" ]; then printf "  %-30s %s\n" "$p$sel" "not started"; else line "$p$sel" "$log"; fi
done
echo
echo "== side builds (not part of the campaign)"
line "06 pre-build" build_06_parallel.log
line "05 ablation 4CU" build_abl05.log
line "06 ablation 2CU" build_abl06.log
echo
echo "== machine"
free -g | sed -n 2p | awk '{printf "  memory: used %s GB, available %s GB\n", $3, $7}'
printf "  load:%s   vivado procs: %s\n" "$(cut -d' ' -f1-3 /proc/loadavg)" "$(pgrep -c -f 'vivado|vpl' 2>/dev/null || echo 0)"
echo
echo "== latest decisions"
tail -3 tune_state/tune.log | sed 's/^/  /'
