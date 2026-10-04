#!/usr/bin/env bash
# =============================================================================
#  r56_queue.sh - runs every remaining round-5 and round-6 step unattended, and politely:
#    * it measures only while nobody else uses the server. When another user starts working, a running
#      measurement is stopped at once (the unfinished run and any run that ended in the last 2 minutes are
#      thrown away and repeated later); the xclbin build is only frozen (SIGSTOP) and continued afterwards.
#      It waits until the server has been quiet for QUIET_MIN minutes (default 10), then continues.
#    * after a power cut or reboot it starts again by itself (crontab @reboot + a 30-minute watchdog);
#      finished runs are never repeated.
#    * the day-to-day sessions s2 and s3 are started at least 20 h after the previous session, and the
#      card runs of the new kernels only after you confirmed that the prediction is published.
#
#  One-time setup (asks for your sudo password once):
#     ./r56_queue.sh setup          RAPL readable + governor at boot, passwordless sudo for ipmitool/perf/
#                                   turbostat/dmidecode/dmesg only, autostart after reboot, BMC power-restore
#  Then:
#     ./r56_queue.sh start          start now (in tmux session 'r56'; safe to run twice)
#     ./r56_queue.sh status         what is done, what runs, who is active, interruptions
#     ./r56_queue.sh published      after you pushed/e-mailed results_round6/predictions (unlocks 'measure')
#     ./r56_queue.sh pause|resume   hold everything by hand (e.g. before you need the machine)
#     ./r56_queue.sh stop           stop now (the current run is repeated next time)
#     ./r56_queue.sh disable        remove the autostart (at the end)
#  Log: r56_queue.log   Activity log (other users, every 2 s): results_round6/activity.csv
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SUITE_DIR" || exit 1
Q="$SUITE_DIR/.r56_queue"; mkdir -p "$Q"
LOG="$SUITE_DIR/r56_queue.log"
GUARD="python3 $SUITE_DIR/r56_guard.py"
QUIET_MIN=${QUIET_MIN:-10}
QUIET_S=${QUIET_S:-$((QUIET_MIN * 60))}
GAP_H=${GAP_H:-20}                 # hours between measurement sessions s1 -> s2 -> s3
GAP_S=${GAP_S:-$((GAP_H * 3600))}
POLL=${POLL:-5}
PIN=${PIN:-taskset -c 95}   # the driver runs on CPU 95, as in the guides
WAIT_POLL=${WAIT_POLL:-30}
IDLE_POLL=${IDLE_POLL:-600}
CARD_WAIT=${CARD_WAIT:-90}
TAG="# r56-queue-autostart"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# ---- the queue: id | command | mode (measure = stop and repeat, build = freeze and continue) | gate --------
QUEUE="
r5_check    | ./round5_measurements.sh check        | measure | -
r5_hygiene  | ./round5_measurements.sh hygiene      | measure | -
r5_s1       | ./round5_measurements.sh session s1   | measure | -
r5_wall     | ./round5_measurements.sh wall         | measure | -
r5_overlap  | ./round5_measurements.sh overlap      | measure | -
r5_mcperf   | ./round5_measurements.sh mcperf       | measure | -
r5_video    | ./round5_measurements.sh videoperf    | measure | -
r6_check    | ./round6_new_kernels.sh check         | measure | -
r6_membw    | ./round6_new_kernels.sh membw         | measure | -
r6_build    | ./round6_new_kernels.sh build         | build   | -
r6_design   | ./round6_new_kernels.sh design        | measure | -
r5_s2       | ./round5_measurements.sh session s2   | measure | after:r5_s1
r6_cputune  | ./round6_new_kernels.sh cputune       | measure | -
r6_cpu      | ./round6_new_kernels.sh cpu           | measure | -
r6_predict  | ./round6_new_kernels.sh predict       | measure | -
r6_mcnative | ./round6_new_kernels.sh mcnative      | measure | -
r5_s3       | ./round5_measurements.sh session s3   | measure | after:r5_s2
r6_measure  | ./round6_new_kernels.sh measure       | measure | published
r5_pack     | ./round5_measurements.sh pack         | measure | done:r5_s3,r6_measure,r6_mcnative
r6_pack     | ./round6_new_kernels.sh pack          | measure | done:r5_s3,r6_measure,r6_mcnative
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

