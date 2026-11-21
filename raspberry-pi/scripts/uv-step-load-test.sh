#!/bin/bash
# uv-step-load-test.sh - measure how much CPU load the power supply can carry.
#
# PROPOSAL ONLY. Run it yourself, while you are at the Pi, AFTER changing the
# power supply or cable. It deliberately pushes the Pi towards an undervoltage
# event, and an undervoltage during heavy SD writes can corrupt the card, so:
#   - it only burns CPU (sha256sum of /dev/zero), it writes nothing to the SD card;
#   - it stops at the first step that triggers an event;
#   - stop the test any time with Ctrl+C (all load processes are killed).
#
# Usage on the Pi:  bash uv-step-load-test.sh [seconds_per_step]   (default 60)
# It runs 1, 2, 3, then 4 busy cores at whatever clock the governor allows.
# For a full-speed test, run it after switching the governor back to ondemand.

STEP=${1:-60}
REST=30
pids=()
cleanup() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2>/dev/null; wait 2>/dev/null; }
trap 'cleanup; echo; echo "stopped"; exit 1' INT TERM

count() { journalctl -k -b --no-pager 2>/dev/null | grep -c 'Undervoltage detected'; }

echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor) start flags: $(vcgencmd get_throttled)"
base=$(count)
for n in 1 2 3 4; do
  echo -n "step $n core(s) for ${STEP}s ... "
  pids=()
  for i in $(seq 1 $n); do timeout "$STEP" sha256sum /dev/zero >/dev/null & pids+=($!); done
  peak=0
  for s in $(seq 1 "$STEP"); do
    sleep 1
    c=$(( $(vcgencmd measure_clock arm | cut -d= -f2) / 1000000 ))
    [ $c -gt $peak ] && peak=$c
    if [ "$(cat /sys/class/hwmon/hwmon1/in0_lcrit_alarm 2>/dev/null)" = "1" ]; then
      cleanup
      echo "UNDERVOLTAGE after ${s}s at up to ${peak} MHz. The supply cannot carry ${n} busy core(s)."
      echo "events this boot: before=$base now=$(count)"
      exit 0
    fi
  done
  wait 2>/dev/null
  echo "ok (clock up to ${peak} MHz, events this boot: $(count))"
  [ $n -lt 4 ] && sleep $REST
done
echo "Passed all steps. Final flags: $(vcgencmd get_throttled) (0x0 after a reboot is the clean baseline)"
