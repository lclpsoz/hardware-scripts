# Pending things

What is still open on the Pi and the laptop-side services, in priority order. Updated 2026-11-21. Finished work is in [CHANGES.md](CHANGES.md); background and evidence are in the numbered docs.

## 1. Needs the owner now

| Item | What to do |
| --- | --- |
| **Run the root script (apt limit + faster power.lan)** | `sudo bash ~/apply-apt-limit-and-uvweb.sh` on the Pi. Then tell me and I verify it. |
| **Phone: ntfy app** | Install ntfy, add the server `http://ntfy.lan`, subscribe to the topic, allow background use ([11-notifications.md](11-notifications.md#set-up-the-phone-once)). Until then the alerts and backup messages go nowhere. The topic is in `~/.config/pi-ops/config` on the laptop. |
| **Check that notifications arrive** | After subscribing, the "Test from laptop", "Pi alerts started" and "Pi backup OK" messages should show. |
| NetAlertX "new device" alert | Enabled but not yet seen working: it fires when the next unknown device joins. Tell me if it doesn't. |

## 2. Wanted by the owner (done, watch it)

- **ntfy alerts**: done 2026-11-21 ([11-notifications.md](11-notifications.md)). Covers services, DNS, internet (netq), encrypted DNS, undervoltage and the flight recorder, backup age, Pi-hole messages (DNS limit and others), updates, system health, reboots, and new devices. Tune the thresholds after a week of real use.
- **Daily backup with notification**: done 2026-11-21, to the laptop, 14 kept ([laptop/README.md](laptop/README.md)). A **second copy off the laptop** (USB drive or an encrypted cloud copy) is not done: decide whether you want one.

## 3. Waiting on something outside

| Item | Waiting for | Then |
| --- | --- | --- |
| New power supply | It arriving | **Remove the apt limit** (`sudo bash ~/apply-apt-limit-and-uvweb.sh --undo`) after the load test passes. Reboot, run [the step load test](scripts/uv-step-load-test.sh), then lift limits in order: CPU governor, NetAlertX `cpus` 1.0, dpkg quota ([04](04-resource-limits.md)). Then the 4 pending package upgrades. |
| Flight recorder | The next undervoltage event | It notifies you; read `/var/log/uv-flight/events.jsonl` for the minute before ([01](01-undervoltage.md)) |
| Blocklist experiments (HaGeZi Pro is on) | Owner's feedback on broken sites | Allow the names that break; check `*.on.aws` and BetterStack from the work laptop |
| Pi-hole limits (rate limit 1000/min, 150 concurrent) | About a week with the new list, around 2026-11-27 | Pi-hole now notifies you when the limit is hit. Compare with the netq charts; if still hit, raise to 5000 and 512 ([02](02-pihole.md#8-can-the-limits-go-up-follow-up-2026-11-20-evening)) |
| Encrypted DNS | A week of data on net.lan | Compare the weeks before and after 2026-11-20 22:07 ([08](08-internet-quality.md), [09](09-encrypted-dns.md)) |
| TV | Next time it is used | Confirm it works with `pool.ntp.org` allowed; `time.samsungcloudsolution.com` stays blocked |
| Router DHCP reservations | Owner configuring them | Set the static-IP flag in NetAlertX for the tablet and phones |

## 4. Decisions and how-tos

- **Root access:** decided 2026-11-21: **no sudo for the assistant** for now. Root changes are prepared as a script (like `apply-root-changes.sh`) and run by the owner.
- **SSH password login** (open): [how to turn it off](05-system-health.md#how-to-turn-off-ssh-password-login). Key login already works, so the password is only an extra way in.
- **NetAlertX password** (open): [how to set it](05-system-health.md#how-to-set-a-netalertx-password). It is off, and the default if enabled is the well-known `123456`.
- **NetAlertX network tree:** optional; attach each device to the router in the Network tab.

## 5. Future services (not started)

### Speed-test client: the Pi asks the laptop for a full speed test

**Why:** the Pi's own speed test is capped by its 100 Mbps Ethernet port and its 600 MHz CPU (about 94 Mbps at best), so it can't show what the line really delivers. The laptop can. A good measurement also needs the context at the moment of the test.

**What it should do** (the owner's request, to be refined when it is built):

1. **The Pi can request a test from the laptop.** For example a button on net.lan ("Run laptop speed test") and an optional schedule.
2. **The laptop runs a full-speed test** (download, upload, latency under load) with no Pi cap.
3. **At the same moment it records the network health around the test:**
   - **Wi-Fi:** the connected network and band, signal strength, link rate, retries.
   - **Ethernet:** link speed, duplex and error counters, if cabled.
   - **Quality:** latency, jitter and packet loss to the router, the ISP gateway and Cloudflare during the test.
   - **qBittorrent:** whether it is running, and how much it is downloading and uploading **at the same time as the test**, so a low result can be explained ("torrents used 70 Mbps") instead of blamed on the line.
4. **The result goes back to the Pi:** shown on net.lan next to the Pi's own tests (marked as "laptop"), with the context, and optionally a short ntfy message ("Laptop speed test: 480 / 95 Mbps, qBittorrent downloading at 60 Mbps").

**How it would fit the existing pieces:**

- Lives in [laptop/services/](laptop/README.md) as `speedtest/` (its own `run.sh`, systemd user units), using the shared `notify`, `pi` and `state` helpers.
- The laptop can't be reached from the Pi reliably (it moves, it sleeps), so the **laptop asks the Pi**: it polls a small endpoint on net.lan every minute or two for "a test was requested", runs it, and posts the result back. When the laptop is off the request simply waits (and expires).
- Needs a new small endpoint in netq (request, result) and a "laptop" series on the speed chart.
- Tools to evaluate: the Cloudflare speed endpoints netq already uses (no account), `iperf3` to the Pi (limited by the Pi's port, so not for the line), Ookla's or LibreSpeed's CLI; `iw`/`nmcli`/`ethtool` for the link; qBittorrent's Web API (`/api/v2/transfer/info`) for the rates and process check for "running".

**Open questions for then:** which test tool; whether to pause the torrents for a second "clean" run; whether to run on a schedule or only on request; what happens on battery or away from home.

*This is a planned example of a future service for the laptop framework. Nothing is built yet.*

## 6. Proposed, not scheduled

- Docker log rotation defaults (`/etc/docker/daemon.json`), Pi-hole `database.maxDBdays` 30 (the database is 86 MB), slower container healthchecks.
- Tailscale for remote access: it would also let ntfy reach the phone away from home, and give real HTTPS certificates ([06](06-ideas.md)).
- **HTTPS for the `.lan` names** ("we will see when"): options and downsides in [07-services.md](07-services.md#https-for-the-lan-names). Not planned now.
- Other ideas in [06-ideas.md](06-ideas.md).

## 7. Decided and closed

- TV name: "Samsung TV (Home)" is fine.
- Ray's devices: no Guest group; named `Ray <model>` with owner Ray ([03](03-netalertx.md#device-naming-pattern-set-2026-11-20)).
- HDMI: off, fine as it is.
- Pi-hole limits: left open on purpose, see the table above.
- Moving the dpkg-db-backup timer off midnight: **dropped.** It finishes in 5 seconds under its 20% CPU quota, and only 3 of the 52 recorded undervoltage events happened in the midnight hour, so it isn't a real cause.
- Housekeeping: this repo has many commits on `main`, not pushed; it is public, so check before pushing.
