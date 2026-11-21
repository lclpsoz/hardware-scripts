#!/bin/bash
out=/var/log/uv-events.log
journalctl -k -f -n 0 --no-pager | while read -r line; do
  case "$line" in
    *"Undervoltage detected"*)
      { echo "=== $(date -Is) $line"
        vcgencmd get_throttled
        cat /proc/loadavg
        top -b -n 1 -w 150 | head -14
        grep -E 'MemAvailable|Dirty|Writeback' /proc/meminfo
      } >> "$out" ;;
  esac
done
