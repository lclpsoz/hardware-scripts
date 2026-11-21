#!/data/data/com.termux/files/usr/bin/bash
# Termux script: send the phone's cumulative step counter to the Pi.
# Needs Termux + Termux:API (apps) and `pkg install termux-api`.
# Run every 2 minutes (termux-job-scheduler or a loop under termux-wake-lock).
# Config in ~/.steps-env: PI=http://192.168.0.250:8097 and TOKEN=...
. ~/.steps-env
out=$(timeout 20 termux-sensor -s "Step Counter" -n 1 2>/dev/null)
n=$(printf '%s' "$out" | grep -A2 '"values"' | grep -oE '[0-9]+(\.[0-9]+)?' | head -1)
[ -n "$n" ] && curl -fsS -m 10 "$PI/steps?steps=$n&t=$TOKEN" >/dev/null
