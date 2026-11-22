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
| New power supply | It arriving | **Remove the apt limit** (`sudo bash ~/apply-apt-limit-and-uvweb.sh --undo`) after the load test passes. Reboot, run [the step load test](scripts/uv-step-load-test.sh), then lift limits in order: CPU governor, NetAlertX `cpus` 1.0, the 5 min healthchecks (back to 30 s; 22 Nov), the light and throttled backup (22 Nov), dpkg quota ([04](04-resource-limits.md)). Then the 4 pending package upgrades. |
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

### Speed-test client: written, waiting for deploy

Designed and built 2026-11-21: [13-laptop-netprobe.md](13-laptop-netprobe.md). Code is in `laptop/services/netprobe/` and `pi/netq/netq.py`; tested on the laptop and in demo mode. **Not yet on the Pi.** Deploy steps: create `~/netq/config.env` with a token, set `NETQ_TOKEN` in the laptop config, copy `netq.py` and the compose file to `~/netq`, `docker compose up -d`, then `bash raspberry-pi/laptop/install.sh`. The improvement ideas are listed at the end of the doc.
