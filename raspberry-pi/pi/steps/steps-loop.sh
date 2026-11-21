#!/data/data/com.termux/files/usr/bin/bash
# Termux loop: report the phone's step counter to the Pi, forever.
# The Pi's reply says how long to wait before the next report (normal and "while walking"
# intervals are settings on http://steps.lan) and whether to sound the alarm (last reminder
# level; type, phrase and volume are settings on the page), so this script never needs editing.
# Needs ~/.steps-env with PI=http://192.168.0.250:8097 and TOKEN=...
# Fetched from the Pi: curl -fsS http://192.168.0.250:8097/phone.sh -o ~/steps-loop.sh
# Every step has a timeout (one stuck Termux command must not freeze the loop) and what it does
# is written to ~/steps-loop.log (kept under about 200 KB).
. ~/.steps-env
LOG=~/steps-loop.log
log() { echo "$(date +%H:%M:%S) $*" >> "$LOG"; }

alarm() {
  # $1 = phrase, $2 = volume in percent of the alarm stream's maximum, $3 = "timer" or "speech"
  # (all three come from the Pi). Both kinds ring on the ALARM volume, so they are heard with the
  # ringer silent; only a Do Not Disturb that blocks alarms mutes them.
  say=${1:-Time to walk}; pct=${2:-50}; mode=${3:-timer}
  mx=$(timeout 10 termux-volume 2>/dev/null | tr -d ' \n' | grep -o '"stream":"alarm","volume":[0-9]*,"max_volume":[0-9]*' | grep -o '[0-9]*$')
  if [ -n "$mx" ]; then
    v=$(( mx * pct / 100 )); [ "$v" -lt 1 ] && v=1
    timeout 10 termux-volume alarm "$v" >/dev/null 2>&1; log "alarm volume $v of $mx rc=$?"
  else
    log "alarm volume: no alarm stream found in termux-volume output"
  fi
  if [ "$mode" = "speech" ]; then
    timeout 5 termux-vibrate -f -d 600 >/dev/null 2>&1 &
    log "speaking: $say"
    timeout 25 termux-tts-speak -s ALARM "$say" >> "$LOG" 2>&1
    log "tts rc=$? (124 = timed out)"
  else
    # A 1-second timer in the Clock app, labelled with the phrase. Rings like any timer.
    # Needs Termux to have "Display over other apps" (Android blocks starting it from the background).
    timeout 15 am start -a android.intent.action.SET_TIMER --ei android.intent.extra.alarm.LENGTH 1 \
      --es android.intent.extra.alarm.MESSAGE "$say" --ez android.intent.extra.alarm.SKIP_UI true >> "$LOG" 2>&1
    log "timer '$say' requested rc=$? (124 = timed out)"
  fi
}

while true; do
  if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ]; then tail -n 300 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi
  wait=60
  out=$(timeout 20 termux-sensor -s "Step Counter" -n 1 2>/dev/null)
  n=$(printf '%s' "$out" | grep -A2 '"values"' | grep -oE '[0-9]+(\.[0-9]+)?' | head -1)
  if [ -n "$n" ]; then
    reply=$(curl -fsS -m 10 "$PI/steps?steps=$n&t=$TOKEN" 2>&1)
    log "steps=$n reply=$reply"
    w=$(printf '%s' "$reply" | grep -oE '"next_s": *[0-9]+' | grep -oE '[0-9]+$')
    [ -n "$w" ] && wait=$w
    if printf '%s' "$reply" | grep -qE '"alarm": *1'; then
      say=$(printf '%s' "$reply" | grep -oE '"say": *"[^"]*"' | sed 's/^"say": *"//; s/"$//')
      pct=$(printf '%s' "$reply" | grep -oE '"vol": *[0-9]+' | grep -oE '[0-9]+$')
      mode=$(printf '%s' "$reply" | grep -oE '"mode": *"[a-z]+"' | grep -oE '[a-z]+"$' | tr -d '"')
      alarm "$say" "$pct" "$mode"
    fi
  else
    log "no step counter value (termux-sensor gave: $out)"
  fi
  sleep "$wait"
done
