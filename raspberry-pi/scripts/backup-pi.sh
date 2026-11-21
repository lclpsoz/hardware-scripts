#!/usr/bin/env bash
# Back up everything important on the Pi to this machine.
#
#   bash raspberry-pi/scripts/backup-pi.sh [destination-dir]
#
# Default destination: ~/backups/rasp-al-gaib-lite/<timestamp>/ (outside the repo, because it
# contains private data: Pi-hole history, NetAlertX device list, TLS keys, password hashes).
#
# What is saved (streamed over ssh, so nothing large lands on the SD card):
#   pi-home.tar.gz           compose projects (pihole, netalertx, beszel, proxy, netq) with their data,
#                            plus ~/.ssh/authorized_keys and shell files. Pi-hole's databases are
#                            consistent SQLite snapshots (etc-pihole/.bk/), not live files.
#   beszel.tar.gz            Beszel hub data and keys (root-owned; live copy, restart-safe)
#   netalertx-volume.tar.gz  the NetAlertX docker volume (devices, scan history, settings)
#   pihole-teleporter.zip    Pi-hole's own export (settings, lists, local DNS), restorable in its web UI
#   pi-system.tar.gz         systemd units and drop-ins, uv scripts, journald and docker config,
#                            boot config, logrotate, the uv event log
#   docker-state.txt         containers, images and volumes (to rebuild by hand)
#   SHA256SUMS               checksums, verified at the end
#
# Safe to run while everything is up. Heavy steps run at the lowest CPU and disk priority
# (nice 19, ionice idle) so they don't trigger undervoltage.
set -euo pipefail

HOST=${PI_HOST:-pi}
STAMP=$(date +%Y-%m-%d_%H%M)
DEST=${1:-$HOME/backups/rasp-al-gaib-lite/$STAMP}
mkdir -p "$DEST"
chmod 700 "$DEST" "$(dirname "$DEST")" 2>/dev/null || true

lo() { ssh "$HOST" "nice -n 19 ionice -c3 $*"; }   # run on the Pi at lowest priority

cleanup() { ssh "$HOST" 'docker exec pihole rm -rf /etc/pihole/.bk' >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== backup of '$HOST' into $DEST"

echo "-- 1/6 consistent snapshots of the Pi-hole databases"
ssh "$HOST" 'nice -n 19 docker exec pihole sh -c "
  rm -rf /etc/pihole/.bk && mkdir -p /etc/pihole/.bk &&
  for db in gravity.db pihole-FTL.db; do
    pihole-FTL sqlite3 /etc/pihole/\$db \".timeout 30000\" \".backup /etc/pihole/.bk/\$db\" || exit 1
  done"'

echo "-- 2/6 home directory projects and Pi-hole data"
# Live database files are skipped; the snapshots in etc-pihole/.bk replace them.
lo "tar czf - -C /home/lclpsoz \
  --exclude=pihole/etc-pihole/gravity.db --exclude=pihole/etc-pihole/gravity_old.db \
  --exclude=pihole/etc-pihole/pihole-FTL.db --exclude=pihole/etc-pihole/pihole-FTL.db-shm \
  --exclude=pihole/etc-pihole/pihole-FTL.db-wal --exclude=pihole/etc-pihole/listsCache \
  pihole netalertx beszel/docker-compose.yml proxy netq .ssh/authorized_keys .bashrc .profile" > "$DEST/pi-home.tar.gz"
cleanup

echo "-- 3/6 Beszel data (root-owned, read through a container) and the NetAlertX volume"
lo "docker run --rm -v /home/lclpsoz/beszel:/b:ro --entrypoint tar python:3.13-alpine czf - -C /b --exclude=beszel_socket ." \
  > "$DEST/beszel.tar.gz"
lo "docker run --rm -v netalertx_netalertx_data:/data:ro --entrypoint tar python:3.13-alpine czf - -C /data ." \
  > "$DEST/netalertx-volume.tar.gz"

echo "-- 4/6 Pi-hole teleporter export"
ssh "$HOST" 'nice -n 19 docker exec pihole sh -c "cd /tmp && rm -f *teleporter*.zip && pihole-FTL --teleporter >/dev/null && cat /tmp/*teleporter*.zip && rm -f /tmp/*teleporter*.zip"' \
  > "$DEST/pihole-teleporter.zip"

echo "-- 5/6 system files"
lo "tar czf - --ignore-failed-read \
  /etc/systemd/system/cpupower-powersave.service /etc/systemd/system/uv-snapshot.service \
  /etc/systemd/system/uv-web.service /etc/systemd/system/dpkg-db-backup.service.d \
  /etc/systemd/journald.conf.d /etc/docker/daemon.json /etc/logrotate.d/uv-events \
  /usr/local/bin/uv-snapshot.sh /usr/local/bin/uv-web.py \
  /boot/firmware/config.txt /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak-hdmi \
  /var/log/uv-events.log /etc/hostname /etc/fstab 2>/dev/null; true" > "$DEST/pi-system.tar.gz"

echo "-- 6/6 docker state"
ssh "$HOST" 'docker ps -a --format "{{.Names}}\t{{.Image}}\t{{.Status}}"; echo; docker images --digests; echo; docker volume ls; echo; uname -a; cat /etc/os-release | head -2' \
  > "$DEST/docker-state.txt"

echo "== verifying"
for f in pi-home.tar.gz beszel.tar.gz netalertx-volume.tar.gz pi-system.tar.gz; do
  tar tzf "$DEST/$f" >/dev/null && echo "  ok  $f ($(du -h "$DEST/$f" | cut -f1))"
done
python3 -I - "$DEST/pihole-teleporter.zip" <<'EOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None
print("  ok  pihole-teleporter.zip (%d files)" % len(z.namelist()))
EOF
for db in gravity.db pihole-FTL.db; do
  tar xzOf "$DEST/pi-home.tar.gz" "pihole/etc-pihole/.bk/$db" > "$DEST/.check.db"
  echo "  $db integrity: $(python3 -I -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('pragma integrity_check').fetchone()[0])" "$DEST/.check.db")"
done
rm -f "$DEST/.check.db"
(cd "$DEST" && sha256sum pi-home.tar.gz beszel.tar.gz netalertx-volume.tar.gz pihole-teleporter.zip pi-system.tar.gz docker-state.txt > SHA256SUMS)
chmod 600 "$DEST"/*
echo "== done: $DEST ($(du -sh "$DEST" | cut -f1))"
