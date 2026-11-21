#!/bin/bash
# Root-level changes, round 2 (2026-11-21). Run ON the Pi with sudo:
#
#   sudo bash ~/apply-apt-limit-and-uvweb.sh          # apply
#   sudo bash ~/apply-apt-limit-and-uvweb.sh --undo   # back to normal (do this with the new power supply)
#
# Does:
#   1. uv-web (power.lan): read the whole kernel log once, then only the last minutes
#   2. limit the daily apt jobs (apt-daily, apt-daily-upgrade) to 30% of one core, lowest CPU
#      and disk priority. Reason: apt-get at 90-100% of a core coincided with undervoltage events
#      (19 Nov 19:58, 21 Nov 06:11). The jobs just take longer. See 01-undervoltage.md.
# --undo removes the apt drop-ins only (uv-web keeps the improvement). Idempotent.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
D=$(date +%F); SRC=${SRC:-/home/lclpsoz/root-changes}
UNITS="apt-daily apt-daily-upgrade"

if [ "${1:-}" = "--undo" ]; then
  for u in $UNITS; do rm -f "/etc/systemd/system/$u.service.d/power-limit.conf"; rmdir "/etc/systemd/system/$u.service.d" 2>/dev/null || true; done
  systemctl daemon-reload
  echo "apt limits removed:"; systemctl show apt-daily-upgrade -p CPUQuotaPerSecUSec -p Nice
  exit 0
fi

echo "== 1/2 uv-web.py"
[ -f /usr/local/bin/uv-web.py ] && ! cmp -s "$SRC/uv-web.py" /usr/local/bin/uv-web.py && cp -a /usr/local/bin/uv-web.py "/usr/local/bin/uv-web.py.bak-$D"
install -m 755 -o root -g root "$SRC/uv-web.py" /usr/local/bin/uv-web.py
systemctl restart uv-web.service

echo "== 2/2 apt limits"
for u in $UNITS; do
  mkdir -p "/etc/systemd/system/$u.service.d"
  cat > "/etc/systemd/system/$u.service.d/power-limit.conf" <<'EOF'
# Added 2026-11-21 against undervoltage events (weak power supply). Remove with
# `sudo bash ~/apply-apt-limit-and-uvweb.sh --undo` once the new supply is in.
[Service]
CPUQuota=30%
Nice=19
IOSchedulingClass=idle
EOF
done
systemctl daemon-reload

echo "== status"
systemctl is-active uv-web
for u in $UNITS; do systemctl show "$u" -p CPUQuotaPerSecUSec -p Nice | tr '\n' ' '; echo; done
curl -s localhost:8099/json | head -c 120; echo
