#!/usr/bin/env bash
# Install (or remove) the laptop-side services as systemd USER units. No root needed.
#
#   bash raspberry-pi/laptop/install.sh              install and start every service in services/
#   bash raspberry-pi/laptop/install.sh --status     show timers and last runs
#   bash raspberry-pi/laptop/install.sh --uninstall  stop and remove them
#
# A service is a folder under services/ with a run.sh and one or more *.service / *.timer
# files. "@DIR@" inside a unit is replaced with the service's folder.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
UNITS=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
CONF=${XDG_CONFIG_HOME:-$HOME/.config}/pi-ops/config
mkdir -p "$UNITS"

case "${1:-install}" in
  --status)
    systemctl --user list-timers --no-pager 'pi-*' || true
    for f in "$HERE"/services/*/pi-*.service; do
      [ -e "$f" ] && systemctl --user --no-pager -n 3 status "$(basename "$f")" | sed -n 1,8p
    done ;;
  --uninstall)
    for t in "$HERE"/services/*/*.timer; do [ -e "$t" ] && systemctl --user disable --now "$(basename "$t")" || true; done
    for f in "$HERE"/services/*/pi-*; do rm -f "$UNITS/$(basename "$f")"; done
    systemctl --user daemon-reload; echo "removed" ;;
  install)
    [ -f "$CONF" ] || { echo "Missing $CONF. Copy $HERE/config.example there and set NTFY_TOPIC."; exit 1; }
    for dir in "$HERE"/services/*/; do
      [ -x "$dir/run.sh" ] || continue
      for f in "$dir"pi-*.service "$dir"pi-*.timer; do
        [ -e "$f" ] && sed "s#@DIR@#${dir%/}#g" "$f" > "$UNITS/$(basename "$f")"
      done
    done
    systemctl --user daemon-reload
    for t in "$HERE"/services/*/pi-*.timer; do [ -e "$t" ] && systemctl --user enable --now "$(basename "$t")"; done
    systemctl --user list-timers --no-pager 'pi-*' ;;
  *) echo "usage: $0 [--status|--uninstall]"; exit 2 ;;
esac
