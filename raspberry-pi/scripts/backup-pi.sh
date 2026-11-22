#!/usr/bin/env bash
# Back up everything important on the Pi to this machine.
#
#   bash raspberry-pi/scripts/backup-pi.sh [destination-dir]
#
# Default destination: ~/backups/rasp-al-gaib-lite/<timestamp>/ (outside the repo, because it
# contains private data: Pi-hole history, NetAlertX device list, TLS keys, password hashes).
#
# What is saved (streamed over ssh, so nothing large lands on the SD card):
#   pi-home.tar.zst          compose projects (pihole, netalertx, beszel, proxy, netq, steps) with their data,
#                            plus ~/.ssh/authorized_keys and shell files. In a FULL backup Pi-hole's databases
#                            are consistent SQLite snapshots (etc-pihole/.bk/), not live files; a LIGHT backup
#                            leaves them out (see below).
#   beszel.tar.gz            Beszel hub data and keys (root-owned; live copy, restart-safe)
#   netalertx-volume.tar.gz  the NetAlertX docker volume (devices, scan history, settings)
#   pihole-teleporter.zip    Pi-hole's own export (settings, lists, local DNS), restorable in its web UI
#   pi-system.tar.gz         systemd units and drop-ins, uv scripts, journald and docker config,
#                            boot config, logrotate, the uv event log
#   docker-state.txt         containers, images and volumes (to rebuild by hand)
#   SHA256SUMS               checksums, verified at the end
#
# Safe to run while everything is up. Written to keep the load on a Pi with a marginal power
# supply low (undervoltage events, see 01-undervoltage.md):
#   - Heavy steps run at the lowest CPU and disk priority (nice 19, ionice idle).
#   - Modes: FULL includes the Pi-hole databases (pihole-FTL.db 91 MB of query history, gravity.db 20 MB),
#     LIGHT leaves them out (gravity.db is rebuilt with `pihole -g`; settings, lists and local DNS are in
#     pihole-teleporter.zip and etc-pihole/pihole.toml). BACKUP_MODE=auto (default) does a FULL backup
#     when no backup folder next to this one has a FULL marker younger than BACKUP_FULL_EVERY_DAYS (7),
#     else LIGHT. Force one with BACKUP_MODE=full or BACKUP_MODE=light.
#   - pi-home is compressed on the Pi with zstd -1 (gzip -6 needs 5 times the CPU at 600 MHz; measured
#     22 Nov: 7.9 s against 1.8 s per 20 MB). Compressing on the laptop instead did not help.
#   - Everything coming from the Pi goes through scripts/throttle.py at BACKUP_RATE_KB (default 2048
#     KB/s, 0 = no limit), so the Pi's tar, compressor and ssh run at a low duty cycle and the
#     network link is not saturated.
# Restore: older backups hold pi-home.tar.gz (tar xzf); newer ones pi-home.tar.zst (tar --zstd -xf).
set -euo pipefail

HOST=${PI_HOST:-pi}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RATE_KB=${BACKUP_RATE_KB:-2048}
FULL_EVERY_DAYS=${BACKUP_FULL_EVERY_DAYS:-7}
MODE=${BACKUP_MODE:-auto}
STAMP=$(date +%Y-%m-%d_%H%M)
DEST=${1:-$HOME/backups/rasp-al-gaib-lite/$STAMP}
mkdir -p "$DEST"
chmod 700 "$DEST" "$(dirname "$DEST")" 2>/dev/null || true

if [ "$MODE" = auto ]; then
  if find "$(dirname "$DEST")" -maxdepth 2 -name FULL -mtime -"$FULL_EVERY_DAYS" 2>/dev/null | grep -q .; then
    MODE=light
  else
    MODE=full
  fi
fi
case "$MODE" in full|light) ;; *) echo "BACKUP_MODE must be auto, full or light" >&2; exit 2 ;; esac

lo() { ssh "$HOST" "nice -n 19 ionice -c3 $*"; }   # run on the Pi at lowest priority

rx() { python3 -I "$SCRIPT_DIR/throttle.py" "$RATE_KB"; }   # limit the rate at which data is taken from the Pi

