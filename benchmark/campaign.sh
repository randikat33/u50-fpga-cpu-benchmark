#!/usr/bin/env bash
# =============================================================================
#  campaign.sh - the WHOLE journal/thesis campaign with one command, resumable at any point.
#
#   stage 1  deps      check compilers, libraries, XRT, Vitis, Vision library
#   stage 2  programs  build the v2 CPU baselines and FPGA hosts            (build_v2.sh)
#   stage 3  tune      build every xclbin, search the best lane counts, check every
#                      design on the card, build the ablation xclbins           (tune_v2.sh)
#   stage 4  check     preflight of the benchmark                             (run_all.sh --check)
#   stage 5  quick     smoke campaign -> UPLOAD_ME_*.zip                      (run_all.sh --quick)
#   stage 6  pause     STOPS here so you can send the quick zip for checking
#                      (skip with --no-pause; continue later with --continue)
#   stage 7  full      full campaign, v2 + v1 key points                     (run_all.sh)
#   stage 8  analyze   final tables, figures, report and upload zip          (run_all.sh --analyze)
#
#  Resuming: every stage and every single build/measurement inside a stage is recorded.
#  After Ctrl-C, a crash or a power failure run   ./campaign.sh   again (or let
#  --setup-autoresume do it at boot); finished work is never repeated.
#
#  ./campaign.sh                    start or resume (in tmux:  tmux new -s campaign)
#  ./campaign.sh --no-pause         do not stop after the quick campaign
#  ./campaign.sh --continue         pass the pause (after the quick zip was checked)
#  ./campaign.sh --status           what is done, what is running, what is next
#  ./campaign.sh --setup-autoresume one-time, asks for sudo: permanent RAPL/governor
#                                   settings + restart the campaign automatically after a reboot
#  ./campaign.sh --disable-autoresume
#  ./campaign.sh --redo <stage>     forget one stage (e.g. after fixing something) and resume
#
#  Log: campaign.log     State: campaign_state/
# =============================================================================
set -u
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SUITE_DIR
cd "$SUITE_DIR"
source "$SUITE_DIR/config.sh"
CS="$SUITE_DIR/campaign_state"; mkdir -p "$CS"
LOGF="$SUITE_DIR/campaign.log"
STAGES=(deps programs tune check quick pause full analyze)
NOPAUSE=0; ACTION=run; REDO=""
[ -f "$CS/no_pause" ] && NOPAUSE=1
while [ $# -gt 0 ]; do
    case $1 in
        --no-pause) NOPAUSE=1; touch "$CS/no_pause" ;;
        --continue) touch "$CS/pause.done" ;;
        --resume) ;;
        --status) ACTION=status ;;
        --setup-autoresume) ACTION=setup ;;
        --disable-autoresume) ACTION=disable ;;
        --redo) REDO=$2; shift ;;
        -h|--help) sed -n 2,33p "$0"; exit 0 ;;
        *) echo "unknown option $1"; exit 1 ;;
    esac; shift
done
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOGF"; }

CRON_TAG="# thesis-campaign-autoresume"
cron_line() {
    local runner
    if command -v tmux >/dev/null; then
        runner="tmux new-session -d -s campaign \"bash -c 'cd $SUITE_DIR && ./campaign.sh >> $LOGF 2>&1'\""
    else
        runner="cd $SUITE_DIR && nohup ./campaign.sh >> $LOGF 2>&1 &"
    fi
    # 10 minutes after boot, and never if ~/STOP_CAMPAIGN exists (gives you time to log in and stop it)
    echo "@reboot sleep 600 && [ ! -e \$HOME/STOP_CAMPAIGN ] && $runner $CRON_TAG"
}

case $ACTION in
status)
    echo "== campaign stages"
    for s in "${STAGES[@]}"; do
        if [ -f "$CS/$s.done" ]; then st="done ($(cat "$CS/$s.done"))"
        elif [ -f "$CS/$s.failed" ]; then st="FAILED: $(cat "$CS/$s.failed")"
        else st="-"; fi
        printf "  %-9s %s\n" "$s" "$st"
    done
    [ -f "$CS/FINISHED" ] && echo "  campaign FINISHED $(cat "$CS/FINISHED")"
    if flock -n "$CS/.lock" true 2>/dev/null; then echo "  (not running now)"; else echo "  RUNNING now (tail -f campaign.log)"; fi
    crontab -l 2>/dev/null | grep -q "$CRON_TAG" && echo "  autoresume after reboot: ON" || echo "  autoresume after reboot: off"
    echo; echo "== design selection"; [ -f tune_state/SUMMARY.md ] && grep -E "SELECTED|^\| project" tune_state/SUMMARY.md || echo "  not started"
    echo; echo "== measurements finished (DONE files)"
    for r in results_quick results; do
        [ -d "$r" ] || continue
        printf "  %-14s %6s runs done, projects complete: %s\n" "$r" "$(find "$r" -name DONE | wc -l)" \
            "$(find "$r" -name PROJECT_DONE | sed "s|$r/||;s|/raw/| |;s|/PROJECT_DONE||" | tr '\n' ',' )"
    done
    ls -1t UPLOAD_ME_*.zip 2>/dev/null | head -3 | sed 's/^/  upload: /'
    exit 0 ;;
setup)
    [ -t 0 ] || { echo "run --setup-autoresume from a terminal (it asks for the sudo password)"; exit 1; }
    echo "Installing /etc/tmpfiles.d/thesis-benchmark.conf (RAPL energy counters readable, governor=performance at every boot)"
    sudo tee /etc/tmpfiles.d/thesis-benchmark.conf >/dev/null <<'EOF'
