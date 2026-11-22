# Raspberry Pi (`rasp-al-gaib-lite`)

A Raspberry Pi 3 Model B (1 GB, Debian 13) at 192.168.0.250 running Pi-hole, NetAlertX, Beszel, a `.lan` proxy and an internet-quality logger, all in Docker. **Start at http://home.lan.**

**What is still open:** [PENDING.md](PENDING.md).

This folder holds:

- the investigation and its decisions (the numbered docs);
- the configuration that runs on the Pi ([pi/](pi/));
- the change log ([CHANGES.md](CHANGES.md)).

## Status on 2026-11-21

| Area | State |
| --- | --- |
| Power | Still marginal: 20 undervoltage events during today's upgrade alone. The CPU stays limited until the new supply arrives and passes [the load test](scripts/uv-step-load-test.sh). |
| Ad blocking | StevenBlack plus HaGeZi Pro for every device, with a work allowlist (320,161 domains, applied 22:08) ([10](10-ad-blocking.md)) |
| NetAlertX | **Fixed:** pages went from 14-40 s to about 1.3 s ([03](03-netalertx.md)) |
| Notifications | **New:** ntfy at `ntfy.lan`, alerts from the Pi, NetAlertX and a daily laptop backup ([11](11-notifications.md)). Phone setup still needed. |
| Names | **New:** `home.lan`, `pihole.lan`, `netalertx.lan`, `beszel.lan`, `power.lan`, `net.lan` ([07](07-services.md)) |
| Internet quality | **New:** `net.lan`, measuring every minute; first speed test 59.7 / 42.0 Mbps, capped by the Pi ([08](08-internet-quality.md)) |
| Laptop probe | **New (22 Nov):** the laptop reports every minute and runs full-speed tests for the Pi ([13](13-laptop-netprobe.md)) |
| Focus blocking | **New (22 Nov):** Reddit, Globo, UOL and BBC blocked for the work laptop only; open one for 5-30 min at `focus.lan` ([15](15-focus-blocking.md)) |
| Pi-hole | Updated to core 6.6 / FTL 6.9. `DBinterval` 300. Upstream is now the encrypted forwarder ([09](09-encrypted-dns.md)). Rate limit and concurrency limit left as they are by choice, to be re-evaluated after some days with the new list ([02](02-pihole.md#8-can-the-limits-go-up-follow-up-2026-11-20-evening)). |
| Reboot | Done 21:56: kernel 6.18.60 running, all containers healthy. |
| HDMI | Off (done by you with sudo); 0 phantom-connect messages after the reboot ([05](05-system-health.md#why-hdmi-says-connected-with-nothing-plugged-in)) |

## Answers to your questions

- **Is ad blocking active for all IPs?** Yes. The list is now on `Default` and the TV group, and `doubleclick.net`, `googleads` and `taboola` return `0.0.0.0` from the laptop. You still see ads because:
  - StevenBlack is exact-match only;
  - YouTube, Instagram and search ads come from the same domains as the content.

  HaGeZi Pro would block 957 of the 2,863 names your devices resolved in a day, against 350 for StevenBlack. It needs two allow entries so it doesn't break BetterStack and AWS for work. A browser blocker (uBlock Origin) covers same-domain ads. → [10-ad-blocking.md](10-ad-blocking.md)
- **Risks of optimizing the NetAlertX UI?** Very low, so it's applied: lint passed, identical output, and a one-command rollback. The one ongoing duty is to recheck the patch when the NetAlertX image changes, and `pi-checkup.sh` now warns about that. → [03-netalertx.md](03-netalertx.md)
- **HDMI isn't connected, so what causes it?** The Pi's own hot-plug line reads "connected". The driver then invents fallback modes and re-checks every 10 s, logging 8,400 lines a day. Nothing external is involved. The fix is `video=HDMI-A-1:d` in `cmdline.txt`, which needs root. → [05-system-health.md](05-system-health.md#why-hdmi-says-connected-with-nothing-plugged-in)
- **Are the SD writes too much?** Not for wear: about 1.46 GB a day, roughly 0.5 TB a year, while even cautious estimates for a 64 GB card are several TB. The bigger risk is writing during undervoltage. `DBinterval` 300 and the HDMI fix reduce writes further. → [05-system-health.md](05-system-health.md#are-the-sd-card-writes-too-much)
- **The work laptop (192.168.0.9) and its ad peaks?** Not many tabs. Each burst started with one ad-heavy news or entertainment page. Header-bidding ads make one such page look up 90-190 names within a minute. Separately, one work API name is looked up about 180 times a minute (scripts without a DNS cache), which counts toward the rate limit. → [02-pihole.md](02-pihole.md#8-can-the-limits-go-up-follow-up-2026-11-20-evening)
- **What can we do about the DNS limits?** Both can go up safely: they are safety valves, not resource limits. Recommended:
  - rate limit 1000 → 5000 per minute (this laptop hit 1,097 at 20:22);
  - concurrent forwards 150 → 512.

  Blocking helps most, because blocked names never go upstream. Faster upstreams and encrypted DNS help next. → [02-pihole.md](02-pihole.md#8-can-the-limits-go-up-follow-up-2026-11-20-evening)
- **The TV is unplugged except when in use.** Its retry storm (35% of queries) returns each time it's connected, unless its two time domains are allowed. → [02-pihole.md](02-pihole.md#3-the-tv-retries-blocked-time-servers-about-a-third-of-all-queries)
- **Impact of the upgrade you ran?** No damage found:
  - no file-system errors;
  - the package database is consistent;
  - the boot files match their sources;
  - all containers came back healthy.

  It did cause 20 undervoltage events in ten minutes. A reboot is pending. → [05-system-health.md](05-system-health.md#impact-of-the-apt-upgrade-you-ran-2032-2039)
- **How would encrypted upstream DNS help?** It hides your lookups from Claro and protects the answers from tampering. It may also smooth the burst losses, and choosing Cloudflare would stop Google receiving your /24. Recommended: dnsproxy with DNS-over-TLS to Cloudflare and Quad9. Avoid cloudflared, which Cloudflare has deprecated. → [09-encrypted-dns.md](09-encrypted-dns.md)

## Needs your decision

0. **Run once with sudo:** `sudo bash ~/apply-root-changes.sh` on the Pi. It fixes the power.lan history across reboots (the page used `journalctl -k`, which shows only the current boot), installs the undervoltage flight recorder, and fixes the netplan file permissions. The files are already on the Pi ([scripts/apply-root-changes.sh](scripts/apply-root-changes.sh)).
1. **Root access.** HDMI off, the reboot, the flight recorder and SSH hardening all need `sudo`, which asks for a password. Options:
   - (a) Run the commands from the docs yourself.
   - (b) Add `/etc/sudoers.d/010-lclpsoz-nopasswd` with `lclpsoz ALL=(ALL) NOPASSWD: ALL`, so I can do it. Your user is in the `docker` group, which is already root-equivalent, so this adds little real exposure, and sudo logs every command to the journal.
   - (c) Let me use the docker group for root-level edits. That works, but it's less transparent than sudo, so I haven't done it.
2. **Pi-hole:**
   - rate limit 5000;
   - `dns-forward-max` 512;
   - HaGeZi Pro plus 2 work allow entries;
   - TV time domains allowed;
   - upstreams switched to Cloudflare, or to the encrypted forwarder.
3. **Reboot timing:** now, or with the new power supply. It applies kernel 6.18.60 and the HDMI fix.

## Next steps (once decided)

1. When the power supply arrives: reboot, run `scripts/uv-step-load-test.sh`, then lift the limits in the order in [04-resource-limits.md](04-resource-limits.md).
2. Pi-hole: apply the agreed settings, then watch netq and the Pi-hole warnings for a week.
3. Optional: the flight recorder ([01](01-undervoltage.md)), the NetAlertX UI password, and more from [06-ideas.md](06-ideas.md) (Tailscale, alerts).

## Findings from the first investigation (20 Nov, 19:30-20:10)

1. **Undervoltage is a supply problem:** 20 events in 78 h at 600 MHz, some with one busy core and two at idle. → [01](01-undervoltage.md)
2. **Only the TV had a blocklist.** Now fixed by you. → [02](02-pihole.md#2-only-the-tv-gets-a-blocklist)
3. **"150 concurrent queries"** came from ad-heavy page loads where about a third of the forwards went unanswered. → [02](02-pihole.md#1-the-maximum-number-of-concurrent-dns-queries-reached-max-150-warning)
4. **NetAlertX's `lang()` bug.** Now patched. → [03](03-netalertx.md)
5. **The TV's time-sync storm.** → [02](02-pihole.md#3-the-tv-retries-blocked-time-servers-about-a-third-of-all-queries)
6. **The router aggregates IPv6-DNS devices,** which nearly hit the rate limit. → [02](02-pihole.md#4-the-router-hides-devices-and-nearly-hit-the-rate-limit)
7. **Resource limits still in place.** → [04](04-resource-limits.md)
8. **Smaller items:** HDMI, SD writes, updates, SSH passwords, NetAlertX without a password, database growth. → [05](05-system-health.md)

## Files

| File | What it is |
| --- | --- |
| [01-undervoltage.md](01-undervoltage.md) | Every event, the evidence it is the supply, and how to see more |
| [02-pihole.md](02-pihole.md) | The 150-query warning, blocklist assignment, TV storm, rate limit, upstream latency, database growth, the DNS limits |
| [03-netalertx.md](03-netalertx.md) | Why pages took 14-40 s, the applied patch, risks |
| [04-resource-limits.md](04-resource-limits.md) | What is still limited, and the order to undo it |
| [05-system-health.md](05-system-health.md) | Security, updates, SD writes, HDMI root cause, upgrade impact |
| [06-ideas.md](06-ideas.md) | More uses for the Pi |
| [07-services.md](07-services.md) | The `.lan` names, the proxy and the links page |
| [08-internet-quality.md](08-internet-quality.md) | netq: what it measures, speed-test limits, operations |
| [09-encrypted-dns.md](09-encrypted-dns.md) | Encrypted upstream DNS: pros, cons, options, draft |
| [10-ad-blocking.md](10-ad-blocking.md) | Blocking status, list comparison, what DNS can't block |
| [11-notifications.md](11-notifications.md) | ntfy push alerts: what you get, phone setup, how it is built |
| [laptop/](laptop/README.md) | Laptop-side services (daily backup, more later) with a shared framework |
| [PENDING.md](PENDING.md) | **What is still open**, by priority, with what each item waits for |
| [CHANGES.md](CHANGES.md) | Everything changed on the Pi, with undo |
| [pi/](pi/) | What runs on the Pi: compose files, Pi-hole changed settings, systemd units, boot config (secrets redacted), plus the proxy, netq and NetAlertX patch that are deployed from here |
| [scripts/pi-checkup.sh](scripts/pi-checkup.sh) | Read-only health check: `ssh pi 'bash -s' < raspberry-pi/scripts/pi-checkup.sh` |
| [scripts/backup-pi.sh](scripts/backup-pi.sh) | Back up all important Pi data to the laptop (outside the repo) |
| [scripts/sync-from-pi.sh](scripts/sync-from-pi.sh) | Refresh `pi/` from the Pi, with redaction and a secret guard |
| [scripts/uv-flight-recorder.py](scripts/uv-flight-recorder.py), [scripts/uv-step-load-test.sh](scripts/uv-step-load-test.sh) | Undervoltage tools (proposed, not installed) |
| [data/](data/) | Evidence: undervoltage events and snapshots, a NetAlertX scan cycle (redacted) |

## Backups

The backup now runs by itself every day from the laptop ([laptop/README.md](laptop/README.md)). `bash raspberry-pi/scripts/backup-pi.sh` is the script it calls, and saves everything that isn't reproducible from this repo to `~/backups/rasp-al-gaib-lite/<timestamp>/` on the laptop. That folder is outside the repo because it holds private data (Pi-hole history, device list, keys). It takes about a minute, runs at the lowest priority, and verifies the archives and both Pi-hole databases at the end. The first run produced 33 MB.

| File | Restores |
| --- | --- |
| `pi-home.tar.zst` (older backups: `.tar.gz`) | `~/pihole`, `~/netalertx`, `~/proxy`, `~/netq`, the Beszel compose file; in a **full** backup, the Pi-hole databases as consistent snapshots in `etc-pihole/.bk/` (a **light** backup leaves them out) |
| `beszel.tar.gz` | Beszel hub data and keys |
| `netalertx-volume.tar.gz` | NetAlertX devices and settings (docker volume) |
| `pihole-teleporter.zip` | Pi-hole settings, lists, local DNS (upload in Settings → Teleporter) |
| `pi-system.tar.gz` | systemd units, uv scripts, boot config, journald config |

A full backup is made when no backup in the folder has a `FULL` marker from the last 7 days, so about once a week; the other days are light (3 MB instead of 46 MB), see [01-undervoltage.md](01-undervoltage.md#update-22-nov-the-last-24-hours). To restore a service: extract its archive into `~` (`tar --zstd -xf pi-home.tar.zst -C ~`, or `tar xzf` for an old `.tar.gz`). For Pi-hole, take the newest backup folder that has a `FULL` file, move its `.bk` snapshots over `gravity.db` and `pihole-FTL.db`, then `docker compose up -d`. If you only have a light backup, import `pihole-teleporter.zip` and run `pihole -g` to rebuild the lists. The query history is then missing. For the NetAlertX volume, extract into the docker volume with `docker run --rm -v netalertx_netalertx_data:/data -i python:3.13-alpine tar xzf - -C /data < netalertx-volume.tar.gz`. It does not cover the operating system itself; for that, image the SD card.

## Keeping this folder in sync with the Pi

- **Files authored here** (`pi/proxy`, `pi/netq`, `pi/netalertx/lang.php.patched`) are deployed with `scp` to the same path under `~` on the Pi.
- **Files that live on the Pi** (compose files, Pi-hole settings, systemd units) are pulled with `bash raspberry-pi/scripts/sync-from-pi.sh`. Its drift check reports when a deployed file differs from the repo.
- **Every change is logged** in [CHANGES.md](CHANGES.md).
- **This repo is public**, so MAC addresses, the public IP, passwords and tokens are kept out, and raw DNS logs are not committed.
