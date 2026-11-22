#!/bin/bash
# Creates the focus groups for the work laptop and their deny rules. Idempotent.
# Run on the Pi:  bash ~/focus/setup-groups.sh   (needs docker, not sudo)
# Docs: raspberry-pi/15-focus-blocking.md
#
# One group per site, so a site can be opened alone by disabling its group.
# The laptop is a client in Default (keeps the ad lists) plus every focus group.
# Deny rules belong to their focus group ONLY (Pi-hole auto-adds new rules to Default; that is removed here).
set -euo pipefail
CLIENT_IP="${FOCUS_CLIENT_IP:-192.168.0.9}"

sql() { docker exec -i pihole pihole-FTL sqlite3 /etc/pihole/gravity.db; }

declare -A RX=(
  [reddit]='(\.|^)(reddit\.com|redd\.it|redditmedia\.com|redditstatic\.com|redditinc\.com)$'
  [globo]='(\.|^)(globo\.com|globo\.tv|glbimg\.com|globoi\.com|globovideos\.com|contem1g\.com\.br|globo)$'
  [uol]='(\.|^)(uol\.com\.br|uol\.com|bol\.com\.br|imguol\.com|imguol\.com\.br)$'
  [bbc]='(\.|^)(bbc\.com|bbc\.co\.uk|bbci\.co\.uk|bbcimg\.co\.uk)$'
)

{
  echo "BEGIN;"
  echo "INSERT OR IGNORE INTO client (ip, comment) VALUES ('$CLIENT_IP', 'work laptop (focus groups)');"
  for site in "${!RX[@]}"; do
    rx="${RX[$site]//\'/\'\'}"
    echo "INSERT OR IGNORE INTO \"group\" (name, description, enabled) VALUES ('focus-$site', 'Blocks $site for the work laptop. Disabled = open.', 1);"
    echo "INSERT OR IGNORE INTO domainlist (type, domain, enabled, comment) VALUES (3, '$rx', 1, 'focus-$site');"
    echo "DELETE FROM domainlist_by_group WHERE domainlist_id=(SELECT id FROM domainlist WHERE type=3 AND domain='$rx');"
    echo "INSERT OR IGNORE INTO domainlist_by_group (domainlist_id, group_id) SELECT d.id, g.id FROM domainlist d, \"group\" g WHERE d.type=3 AND d.domain='$rx' AND g.name='focus-$site';"
    echo "INSERT OR IGNORE INTO client_by_group (client_id, group_id) SELECT c.id, g.id FROM client c, \"group\" g WHERE c.ip='$CLIENT_IP' AND g.name='focus-$site';"
  done
  echo "COMMIT;"
} | sql
docker exec pihole pihole reloadlists >/dev/null
echo "focus groups ready for $CLIENT_IP"
