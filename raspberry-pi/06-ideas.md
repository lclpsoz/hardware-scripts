# More things this Pi could do

Written 2026-11-20. These are suggestions only; nothing is installed.

**Bottom line:** after the power fix, this Pi 3B has room for two or three more small services. It has about 400 MB of RAM free and its CPU is mostly idle. The best fits are the ones that extend what you already do: controlling and watching your home network, and diagnosing hardware.

Constraints that shape the list:

- **1 GB of RAM,** already shared by Pi-hole, NetAlertX and Beszel.
- **A slow CPU,** which also triggers undervoltage under load until the supply is fixed.
- **100 Mbps Ethernet and USB 2.0.**
- **Claro carrier-grade NAT:** nothing from the internet can reach the Pi directly, and port forwarding will not work.

## Good fits, best first

| Idea | What you get | Cost on this Pi | Why it fits you |
| --- | --- | --- | --- |
| **Tailscale** | SSH, Pi-hole, Beszel and NetAlertX from anywhere. Use Pi-hole's filtering on your phone over mobile data. | About 30-40 MB of RAM, near-zero CPU when idle | Works through Claro's NAT with no port forwarding. Closes the "devices on mobile data" gap listed in your DNS lockdown runbook (section 9). Optionally, the Pi becomes a "subnet router" so you can reach the router's admin page remotely. |
| **Encrypted upstream DNS** (a small DNS-over-TLS forwarder such as `dnsproxy`, or `unbound` in forwarding mode) | Claro can no longer see or tamper with your DNS. Uses one long-lived connection instead of hundreds of separate UDP flows through the NAT. | About 20-40 MB of RAM | Directly helps the unanswered-query bursts behind the "150 concurrent" warning ([02-pihole.md](02-pihole.md)). Avoid `unbound` as a full recursive resolver here: it would mean more upstream queries and slower cold lookups on this CPU. |
| **Phone alerts with ntfy** | A push notification when the Pi has an undervoltage event, NetAlertX sees a new device, or the Pi or Pi-hole goes down | Nothing extra with the public ntfy.sh server (use a long random topic name, since topics are public). About 20 MB to self-host. | NetAlertX has a built-in ntfy publisher, and Beszel's alerts can send to ntfy. The proposed flight recorder can post one line per event. |
| **Internet quality logger** | Every 30-60 s: latency and loss to the router, Claro's NAT gateway (100.66.64.1), 1.1.1.1 and 8.8.8.8, plus DNS lookup time. Stored as a CSV and a small phone page, like `uv-web`. | A small Python script: a few MB. Gatus (status page plus alerts) is about 20-30 MB. | Gives you evidence when Claro has problems, and shows whether DNS bursts line up with packet loss. Skip bandwidth tests: the Pi's 100 Mbps port caps them. |
| **Backups and config as code** | Nightly copy of the Pi-hole Teleporter export, the three compose files, NetAlertX's `app.conf` and CSV backup, and the Beszel data, to the laptop or this repo (without secrets) | Seconds of CPU a night | An SD card that has lived through months of undervoltage is the most likely part to fail. With this, a rebuild takes an hour instead of a weekend. |
| **Local names** | `pi.lan`, `beszel.lan`, `netalertx.lan` and `router.lan` instead of IPs and ports | None (Pi-hole local DNS records) | A five-minute convenience |

## Fun or hardware projects, after the power fix

| Idea | What it is | Notes |
| --- | --- | --- |
| **Power logging with the Tuya device** (if it is a smart plug with energy metering) | Read watts locally with `tinytuya` and log them next to the Pi's undervoltage events | Matches your power-estimation scripts. Plugging the strip's other loads into it would test the "appliance on the strip" theory for the idle events. |
| **Flight tracking (ADS-B)** with an RTL-SDR USB dongle | Live map of nearby aircraft. You can feed FlightAware or Flightradar24, which give feeders a free premium account. | The classic Pi 3B project. The dongle draws a few hundred mA from USB, so only after the supply passes the step test. |
| **Wake-on-LAN relay** | Wake the laptop or another PC from the phone (through Tailscale when away) | A one-line command or a tiny web button |

## Not a good fit for this Pi

- **Home Assistant:** needs more RAM than is left. Use a Pi 4/5 or a mini PC.
- **Jellyfin, Plex, Immich or Nextcloud:** no RAM for them, and USB 2.0 storage is slow.
- **Acting as your router or firewall:** your Pi-hole doc already covers why. The single 100 Mbps port shares the USB bus.
- **Netdata or Prometheus with Grafana:** too much RAM, which is why you chose Beszel.
- **ntopng:** heavy, and it needs a mirrored port that the ZTE router can't provide.

If you want several of those, a used mini PC (for example an Intel N100) or a Pi 4/5 would be the next box. This Pi 3B would stay as a dedicated DNS and monitoring device, which is what it does best.
