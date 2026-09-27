#!/usr/bin/env bash
# Wait for the tune stage to finish, pause the campaign, run the MC 7-lane retry.
cd "$(dirname "$0")"
while [ ! -f campaign_state/tune.done ]; do sleep 120; done
sleep 60
touch "$HOME/STOP_CAMPAIGN"          # no autoresume while we build
pkill -f "run_all.sh" 2>/dev/null
pkill -f "campaign.sh" 2>/dev/null
sleep 15
echo "[$(date '+%F %T')] tune finished - campaign held, starting MC L7 retry" >> mc_l7_retry.log
./mc_l7_retry.sh >> mc_l7_retry.log 2>&1
echo "[$(date '+%F %T')] retry finished - send me mc_l7_retry.log" >> mc_l7_retry.log
