#!/usr/bin/env bash
# Laptop half of the speed test that the Pi asks for, and the laptop's heartbeat. Docs: ../../../13-laptop-netprobe.md
#
#   run.sh        one round: heartbeat to netq on the Pi, and a speed test if the Pi asks for one
#
# Called every minute by pi-netprobe.timer. Needs NETQ_TOKEN in ~/.config/pi-ops/config (the same value as
# NETQ_LAPTOP_TOKEN in ~/netq/config.env on the Pi). Optional: NETQ_URL (default http://net.lan),
# QBIT_URL (qBittorrent Web UI, default http://127.0.0.1:8080, only used if reachable).
SERVICE=netprobe
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=../../lib/common.sh
. "$HERE/../../lib/common.sh"

NETQ_URL=${NETQ_URL:-http://net.lan}
NETQ_TOKEN=${NETQ_TOKEN:-}
QBIT_URL=${QBIT_URL:-}
export NETQ_URL NETQ_TOKEN QBIT_URL PI_OPS_STATE

lock
# probe.py prints "LOG<TAB>text" and "NOTIFY<TAB>title<TAB>message<TAB>prio<TAB>tags" lines
python3 -I "$HERE/probe.py" | while IFS=$'\t' read -r kind a b c d; do
  case $kind in
    LOG) log "$a" ;;
    NOTIFY) notify "$a" "$b" "${c:-3}" "${d:-}" ;;
  esac
done
