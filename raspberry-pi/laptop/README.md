# Laptop-side services

Things that run on the laptop (not the Pi) to look after the Pi. They are plain shell scripts started by **systemd user timers**, so no root is needed, and they share one helper library and one config file. Notifications go through the Pi's ntfy server ([../11-notifications.md](../11-notifications.md)).

```
laptop/
  lib/common.sh            config, log, notify(), lock(), pi (ssh), state helpers
  config.example           copy to ~/.config/pi-ops/config (outside git: holds the secret topic)
  install.sh               installs/removes the user units of every service below
  services/
    backup/                daily backup of the Pi to this laptop   (run.sh + pi-backup.{service,timer})
```

## Use

```bash
cp raspberry-pi/laptop/config.example ~/.config/pi-ops/config   # then edit NTFY_TOPIC; chmod 600
bash raspberry-pi/laptop/install.sh              # install and start
bash raspberry-pi/laptop/install.sh --status     # timers and last runs
bash raspberry-pi/laptop/install.sh --uninstall
journalctl --user -u pi-backup.service           # or: tail ~/.local/state/pi-ops/backup.log
```

If you move the repo, run `install.sh` again: the units hold the path.

## The backup service

- A timer fires **every hour**, and `run.sh` exits at once unless the last good backup is older than 20 hours. So there is one backup a day, and if the laptop was off or away from home the next hour retries. `Persistent=true` also runs a missed one at the next boot.
- It calls [../scripts/backup-pi.sh](../scripts/backup-pi.sh) into `~/backups/rasp-al-gaib-lite/<timestamp>/` (outside the repo, because it holds private data), keeps the newest 14 and deletes the older ones. Each is 40 MB today, so about 600 MB.
- **Notifications:** "Pi backup OK" at low priority (no sound), "Backup failed" at high priority, at most once a day.
- **On success it writes `~/ops-state/last-backup.json` on the Pi.** The Pi's alerts service reads that file and alerts if it is older than 36 hours: this catches a laptop that stopped backing up.
- **Watchdog:** after each backup it also checks the Pi's alerts heartbeat, and notifies if that service looks dead.
- `bash raspberry-pi/laptop/services/backup/run.sh --force` runs a backup now.

Restore steps are in [../README.md](../README.md#backups).

## Add a service

1. Create `services/<name>/run.sh`. Start with:
   ```bash
   SERVICE=<name>
   HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
   . "$HERE/../../lib/common.sh"
   lock                       # one run at a time
   ```
   then use `log "..."`, `notify "Title" "message" <priority 1-5> "tag,tag"`, `pi <command>` (ssh to the Pi), `state_get`/`state_set`.
2. Add `pi-<name>.service` (`ExecStart=@DIR@/run.sh`) and, if it is periodic, `pi-<name>.timer`. `@DIR@` is replaced with the folder by `install.sh`.
3. Run `bash raspberry-pi/laptop/install.sh`.

A planned example is in [../PENDING.md](../PENDING.md) (speed tests started by the Pi and run by the laptop).
