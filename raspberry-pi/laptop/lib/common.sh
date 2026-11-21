#!/usr/bin/env bash
# Shared helpers for the laptop-side services of the Pi setup (source this file).
#
#   source "$(dirname "${BASH_SOURCE[0]}")/../../lib/common.sh"     # from services/<name>/run.sh
#
# Provides: config loading, log, notify, lock, pi (ssh to the Pi), state_get/state_set.
# Config file: ~/.config/pi-ops/config (outside the repo: it holds the secret ntfy topic).
#   NTFY_URL=http://ntfy.lan        NTFY_TOPIC=<secret topic>
#   PI_HOST=pi                      (ssh alias, default "pi")
#   BACKUP_DIR=~/backups/rasp-al-gaib-lite     BACKUP_KEEP=14
set -euo pipefail

PI_OPS_CONFIG=${PI_OPS_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/pi-ops/config}
PI_OPS_STATE=${PI_OPS_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/pi-ops}
mkdir -p "$PI_OPS_STATE"

if [ -f "$PI_OPS_CONFIG" ]; then
  # shellcheck disable=SC1090
  . "$PI_OPS_CONFIG"
fi
NTFY_URL=${NTFY_URL:-http://ntfy.lan}
NTFY_TOPIC=${NTFY_TOPIC:-}
PI_HOST=${PI_HOST:-pi}
SERVICE=${SERVICE:-$(basename "$(dirname "$(readlink -f "${BASH_SOURCE[1]:-$0}")")")}

log() { printf '%s [%s] %s\n' "$(date '+%F %T')" "$SERVICE" "$*" | tee -a "$PI_OPS_STATE/$SERVICE.log" >&2; }

# notify <title> <message> [priority 1-5, default 3] [tags, comma separated]
# Never fails the caller: a notification problem must not turn a good run into a bad one.
notify() {
  local title=$1 msg=$2 prio=${3:-3} tags=${4:-}
  [ -n "$NTFY_TOPIC" ] || { log "no NTFY_TOPIC in $PI_OPS_CONFIG; not notifying: $title"; return 0; }
  python3 -I - "$NTFY_URL" "$NTFY_TOPIC" "$title" "$msg" "$prio" "$tags" <<'PY' || log "could not reach ntfy at $NTFY_URL (notification: $title)"
import json, sys, urllib.request
url, topic, title, msg, prio, tags = sys.argv[1:7]
body = json.dumps({"topic": topic, "title": title, "message": msg, "priority": int(prio),
                   "tags": [t for t in tags.split(",") if t]}).encode()
urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=10).read()
PY
  return 0
}

# One run of a service at a time.
lock() { exec 9>"$PI_OPS_STATE/$SERVICE.lock"; flock -n 9 || { log "already running"; exit 0; }; }

pi() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$PI_HOST" "$@"; }

state_get() { cat "$PI_OPS_STATE/$SERVICE.$1" 2>/dev/null || true; }
state_set() { printf '%s' "$2" > "$PI_OPS_STATE/$SERVICE.$1"; }
