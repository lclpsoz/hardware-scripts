#!/bin/bash
# pi-checkup.sh - read-only health snapshot of the Pi (rasp-al-gaib-lite).
#
# Run from the laptop:   ssh pi 'bash -s' < raspberry-pi/scripts/pi-checkup.sh
# Optional UI timing:    ssh pi 'bash -s -- --ui' < raspberry-pi/scripts/pi-checkup.sh
#
# Changes nothing. Needs no sudo (uses the docker group and world-readable files).
# Kept deliberately light: on the current power supply even one busy core for a
# minute can trigger an undervoltage event, so there are no heavy database scans.

UI=0; [ "${1:-}" = "--ui" ] && UI=1
hr() { printf '\n== %s\n' "$1"; }

hr "Board and uptime"
tr -d '\0' < /proc/device-tree/model; echo
uptime

hr "Power and CPU"
vcgencmd get_throttled
echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor) clock=$(( $(vcgencmd measure_clock arm | cut -d= -f2) / 1000000 )) MHz $(vcgencmd measure_temp)"
since24=$(journalctl -k --since "24 hours ago" --no-pager 2>/dev/null | grep -c 'Undervoltage detected')
since7d=$(journalctl -k --since "7 days ago" --no-pager 2>/dev/null | grep -c 'Undervoltage detected')
echo "undervoltage events: last 24 h = $since24, last 7 days = $since7d (journal starts $(journalctl --list-boots --no-pager 2>/dev/null | awk 'NR==2{print $4, $5}'))"
journalctl -k --since "24 hours ago" --no-pager -o short-iso 2>/dev/null | grep 'Undervoltage detected' | tail -5 | cut -c1-25 | sed 's/^/  /'

hr "Memory and disk"
free -h | sed -n '1,3p'
df -h / | tail -1
read -r _ _ _ _ _ _ wsec _ < /sys/block/mmcblk0/stat
up=$(cut -d. -f1 /proc/uptime)
echo "SD writes since boot: $((wsec / 2048)) MiB, about $((wsec / 2048 * 86400 / up)) MiB/day"

hr "Containers"
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'

hr "NetAlertX CPU cap and throttling"
ID=$(docker inspect -f '{{.Id}}' netalertx 2>/dev/null)
CG=/sys/fs/cgroup/system.slice/docker-$ID.scope
if [ -r "$CG/cpu.stat" ]; then
  echo "cpu.max: $(cat $CG/cpu.max)  (50000 100000 = half a core)"
  awk '/nr_periods/{p=$2} /nr_throttled/{t=$2} END{if (p) printf "throttled in %.0f%% of scheduler periods since start\n", 100*t/p}' $CG/cpu.stat
fi
if [ $UI = 1 ]; then
  for p in /devices.php /network.php; do
    printf "  %-14s " "$p"; curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' "http://127.0.0.1:20211$p"
  done
fi

hr "Pi-hole"
docker exec pihole pihole version 2>/dev/null | tr '\n' ';'; echo
docker exec pihole pihole-FTL sqlite3 -readonly /etc/pihole/pihole-FTL.db \
  "SELECT datetime(timestamp,'unixepoch','localtime') || '  ' || type || '  ' || message FROM message ORDER BY timestamp DESC LIMIT 5;" 2>/dev/null | sed 's/^/  /'
echo "concurrency warnings in current FTL.log: $(docker exec pihole sh -c 'grep -a -c "concurrent DNS queries" /var/log/pihole/FTL.log' 2>/dev/null)"
echo "rate-limit messages in current FTL.log: $(docker exec pihole sh -c 'grep -a -c -i "rate-limit" /var/log/pihole/FTL.log' 2>/dev/null)"
echo "Default group adlists: $(docker exec pihole pihole-FTL sqlite3 -readonly /etc/pihole/gravity.db 'SELECT count(*) FROM adlist_by_group WHERE group_id=0;' 2>/dev/null) (0 means devices outside the TV group get no blocklist)"
docker exec pihole sh -c 'ls -la /etc/pihole/pihole-FTL.db' 2>/dev/null | awk '{printf "FTL database size: %.0f MB\n", $5/1048576}'

hr "HDMI"
echo "connector: $(cat /sys/class/drm/card0-HDMI-A-1/status 2>/dev/null), EDID bytes: $(wc -c < /sys/class/drm/card0-HDMI-A-1/edid 2>/dev/null)"
echo "'Unknown ELD' kernel messages in last 24 h: $(journalctl -k --since '24 hours ago' --no-pager 2>/dev/null | grep -c 'Unknown ELD')"

hr "Updates"
echo "upgradable packages: $(apt list --upgradable 2>/dev/null | grep -c upgradable)"
