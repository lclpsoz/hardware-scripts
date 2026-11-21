# Pending things

What is still open on the Pi, in priority order. Updated 2026-11-21 (after the encrypted DNS, blocklist, naming and netq work). Finished work is in [CHANGES.md](CHANGES.md); background and evidence are in the numbered docs.

## 1. Wanted by the owner (priority)

### ntfy alerts: "I want this"

Push notifications to the phone when something needs attention. [ntfy](https://ntfy.sh) is a small push service: a script sends an HTTP request to a topic, and the phone app shows it.

Proposed alerts (each one is one condition in a small script on the Pi, or a NetAlertX notification setting):

| Alert | Source |
| --- | --- |
| Internet down, or DNS failures above 10% | netq status (`/api/summary` on 127.0.0.1:8098) |
| A new, unnamed device joined | NetAlertX (it has an ntfy notification plugin) |
| A container stopped or unhealthy | `docker ps` check every few minutes |
| Undervoltage event | the flight recorder's `events.jsonl` |
| Backup failed or is older than 2 days | the backup job (see next item) |

Decisions needed:

- **Where the server lives:** the public `ntfy.sh` with a long random topic name (simplest; anyone who learns the name can read and publish, so keep it secret), or a self-hosted ntfy container on the Pi (about 20 MB of RAM; the phone then needs a way to reach it away from home, for example Tailscale).
- **Which alerts** from the table, and quiet hours.

### Scheduled backup: "I want this"

[scripts/backup-pi.sh](scripts/backup-pi.sh) works but only runs when started by hand, and the only copy is on the laptop.

Proposed: run it automatically, keep the last 14, and keep a second copy somewhere else. The backups contain private data (Pi-hole history, device list, keys), so a copy that leaves the laptop should be encrypted first (for example with `age`).

Decisions needed:

- **When and where it runs:** a laptop timer (catches up when the laptop was off), or a job on the Pi that pushes to the laptop.
- **Second copy:** an external USB drive, a cloud bucket (for example through `rclone`), or none for now.
- **Alert** on failure, through ntfy above.

### HTTPS for Pi-hole (question asked)

See [07-services.md](07-services.md#https-for-the-lan-names). Waiting on a choice.

## 2. Waiting on something outside the Pi

| Item | Waiting for | Then |
| --- | --- | --- |
| New power supply | It arriving | Reboot, run [the step load test](scripts/uv-step-load-test.sh), then lift limits in order: CPU governor, NetAlertX `cpus` 1.0, dpkg quota ([04](04-resource-limits.md)). Then the 4 pending package upgrades. |
| Flight recorder | The next undervoltage event | Read `/var/log/uv-flight/events.jsonl` to see what ran in the minute before ([01](01-undervoltage.md)) |
| Blocklist experiments (HaGeZi Pro is on) | Owner's feedback on broken sites | Allow the names that break; check `*.on.aws` and BetterStack from the work laptop |
| Pi-hole limits (rate limit 1000/min, 150 concurrent) | About a week with the new list, around 2026-11-27 | Compare warnings and netq charts; if still hit, raise to 5000 and 512 ([02](02-pihole.md#8-can-the-limits-go-up-follow-up-2026-11-20-evening)) |
| Encrypted DNS | A week of data on net.lan | Compare the weeks before and after 2026-11-20 22:07 ([08](08-internet-quality.md), [09](09-encrypted-dns.md)) |
| TV | Next time it is used | Confirm it works with `pool.ntp.org` allowed; `time.samsungcloudsolution.com` stays blocked |
| Router DHCP reservations | Owner configuring them | Set the static-IP flag in NetAlertX for the tablet and phones |

## 3. Decisions and how-tos

- **Root access:** decided 2026-11-21: **no sudo for the assistant** for now. Root changes are prepared as a script (like `apply-root-changes.sh`) and run by the owner.
- **SSH password login** (open, "how?" answered in [05](05-system-health.md#how-to-turn-off-ssh-password-login)): key login already works, so password login is only an extra way in.
- **NetAlertX password** (open, answered in [05](05-system-health.md#how-to-set-a-netalertx-password)): the password feature exists but is off, and its default password, if enabled, is the well-known `123456`.
- **NetAlertX network tree:** optional; attach each device to the router in the Network tab.

## 4. Proposed, not scheduled

- Docker log rotation defaults (`/etc/docker/daemon.json`), Pi-hole `database.maxDBdays` 30 (the database is 86 MB), slower container healthchecks.
- Tailscale for remote access and real HTTPS certificates ([06](06-ideas.md)).
- netq: Pi-hole concurrency warnings on the page (needs a read-only Pi-hole app password).
- Other ideas in [06-ideas.md](06-ideas.md).

## 5. Decided and closed

- TV name: "Samsung TV (Home)" is fine.
- Ray's devices: no Guest group; named `Ray <model>` with owner Ray ([03](03-netalertx.md#device-naming-pattern-set-2026-11-20)).
- HDMI: off, fine as it is.
- Pi-hole limits: left open on purpose, see the table above.
- Moving the dpkg-db-backup timer off midnight: **dropped.** It finishes in 5 seconds under its 20% CPU quota, and only 3 of the 52 recorded undervoltage events happened in the midnight hour, so it isn't a real cause.
- Housekeeping from the repo: this folder has 40+ commits on `main`, not pushed; the repo is public, so check before pushing.
