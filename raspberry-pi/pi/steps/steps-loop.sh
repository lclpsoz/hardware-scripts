#!/data/data/com.termux/files/usr/bin/bash
# Termux loop: report the phone's step counter to the Pi, forever.
# The Pi's reply says how long to wait before the next report (normal and "while walking"
# intervals are settings on http://steps.lan), so this script never needs editing.
# Needs ~/.steps-env with PI=http://192.168.0.250:8097 and TOKEN=...
# Fetched from the Pi: curl -fsS http://192.168.0.250:8097/phone.sh -o ~/steps-loop.sh
. ~/.steps-env
while true; do
  wait=60
  out=$(timeout 20 termux-sensor -s "Step Counter" -n 1 2>/dev/null)
  n=$(printf '%s' "$out" | grep -A2 '"values"' | grep -oE '[0-9]+(\.[0-9]+)?' | head -1)
  if [ -n "$n" ]; then
    reply=$(curl -fsS -m 10 "$PI/steps?steps=$n&t=$TOKEN" 2>/dev/null)
    w=$(printf '%s' "$reply" | grep -oE '"next_s": *[0-9]+' | grep -oE '[0-9]+$')
    [ -n "$w" ] && wait=$w
  fi
  sleep "$wait"
done
