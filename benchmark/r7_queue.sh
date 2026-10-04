#!/usr/bin/env bash
# =============================================================================
#  r7_queue.sh - runs every round-7 step unattended, and politely (same rules as r56_queue.sh):
#    * it measures only while nobody else uses the server. When another user starts working, a running
#      measurement is stopped at once (the unfinished run and any run that ended in the last 2 minutes are
#      thrown away and repeated later). The xclbin build is not a measurement: it runs while others use up to
#      BUILD_OTHER_MAX cores (default 8 of 96) and is only frozen (SIGSTOP) above that. It runs first, so the
#      day-time hours go to the build and the measurements use the quiet hours.
#      It waits until the server has been quiet for QUIET_MIN minutes (default 10), then continues.
#    * after a power cut or reboot it starts again by itself (crontab @reboot + a 30-minute watchdog);
#      finished runs are never repeated.
#    * the sweep design runs on the card only after you confirmed that the prediction is e-mailed.
#
#  One-time setup (asks for your sudo password once):
#     ./r7_queue.sh setup          RAPL readable + governor at boot, passwordless sudo for ipmitool/perf/
#                                   turbostat/dmidecode/dmesg only, autostart after reboot, BMC power-restore
#  Then:
#     ./r7_queue.sh start          start now (in tmux session 'r7'; safe to run twice)
#     ./r7_queue.sh status         what is done, what runs, who is active, interruptions
#     ./r7_queue.sh published      after you e-mailed results_round7/predictions (unlocks 'measure')
#     ./r7_queue.sh pause|resume   hold everything by hand (e.g. before you need the machine)
#     ./r7_queue.sh stop           stop now (the current run is repeated next time)
#     ./r7_queue.sh disable        remove the autostart (at the end)
#  Log: r7_queue.log   Activity log (other users, every 2 s): results_round7/activity.csv
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SUITE_DIR" || exit 1
Q="$SUITE_DIR/.r7_queue"; mkdir -p "$Q"
LOG="$SUITE_DIR/r7_queue.log"
GUARD="python3 $SUITE_DIR/r7_guard.py"
QUIET_MIN=${QUIET_MIN:-10}
QUIET_S=${QUIET_S:-$((QUIET_MIN * 60))}
GAP_H=${GAP_H:-20}                 # hours between measurement sessions s1 -> s2 -> s3
GAP_S=${GAP_S:-$((GAP_H * 3600))}
POLL=${POLL:-5}
BUILD_OTHER_MAX=${BUILD_OTHER_MAX:-8}   # the build (not measured) only freezes when other users use > this many cores
PIN=${PIN:-taskset -c 95}   # the driver runs on CPU 95, as in the guides
WAIT_POLL=${WAIT_POLL:-30}
IDLE_POLL=${IDLE_POLL:-600}
CARD_WAIT=${CARD_WAIT:-90}
TAG="# r7-queue-autostart"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# ---- the queue: id | command | mode (measure = stop and repeat, build = freeze and continue) | gate --------
QUEUE="
r7_check    | ./round7_sweep.sh check       | measure | -
r7_build    | ./round7_sweep.sh build       | build   | done:r7_check
r7_design   | ./round7_sweep.sh design      | build   | done:r7_build
r7_pfnative | ./round7_sweep.sh pfnative    | measure | done:r7_check
r7_cpu      | ./round7_sweep.sh cpu         | measure | done:r7_check
r7_predict  | ./round7_sweep.sh predict     | measure | done:r7_design,r7_cpu
r7_measure  | ./round7_sweep.sh measure     | measure | published
r7_pack     | ./round7_sweep.sh pack        | measure | done:r7_measure,r7_pfnative
"
items() { echo "$QUEUE" | grep -v '^\s*$'; }

