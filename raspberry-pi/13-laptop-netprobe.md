# Laptop probe: full-speed tests and uptime (netprobe)

Written 2026-11-21, extended and **deployed 2026-11-22**. Running: netq and alerts on the Pi, `pi-netprobe.timer` on the laptop.

The Pi cannot measure the fibre: its 100 Mbps port and 600 MHz CPU cap a speed test near 60-94 Mbps. The laptop can (a trial from it on Wi-Fi gave **530 / 170 Mbps in 17 s**, against 60 from the Pi). So the Pi asks the laptop to run the test, and the laptop reports back with everything that explains the number.

## How it works

```
every minute:  laptop ── POST http://net.lan/api/laptop/poll  (heartbeat + context) ──► netq on the Pi
               laptop ◄── {"run": null}   or   {"run": {id, durations, ping targets}} ──
if asked:      laptop runs the test (about 20 s) ── POST /api/laptop/result ──► netq stores it, answers with the usual speed
```

- **The laptop initiates every call.** The laptop moves and sleeps, so the Pi never tries to reach it. A request the laptop does not pick up expires after 15 minutes.
- **The Pi decides when to test:** every **30 minutes** by default, changeable on the page ("Laptop test schedule", 0 = off, otherwise 5 to 1440; saved in `~/netq/data/laptop.json`), and on the **Run laptop speed test** button at http://net.lan.
- **Series:** on the same card, "Run N tests, M minutes apart" (M = 0: one right after the other, the laptop keeps sending heartbeats between them). A series replaces the schedule until it is done and can be cancelled.
- **Tests never overlap:** a new request is created only when the previous one has finished, and the spacing counts from the *start* of the previous test, so a test that takes longer than the interval just makes the wait shorter.
- **Light probe:** every 10 minutes (`NETQ_LAPTOP_LIGHT_EVERY_MIN`) the Pi also asks for a 3 s ping-only probe (router, ISP gateway, Cloudflare from the laptop). No downloads, no data cost. Full tests also store their idle pings, so the "Latency from the laptop" chart has a point every 10 minutes. It hands a request out only when the laptop says it is ready: not just back from sleep or a reconnect (the first minute is unstable), not on battery, and on the home network (its default gateway must be the router netq pings).
- **Auth:** a shared token in the `X-Netq-Token` header (`NETQ_LAPTOP_TOKEN` in `~/netq/config.env` on the Pi, `NETQ_TOKEN` in the laptop's `~/.config/pi-ops/config`). Without it the endpoints answer 403. The traffic is plain HTTP on the LAN, like the rest of the setup.
- **While a laptop test runs, the Pi's own probes pause** (up to about 75 s, once an hour), and the Pi's own speed test waits. Otherwise the saturated line would show up as latency and loss spikes and as a false alert.
- **Cost on the Pi:** one small POST a minute (a few ms of CPU), about 1,440 small rows a day in a separate `laptop` table (kept 60 days, written with the other samples every 5 minutes).

## What a test measures

- **Speed:** 4 parallel HTTPS streams to Cloudflare, 8 s down and 6 s up, rate taken from the steady part (after a 2 s warm-up). Each direction stops early at 300 MB (`NETQ_LAPTOP_MAX_MB`), so a test costs at most about 0.6 GB. **At the 30-minute default that is up to about 28 GB a day** on this line (the first live test moved about 0.5 GB). It also saturates the line for about 15 s every half hour, which the TV or a call would notice. If that matters, raise the interval on the page or lower `NETQ_LAPTOP_MAX_MB`.
- **Latency under load**, to the router, the ISP gateway and Cloudflare: idle, during the download and during the upload, with jitter and loss. A router latency that jumps under load on Wi-Fi is the laptop-to-router link, not the ISP.
- **The link:** Wi-Fi band, signal in dBm, link rate, retries and missed beacons during the test; or Ethernet speed, duplex and error counters. No SSID, MAC or address is sent.
- **Other traffic**, to tell a bad line from a busy one:
  1. the traffic on the interface in a quiet 3 s before the test;
  2. during each phase, the interface counters minus the bytes the test itself moved (with a margin for headers and retransmits);
  3. whether qBittorrent is running, and, if its Web UI is on, its exact download and upload rates.

  If other traffic before the test is above max(5 Mbps, 10% of the rate the test then reached), or during a phase above max(8 Mbps, 12% of the rate), the test is marked **contended**: a hollow dot on the chart, and the notes say why ("other traffic during upload: 62 Mbps"). Contended tests are kept, but left out of the baseline.
- **Wi-Fi weak-link alert** (`laptop_wifi` in `/api/summary`, ntfy after 15 minutes): the laptop is on Wi-Fi, its pings to the router over the last 30 minutes show a median above 15 ms, 5% loss or more, or a signal below -75 dBm, **and** the Pi sees the ISP gateway and Cloudflare healthy (under 5% loss). If the Pi also sees the line failing, it is not blamed on Wi-Fi. A "Laptop Wi-Fi" tile shows the state.
- **Weekly summary:** Sunday after 20:00, one low-priority ntfy message: clean tests, median and range, best and slowest hour of the day, laptop uptime, line incidents, median laptop-to-router latency. The page has the same by hour of day ("Laptop speed by hour of day"); `GET /api/weekly` returns the text.
- **Low-speed notification:** if a clean (not contended) test falls under half the median of the last clean ones (at least 4 needed, 7 days), one ntfy message says so with the link and latency, at most once every 3 hours.

**qBittorrent detail.** Without the Web UI the probe only knows it is running and how much traffic the laptop moves in total, which already shows the effect. For exact rates, turn on Tools → Options → Web UI on 127.0.0.1:8080 and tick "Bypass authentication for clients on localhost" (no password is stored). Not changed by this work; it is your qBittorrent setting.

## What the page shows

Tiles **Laptop** (online or offline, since when, link, uptime in the last 24 h) and **Laptop speed test**; **Laptop network use** chart (average Mbps on the laptop's interface, all programs); **Laptop uptime** strip with a gap list; **Laptop speed tests** chart and table (link, loaded latency, other traffic, notes); and the old **Pi speed tests**, now labelled as capped.

**Uptime and why a gap happened.** Each heartbeat carries the laptop's uptime and the seconds it has spent suspended since boot. Between two heartbeats more than 150 s apart:

| What changed | Shown as |
| --- | --- |
| Uptime restarted | Off or rebooted |
| Suspended seconds grew by about the gap | Asleep |
| The Pi has no router pings in the gap either | The Pi was not recording |
| None of the above | Awake but not reachable: away from home, or Wi-Fi down |

Limits: if net.lan stops resolving at home (Pi-hole down), heartbeats stop and it shows as "not reachable". Unplugging the power without a clean shutdown shows as "off" or "away" depending on the next boot.

## Operations

```bash
bash raspberry-pi/laptop/install.sh                    # installs pi-netprobe.timer with the other services
tail ~/.local/state/pi-ops/netprobe.log               # tests and state changes
curl -s http://net.lan/api/summary | python3 -m json.tool | grep -A12 '"laptop"'
ssh pi 'cd ~/netq && docker compose up -d'            # after editing netq.py or the compose file
```

Setup on the Pi: create `~/netq/config.env` from [pi/netq/config.env.example](pi/netq/config.env.example) with a long random token, and put the same token in the laptop config as `NETQ_TOKEN`. Undo: `bash raspberry-pi/laptop/install.sh --uninstall` removes every laptop service; to remove only this one, `systemctl --user disable --now pi-netprobe.timer`. On the Pi, restore `netq.py` and `docker-compose.yml` from git history (backups `*.bak-pre-laptop` on the Pi) and `docker compose up -d`.

## Done from the first list of ideas (2026-11-22)

Wi-Fi weak-link alert, weekly summary by hour of day, the 3 s light probe every 10 minutes, a changeable schedule and series.

## Still proposed

1. **Latency-under-load grade** (A-F) on the page, from idle versus loaded latency.
2. **Pause qBittorrent for the test** (Web API, once its UI is enabled) for a second, clean result, then resume it.
3. **Two vantage points on every outage:** when the Pi sees packet loss, show whether the laptop was online then and on which link.
4. **Wi-Fi band and signal log** from the heartbeat, to see when the laptop drops to 2.4 GHz.
5. **A second server** (another CDN) when Cloudflare is slow, to separate the line from the route.
6. **Retire the Pi's own 6-hourly speed test** after a week of this running (1.5 GB a month, little value now).
7. **Smaller:** a laptop-offline note on home.lan, job markers (apt, backup) on the charts, a second machine using the same token (add a `host` field).
8. **Lower the full-test cost:** a shorter test (4 s each way) or an adaptive interval (test more often only when the light probes look off).
