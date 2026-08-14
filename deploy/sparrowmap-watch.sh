#!/bin/bash
# Watch the hub's own health endpoint and act before a person notices.
#
# 🚨 WRITTEN AFTER A TWO-HOUR OUTAGE THAT NOTHING WAS WATCHING.
# The box ran a backup cron and a purge cron and nothing else. The failure
# signal existed the entire time - 105,768 log lines in four days - and there
# was no reader. The gap was never the data.
#
# Design notes, each one paid for:
#
#  * A TIMER, NOT A DAEMON. A long-running watchdog can hang silently and then
#    it is one more thing that needs watching. Each run here is bounded by
#    systemd (TimeoutStartSec) and either finishes or is killed.
#
#  * TWO STRIKES BEFORE ACTING. One failed poll is a network blip or a restart
#    in progress. Restarting on a single sample makes the watchdog the thing
#    that causes outages.
#
#  * A FLAP GUARD. If restarting does not fix it, restarting again will not
#    either, and a service that bounces every two minutes destroys the evidence
#    needed to diagnose it. After MAX_RESTARTS in an hour it stops acting and
#    keeps shouting.
#
#  * IT LOGS THE NUMBERS EVERY RUN, not just the failures. The descriptor
#    exhaustion that caused the outage was a 45-minute RAMP. Only recording
#    incidents would have captured the cliff and none of the slope.

set -uo pipefail

URL="${SPARROW_HEALTH_URL:-https://map.sparrowmap.com/api/health}"
SERVICE="sparrowmap.service"
STATE="/var/lib/sparrowmap-watch"
LOG="/opt/sparrowmap/logs/watch.log"
MAX_RESTARTS=3          # per hour
WARN_PCT=80             # descriptor headroom worth shouting about

mkdir -p "$STATE" "$(dirname "$LOG")"
FAILS="$STATE/consecutive_fails"
[ -f "$FAILS" ] || echo 0 > "$FAILS"

say() { echo "$(date -Is) $*" >> "$LOG"; }

BODY=$(curl -fsS --max-time 10 "$URL" 2>/dev/null)
CURL_RC=$?

# jq is not assumed - this box has no reason to carry it for four fields.
field() { echo "$BODY" | grep -o "\"$1\": *[^,}]*" | head -1 | sed 's/.*: *//;s/"//g'; }

if [ $CURL_RC -ne 0 ]; then
    STATUS="unreachable(curl=$CURL_RC)"
    HEALTHY=0
else
    OK=$(field ok); PCT=$(field fd_used_pct); THR=$(field threads); DB=$(field db)
    STATUS="ok=$OK db=$DB fd=${PCT}% threads=$THR"
    [ "$OK" = "true" ] && HEALTHY=1 || HEALTHY=0
    # Shout about the ramp even while everything still answers. This is the
    # whole point: the cliff is not the first thing that happens.
    if [ -n "${PCT:-}" ] && [ "${PCT%%.*}" -ge "$WARN_PCT" ] 2>/dev/null; then
        say "WARN  descriptors at ${PCT}% of the limit - $STATUS"
    fi
fi

if [ "$HEALTHY" = "1" ]; then
    [ "$(cat "$FAILS")" != "0" ] && say "recovered - $STATUS"
    echo 0 > "$FAILS"
    say "ok    $STATUS"
    exit 0
fi

N=$(( $(cat "$FAILS") + 1 ))
echo "$N" > "$FAILS"
say "FAIL  ($N) $STATUS"

# One bad sample is a blip. Two is a problem.
[ "$N" -lt 2 ] && exit 0

# Flap guard: count restarts in the last hour.
HOUR=$(date +%Y%m%d%H)
RC_FILE="$STATE/restarts_$HOUR"
[ -f "$RC_FILE" ] || echo 0 > "$RC_FILE"
RC=$(cat "$RC_FILE")
if [ "$RC" -ge "$MAX_RESTARTS" ]; then
    say "GIVING UP restarting: $RC restarts this hour already and it is still "\
"failing. This needs a human - restarting again only destroys the evidence."
    exit 1
fi

# Drop previous hours' counters first, then record this one, so the directory
# does not grow a file per hour for ever.
find "$STATE" -name 'restarts_*' ! -name "restarts_$HOUR" -delete 2>/dev/null
echo $(( RC + 1 )) > "$RC_FILE"
say "RESTARTING $SERVICE (attempt $(( RC + 1 )) this hour) - $STATUS"
systemctl restart "$SERVICE"
sleep 5
AFTER=$(curl -fsS --max-time 10 "$URL" 2>/dev/null)
if echo "$AFTER" | grep -q '"ok": *true'; then
    say "restart fixed it - $AFTER"
    echo 0 > "$FAILS"
else
    say "restart did NOT fix it - $AFTER"
fi