gate_ok() {   # gate_ok <gate>
    case $1 in
        -) return 0 ;;
        published) [ -f "$Q/PUBLISHED" ] ;;
        done:*) local x; for x in $(tr ',' ' ' <<< "${1#done:}"); do [ -f "$Q/$x.done" ] || return 1; done; return 0 ;;
        after:*) local p=${1#after:}
                 [ -f "$Q/$p.done" ] || return 1
                 [ $(( $(date +%s) - $(stat -c %Y "$Q/$p.done") )) -ge "$GAP_S" ] ;;
        *) return 1 ;;
    esac
}
trim() { local x=$1; x=${x#"${x%%[![:space:]]*}"}; echo "${x%"${x##*[![:space:]]}"}"; }
next_item() {   # prints "id|command|mode" of the first pending item whose gate is open
    local id cmd mode gate
    while IFS='|' read -r id cmd mode gate; do
        id=$(trim "$id"); gate=$(trim "$gate")
        [ -f "$Q/$id.done" ] && continue
        gate_ok "$gate" && { echo "$id|$(trim "$cmd")|$(trim "$mode")"; return 0; }
    done < <(items)
    return 1
}
pending() { local n=0 id; for id in $(items | cut -d'|' -f1); do [ -f "$Q/$id.done" ] || n=$((n+1)); done; echo $n; }

paused() { [ -f "$Q/PAUSE" ] || [ -f "$HOME/STOP_R7" ]; }
LAST_BUSY=0
busy() {
    $GUARD check --window 10 > "$Q/guard.txt" 2>&1
    [ $? = 1 ] || return 1
    LAST_BUSY=$(date +%s)
    return 0
}

busy_build() {   # the build is not a measurement: light use by others (e.g. one simulator) does not freeze it
    OTHER_CPU_MAX=$BUILD_OTHER_MAX SYS_CPU_MAX=$BUILD_OTHER_MAX $GUARD check --window 10 > "$Q/guard.txt" 2>&1
    [ $? = 1 ]
}
wait_build_ok() {   # returns as soon as others use <= BUILD_OTHER_MAX cores (no quiet period)
    local said=0
    while paused || busy_build; do
        [ $said = 1 ] || { say "build waiting: $(paused && echo 'manual pause' || cat "$Q/guard.txt")"; said=1; }
        sleep "$WAIT_POLL"
    done
    [ $said = 0 ] || say "build may continue"
}

wait_quiet() {   # returns once nobody else has been active for QUIET_S seconds (and there is no manual pause)
    local said=0
    while :; do
        if paused; then
            [ $said = 1 ] || { say "paused by hand ($Q/PAUSE or ~/STOP_R7); waiting"; said=1; }
            LAST_BUSY=$(date +%s); sleep "$WAIT_POLL"; continue
        fi
        if busy; then
            [ $said = 2 ] || { say "waiting: $(cat "$Q/guard.txt")"; said=2; }
            sleep "$WAIT_POLL"; continue
        fi
        if [ $(( $(date +%s) - LAST_BUSY )) -ge "$QUIET_S" ]; then
            [ $said = 0 ] || say "quiet for $((QUIET_S / 60)) min; continuing"
            return 0
        fi
        sleep "$WAIT_POLL"
    done
}

invalidate_recent() {   # runs that finished in the last 2 minutes may have overlapped the other user's work
    local f n=0
    for f in $(find "$SUITE_DIR/results_round7/raw" -name DONE -newermt "2 minutes ago" 2>/dev/null); do
        rm -f "$f"; n=$((n + 1)); echo "$(date -Is) $(dirname "$f")" >> "$Q/invalidated_runs.txt"
    done
    [ $n -gt 0 ] && say "  $n run(s) that ended in the last 2 minutes will be repeated"
}

run_item() {   # run_item <id> <command> <mode>; returns 0 when the step finished
    local id=$1 cmd=$2 mode=$3 pid rc
    if [ "$mode" = build ]; then wait_build_ok; else wait_quiet; fi
    say "START $id: $cmd"
    echo "$id" > "$Q/current"
    setsid $PIN bash -c "$cmd" >> "$Q/$id.out" 2>&1 &
    pid=$!
    echo "$pid" > "$Q/current.pid"
    while kill -0 "$pid" 2>/dev/null; do
        sleep "$POLL"
        kill -0 "$pid" 2>/dev/null || break
        local hold=1
        if [ "$mode" = build ]; then paused || busy_build || hold=0; else paused || busy || hold=0; fi
        if [ $hold = 1 ]; then
            local why; why=$(paused && echo "manual pause" || cat "$Q/guard.txt")
            if [ "$mode" = build ]; then
                say "  freeze $id ($why)"
                $GUARD tree "$pid" STOP >/dev/null
                wait_build_ok
                say "  continue $id"
                $GUARD tree "$pid" CONT >/dev/null
            else
                say "  STOP $id ($why); the unfinished run will be repeated"
                echo "$(date -Is) $id $why" >> "$Q/interruptions.txt"
                $GUARD tree "$pid" TERM >/dev/null; sleep 20; $GUARD tree "$pid" KILL >/dev/null
                wait "$pid" 2>/dev/null
                invalidate_recent
                rm -f "$Q/current" "$Q/current.pid"
                return 1
            fi
        fi
    done
    wait "$pid"; rc=$?
    rm -f "$Q/current" "$Q/current.pid"
    # after the build: the next measurement needs a fresh QUIET_MIN without other users (not checked during the build)
    [ "$mode" = build ] && LAST_BUSY=$(date +%s)
    if [ $rc = 0 ]; then date -Is > "$Q/$id.done"; say "DONE  $id"; return 0; fi
    echo $(( $(cat "$Q/$id.fails" 2>/dev/null || echo 0) + 1 )) > "$Q/$id.fails"
    say "FAILED $id (rc=$rc, attempt $(cat "$Q/$id.fails"); see $Q/$id.out)"
    [ "$(cat "$Q/$id.fails")" -ge 3 ] && { date -Is > "$Q/$id.done"; echo "gave up after 3 failures" >> "$Q/$id.done"; \
        say "  giving up on $id after 3 failures (the queue continues; check $Q/$id.out)"; }
    return 1
}

wait_for_card() {
    local i
    for i in $(seq 1 "$CARD_WAIT"); do
        xbutil examine 2>/dev/null | grep -qi "u50" && return 0
        [ "$i" = 1 ] && say "waiting for the Alveo card (xbutil examine) ..."
        sleep 10
    done
    say "WARNING: card not visible; continuing (card steps will fail and be retried)"
}

cmd_run() {
    exec 9>"$Q/.lock"
    flock -n 9 || { echo "already running (./r7_queue.sh status)"; exit 0; }
    # shellcheck disable=SC1091
    [ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
    rm -f "$Q/current" "$Q/current.pid"          # left over from a power cut: that step simply runs again
    say "queue runner started (pid $$, $(pending) item(s) pending)"
    sudo -n true 2>/dev/null || say "WARNING: passwordless sudo is not working - wall power, perf and turbostat will be skipped (run ./r7_queue.sh setup again)"
    LAST_BUSY=$(date +%s)          # after a (re)boot: first make sure the server has been quiet for a while
    wait_for_card
    $GUARD log "$SUITE_DIR/results_round7/activity.csv" 2>/dev/null &
    local ACT=$!
    trap 'kill $ACT 2>/dev/null' EXIT
    mkdir -p "$SUITE_DIR/results_round7"
    while [ "$(pending)" -gt 0 ]; do
        local it
        if ! it=$(next_item); then
            if [ -f "$Q/r7_predict.done" ] && [ ! -f "$Q/PUBLISHED" ] && [ ! -f "$Q/r7_measure.done" ]; then
                [ -f "$Q/.told_publish" ] || { say "WAITING FOR YOU: e-mail results_round7/predictions/predictions.md and the two SHA-256 lines (guide step 4), then run: ./r7_queue.sh published"; touch "$Q/.told_publish"; }
            fi
            sleep "$IDLE_POLL"; continue
        fi
        IFS='|' read -r id cmd mode <<< "$it"
        run_item "$id" "$cmd" "$mode" || sleep "$WAIT_POLL"
    done
    say "ALL DONE. Upload the newest results_round7_*.tar.gz. (./r7_queue.sh disable removes the autostart)"
}

cmd_status() {
    echo "== queue ($(pending) pending)"
    local id cmd mode gate st
    while IFS='|' read -r id cmd mode gate; do
        id=$(trim "$id"); gate=$(trim "$gate")
        if [ -f "$Q/$id.done" ]; then st="done $(head -1 "$Q/$id.done")$(sed -n 2p "$Q/$id.done" | sed 's/^/ - /')"
        elif [ "$(cat "$Q/current" 2>/dev/null)" = "$id" ]; then st="RUNNING"
        elif ! gate_ok "$gate"; then st="waiting for: $gate"
        else st="-"; fi
        printf "  %-12s %-40s %s\n" "$id" "$(trim "$cmd")" "$st"
    done < <(items)
    echo "== runner: $(flock -n "$Q/.lock" true 2>/dev/null && echo 'not running' || echo 'running')$(paused && echo ', PAUSED by hand')"
    echo "== now: $($GUARD check --window 3)"
    [ -f "$Q/interruptions.txt" ] && { echo "== interruptions: $(wc -l < "$Q/interruptions.txt") (last 3)"; tail -3 "$Q/interruptions.txt" | sed 's/^/  /'; }
    [ -f "$Q/PUBLISHED" ] && echo "== prediction marked published: $(cat "$Q/PUBLISHED")"
    crontab -l 2>/dev/null | grep -q "$TAG" && echo "== autostart after reboot: ON" || echo "== autostart after reboot: off (run ./r7_queue.sh setup)"
    echo "== last log lines"; tail -5 "$LOG" 2>/dev/null | sed 's/^/  /'
}

cmd_start() {
    rm -f "$HOME/STOP_R7"
    if command -v tmux >/dev/null; then
        tmux has-session -t r7 2>/dev/null && { echo "already running in tmux session r7 (tmux attach -t r7)"; return; }
        tmux new-session -d -s r7 "bash -c 'cd $SUITE_DIR && ./r7_queue.sh run >> $Q/runner.out 2>&1'"
        echo "started in tmux session r7 (watch: tail -f r7_queue.log)"
    else
        nohup ./r7_queue.sh run >> "$Q/runner.out" 2>&1 &
        echo "started (watch: tail -f r7_queue.log)"
    fi
}

cmd_stop() {
    touch "$HOME/STOP_R7"
    local pid; pid=$(cat "$Q/current.pid" 2>/dev/null)
    [ -n "$pid" ] && { $GUARD tree "$pid" CONT >/dev/null; $GUARD tree "$pid" TERM >/dev/null; sleep 15; $GUARD tree "$pid" KILL >/dev/null; }
    tmux kill-session -t r7 2>/dev/null; pkill -f "r7_queue.sh run" 2>/dev/null; pkill -f "r7_guard.py log" 2>/dev/null
    rm -f "$Q/current" "$Q/current.pid"
    echo "stopped. ./r7_queue.sh start continues (the interrupted run is repeated). The autostart stays on"
    echo "but will not start while ~/STOP_R7 exists; 'start' removes it."
}

cmd_setup() {
    [ -t 0 ] || { echo "run setup from a terminal (it asks for the sudo password)"; exit 1; }
    local me; me=$(id -un)
    echo "1/4 boot settings: RAPL energy counters readable, governor = performance"
    sudo tee /etc/tmpfiles.d/thesis-benchmark.conf >/dev/null <<'EOF'
# thesis benchmark (r7_queue.sh setup); remove this file when finished
z /sys/class/powercap/intel-rapl:*/energy_uj 0444 - - -
z /sys/class/powercap/intel-rapl:*:*/energy_uj 0444 - - -
w /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor - - - - performance
EOF
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/thesis-benchmark.conf 2>/dev/null || true
    echo "2/4 passwordless sudo ONLY for the measurement tools (so it works after a reboot without you)"
    local list="" t
    for t in true ipmitool perf turbostat dmidecode dmesg lspci; do
        p=$(type -P $t 2>/dev/null || ls /usr/sbin/$t 2>/dev/null); [ -n "$p" ] && list="$list, $p"   # type -P: a path, never a shell builtin
    done
    pk=$(type -P pkill)
    [ -n "$pk" ] && list="$list, $pk -INT -f turbostat*, $pk -INT -f perf stat*"   # round5 stops its own monitors only
    list=${list#, }
    echo "$me ALL=(root) NOPASSWD: $list" | sudo tee /etc/sudoers.d/thesis-benchmark >/dev/null
    sudo chmod 440 /etc/sudoers.d/thesis-benchmark
    sudo visudo -cf /etc/sudoers.d/thesis-benchmark || { sudo rm -f /etc/sudoers.d/thesis-benchmark; echo "sudoers check failed; removed"; }
    echo "   $(cat /etc/sudoers.d/thesis-benchmark 2>/dev/null)"
    echo "3/4 autostart: 10 min after every boot, and a watchdog every 30 min (does nothing if already running)"
    local run="cd $SUITE_DIR && ./r7_queue.sh start >/dev/null 2>&1"
    ( crontab -l 2>/dev/null | grep -v "$TAG"
      echo "@reboot sleep 600 && [ ! -e \$HOME/STOP_R7 ] && $run $TAG"
      echo "*/30 * * * * [ ! -e \$HOME/STOP_R7 ] && $run $TAG" ) | crontab -
    crontab -l | grep "$TAG" | sed 's/^/   /'
    echo "4/4 power comes back -> server switches on by itself (BMC power-restore policy)"
    if command -v ipmitool >/dev/null; then
        sudo ipmitool chassis policy always-on >/dev/null 2>&1 && echo "   set: always-on" || echo "   could not set it; set 'Power restore policy = Always on / Restore' in XClarity (BMC) or BIOS"
        sudo ipmitool chassis status 2>/dev/null | grep -i "restore" | sed 's/^/   /'
    else
        echo "   ipmitool missing: set 'Power restore policy = Always on / Restore' in XClarity (BMC) or BIOS"
    fi
    echo "Done. Now: ./r7_queue.sh start"
}

cmd_disable() {
    crontab -l 2>/dev/null | grep -v "$TAG" | crontab -
    echo "autostart removed. At the very end also: sudo rm /etc/sudoers.d/thesis-benchmark /etc/tmpfiles.d/thesis-benchmark.conf"
}

case "${1:-}" in
    run) cmd_run ;;
    start) cmd_start ;;
    status) cmd_status ;;
    stop) cmd_stop ;;
    pause) touch "$Q/PAUSE"; echo "paused: the current measurement is stopped within ${POLL}s (a build is frozen); ./r7_queue.sh resume" ;;
    resume) rm -f "$Q/PAUSE"; echo "resumed (continues after ${QUIET_MIN} quiet minutes)" ;;
    published) [ -f "$SUITE_DIR/results_round7/predictions/predictions.json" ] || { echo "no prediction yet (results_round7/predictions); nothing marked"; exit 1; }
               date -Is > "$Q/PUBLISHED"; echo "noted: the sweep design may now run on the card" ;;
    setup) cmd_setup ;;
    disable) cmd_disable ;;
    redo) [ -n "${2:-}" ] && rm -f "$Q/$2.done" "$Q/$2.fails" && echo "will run $2 again" ;;
    *) sed -n 2,25p "$0"; exit 1 ;;
esac
