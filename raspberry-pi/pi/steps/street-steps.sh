#!/data/data/com.termux/files/usr/bin/bash
# Street phone: log the step counter and send the backlog to the Pi when it is reachable.
# No loop and no wake-lock: Android's job scheduler starts this script (see street-setup.sh), so the
# phone does nothing in between. Out of the house the samples pile up in ~/.steps-queue; on the home
# Wi-Fi they are sent in one request and the Pi credits the steps and resets the sitting timer.
# Needs ~/.steps-env with PI=http://192.168.0.250:8097 and TOKEN=...
# Fetched from the Pi: curl -fsS http://192.168.0.250:8097/street.sh -o ~/street-steps.sh
. ~/.steps-env
Q=~/.steps-queue
LOG=~/street-steps.log
log() { echo "$(date +%m-%d\ %H:%M:%S) $*" >> "$LOG"; }

# Output goes to a file, not a $(...) pipe: if termux-sensor hangs, its children could keep a pipe open and
# block the shell forever; -k makes sure it is killed.
TMP=$(mktemp)
timeout -k 2 20 termux-sensor -s "Step Counter" -n 1 > "$TMP" 2>/dev/null < /dev/null
n=$(grep -A2 '"values"' "$TMP" | grep -oE '[0-9]+(\.[0-9]+)?' | head -1 | cut -d. -f1)
rm -f "$TMP"
[ -n "$n" ] && echo "$(date +%s) $n" >> "$Q" || log "no step counter reading"
[ -s "$Q" ] || exit 0
tail -n 700 "$Q" > "$Q.tmp" && mv "$Q.tmp" "$Q"        # keep about a week at one sample per 15 min

cnt=$(wc -l < "$Q")
body=$(head -n "$cnt" "$Q" | awk 'BEGIN{printf "{\"samples\":["} {printf "%s[%s,%s]", (NR>1?",":""), $1, $2} END{print "]}"}')
# Battery goes along with the upload (read only here, so nothing extra while out of the house). The Pi warns
# the main phone when it is low and not charging. Output goes to a file for the same reason as the sensor.
BT=$(mktemp)
timeout -k 2 10 termux-battery-status > "$BT" 2>/dev/null < /dev/null
pct=$(grep -oE '"percentage": *[0-9]+' "$BT" | grep -oE '[0-9]+$')
grep -qE '"status": *"(CHARGING|FULL)"' "$BT" && plugged=true || plugged=false
rm -f "$BT"
[ -n "$pct" ] && body="${body%\}},\"battery\":{\"pct\":$pct,\"plugged\":$plugged}}"
if r=$(curl -fsS -m 10 --connect-timeout 4 -X POST -H "X-Token: $TOKEN" -H "Content-Type: application/json" \
       -d "$body" "$PI/steps/batch?device=street" 2>&1); then
  tail -n +$((cnt + 1)) "$Q" > "$Q.tmp"; mv "$Q.tmp" "$Q"    # sent: drop exactly what was sent
  log "sent $cnt samples: $r"
else
  log "Pi not reachable, $cnt samples kept"
fi
# keep the log small
[ "$(wc -c < "$LOG")" -gt 100000 ] && tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit 0