# thesis benchmark campaign (campaign.sh --setup-autoresume); remove this file when finished
z /sys/class/powercap/intel-rapl:*/energy_uj 0444 - - -
z /sys/class/powercap/intel-rapl:*:*/energy_uj 0444 - - -
w /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor - - - - performance
EOF
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/thesis-benchmark.conf 2>/dev/null || true
    cat /sys/class/powercap/intel-rapl:0/energy_uj >/dev/null 2>&1 && echo "  RAPL readable: yes" || echo "  RAPL readable: NO (check BIOS / intel_rapl module)"
    echo "  governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
    ( crontab -l 2>/dev/null | grep -v "$CRON_TAG"; cron_line ) | crontab -
    echo "Autoresume installed (your user crontab):"; crontab -l | grep "$CRON_TAG"
    echo
    echo "Also recommended once: BIOS 'restore on AC power loss' = Power On, and automatic"
    echo "login disabled is fine (cron runs without a login). Remove with --disable-autoresume."
    exit 0 ;;
disable)
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
    echo "autoresume removed. (sudo rm /etc/tmpfiles.d/thesis-benchmark.conf to drop the boot settings)"
    exit 0 ;;
esac

if [ -n "$REDO" ]; then
    rm -f "$CS/$REDO.done" "$CS/$REDO.failed"
    [ "$REDO" = tune ] && echo "note: finished tuning variants stay recorded in tune_state/ (delete tune_state/<project> to redo one)"
fi

exec 8>"$CS/.lock"
flock -n 8 || { echo "campaign.sh is already running (see campaign.log)"; exit 0; }
export UNATTENDED=1

tools() {
    for f in "$VITIS_SETTINGS" "$XRT_SETUP"; do
        # shellcheck disable=SC1090
        [ -f "$f" ] && source "$f" >/dev/null 2>&1
    done
}
wait_for_card() {
    local i
    for i in $(seq 1 90); do
        xbutil examine 2>/dev/null | grep -qiE "u50" && return 0
        [ "$i" = 1 ] && say "waiting for the Alveo card to appear (xbutil examine) ..."
        sleep 10
    done
    say "card not visible after 15 min"; return 1
}
all_projects_done() {   # all_projects_done <results_root>
    local p ver miss=0
    for ver in v2 v1; do
        for p in 01_AES 02_Convolution 03_MC_Heston 04_Portfolio 05_LiveStream_Single 06_LiveStream_Multi; do
            [ -f "$1/$ver/raw/$p/PROJECT_DONE" ] || { echo "$ver/$p"; miss=1; }
        done
    done
    return $miss
}

run_stage() {
    local s=$1 rc=0 a
    case $s in
        deps)     ./build_v2.sh --deps --xclbin >> "$LOGF" 2>&1; rc=$? ;;
        programs) ./build_v2.sh >> "$LOGF" 2>&1; rc=$? ;;
        tune)     wait_for_card || return 1
                  # after a failed attempt (you fixed something), failed variants are built again
                  local retry=(); [ "$WAS_FAILED" = 1 ] && retry=(--retry-failed)
                  ./tune_v2.sh --ablation "${retry[@]}" >> "$LOGF" 2>&1; rc=$? ;;
        check)    wait_for_card || return 1
                  ./run_all.sh --check >> "$LOGF" 2>&1; rc=$? ;;
        quick|full)
                  local root=results opt=()
                  [ "$s" = quick ] && { root=results_quick; opt=(--quick); }
                  wait_for_card || return 1
                  for a in 1 2 3; do
                      ./run_all.sh "${opt[@]}" >> "$LOGF" 2>&1
                      missing=$(all_projects_done "$root" | tr '\n' ' ')
                      [ -z "$missing" ] && break
                      say "$s: attempt $a left incomplete projects: $missing"
                  done
                  if [ -n "$missing" ]; then
                      # keep going: the data that exists is analysed; the gaps are listed in REPORT_v2.md
                      say "$s: still incomplete after 3 attempts ($missing) - see campaign.log; continuing"
                      echo "$missing" > "$CS/$s.incomplete"
                  fi
                  rc=0 ;;
        pause)    if [ $NOPAUSE = 1 ]; then rc=0
                  else
                      say "PAUSED after the quick campaign. Upload $(ls -1t UPLOAD_ME_*.zip 2>/dev/null | head -1) for checking,"
                      say "then run:  ./campaign.sh --continue   (or start with --no-pause to never stop here)"
                      exit 0
                  fi ;;
        analyze)  ./run_all.sh --analyze >> "$LOGF" 2>&1; rc=$? ;;
    esac
    return $rc
}

say "campaign.sh started (host $(hostname))"
tools
for s in "${STAGES[@]}"; do
    [ -f "$CS/$s.done" ] && continue
    WAS_FAILED=0; [ -f "$CS/$s.failed" ] && WAS_FAILED=1
    rm -f "$CS/$s.failed"
    say "=== stage $s"
    if run_stage "$s"; then
        date '+%F %T' > "$CS/$s.done"
        say "=== stage $s finished"
    else
        echo "$(date '+%F %T') see campaign.log" > "$CS/$s.failed"
        say "=== stage $s FAILED - fix the cause (campaign.log, build_logs/, tune_state/), then run ./campaign.sh again"
        exit 1
    fi
done
date '+%F %T' > "$CS/FINISHED"
say "CAMPAIGN FINISHED. Upload: $(ls -1t UPLOAD_ME_*.zip 2>/dev/null | head -1)"
crontab -l 2>/dev/null | grep -q "$CRON_TAG" && { crontab -l | grep -v "$CRON_TAG" | crontab -; say "autoresume removed (campaign finished)"; }
exit 0
