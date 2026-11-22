# Internet quality logger (netq)

Running since 2026-11-20 21:06 at **http://net.lan**. It records how the internet connection behaves over time, so a problem has evidence behind it: slow evenings, packet loss, DNS trouble or outages. The code is [pi/netq/netq.py](pi/netq/netq.py), a single Python file with no dependencies, in a `python:3.13-alpine` container ([pi/netq/docker-compose.yml](pi/netq/docker-compose.yml)).

## What it measures

| Every | Probe | What it tells you |
| --- | --- | --- |
| 1 min | Ping (5 packets) to the **router** (192.168.0.1) | LAN and router health |
| 1 min | Ping to the **ISP gateway** (100.66.64.1, Claro's first hop) | The fibre and access network |
| 1 min | Ping to **Cloudflare** (1.1.1.1) and **Google** (8.8.8.8) | The path to the wider internet |
| 1 min | DNS through **Pi-hole** (a popular, usually cached name) | Whether Pi-hole answers fast |
| 1 min | DNS through **Pi-hole** with a new random name each time | The full path: Pi-hole, then the forwarder, then the internet |
| 1 min | DNS to the **encrypted forwarder** (dnsproxy on 127.0.0.1:5053, new random name each time) | Whether the DNS-over-TLS path works and how fast it is |
| 1 min | DNS straight to Cloudflare and Google over plain UDP (a new random name each time) | A control: how fast each upstream is without the forwarder |
| 5 min | HTTPS to Cloudflare: TCP connect, TLS handshake, first byte | Real connection set-up time, including the Pi's own TLS work |
| 6 h, or the button | Speed test: 10 MB download and 2 MB upload from Cloudflare, with ping measured during the download | Throughput and latency under load ("bufferbloat") |

The page shows status tiles, four line charts, a packet-loss strip per target, the speed tests and an incident list (minutes when the router or both Cloudflare and Google stopped answering). Every chart has hover values and a table view.

The ranges are 6 h, 24 h, 7 days and 30 days. Data is kept for 60 days.

## The speed test, and what it can't show

- **The Pi caps it.** The Pi 3B's Ethernet port is 100 Mbps, and real throughput tops out near 94 Mbps. At the current 600 MHz clock, TLS decryption also costs CPU. The first test gave **59.7 Mbps down / 42.0 up**, a Pi limit, not a fibre limit. Expect higher numbers after the power supply arrives and the CPU runs at full speed again.
- **Still useful:**
  - It shows big drops: if a test falls well below the usual number, the line is degraded.
  - Ping during the download tests bufferbloat. The first test showed 31.4 ms idle and 31.8 ms loaded, which is no bufferbloat.
  - The "undervoltage during test" note shows whether the test itself caused a voltage drop.
- **For the real fibre speed,** the laptop now runs the test when the Pi asks: see [13-laptop-netprobe.md](13-laptop-netprobe.md) (first result: 530 / 170 Mbps).
- **Data cost:** about 12 MB per test, 4 times a day, roughly 1.5 GB a month. Set `NETQ_SPEED_EVERY_H: "0"` to stop scheduled tests and keep the button.

## First readings (2026-11-20, 21:10)

| Probe | Value |
| --- | --- |
| Router | 1.1 ms |
| ISP gateway | 2.2 ms |
| Cloudflare / Google ping | 31.6 / 33.4 ms |
| DNS via Pi-hole | 1.8 ms |
| Full lookup at Cloudflare / Google | **38 / 144 ms** |
| HTTPS to Cloudflare (Rio edge, "GIG") | about 255 ms (much of it the Pi's TLS work at 600 MHz) |

Google takes about 4 times as long as Cloudflare for a new name from here. That is one of the reasons for the upstream change suggested in [09-encrypted-dns.md](09-encrypted-dns.md).

## Cost on the Pi

| Resource | Use |
| --- | --- |
| RAM | about 15 MiB (limit 64 MiB) |
| CPU | under 1% on average. The speed test is a short burst, allowed up to 1 core. |
| SD card | Samples stay in RAM and are written every 5 minutes, about 10 rows a minute. The database stays in the low tens of MB at 60 days. |
| Network | about 1 KB a minute for the probes, plus the speed tests above |
| Pi-hole log | Two queries a minute from 127.0.0.1 (the cached-name and new-name probes) |

## Operations

```bash
ssh pi 'docker logs --tail 20 netq'                      # start-up line and errors
curl -s http://net.lan/api/summary | python3 -m json.tool # current status as JSON
curl -s 'http://net.lan/api/data?range=7d' > netq-7d.json # bucketed data for your own analysis
ssh pi 'cd ~/netq && docker compose restart'              # restart (samples are flushed on stop)
```

Change targets or intervals with the `NETQ_*` variables in the compose file, then run `docker compose up -d`. To test the page without a network, run it on a laptop: `NETQ_DB=/tmp/netq.db NETQ_PORT=18098 python3 netq.py --demo` writes 7 days of synthetic data.

The container only listens on 127.0.0.1:8098, so it is reachable through `net.lan` and nothing else. It has no `docker exec` healthcheck, which would cause periodic CPU spikes.

## Update 2026-11-21: probes for the encrypted DNS

Since Pi-hole forwards to dnsproxy ([09](09-encrypted-dns.md)), the page measures that path too:

- **New tile** "Encrypted DNS" (median and failure share, last 15 minutes) and two DNS charts: *your network* (Pi-hole with a cached name, Pi-hole with a new name, the forwarder) and *direct, unencrypted (control)* (Cloudflare, Google). Reading them: if the forwarder slows while the control stays flat, suspect dnsproxy or the TLS path; if all rise together, it's the ISP line.
- **Status:** failures above 10% on any of the three Pi-hole-side probes now turn the status to "Packet loss or DNS failures".
- **Marker:** the change to encrypted upstream happened 2026-11-20 22:07. Earlier DNS history is the plain-UDP era; compare the weeks before and after.
- **Not included:** Pi-hole's "maximum concurrent queries" warnings. They need the Pi-hole API, which requires a password or app password, and netq has none. Read them with `docker exec pihole grep -c "Maximum number" /var/log/pihole/FTL.log` or in Pi-hole's Diagnosis page.
- **Cost:** two more small DNS probes a minute, about 19 MB of RAM for netq in total. Existing history is kept.
- **Palette:** the new line colors passed `validate_palette.js` in light and dark.
