# Laptop probe: full-speed tests and uptime (netprobe)

Written 2026-11-21. **Status: code written and tested on the laptop and in demo mode; not yet deployed to the Pi** (see [PENDING.md](PENDING.md)).

The Pi cannot measure the fibre: its 100 Mbps port and 600 MHz CPU cap a speed test near 60-94 Mbps. The laptop can (a trial from it on Wi-Fi gave **530 / 170 Mbps in 17 s**, against 60 from the Pi). So the Pi asks the laptop to run the test, and the laptop reports back with everything that explains the number.

## How it works

```
every minute:  laptop ── POST http://net.lan/api/laptop/poll  (heartbeat + context) ──► netq on the Pi
               laptop ◄── {"run": null}   or   {"run": {id, durations, ping targets}} ──
if asked:      laptop runs the test (about 20 s) ── POST /api/laptop/result ──► netq stores it, answers with the usual speed
```

- **The laptop initiates every call.** The laptop moves and sleeps, so the Pi never tries to reach it. A request the laptop does not pick up expires after 15 minutes.
- **The Pi decides when to test:** every 60 minutes (`NETQ_LAPTOP_SPEED_EVERY_MIN`, 0 = only the button) and on the **Run laptop speed test** button at http://net.lan. It hands a request out only when the laptop says it is ready: not just back from sleep or a reconnect (the first minute is unstable), not on battery, and on the home network (its default gateway must be the router netq pings).
- **Auth:** a shared token in the `X-Netq-Token` header (`NETQ_LAPTOP_TOKEN` in `~/netq/config.env` on the Pi, `NETQ_TOKEN` in the laptop's `~/.config/pi-ops/config`). Without it the endpoints answer 403. The traffic is plain HTTP on the LAN, like the rest of the setup.
- **While a laptop test runs, the Pi's own probes pause** (up to about 75 s, once an hour), and the Pi's own speed test waits. Otherwise the saturated line would show up as latency and loss spikes and as a false alert.
- **Cost on the Pi:** one small POST a minute (a few ms of CPU), about 1,440 small rows a day in a separate `laptop` table (kept 60 days, written with the other samples every 5 minutes).

## What a test measures

- **Speed:** 4 parallel HTTPS streams to Cloudflare, 8 s down and 6 s up, rate taken from the steady part (after a 2 s warm-up). Each direction stops early at 300 MB (`NETQ_LAPTOP_MAX_MB`), so a test costs at most about 0.6 GB: up to about 14 GB a day at the hourly schedule on a fast line. Lower `NETQ_LAPTOP_MAX_MB` or raise the interval if that matters.
- **Latency under load**, to the router, the ISP gateway and Cloudflare: idle, during the download and during the upload, with jitter and loss. A router latency that jumps under load on Wi-Fi is the laptop-to-router link, not the ISP.
- **The link:** Wi-Fi band, signal in dBm, link rate, retries and missed beacons during the test; or Ethernet speed, duplex and error counters. No SSID, MAC or address is sent.
- **Other traffic**, to tell a bad line from a busy one:
  1. the traffic on the interface in a quiet 3 s before the test;
  2. during each phase, the interface counters minus the bytes the test itself moved (with a margin for headers and retransmits);
  3. whether qBittorrent is running, and, if its Web UI is on, its exact download and upload rates.

  If other traffic is above 5 Mbps before the test, or above max(8 Mbps, 12% of the rate) during it, the test is marked **contended**: a hollow dot on the chart, and the notes say why ("other traffic during upload: 62 Mbps"). Contended tests are kept, but left out of the baseline.
- **Notification:** if a clean (not contended) test falls under half the median of the last clean ones (at least 4 needed, 7 days), one ntfy message says so with the link and latency, at most once every 3 hours.

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

## Other improvements to netq that this setup makes possible

Proposed, not done. In rough order of value:

1. **Alert when the laptop's link is the bottleneck.** Compare the laptop's loaded router latency and Wi-Fi retries with the line. If the router leg degrades but the ISP leg does not, say "your Wi-Fi", not "the ISP". The data is already collected.
2. **Latency-under-load grade** (A-F like the usual bufferbloat tests) on the page, from idle vs loaded latency. Cheap, and the number that best predicts video calls.
3. **Daily and weekly summary** by ntfy or on the page: median speed by hour, worst hour, uptime of the laptop, contended tests excluded. This answers "is the evening slow?" with data.
4. **Pause qBittorrent for the test** (Web API, only when its UI is enabled) for a second, clean result, and resume it. This gives both "what the line can do" and "what I get with the torrent running". Needs your say-so because it touches a running download.
5. **Two vantage points on every outage.** When the Pi sees packet loss, the laptop heartbeat says whether the laptop was online at the same moment and on which link: LAN problem versus line problem.
6. **Wi-Fi roaming and band log** from the heartbeat context (band, signal changes), to find the spots or times when the laptop drops to 2.4 GHz.
7. **Compare against another server** (for example a second CDN) when Cloudflare is slow, to separate "the line" from "the route to Cloudflare". Add as a second target in the test.
8. **Lighter ping test on the laptop between full tests** (a 3 s run every 10 min: idle router and Cloudflare latency, no big downloads), to see Wi-Fi quality continuously at no data cost.
9. **Smaller items:** a Pi-hole load tile next to the speed result (same minute), a laptop-offline note on the home.lan page, the apt/backup jobs shown on the charts as markers, an optional second laptop or desktop using the same token (add a `host` field), and retiring the Pi's own 6-hourly test once this has run for a week (it costs 1.5 GB a month and adds little).
