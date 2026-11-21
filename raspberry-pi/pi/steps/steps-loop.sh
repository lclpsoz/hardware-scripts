#!/data/data/com.termux/files/usr/bin/bash
# Termux loop: report the phone's step counter to the Pi, forever.
# The Pi's reply says how long to wait before the next report (normal and "while walking"
# intervals are settings on http://steps.lan) and whether to sound the loud alarm
# (last reminder level, switch "Phone alarm" on the page), so this script never needs editing.
# Needs ~/.steps-env with PI=http://192.168.0.250:8097 and TOKEN=...
# Fetched from the Pi: curl -fsS http://192.168.0.250:8097/phone.sh -o ~/steps-loop.sh
. ~/.steps-env

alarm() {
  # $1 = phrase, $2 = volume in percent of the alarm stream's maximum (both come from the Pi).
  # Plays on the ALARM stream, so it is heard with the ringer silent; only a Do Not Disturb that
  # blocks alarms mutes it.
  say=${1:-Time to walk}; pct=${2:-50}
  if command -v termux-volume >/dev/null; then
    mx=$(termux-volume 2>/dev/null | tr -d ' \n' | grep -o '"stream":"alarm","volume":[0-9]*,"max_volume":[0-9]*' | grep -o '[0-9]*$')
    if [ -n "$mx" ]; then v=$(( mx * pct / 100 )); [ "$v" -lt 1 ] && v=1; termux-volume alarm "$v" >/dev/null 2>&1; fi
  fi
  command -v termux-vibrate >/dev/null && termux-vibrate -f -d 600 &
  command -v termux-tts-speak >/dev/null && termux-tts-speak -s ALARM "$say"
}

while true; do
  wait=60
  out=$(timeout 20 termux-sensor -s "Step Counter" -n 1 2>/dev/null)
  n=$(printf '%s' "$out" | grep -A2 '"values"' | grep -oE '[0-9]+(\.[0-9]+)?' | head -1)
  if [ -n "$n" ]; then
    reply=$(curl -fsS -m 10 "$PI/steps?steps=$n&t=$TOKEN" 2>/dev/null)
    w=$(printf '%s' "$reply" | grep -oE '"next_s": *[0-9]+' | grep -oE '[0-9]+$')
    [ -n "$w" ] && wait=$w
    if printf '%s' "$reply" | grep -qE '"alarm": *1'; then
      say=$(printf '%s' "$reply" | grep -oE '"say": *"[^"]*"' | sed 's/^"say": *"//; s/"$//')
      pct=$(printf '%s' "$reply" | grep -oE '"vol": *[0-9]+' | grep -oE '[0-9]+$')
      alarm "$say" "$pct"
    fi
  fi
  sleep "$wait"
done
