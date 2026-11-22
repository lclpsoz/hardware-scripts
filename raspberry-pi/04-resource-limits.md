# Resource limits still in place

Checked 2026-11-20, read-only.

**Yes, the Pi is still limited.** The CPU is pinned at half speed, NetAlertX is held to half a core, and the nightly package backup is throttled. **Keep these until the new supply passes the step load test.** Undervoltage still happens at the current settings ([01-undervoltage.md](01-undervoltage.md)), so removing them now would mean more events.

Wi-Fi off is a special case: keep it off regardless of power, because the router's firewall rules are built around the Pi having a single address.

| Limit | Where it lives | State now | Why it was added | What it costs | When to undo |
| --- | --- | --- | --- | --- | --- |
| CPU governor `powersave` (600 MHz instead of up to 1200 MHz) | `cpupower-powersave.service` (enabled, runs at boot) | Active: 600 MHz, `scaling_max_freq` still 1200000 | Undervoltage | Everything runs at half speed, including the NetAlertX pages, scans and `apt` | First, after the supply passes the step test at 600 MHz |
| Wi-Fi off | `dtoverlay=disable-wifi` in `/boot/firmware/config.txt` | Active (no `wlan0`) | Power, **and** the firewall design: the `pihole-v4` permit rule assumes the Pi has one address | Nothing (Ethernet is used) | **Keep.** Undoing it would give the Pi a second address and break the DNS firewall rules |
| Bluetooth off | `dtoverlay=disable-bt` | Active | Power | Nothing (unused) | Keep, unless you want Bluetooth for something |
| NetAlertX limited to half a core | `cpus: "0.5"` in `~/netalertx/docker-compose.yml`, plus `cpu_shares: 256`, `mem_limit: 300m`, `pids_limit: 256` | Active: throttled in 63% of periods | Undervoltage (it was the busiest process) | Pages and scans wait for CPU; plugin start-up takes 8-11 s | Second. Raise to `"1.0"` after the governor change passes the test. Keep `mem_limit` |
| NetAlertX schedules: ARP scan every 10 min, internet check every 15 min, cleanup at 15 min past every 3rd hour; four naming plugins off | NetAlertX settings (`app.conf`) | Active | CPU load | Device names come only from the ARP scan, and presence updates are slower | Optional. These are reasonable values for a Pi 3B anyway |
| `dpkg-db-backup` limited to 20% CPU at `Nice=19` | `/etc/systemd/system/dpkg-db-backup.service.d/limit.conf` | Active | The midnight event | The job takes a few seconds longer | Any time; it is harmless either way |
| Pi-hole and NetAlertX healthchecks every 5 min (image default: 30 s) | `healthcheck: interval: 5m` in `~/pihole/` and `~/netalertx/docker-compose.yml` (22 Nov) | Active | Each check is a `docker exec`, a burst of 1-2 cores for about a second, right before some events | A dead container is noticed up to 15 min later by Docker (the `alerts` container still probes the services every minute); "starting" for 5 min after a recreate | Third, after the NetAlertX limit. Delete the block in both files and `docker compose up -d` |
| cgroup memory accounting (`cgroup_enable=memory cgroup_memory=1`) | `/boot/firmware/cmdline.txt` | Active | Beszel per-container memory | Not a limit | Keep |

Items from the earlier docs that are **not** in effect:

- **`arm_freq=600`:** ignored. `vcgencmd get_config arm_freq` reports 1200.
- **`hdmi_blanking`:** never added.
- **The `pi-health` CSV timer:** never installed.

`arm_boost=1` in `config.txt` only affects a Pi 4 and is harmless here.

## Order to lift them, once the supply is fixed

1. Reboot to clear the sticky flags, and confirm `vcgencmd get_throttled` shows `0x0`.
2. Run [scripts/uv-step-load-test.sh](scripts/uv-step-load-test.sh) at 600 MHz. It must pass all 4 steps.
3. Switch the governor back. These are the steps from the "changes, checks and revert" doc:
   ```bash
   sudo systemctl disable --now cpupower-powersave.service
   echo ondemand | sudo tee /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
   ```
   Run the step test again: it must pass at up to 1200 MHz. Then watch for a day, with zero events as the target. Only after that, `sudo rm /etc/systemd/system/cpupower-powersave.service && sudo systemctl daemon-reload`.
4. Raise the NetAlertX limit to `cpus: "1.0"` with `docker compose up -d` in `~/netalertx`. Watch for a day.
5. Put the healthchecks back to the image default (30 s): delete the `healthcheck:` block, with its comment, from `~/pihole/docker-compose.yml` and `~/netalertx/docker-compose.yml`, then `docker compose up -d` in each directory (short DNS gap for Pi-hole). Check `journalctl -k | grep Undervoltage` for a day.
6. Optionally remove the dpkg quota: `sudo rm -r /etc/systemd/system/dpkg-db-backup.service.d && sudo systemctl daemon-reload`.

If an event comes back at any step, undo that step and treat the supply path as still marginal.