# The snapshot folder only exists in a FULL backup, so a LIGHT one needs no docker exec to remove it.
cleanup() { [ "$MODE" = full ] || return 0; ssh "$HOST" 'docker exec pihole rm -rf /etc/pihole/.bk' >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== $MODE backup of '$HOST' into $DEST (limit ${RATE_KB} KB/s)"

echo "-- 1/6 consistent snapshots of the Pi-hole databases"
if [ "$MODE" = full ]; then
  ssh "$HOST" 'nice -n 19 docker exec pihole sh -c "
    rm -rf /etc/pihole/.bk && mkdir -p /etc/pihole/.bk &&
    for db in gravity.db pihole-FTL.db; do
      pihole-FTL sqlite3 /etc/pihole/\$db \".timeout 30000\" \".backup /etc/pihole/.bk/\$db\" || exit 1
    done"'
else
  echo "   skipped (light backup)"
fi

echo "-- 2/6 home directory projects and Pi-hole data"
# Live database files are skipped; in a FULL backup the snapshots in etc-pihole/.bk replace them.
EXCL="--exclude=pihole/etc-pihole/gravity.db --exclude=pihole/etc-pihole/gravity_old.db \
  --exclude=pihole/etc-pihole/pihole-FTL.db --exclude=pihole/etc-pihole/pihole-FTL.db-shm \
  --exclude=pihole/etc-pihole/pihole-FTL.db-wal --exclude=pihole/etc-pihole/listsCache"
if [ "$MODE" = light ]; then EXCL="$EXCL --exclude=pihole/etc-pihole/.bk"; fi
lo "tar cf - -C /home/lclpsoz $EXCL \
  pihole netalertx beszel/docker-compose.yml proxy netq dnsproxy ntfy alerts steps ops-state .ssh/authorized_keys .bashrc .profile \
  | nice -n 19 zstd -1 -T1 -c" | rx > "$DEST/pi-home.tar.zst"
cleanup

echo "-- 3/6 Beszel data (root-owned, read through a container) and the NetAlertX volume"
lo "docker run --rm -v /home/lclpsoz/beszel:/b:ro --entrypoint tar python:3.13-alpine czf - -C /b --exclude=beszel_socket ." \
  | rx > "$DEST/beszel.tar.gz"
lo "docker run --rm -v netalertx_netalertx_data:/data:ro --entrypoint tar python:3.13-alpine czf - -C /data ." \
  | rx > "$DEST/netalertx-volume.tar.gz"

echo "-- 4/6 Pi-hole teleporter export"
ssh "$HOST" 'nice -n 19 docker exec pihole sh -c "cd /tmp && rm -f *teleporter*.zip && pihole-FTL --teleporter >/dev/null && cat /tmp/*teleporter*.zip && rm -f /tmp/*teleporter*.zip"' \
  | rx > "$DEST/pihole-teleporter.zip"

echo "-- 5/6 system files"
lo "tar czf - --ignore-failed-read \
  /etc/systemd/system/cpupower-powersave.service /etc/systemd/system/uv-snapshot.service \
  /etc/systemd/system/uv-web.service /etc/systemd/system/dpkg-db-backup.service.d \
  /etc/systemd/journald.conf.d /etc/docker/daemon.json /etc/logrotate.d/uv-events \
  /usr/local/bin/uv-snapshot.sh /usr/local/bin/uv-web.py \
  /boot/firmware/config.txt /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak-hdmi \
  /var/log/uv-events.log /etc/hostname /etc/fstab 2>/dev/null; true" | rx > "$DEST/pi-system.tar.gz"

echo "-- 6/6 docker state"
ssh "$HOST" 'docker ps -a --format "{{.Names}}\t{{.Image}}\t{{.Status}}"; echo; docker images --digests; echo; docker volume ls; echo; uname -a; cat /etc/os-release | head -2' \
  > "$DEST/docker-state.txt"

echo "== verifying"
tar --zstd -tf "$DEST/pi-home.tar.zst" >/dev/null && echo "  ok  pi-home.tar.zst ($(du -h "$DEST/pi-home.tar.zst" | cut -f1))"
for f in beszel.tar.gz netalertx-volume.tar.gz pi-system.tar.gz; do
  tar tzf "$DEST/$f" >/dev/null && echo "  ok  $f ($(du -h "$DEST/$f" | cut -f1))"
done
python3 -I - "$DEST/pihole-teleporter.zip" <<'EOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None
print("  ok  pihole-teleporter.zip (%d files)" % len(z.namelist()))
EOF
if [ "$MODE" = full ]; then
  for db in gravity.db pihole-FTL.db; do
    tar --zstd -xOf "$DEST/pi-home.tar.zst" "pihole/etc-pihole/.bk/$db" > "$DEST/.check.db"
    echo "  $db integrity: $(python3 -I -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('pragma integrity_check').fetchone()[0])" "$DEST/.check.db")"
  done
  rm -f "$DEST/.check.db"
  touch "$DEST/FULL"
fi
(cd "$DEST" && sha256sum pi-home.tar.zst beszel.tar.gz netalertx-volume.tar.gz pihole-teleporter.zip pi-system.tar.gz docker-state.txt > SHA256SUMS)
chmod 600 "$DEST"/*
echo "== done: $DEST ($(du -sh "$DEST" | cut -f1))"
