#!/bin/bash
# Root-level changes for the Pi. Run ON the Pi, as your user, with sudo:
#
#   sudo bash ~/apply-root-changes.sh
#
# Deployed from raspberry-pi/scripts/ (copied to ~ by the assistant, which has no sudo).
# Idempotent; every replaced file is first saved as <file>.bak-<date>. Does:
#   1. uv-web (power.lan): read the kernel log of ALL boots, not only the current one
#   2. install the undervoltage flight recorder as a service
#   3. netplan: make /lib/netplan/00-network-manager-all.yaml readable by root only
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
D=$(date +%F); SRC=${SRC:-/home/lclpsoz/root-changes}

put() {  # put <source> <target> <mode>
  [ -f "$2" ] && ! cmp -s "$1" "$2" && cp -a "$2" "$2.bak-$D"
  install -m "$3" -o root -g root "$1" "$2"
}

echo "== 1/3 uv-web.py"
put "$SRC/uv-web.py" /usr/local/bin/uv-web.py 755
systemctl restart uv-web.service

echo "== 2/3 flight recorder"
put "$SRC/uv-flight-recorder.py" /usr/local/bin/uv-flight-recorder.py 755
put "$SRC/uv-flight-recorder.service" /etc/systemd/system/uv-flight-recorder.service 644
systemctl daemon-reload
systemctl enable --now uv-flight-recorder.service

echo "== 3/3 netplan permissions"
chmod 600 /lib/netplan/00-network-manager-all.yaml

echo "== status"
systemctl is-active uv-web uv-flight-recorder
ls -l /lib/netplan/00-network-manager-all.yaml
curl -s localhost:8099/ | grep -o '7 days <b>[0-9]*</b>' || true
