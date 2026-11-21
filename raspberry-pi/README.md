# Raspberry Pi (`rasp-al-gaib-lite`): investigation, 2026-11-20

A read-only investigation over SSH on 20 Nov 2026, 19:30-20:10. **Nothing on the Pi was changed.** Everything below is a finding or a proposal for us to discuss.

The Pi is a Raspberry Pi 3 Model B (1 GB) running Debian 13, with Pi-hole, NetAlertX and Beszel in Docker, on 192.168.0.250.

## What I found, most important first

1. **Undervoltage is still a power-supply problem.** There were 20 events in 78 h while the CPU is pinned at 600 MHz. One busy core was enough to trigger one, and two happened with the Pi idle. The board draws far less than a healthy 2.5 A supply delivers, so the supply, the cable or the connector is at fault. The software limits only make events rarer. → [01-undervoltage.md](01-undervoltage.md)
2. **Pi-hole blocks ads only for the TV.** The only blocklist (StevenBlack, 77k domains) is assigned to the TV's group alone, so the `Default` group that every other device uses has none. I confirmed it live: `doubleclick.net` resolves normally for this laptop. → [02-pihole.md](02-pihole.md#2-only-the-tv-gets-a-blocklist)
3. **The "150 concurrent queries" warning is not a resource limit.** It happened at 4 moments in 3 days, each time one device was loading ad-heavy pages: 596 queries in 10 s, with about a third of the forwards never answered. Unanswered queries hold dnsmasq's 150 slots. Fixing item 2 would have answered about half of those names locally. → [02-pihole.md](02-pihole.md#1-the-maximum-number-of-concurrent-dns-queries-reached-max-150-warning)
4. **NetAlertX is slow because of a bug in its own code, not because of your 10 devices.** Every translated word re-reads all 24 language files: 52 times per page, 421 ms each, about 22 s per page. I wrote a 15-line patch and tested it: same output, 0.3 ms. The 0.5-CPU limit (throttled in 63% of periods) and the 600 MHz clock make it worse. → [03-netalertx.md](03-netalertx.md)
5. **The TV makes 35% of all DNS queries** (37.6k a day) because it keeps retrying blocked time servers. Allowing two time domains for the TV group stops it. → [02-pihole.md](02-pihole.md#3-the-tv-retries-blocked-time-servers-about-a-third-of-all-queries)
6. **The router nearly tripped Pi-hole's rate limit:** 953 queries in one minute, against a limit of 1000. Devices that use the router's IPv6 address for DNS all appear as one client, 192.168.0.1. At the limit they would all lose DNS at once. → [02-pihole.md](02-pihole.md#4-the-router-hides-devices-and-nearly-hit-the-rate-limit)
7. **Yes, the resources are still limited:** 600 MHz CPU, NetAlertX at half a core, the dpkg backup throttled. Keep the limits until a new supply passes a load test. Wi-Fi off should stay off anyway, because the firewall rules rely on the Pi having one address. → [04-resource-limits.md](04-resource-limits.md)
8. **Smaller items:**
   - Something on HDMI with an empty EDID is re-probed every 10 s (8.4k log lines a day).
   - About 1.46 GB a day is written to the SD card.
   - 34 package updates are pending, some of them security fixes.
   - SSH still accepts passwords, and NetAlertX has no password.
   - Pi-hole's database grows 9 MB a day with 91-day retention.

   → [05-system-health.md](05-system-health.md)

Ideas for more uses of the Pi: [06-ideas.md](06-ideas.md). Top picks: Tailscale, encrypted upstream DNS, phone alerts, and an internet-quality logger.

## Suggested order when you're back

| Phase | What | Risk | Depends on power fix? |
| --- | --- | --- | --- |
| 0. Quick wins | Pi-hole: give `Default` the blocklist, allow the TV's time domains, raise the rate limit to 5000, keep 30 days of history. NetAlertX: the `lang()` patch, `LOG_LEVEL` minimal, a UI password. | Low. Each change is a single setting with a one-line undo. | No |
| 1. Power | Tell me the supply and cable model. Unplug HDMI as a test. Optionally install the flight recorder first to get a baseline. New 5.1 V / 2.5 A supply. Step load test. | None (unplugging HDMI) to low (the load test pushes the Pi on purpose) | This is the power fix |
| 2. Unlock | Governor back to `ondemand`, NetAlertX to 1 CPU, `apt full-upgrade`, Pi-hole image update, upstream change, maybe a stronger blocklist | Medium: updates and heavier load | Yes |
| 3. Harden and extend | SSH keys only, Docker log defaults, cleanup, Tailscale, ntfy alerts, backups | Low | Partly |

## Questions for you

1. **Power supply:** brand and model, rated voltage and current, captive or separate cable, cable length? What else is plugged into that strip?
2. **HDMI:** what is plugged into the Pi's HDMI port (TV, monitor, adapter)? Is it needed?
3. **Blocklist:** was removing the blocklist from the `Default` group intentional? I assume not.
4. **192.168.0.9:** a device with a random (private) MAC, VS Code and Microsoft telemetry, and the ad-heavy browsing behind the warnings. A work laptop?
5. **TV time sync:** OK to let the TV sync time (two domains, TV group only)?
6. **Upstream DNS:** any preference between Google (current), Cloudflare and Quad9? Do you want encrypted upstream DNS?
7. **Flight recorder:** OK to install it ([01-undervoltage.md](01-undervoltage.md#1-a-flight-recorder-for-the-60-s-before-each-event))? It writes only when an event happens.

## Proposed commands (not run)

Run these on the Pi. Pi-hole settings changed with `pihole-FTL --config` apply live and persist in `~/pihole/etc-pihole/pihole.toml`. Check each one by reading the key back, for example `docker exec pihole pihole-FTL --config dns.rateLimit.count`, then test `dig @192.168.0.250 example.com +short` from the laptop.

**Phase 0, Pi-hole (in the web UI unless noted):**

1. Lists page → StevenBlack row → Group assignment → tick **Default** as well → Save. Then from the laptop: `dig @192.168.0.250 doubleclick.net +short` should print `0.0.0.0`.
2. Domains page → add exact **allow** entries for `time.samsungcloudsolution.com` and `pool.ntp.org`, and a regex allow `(\.|^)pool\.ntp\.org$`. Group assignment: `YouTubeAndPrimeOnly` only.
3. In a terminal:
   ```bash
   docker exec pihole pihole-FTL --config dns.rateLimit.count 5000         # per client per 60 s (default 1000)
   docker exec pihole pihole-FTL --config database.maxDBdays 30            # default 91
   docker exec pihole pihole-FTL --config misc.dnsmasq_lines '["dns-forward-max=512"]'   # optional safety margin
   docker exec pihole sh -c 'grep dns-forward-max /etc/pihole/dnsmasq.conf; tail -n 5 /var/log/pihole/FTL.log'
   ```
4. Optional, upstream change: `docker exec pihole pihole-FTL --config dns.upstreams '["1.1.1.1","1.0.0.1"]'`.

**Phase 0, NetAlertX:**

- The `lang()` patch: the steps and undo are in [03-netalertx.md](03-netalertx.md#a-patch-lang-locally-recommended-independent-of-the-power-fix).
- In Settings: `LOG_LEVEL` → `minimal`, and turn on the SETPWD plugin with a password.

**While waiting for the new supply (optional, reduces load-triggered events only):**

```bash
# move the nightly dpkg backup away from midnight (NetAlertX updates vendors at 00:00 too)
sudo mkdir -p /etc/systemd/system/dpkg-db-backup.timer.d
printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 03:30:00\n' | sudo tee /etc/systemd/system/dpkg-db-backup.timer.d/time.conf
sudo systemctl daemon-reload && systemctl list-timers dpkg-db-backup.timer
# undo: sudo rm -r /etc/systemd/system/dpkg-db-backup.timer.d && sudo systemctl daemon-reload
```

To check the healthchecks less often (each one is a `docker exec`, which briefly loads `containerd`), add this to the `pihole` and `netalertx` services in their compose files:

```yaml
    healthcheck:
      interval: 5m
```

Then run `docker compose up -d`. Check with `docker inspect -f '{{json .Config.Healthcheck}}' pihole`: the test should be unchanged and the interval 300000000000.

**Phase 1 and 2:** see [01-undervoltage.md](01-undervoltage.md#what-fixes-it) and [04-resource-limits.md](04-resource-limits.md#order-to-lift-them-once-the-supply-is-fixed).

## Files in this folder

| File | What it is |
| --- | --- |
| [01-undervoltage.md](01-undervoltage.md) | Every event, the evidence that it is the supply, and how to see more |
| [02-pihole.md](02-pihole.md) | The 150-query warning, the blocklist assignment, the TV storm, the rate-limit near miss, upstream latency, database growth |
| [03-netalertx.md](03-netalertx.md) | Why pages take 14-26 s, the tested patch, an upstream issue draft |
| [04-resource-limits.md](04-resource-limits.md) | What is still limited, and the order to undo it |
| [05-system-health.md](05-system-health.md) | Security, updates, SD writes, HDMI, Docker, network |
| [06-ideas.md](06-ideas.md) | More uses for the Pi that fit its limits |
| [scripts/pi-checkup.sh](scripts/pi-checkup.sh) | Read-only health snapshot. Run it after each change: `ssh pi 'bash -s' < raspberry-pi/scripts/pi-checkup.sh` (add `-- --ui` after `-s` to time NetAlertX pages) |
| [scripts/uv-flight-recorder.py](scripts/uv-flight-recorder.py), [.service](scripts/uv-flight-recorder.service) | Proposed recorder of the 60 s before each undervoltage event (not installed) |
| [scripts/uv-step-load-test.sh](scripts/uv-step-load-test.sh) | Proposed load test to check a supply (run it yourself, at the Pi) |
| [pi/netalertx/lang-cache.patch](pi/netalertx/lang-cache.patch), `lang.php.patched`, `lang.php.v26.10.28.orig` | The NetAlertX fix and the original file |
| [data/undervoltage-events.csv](data/undervoltage-events.csv) | All 20 events with what the Pi was doing |
| [data/uv-events-snapshots.log](data/uv-events-snapshots.log) | Copy of the Pi's `/var/log/uv-events.log` |
| [data/netalertx-scan-cycle-2026-11-20-2000.log](data/netalertx-scan-cycle-2026-11-20-2000.log) | One NetAlertX scan cycle, showing the slow plugin start-up |

## How this was done, and what it cost the Pi

- **Access:** SSH as `lclpsoz`, using the `docker` group. There was no sudo: it needs a password, which I did not ask for. Everything ran read-only, through `docker exec`, read-only SQLite (`-readonly`), `/proc`, `/sys` and the journal.
- **Background:** your six Claude Docs about this Pi (the undervoltage docs, Beszel, NetAlertX, Pi-hole host networking, and the DNS lockdown runbook).
- **Side effects:**
  - **Three undervoltage events, at 19:38, 19:40 and 19:42, were caused by my own database queries.** One long query was still running after I stopped it, so I ended that process. After that I ran heavier queries at `nice 19`. This accidental test is the clearest evidence of how thin the margin is.
  - **My test lookups appear in the Pi-hole log:** `claude-probe-*.example.com`, `google.com` from the container, and `doubleclick.net` from the laptop.
  - **Temporary files** I created in the Pi-hole container's `/tmp` were deleted again.
- **Docs that are now out of date (I didn't edit them):**
  - "changes, checks and revert" says the supply swap fixed the undervoltage.
  - The DNS runbook's device list has moved: the TV is now .3, and the phone is probably .5.