paused() { [ -f "$Q/PAUSE" ] || [ -f "$HOME/STOP_R56" ]; }
LAST_BUSY=0
busy() {
    $GUARD check --window 10 > "$Q/guard.txt" 2>&1
    [ $? = 1 ] || return 1
    LAST_BUSY=$(date +%s)
    return 0
}

wait_quiet() {   # returns once nobody else has been active for QUIET_S seconds (and there is no manual pause)
    local said=0
    while :; do
        if paused; then
            [ $said = 1 ] || { say "paused by hand ($Q/PAUSE or ~/STOP_R56); waiting"; said=1; }
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
    for f in $(find "$SUITE_DIR/results_round5/raw" "$SUITE_DIR/results_round6/raw" -name DONE -newermt "2 minutes ago" 2>/dev/null); do
        rm -f "$f"; n=$((n + 1)); echo "$(date -Is) $(dirname "$f")" >> "$Q/invalidated_runs.txt"
    done
    [ $n -gt 0 ] && say "  $n run(s) that ended in the last 2 minutes will be repeated"
}

run_item() {   # run_item <id> <command> <mode>; returns 0 when the step finished
    local id=$1 cmd=$2 mode=$3 pid rc
    wait_quiet
    say "START $id: $cmd"
    echo "$id" > "$Q/current"
    setsid $PIN bash -c "$cmd" >> "$Q/$id.out" 2>&1 &
    pid=$!
    echo "$pid" > "$Q/current.pid"
    while kill -0 "$pid" 2>/dev/null; do
        sleep "$POLL"
        kill -0 "$pid" 2>/dev/null || break
        if paused || busy; then
            local why; why=$(paused && echo "manual pause" || cat "$Q/guard.txt")
            if [ "$mode" = build ]; then
                say "  freeze $id ($why)"
                $GUARD tree "$pid" STOP >/dev/null
                wait_quiet
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
    flock -n 9 || { echo "already running (./r56_queue.sh status)"; exit 0; }
    # shellcheck disable=SC1091
    [ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
    rm -f "$Q/current" "$Q/current.pid"          # left over from a power cut: that step simply runs again
    say "queue runner started (pid $$, $(pending) item(s) pending)"
    sudo -n true 2>/dev/null || say "WARNING: passwordless sudo is not working - wall power, perf and turbostat will be skipped (run ./r56_queue.sh setup again)"
    LAST_BUSY=$(date +%s)          # after a (re)boot: first make sure the server has been quiet for a while
    wait_for_card
    $GUARD log "$SUITE_DIR/results_round6/activity.csv" 2>/dev/null &
    local ACT=$!
    trap 'kill $ACT 2>/dev/null' EXIT
    mkdir -p "$SUITE_DIR/results_round6"
    while [ "$(pending)" -gt 0 ]; do
        local it
        if ! it=$(next_item); then
            if [ -f "$Q/r6_predict.done" ] && [ ! -f "$Q/PUBLISHED" ] && [ ! -f "$Q/r6_measure.done" ]; then
                [ -f "$Q/.told_publish" ] || { say "WAITING FOR YOU: publish results_round6/predictions (guide step 4), then run: ./r56_queue.sh published"; touch "$Q/.told_publish"; }
            fi
            sleep "$IDLE_POLL"; continue
        fi
        IFS='|' read -r id cmd mode <<< "$it"
        run_item "$id" "$cmd" "$mode" || sleep "$WAIT_POLL"
    done
    say "ALL DONE. Upload the newest results_round5_*.tar.gz and results_round6_*.tar.gz. (./r56_queue.sh disable removes the autostart)"
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
    crontab -l 2>/dev/null | grep -q "$TAG" && echo "== autostart after reboot: ON" || echo "== autostart after reboot: off (run ./r56_queue.sh setup)"
    echo "== last log lines"; tail -5 "$LOG" 2>/dev/null | sed 's/^/  /'
}

cmd_start() {
    rm -f "$HOME/STOP_R56"
    if command -v tmux >/dev/null; then
        tmux has-session -t r56 2>/dev/null && { echo "already running in tmux session r56 (tmux attach -t r56)"; return; }
        tmux new-session -d -s r56 "bash -c 'cd $SUITE_DIR && ./r56_queue.sh run >> $Q/runner.out 2>&1'"
        echo "started in tmux session r56 (watch: tail -f r56_queue.log)"
    else
        nohup ./r56_queue.sh run >> "$Q/runner.out" 2>&1 &
        echo "started (watch: tail -f r56_queue.log)"
    fi
}

cmd_stop() {
    touch "$HOME/STOP_R56"
    local pid; pid=$(cat "$Q/current.pid" 2>/dev/null)
    [ -n "$pid" ] && { $GUARD tree "$pid" CONT >/dev/null; $GUARD tree "$pid" TERM >/dev/null; sleep 15; $GUARD tree "$pid" KILL >/dev/null; }
    tmux kill-session -t r56 2>/dev/null; pkill -f "r56_queue.sh run" 2>/dev/null; pkill -f "r56_guard.py log" 2>/dev/null
    rm -f "$Q/current" "$Q/current.pid"
    echo "stopped. ./r56_queue.sh start continues (the interrupted run is repeated). The autostart stays on"
    echo "but will not start while ~/STOP_R56 exists; 'start' removes it."
}

cmd_setup() {
    [ -t 0 ] || { echo "run setup from a terminal (it asks for the sudo password)"; exit 1; }
    local me; me=$(id -un)
    echo "1/4 boot settings: RAPL energy counters readable, governor = performance"
    sudo tee /etc/tmpfiles.d/thesis-benchmark.conf >/dev/null <<'EOF'
# thesis benchmark (r56_queue.sh setup); remove this file when finished
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
    local run="cd $SUITE_DIR && ./r56_queue.sh start >/dev/null 2>&1"
    ( crontab -l 2>/dev/null | grep -v "$TAG"
      echo "@reboot sleep 600 && [ ! -e \$HOME/STOP_R56 ] && $run $TAG"
      echo "*/30 * * * * [ ! -e \$HOME/STOP_R56 ] && $run $TAG" ) | crontab -
    crontab -l | grep "$TAG" | sed 's/^/   /'
    echo "4/4 power comes back -> server switches on by itself (BMC power-restore policy)"
    if command -v ipmitool >/dev/null; then
        sudo ipmitool chassis policy always-on >/dev/null 2>&1 && echo "   set: always-on" || echo "   could not set it; set 'Power restore policy = Always on / Restore' in XClarity (BMC) or BIOS"
        sudo ipmitool chassis status 2>/dev/null | grep -i "restore" | sed 's/^/   /'
    else
        echo "   ipmitool missing: set 'Power restore policy = Always on / Restore' in XClarity (BMC) or BIOS"
    fi
    echo "Done. Now: ./r56_queue.sh start"
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
    pause) touch "$Q/PAUSE"; echo "paused: the current measurement is stopped within ${POLL}s (a build is frozen); ./r56_queue.sh resume" ;;
    resume) rm -f "$Q/PAUSE"; echo "resumed (continues after ${QUIET_MIN} quiet minutes)" ;;
    published) date -Is > "$Q/PUBLISHED"; echo "noted: card runs of the new kernels are now allowed" ;;
    setup) cmd_setup ;;
    disable) cmd_disable ;;
    redo) [ -n "${2:-}" ] && rm -f "$Q/$2.done" "$Q/$2.fails" && echo "will run $2 again" ;;
    *) sed -n 2,27p "$0"; exit 1 ;;
esac
