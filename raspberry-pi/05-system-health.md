# Other findings and optimizations

Checked 2026-11-20, read-only.

**Bottom line:** the Pi is healthy apart from power. Memory, temperature, time sync and the SD card are fine. The items below are improvements, roughly in order of value.

## Security and exposure

| Item | Finding | Suggestion |
| --- | --- | --- |
| SSH | Key login works. Password login is still allowed (the Debian default; only `KbdInteractiveAuthentication no` is set). | After confirming key login from every machine you use: `echo 'PasswordAuthentication no' \| sudo tee /etc/ssh/sshd_config.d/10-keys-only.conf && sudo sshd -t && sudo systemctl reload ssh`. Keep one session open while testing. |
| NetAlertX | No UI password. Anyone on the LAN can change settings and read the API token. | Turn on the SETPWD plugin ([03-netalertx.md](03-netalertx.md)) |
| Services open to the whole LAN | 22 (SSH), 53 (DNS), 80/443 (Pi-hole), 123/udp (NTP), 5353/udp (mDNS), 8090 (Beszel), 8099 (`uv-web`), 20211/20212 (NetAlertX) | Fine for a home LAN, but the TV and IoT devices share that LAN. The cheapest gain is the NetAlertX password. A host firewall (nftables) allowing only the laptop and phone to reach 8090, 8099 and 20211 is an option for later. |
| Host firewall | `nftables` and `ufw` inactive | See the line above |
| Pi-hole admin password | Set through the compose environment (`FTLCONF_webserver_api_password`), so it is plain text in `~/pihole/docker-compose.yml` | Fine, since only your user reads that folder. Don't commit that file anywhere. |

## Pending updates

- **34 packages are upgradable,** including security updates: `apt`, `libapt-pkg7.0`, `libbrotli1`, `cifs-utils`, plus firmware and libcamera. There is no unattended-upgrades package, so nothing installs them automatically.
- **Do it after the power fix.** An upgrade is long, heavy CPU work while writing to the SD card, and an undervoltage during package writes is the main SD-corruption risk.

  ```bash
  sudo apt update && sudo apt full-upgrade
  ```
- **The Pi 5 kernel is installed** next to the Pi 3 one: `linux-image-rpi-2712`, 33 MB of modules plus a 10 MB kernel image. This is normal on Raspberry Pi OS images. Each kernel update builds files for both, so removing the Pi 5 one halves that work. It is safe on a Pi 3B because it boots `kernel8.img`. Check the plan first with `sudo apt remove --dry-run linux-image-rpi-2712 'linux-image-*-rpi-2712'`.
- **Pi-hole** Core v6.6 and FTL v6.9 are available ([02-pihole.md](02-pihole.md)).

## SD card writes: about 1.46 GB a day

The card is a 64 GB SanDisk made in 04/2024. It shows no MMC or ext4 errors. About 4.8 GB has been written since boot, 78 hours ago.

| Writer (cgroup) | Since boot | Per day | What it is |
| --- | --- | --- | --- |
| NetAlertX container (restarted 36 h ago) | 559 MiB | ~370 MiB | SQLite WAL churn in `app.db` |
| Pi-hole container | 593 MiB | ~180 MiB | `pihole.log` (33-37 MB a day) plus FTL database writes every 60 s |
| Beszel hub | 587 MiB | ~180 MiB | PocketBase SQLite WAL (the database is 1 MB, the WAL 4 MB) |
| systemd-journald | 576 MiB | ~177 MiB | Mostly the HDMI messages (next section) |
| Other (short jobs, kernel, earlier containers) | ~2.4 GB | | apt, logrotate, dpkg backup, the NetAlertX container replaced on 19 Nov |

That is about 530 GB a year. It isn't alarming for a 64 GB card, but it is more than the data justifies. Cheap reductions:

- Fix the TV time-sync storm ([02-pihole.md](02-pihole.md)), which cuts Pi-hole's log and database writes by about a third.
- Fix or unplug the HDMI device, which cuts most journal writes.
- Set `database.DBinterval` from 60 to 300 in Pi-hole, so writes are batched every 5 minutes. You lose at most 5 minutes of history on a crash.

## HDMI connector

- **Symptom:** the HDMI connector reports "connected", but its EDID is 0 bytes. The kernel re-checks it every 10 s and logs `hdmi-audio-codec ... HDMI: Unknown ELD version 0`: 24,440 times since boot, about 8,400 a day. That is most of the journal's volume.
- **Likely cause:** a display that is off, an HDMI switch, or an adapter that asserts hot-plug without identifying itself.
- **If no screen is needed:** unplug it. That also frees the Pi's 5 V rail if it is an active adapter.
- **If you want it headless permanently:** you could also set `gpu_mem=16` and drop the `vc4-kms-v3d` overlay. That frees about 60 MB of RAM, since `vcgencmd get_mem gpu` reports 76 MB now. This is a boot-config change, so it needs a reboot. Only do it if you never use a screen on this Pi.

## Docker

- **No log rotation for three containers.** `pihole`, `beszel` and `beszel-agent` use `json-file` logs without size limits. They are small today (72 KB, 0.4 KB and 3.6 KB), but nothing caps them. NetAlertX already has 5 MB × 2. Add defaults in `/etc/docker/daemon.json`:

  ```json
  { "log-driver": "json-file", "log-opts": { "max-size": "5m", "max-file": "2" } }
  ```

  Restarting Docker briefly stops DNS. The setting only applies to containers created afterwards, so recreate them with `docker compose up -d --force-recreate` in each folder.
