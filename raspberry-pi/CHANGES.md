# Changes made on the Pi

Newest first. Earlier entries come from the Claude Docs written during previous sessions, so their dates are those docs' dates. The current configuration is mirrored in [pi/](pi/); refresh it with `bash raspberry-pi/scripts/sync-from-pi.sh`.

| Date | Change | Where | Why | Undo |
| --- | --- | --- | --- | --- |
| 2026-11-20 ~23:50 | HDMI port turned off: `video=HDMI-A-1:d` added to `cmdline.txt` and the port set to `off` now; reads `disconnected` | `/boot/firmware/cmdline.txt` (backup `cmdline.txt.bak-hdmi`), done by you with sudo | Stops the phantom-connected polling and 8,400 log lines a day | `sudo cp /boot/firmware/cmdline.txt.bak-hdmi /boot/firmware/cmdline.txt`, then reboot |
| 2026-11-20 21:06 | netq internet-quality logger at `net.lan` | `~/netq` (container `netq`) | Evidence for ISP and DNS problems; speed tests | `cd ~/netq && docker compose down` |
| 2026-11-20 20:57 | Local DNS names: `home`, `pi`, `pihole`, `netalertx`, `beszel`, `power`, `net` `.lan` → 192.168.0.250, plus `router.lan` | Pi-hole `dns.hosts` | Easy addresses | `docker exec pihole pihole-FTL --config dns.hosts '[]'` |
| 2026-11-20 20:56 | nginx proxy on port 80, with the links page at `home.lan` | `~/proxy` (container `proxy`) | Name-based routing to each service | `cd ~/proxy && docker compose down` |
| 2026-11-20 20:56 | Pi-hole web UI moved to 8080 (HTTPS 8443) | `FTLCONF_webserver_port` in `~/pihole/docker-compose.yml` (backup `.bak-2026-11-20`) | Frees port 80 for the proxy | Restore the backup, then `docker compose up -d` |
| 2026-11-20 20:51 | NetAlertX `lang.php` caching patch (pages 14-40 s → about 1.3 s) | `~/netalertx/lang.php.patched`, mounted read-only; compose backup `.bak-2026-11-20` | UI was unusable | Restore the compose backup, then `docker compose up -d` |
| 2026-11-20 20:50 | Pi-hole `database.DBinterval` 60 → 300 | pihole.toml | Fewer SD card writes | `docker exec pihole pihole-FTL --config database.DBinterval 60` |
| 2026-11-20 20:32 | `apt upgrade` (50 packages, kernel 6.18.60 pending a reboot) | system, done by you | Updates | n/a |
| 2026-11-20 ~20:00 | StevenBlack list re-assigned to the `Default` group as well as the TV group | Pi-hole Lists page, done by you | Ad blocking for all devices | Remove `Default` from the list's groups |
| 2026-11-19 | NetAlertX limited to 0.5 CPU; schedules lengthened; naming plugins off | `~/netalertx/docker-compose.yml`, NetAlertX settings | Undervoltage | See [04-resource-limits.md](04-resource-limits.md) |
| 2026-11-19 | `dpkg-db-backup` limited to 20% CPU at `Nice=19` | `/etc/systemd/system/dpkg-db-backup.service.d/limit.conf` | Midnight undervoltage | `sudo rm -r …/dpkg-db-backup.service.d && sudo systemctl daemon-reload` |
| 2026-11-19 | `uv-snapshot` and `uv-web` (port 8099) services | `/etc/systemd/system`, `/usr/local/bin` | Undervoltage history | See the monitoring Claude Doc |
| 2026-11-17 | Persistent journal, 50 MB cap | `/etc/systemd/journald.conf.d/size.conf` | Keep the kernel log across reboots | Remove the file and restart journald |
| 2026-11-16 | Beszel hub and agent; cgroup memory flags in `cmdline.txt` | `~/beszel`, `/boot/firmware/cmdline.txt` | Monitoring | See [04-resource-limits.md](04-resource-limits.md) |
| 2026-11-16 | CPU governor `powersave` (600 MHz); Wi-Fi and Bluetooth off | `cpupower-powersave.service`, `config.txt` | Undervoltage (Wi-Fi off also matches the firewall design) | [04-resource-limits.md](04-resource-limits.md) |
| 2026-11-11 | NetAlertX (lite) installed | `~/netalertx` | Device inventory | `docker compose down` |
| 2026-11-11 | Pi-hole host networking, listening mode `LOCAL`; static 192.168.0.250 on eth0 | `~/pihole/docker-compose.yml`, NetworkManager | DNS lockdown project | See the DNS lockdown runbook (Claude Doc) |
