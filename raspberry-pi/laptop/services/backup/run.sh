#!/usr/bin/env bash
# Daily backup of the Pi to this laptop, with ntfy notifications.
#
#   run.sh            back up if the last good backup is older than 20 h (what the timer calls)
#   run.sh --force    back up now
#
# Called hourly by pi-backup.timer; most runs exit at once ("recent backup exists"). That is on
# purpose: if the laptop was off or away from home at the usual time, the next hour retries.
# A failure notifies at most once a day.
SERVICE=backup
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=../../lib/common.sh
. "$HERE/../../lib/common.sh"
REPO_SCRIPTS="$HERE/../../../scripts"

BACKUP_DIR=${BACKUP_DIR:-$HOME/backups/rasp-al-gaib-lite}
BACKUP_KEEP=${BACKUP_KEEP:-14}
MIN_GAP_H=20
FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1

lock
now=$(date +%s)

# Skip when a recent backup exists
last=$(state_get last_ok)
if [ "$FORCE" = 0 ] && [ -n "$last" ] && [ $((now - last)) -lt $((MIN_GAP_H * 3600)) ]; then
  exit 0
fi

# Failure notice: once per day at most
fail() {
  local msg=$1 today; today=$(date +%F)
  log "FAILED: $msg"
  if [ "$(state_get last_fail_notice)" != "$today" ]; then
    notify "Backup failed" "$msg" 4 "x,floppy_disk"
    state_set last_fail_notice "$today"
  fi
  exit 1
}

pi true 2>/dev/null || fail "The Pi is not reachable over ssh (is the laptop away from home?). Will retry every hour."

stamp=$(date +%Y-%m-%d_%H%M)
dest="$BACKUP_DIR/$stamp"
log "starting backup into $dest"
if ! bash "$REPO_SCRIPTS/backup-pi.sh" "$dest" >> "$PI_OPS_STATE/$SERVICE.log" 2>&1; then
  rm -rf "$dest"
  fail "backup-pi.sh failed; the partial folder was removed. Log: $PI_OPS_STATE/$SERVICE.log"
fi

# Keep the newest BACKUP_KEEP folders named like 2026-11-20_2138
mapfile -t all < <(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}' | sort)
if [ "${#all[@]}" -gt "$BACKUP_KEEP" ]; then
  for old in "${all[@]:0:${#all[@]}-BACKUP_KEEP}"; do log "pruning $old"; rm -rf "$old"; done
fi

size=$(du -sm "$dest" | cut -f1)
kept=$(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d | wc -l)
state_set last_ok "$now"

# Tell the Pi, so it can alert if the laptop stops backing up (alerts container reads this file)
pi "mkdir -p ~/ops-state && printf '{\"ts\": %s, \"ok\": true, \"name\": \"%s\", \"size_mb\": %s}' $now '$stamp' $size > ~/ops-state/.last-backup.tmp && mv ~/ops-state/.last-backup.tmp ~/ops-state/last-backup.json" \
  || log "could not write the stamp on the Pi (backup itself is fine)"

notify "Pi backup OK" "$size MB saved to $dest. Keeping the last $kept." 2 "white_check_mark,floppy_disk"

# Watchdog: is the alerts service on the Pi still alive? (it writes a heartbeat file every ~10 min)
hb=$(pi 'stat -c %Y ~/alerts/data/heartbeat 2>/dev/null' || true)
if [ -z "$hb" ] || [ $((now - hb)) -gt 1800 ]; then
  notify "Pi alerts are not reporting" "The alerts service on the Pi has not written its heartbeat for over 30 min, so you may not get Pi notifications. Check: ssh pi docker ps; docker logs alerts" 4 "warning"
fi
log "done: $size MB, $kept backups kept"