- **A leftover `hello-world` container** (exited 9 days ago) and its image can go: `docker rm pedantic_volhard && docker image rm hello-world`.
- **All images use `:latest`.** That's fine for a home box. Pinning a specific release tag from Docker Hub instead makes updates deliberate and easy to roll back.

## Network

- **eth0** runs at 100 Mbps full duplex, as expected for the Pi 3B's USB-attached Ethernet. There are 0 receive errors, 0 CRC errors and 6 transmit drops.
- **The `dropped` receive counter is 2.7M of 14.5M packets (19%).** That isn't packet loss: `rx_nohandler`, `rx_missed_errors` and the kernel softnet drops are all 0. It is most likely frames the Pi has no use for, such as VLAN-tagged traffic from the ISP router. No action needed.
- **Default route** goes via 192.168.0.1, then Claro's carrier-grade NAT gateway 100.66.64.1. The Pi's public IPv4 (from the NetAlertX internet check) is shared with other Claro customers.

## Things that are fine

- **Memory:** 905 MiB total, 415 MiB available. zram swap holds 28 MiB (`swappiness` 60).
- **Disk:** the root filesystem is 8% used (4.4 GB of 58 GB) and mounted `noatime`.
- **Temperature:** 50 °C.
- **Time:** synchronised (systemd-timesyncd), time zone America/Sao_Paulo.
- **Journal:** persistent with a 50 MB cap, 25 MB used.
- **Failed systemd units:** none.
- **Containers:** none restarting. Pi-hole and NetAlertX report healthy.

## Follow-ups, 2026-11-20 evening

### Why HDMI says "connected" with nothing plugged in

You confirmed that nothing is connected. The cause is on the Pi's side:

1. **The hot-plug line reads as asserted.** The kernel's HDMI driver (vc4) reads it through the firmware: line 4 `HDMI_HPD_N` on the GPIO expander, active-low. It says a display is attached.
2. **There is no display data.** No screen answers, so the EDID is 0 bytes and the driver falls back to stock modes (1024x768, 800x600, 848x480, 640x480).
3. **It is checked every 10 s.** That line can't raise an interrupt, so the kernel polls the connector. Each check reads the empty data again and logs `HDMI: Unknown ELD version 0`, about 8,400 lines a day.

So either the board's hot-plug circuit reads "connected" when it shouldn't, or the firmware reports it that way. Nothing outside the Pi is involved. Since the port isn't used, the fix is to turn the connector off.

**This needs root, which I don't have** (sudo asks for a password). Run these yourself, or see the sudo note in the README:

```bash
# immediately, until the next reboot:
echo off | sudo tee /sys/class/drm/card0-HDMI-A-1/status
# permanently: append to the single line of cmdline.txt (keep a backup), then reboot
sudo cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak-hdmi
sudo sed -i '1 s/$/ video=HDMI-A-1:d/' /boot/firmware/cmdline.txt
cat /boot/firmware/cmdline.txt        # must still be ONE line ending in video=HDMI-A-1:d
```

Check after the reboot: `cat /sys/class/drm/card0-HDMI-A-1/status` should print `disconnected`, and `journalctl -k -b | grep -c 'Unknown ELD'` should print 0. Undo: `sudo cp /boot/firmware/cmdline.txt.bak-hdmi /boot/firmware/cmdline.txt` and reboot.

### Are the SD card writes too much?

**No, not for wear.**

- **Normal days:** about 1.46 GB a day, roughly 0.5 TB a year.
- **Today's upgrade:** about 1.8 GB more in ten minutes. That covers the kernel, its initramfs for both the Pi 3 and Pi 5 kernels, and the Docker packages.

SanDisk doesn't publish an endurance figure for standard microSD cards, but even cautious estimates for a 64 GB card are several TB of writes, so wear is years away at this rate (an estimate, not a measurement).

**The real risk to the card is power, not volume.** Today's upgrade wrote files during 20 undervoltage events.

Reductions done or under way:

- **Pi-hole `DBinterval` 60 → 300 (done).** Fewer, larger database commits.
- **The TV's retry storm (paused while it's unplugged)** was about 35% of Pi-hole's query log and database rows.
- **The HDMI fix above** removes most of the journal's lines.
- **netq writes in batches** every 5 minutes.

Re-check in a few days: `pi-checkup.sh` prints MiB/day since boot.

### Impact of the `apt upgrade` you ran (20:32-20:39)

| Check | Result |
| --- | --- |
| Packages | 50 upgraded, including docker-ce and containerd, kernel 6.18.60 (Pi 3 and Pi 5 variants), rpi-eeprom (unused on a Pi 3) and security fixes. 4 camera packages are held back because they need new dependencies (`apt full-upgrade` would add them). Not needed on this Pi. |
| Undervoltage | **20 events in those ten minutes**, close to a whole day's usual count, from the CPU and disk load of unpacking |
| Containers | The Docker upgrade restarted all of them at 20:37, so DNS was down for a few seconds. All came back healthy. |
| File system | No ext4 or MMC errors in the kernel log. `dpkg --audit` is clean (no half-installed packages). |
| Boot files | `kernel8.img` matches the package checksum, and `initramfs8` is identical to its source, so the next boot is safe from that side |
| Reboot | **Pending.** The new kernel 6.18.60 is installed, and 6.18.50 is running. Best done once, after the power supply arrives, together with the HDMI line above. |
| Memory and CPU now | 506 MiB available, swap 58 MiB used, load back below 1 |
