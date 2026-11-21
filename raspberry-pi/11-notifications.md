# Notifications (ntfy)

Running since 2026-11-21. Alerts from the Pi and the laptop arrive as push notifications on the phone, through a small [ntfy](https://ntfy.sh) server that lives on the Pi. Nothing is sent to an outside service.

```
Pi: alerts container ──┐
Pi: NetAlertX plugin ──┼──► ntfy server (Pi, ntfy.lan) ──► ntfy app on the phone
Laptop: backup service ┘
```

## What it costs

| Part | RAM | CPU |
| --- | --- | --- |
| `ntfy` container | about 12-16 MB | 0.4% |
| `alerts` container | about 19 MB | 0% idle, a short burst each minute |

## Set up the phone (once)

1. Install the **ntfy** app (Google Play or F-Droid).
2. In the app: Settings → **Default server** or Add subscription → "Use another server", and enter `http://ntfy.lan`.
3. Subscribe to your **topic**. The topic is the channel's secret and is **not in this repo**: it is in `~/alerts/config.env` on the Pi (`NTFY_TOPIC=`) and in `~/.config/pi-ops/config` on the laptop.
4. Android: allow the app to run in the background and turn off battery optimization for it. The app keeps one connection open to the server; without that permission Android delays messages.

**Limits:** the server is only reachable on the home network (the name `ntfy.lan` comes from Pi-hole). Away from home you get nothing until Tailscale or similar is set up ([PENDING.md](PENDING.md)). Anyone on the LAN who knows the topic can read and publish, so it is a long random string.

Test from any terminal at home: `curl -d "hello" http://ntfy.lan/<topic>`.

## What you get

Each condition notifies **once** after it has been bad for the delay below, and once more ("Resolved: ...") when it clears. No repeats while it stays bad.

| Alert | Priority | After | Source |
| --- | --- | --- | --- |
| A service stops answering: Pi-hole web, NetAlertX, Beszel, proxy, power page, ntfy, netq | default (Pi-hole web high) | 2 min | HTTP probes |
| **Pi-hole DNS stops answering** | urgent | 1 min | DNS query to 127.0.0.1 |
| **Internet problem** (netq status "serious" or "critical") | default, high if critical | 3 min | netq summary |
| netq stopped sampling | default | 1 min | netq |
| Encrypted DNS forwarder failing (50% or more of lookups) | high | 3 min | netq |
| **Undervoltage event**, with the busiest container before it; bursts are grouped into one message per 30 min | default | immediate | flight recorder file |
| Flight recorder not running | default | 3 min | process check |
| **Backup older than 36 h**, or none ever | default | immediate | `~/ops-state/last-backup.json`, written by the laptop |
| **Pi-hole messages** such as the "max concurrent DNS queries" limit or the rate limit | default | next check (5 min) | Pi-hole API, `/api/info/messages` |
| Pi-hole update available (core, web or FTL) | low | 5 min | Pi-hole API |
| Pi-hole blocklists not updated for 10 days | default | 5 min | Pi-hole API |
| Pi-hole blocking switched off for 10 min | default | 10 min | Pi-hole API |
| Low memory (under 80 MiB), CPU 75 °C or more, SD card 85% full | default | 5 / 2 / 10 min | `/proc`, `/sys` |
| Pi rebooted | low | next check | boot id |
| **New device on the network** (and devices flagged "alert down") | default | next scan (10 min) | NetAlertX's own ntfy plugin |
| **Daily backup result**: OK (low priority) or failed (high, once a day) | low / high | at the run | laptop service |
| The alerts service itself stopped | high | at the next backup run | laptop watchdog, reads the Pi's heartbeat file |

Other relevant things I considered and left out on purpose: per-minute packet loss (too noisy, it is on net.lan), phones going offline (they sleep), CPU load (it is high during normal jobs), TV traffic.

## How it is built

- **Pi:** [pi/ntfy/](pi/ntfy/) (server) and [pi/alerts/](pi/alerts/) (the checks, one Python file with no dependencies). The alerts container has no Docker socket: it probes services over HTTP/DNS, reads `/proc` and `/sys`, a read-only mount of the flight-recorder log, and Pi-hole's API using the password file Pi-hole creates for its own CLI (mounted read-only). It runs as an unprivileged user with all capabilities dropped. `pid: host` lets it see whether the flight recorder process exists.
- **Laptop:** [laptop/](laptop/) holds the backup service and the shared helpers; see [laptop/README.md](laptop/README.md).
- **NetAlertX:** its `NTFY` plugin was enabled in `app.conf` (`NTFY_RUN='on_notification'`, host `http://127.0.0.1:2586`, the topic, priority default) and the notification sections were cut to `new_devices` and `down_devices`, so connect and disconnect events of phones don't flood the channel. The backup of the previous file is `app.conf.bak-before-ntfy-2026-11-21` in the same folder. Not yet seen working end to end: it fires when the next unknown device joins.
- **State:** `~/alerts/data/state.json` holds what was already announced. It is written only when something changes.

## Operating it

| Task | Command (on the Pi, in `~/alerts`) |
| --- | --- |
| See what the checks think right now, sending nothing | `docker run --rm --user 1000:1000 --network host --pid host --env-file config.env -e NTFY_URL=http://127.0.0.1:2586 -v $PWD/alerts.py:/app/alerts.py:ro -v /tmp:/data -v $HOME/pihole/etc-pihole/cli_pw:/run/secrets/pihole_cli_pw:ro -v /var/log/uv-flight:/uv-flight:ro -v $HOME/ops-state:/ops-state:ro python:3.13-alpine python /app/alerts.py --selftest` |
| Send a test message | `docker exec alerts python /app/alerts.py --send-test` |
| Logs | `docker logs alerts` |
| Silence everything | `docker compose stop alerts` (and in NetAlertX set `NTFY_RUN` to `disabled`) |
| Change a threshold | edit `alerts.py` in this repo, `scp` it to `~/alerts/`, `docker compose up -d` |

**Add a check:** write a function that calls `observe(key, is_bad, title, message, priority, after=seconds)` for a condition, or `event(title, message)` for a one-off, then add it to the `CHECKS` list. The `observe` engine handles the one-message-per-problem logic; its tests are in [pi/alerts/test_engine.py](pi/alerts/test_engine.py) (`python3 -I test_engine.py`).

## Troubleshooting

- **No messages at all:** open `http://ntfy.lan/v1/health` in the phone's browser. If that fails, the phone is not using Pi-hole for DNS or is off the home network.
- **Messages delayed:** battery optimization on the phone (step 4 above).
- **`Pi alerts are not reporting` from the laptop:** `docker ps` and `docker logs alerts` on the Pi.
