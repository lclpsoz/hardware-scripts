# Undervoltage: cause, evidence and better observability

Checked 2026-11-20, 19:30 to 20:10, read-only over SSH. Pi: Raspberry Pi 3 Model B Rev 1.2 (`rasp-al-gaib-lite`), Debian 13, kernel 6.18.50, up since 2026-11-17 13:25.

**Bottom line: the power supply path is still the cause.** The Pi had 20 undervoltage events in 78 hours. That is while it is pinned at 600 MHz, half its normal clock. Some events came with only one core busy, and two came with the Pi idle. The software limits from the earlier session only make events rarer. The fix is the supply, the cable, or the connector. The doc "Raspberry Pi 3B undervoltage: changes, checks and revert" says the supply swap fixed it. That is no longer true: the swap cut events from every 20-40 seconds to about 4-6 a day.

## Current state

| Check | Value | Meaning |
| --- | --- | --- |
| `vcgencmd get_throttled` | `0x50000` | Undervoltage and throttling have happened since boot. Neither is happening right now. |
| During each event | `0x50005` | Undervoltage now, and throttled now |
| CPU governor and clock | `powersave`, 600 MHz | Still limited (see [04-resource-limits.md](04-resource-limits.md)) |
| Temperature | 50 °C | Fine, so heat is not a factor |
| USB devices | None besides the built-in hub and Ethernet | Nothing extra draws from the USB ports |
| HDMI | "connected", but the EDID is 0 bytes | Something is plugged in. See "The HDMI device" below. |
| Events | 20 since boot; 17 not counting the 3 I caused | About 4-6 per day after the boot cluster |
| Duration | 4-14 s each (39 s once, at boot) | Measured from "detected" to "normalised" |

## Every event since the last boot

The full table is in [data/undervoltage-events.csv](data/undervoltage-events.csv), and the raw snapshots are in [data/uv-events-snapshots.log](data/uv-events-snapshots.log). Snapshots only exist from 19 Nov, after the `uv-snapshot` service was installed.

| Detected | Lasted | What the Pi was doing (from the snapshot) | Kind |
| --- | --- | --- | --- |
| 20 Nov 19:42:20 | 4 s | One `sqlite3` query from this investigation, about 1 core busy | Load (1 core) |
| 20 Nov 19:40:25 | 6 s | Two `sqlite3` processes from this investigation, about 2 cores | Load |
| 20 Nov 19:38:18 | 4 s | `containerd` 107%, `dockerd` 73%, `sqlite3` 87% (a `docker exec` plus a query) | Load |
| 20 Nov 15:40:59 | 6 s | `pihole-FTL` at 50%, otherwise idle | Light |
| 20 Nov 00:00:11 | 6 s | `gzip` (dpkg-db-backup, already capped at 20%) at 100%, plus NetAlertX `awk`/`sed` (vendor list update) | Load |
| 19 Nov 19:58:54 | 4 s | `apt-get` (the apt-daily timer) at 93% | Load |
| 19 Nov 18:16:43 | 5 s | 91% idle, nothing busy | Idle |
| 19 Nov 16:45:00 | 4 s | NetAlertX backend at 57% | Light |
| 19 Nov 07:16:29 | 14 s | `containerd`, a shim and `dockerd` (a `docker exec`), 11.5% I/O wait | Load |
| 19 Nov 04:40:13 | 4 s | 93% idle, nothing busy | Idle |
| 19 Nov 00:00:05 | 14 s | `gzip` (dpkg-db-backup) 93%, NetAlertX backend 93% | Load |
| 18 Nov 10:01:12 | 6 s | No snapshot. About 12 s after an ARP scan finished. | ? |
| 18 Nov 00:00:10 | 10 s | No snapshot. dpkg-db-backup started 1 s before. | Load (likely) |
| 17 Nov 23:31:00 | 6 s | No snapshot. About 12 s after an ARP scan finished. | ? |
| 17 Nov 19:16:12 | 4 s | No snapshot | ? |
| 17 Nov 13:51 and 13:53 | 10 s, 6 s | No snapshot | ? |
| 17 Nov 13:25 to 13:31 (3 events) | 39 s, 8 s, 4 s | Boot: containers starting, Pi-hole loading 167k queries from its database | Load |

Events per day: 7 on 17 Nov (from 13:25), 2 on 18 Nov, 6 on 19 Nov, and 5 on 20 Nov (3 of them caused by this investigation).

## Why this points at the supply and not at the software

- **A single busy core is enough.** At 19:42 the only busy process was one SQLite query on one core at 600 MHz, and the Pi dropped below the threshold. The event at 19:40 had about 2 cores busy, and the one at 00:00 on 20 Nov had a `gzip` already limited to 20% of a core.
- **The board draws far less than the supply is supposed to deliver.** Raspberry Pi's documentation lists, for the Pi 3 Model B:
  - a 2.5 A recommended supply;
  - about 400 mA "typical bare-board active current consumption";
  - a 5.1 V supply requirement for all models;
  - a warning that triggers below 4.63 V (±5%).

  Source: [Raspberry Pi documentation, Power supply](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html). A healthy 2.5 A supply should not sag below 4.63 V at well under 1 A.
- **Two events happened with the Pi idle** (19 Nov 04:40 and 18:16). No software setting can prevent those. They mean the voltage at the board sits close to the threshold even at idle, and small disturbances push it over.
- **The same documentation names thin cables as a cause:** "the power supply cable could use wires that are too thin". Here is the cable maths for a 1.5 m micro-USB cable at 0.6 A. The gauge of your cable is unknown, so this is only an illustration.

| Cable power wires | Loop resistance (2 × 1.5 m) | Drop at 0.6 A | Voltage at the Pi from a 5.0 V adapter |
| --- | --- | --- | --- |
| 28 AWG (common in cheap cables) | about 0.64 Ω | about 0.38 V | about 4.62 V, below the 4.63 V threshold |
| 24 AWG | about 0.25 Ω | about 0.15 V | about 4.85 V |
| 20 AWG (good Pi supplies) | about 0.10 Ω | about 0.06 V | about 4.94 V |

This is why official Pi supplies output 5.1 V and use a thick, captive cable.

### The power strip

A regulated adapter's output does not depend on the other loads on the strip, as long as the strip and the plug contacts are sound. Two ways the strip can still matter:

- **Mains dips pass through a weak adapter.** A cheap or overloaded adapter can pass a mains dip through to its output, for example when a fridge compressor, a heater or a microwave starts on the same circuit. That would fit the idle events.
- **A worn contact.** A loose strip socket or a worn adapter plug adds resistance.

Both are tested by moving the Pi to a known-good supply on a different outlet.

### The HDMI device

The HDMI connector reports "connected", but the display data (EDID) is empty, 0 bytes. The kernel re-reads the connector every 10 seconds. Each time, it logs `HDMI: Unknown ELD version 0`: 24,440 times since boot, about 8,400 a day, which makes it most of the journal.

If the device is an HDMI-to-VGA or similar active adapter, it is powered from the Pi's own 5 V rail. The "20 of 20 events had an HDMI message in the 30 s before" check means nothing, because there is a message every 10 s anyway. **Question for you: what is plugged into the HDMI port, and is it needed?** Unplugging it for a day is a free test.

## Software that makes bursts worse (only matters while the supply is weak)

| Burst source | When | Notes |
| --- | --- | --- |
| `dpkg-db-backup` (gzip) | 00:00 daily | Capped at 20% CPU with `Nice=19` since 19 Nov, and it still coincided with the 20 Nov midnight event |
| NetAlertX `update_vendors.sh` | 00:00, 06:00, 12:00, 18:00 | Downloads the IEEE vendor list and sorts it twice. At 00:00 it overlaps with the dpkg backup. |
| `apt-get update` (apt-daily) | Random times | 19 Nov 19:58 |
| `apt-daily-upgrade` (apt.systemd.daily) | 06:11 daily (random delay up to 60 min) | 21 Nov 06:11:51: `apt-get` at 90 to 100% of one core for 24 s, with `uv-web` and NetAlertX on top. Two short events (6 s and 4 s). No CPU quota set on this unit. Limited by a `CPUQuota=30%`, `Nice=19`, idle-IO drop-in ([scripts/apply-apt-limit-and-uvweb.sh](scripts/apply-apt-limit-and-uvweb.sh), 21 Nov). **Temporary: remove with `--undo` when the new supply works.** |
| `docker exec` (healthchecks) | Every 30 s for Pi-hole and NetAlertX | Each one wakes `containerd`, the shim and `dockerd` (19 Nov 07:16, 20 Nov 19:38) |
| Boot | First 30 minutes | 5 events on 17 Nov: containers starting, Pi-hole loading 24 h of queries |

Cheap mitigations while you wait for a new supply. These are proposals only, and the commands are in [README.md](README.md#proposed-commands-not-run):

- **Move the dpkg backup off midnight** (to 03:30).
- **Lengthen the two Docker healthchecks** from 30 s to 5 minutes.

These only reduce the count of load-triggered events. They do nothing for idle events.

## Update 22 Nov: the last 24 hours

Checked 2026-11-22 12:10 to 12:25, from the kernel log, `uv-events.log` and the flight recorder (`/var/log/uv-flight/events.jsonl`, 1 sample a second). There were **7 events in 24 hours** (21 Nov 18:15, 22 Nov 00:11, 06:16, 06:26, 07:37, 08:42, 09:19), each 4-6 s. The flight recorder shows the second-by-second load before each one:

| Detected | Just before the drop |
| --- | --- |
| 21 Nov 18:15 | A docker + containerd burst of about 1.8 s of CPU (a healthcheck `docker exec`) |
| 22 Nov 00:11 | A Pi-hole burst at about 90% of a core, 3-4 s earlier |
| 22 Nov 06:16 | A docker + containerd burst of about 1.2 s of CPU |
| 22 Nov 06:26 | Only NetAlertX at its cap |
| 22 Nov 07:37 | A Pi-hole burst at about 84% of a core, 2-3 s earlier |
| 22 Nov 08:42 | Only NetAlertX at its cap |
| 22 Nov 09:19 | The laptop's `pi-backup` (started 09:18:05): `gzip` at 87%, `docker run … tar`, `dockerd` |

- **NetAlertX was at its 0.5 CPU cap in the 1-3 s before all 7 events,** and in about 40% of the seconds sampled. It is the constant background load, not the trigger. I did not find what its python process (about 17% of a core on average) does all the time; reading its log from the host would show.
- **Idle events:** at 4 of the 7 events `top` shows nothing busy. As on 19 Nov, the supply sits just above the threshold, and any small step crosses it.
- The Pi-hole bursts at 00:11 and 07:37 are not explained yet. A guess is the database write (`DBinterval` 300 s); not checked.

**Applied (temporary):** Pi-hole and NetAlertX healthchecks every 5 min instead of 30 s, to remove the `docker exec` bursts ([CHANGES.md](CHANGES.md)). It is too early to show a result. Compare the daily event count over the next days with the 7 above (5-7 on previous days).

**Tried and reverted: compressing the backup on the laptop.** The idea was that `nice` does not stop the Pi-side `gzip` from using a whole core for about 90 s. I changed `backup-pi.sh` to stream plain `tar cf -` and gzip on the laptop, and ran it twice. The old script caused 1 event (09:19). The new one caused 5 events (12:19-12:20) and then 3 (12:20-12:21), with a run time of 55 s and 45 s. Both runs are confounded, because the first overlapped the restart of the two containers and the second started while the load average was still 1.98. Even so, there is no sign of an improvement, and sending 3-4 times more data through ssh encryption on the Pi's CPU probably cancels the gzip saving. The script is back to the original. The next idea, untested: keep gzip on the Pi, but slow the stream from the laptop side (for example `pv -L 2m` on the receiving end) so that the Pi's `tar` and `sshd` run at a low duty cycle. The laptop's 12:19 and 12:20 backups are valid and verified.

Other ideas judged not worth it: lowering NetAlertX to 0.25 CPU (a CPU quota makes the load pulse, so the effect is unknown), `maxcpus=2`, turning the LEDs off (a few tens of mA at most). The governor is already at its 600 MHz minimum and HDMI is already off.

**Free physical tests, probably the most effective:** use the shortest, thickest micro-USB cable available and a known-good 5 V ≥2.5 A charger, plugged straight into the wall; check that the connector is seated. The supply and cable in use now are still unknown (see "What fixes it").

**When the new supply works:** revert the healthcheck change as step 5 in [04-resource-limits.md](04-resource-limits.md#order-to-lift-them-once-the-supply-is-fixed).

## What fixes it

1. **Tell me which supply and cable are used now.** Brand and model, rated voltage and current, captive or separate cable, and its length. Also what else is on the strip.
2. **Replace the supply with the official Raspberry Pi 5.1 V / 2.5 A micro-USB supply,** or another 5.1-5.25 V, ≥2.5 A supply with a short, thick cable (≤1 m, 20-24 AWG power wires). Avoid TV USB ports, USB hubs, phone chargers that output 5.0 V, and long, thin cables.
3. **Plug it into a wall outlet,** or a strip without heavy appliances, for the test.
4. **Reboot,** to clear the sticky flags, then run the step load test below.
5. **Only after it passes, remove the limits in stages** ([04-resource-limits.md](04-resource-limits.md)).

## Better observability (proposals, nothing installed)

### 1. A "flight recorder" for the 60 s before each event

The current `uv-snapshot` service runs `top` after the kernel logs the event. A short burst that caused the drop, such as a `docker exec` or a Python plugin starting, is often over by then.

[scripts/uv-flight-recorder.py](scripts/uv-flight-recorder.py) works differently:

- **Sampling:** once a second, it reads the undervoltage flag (`/sys/class/hwmon/hwmon1/in0_lcrit_alarm`), the per-core CPU use, the CPU time of each service and container (cgroup `cpu.stat`), the clock and the temperature. It starts no subprocesses.
- **Storage:** it keeps 90 seconds in RAM.
- **On an event:** it writes one JSON line per event to `/var/log/uv-flight/events.jsonl`, and a one-line summary to the journal. Each event gets a label: idle, light, or load. The label is based on the busiest single core.
- **Testing:** I dry-ran it on the Pi (3 samples, nothing written) and unit-tested the summary logic on the laptop.
- **Expected cost:** about 1% of one core and 10-15 MB of RAM. It writes nothing to the SD card between events.

Install it when you agree:

```bash
# from the laptop
scp raspberry-pi/scripts/uv-flight-recorder.py raspberry-pi/scripts/uv-flight-recorder.service pi:/tmp/
# on the Pi
sudo install -m 755 /tmp/uv-flight-recorder.py /usr/local/bin/
sudo install -m 644 /tmp/uv-flight-recorder.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now uv-flight-recorder.service
journalctl -u uv-flight-recorder -f            # summary line per event
sudo tail -n 1 /var/log/uv-flight/events.jsonl | python3 -m json.tool | head -40
```

To remove it: `sudo systemctl disable --now uv-flight-recorder && sudo rm /etc/systemd/system/uv-flight-recorder.service /usr/local/bin/uv-flight-recorder.py && sudo rm -r /var/log/uv-flight`.

### 2. A step load test, before and after the supply change

[scripts/uv-step-load-test.sh](scripts/uv-step-load-test.sh) loads 1, 2, 3, then 4 cores for 60 s each, and stops at the first step that triggers an event. It only burns CPU and writes nothing to the SD card. Run it at the Pi, with no updates or backups running.

From this session we already know the current setup fails at step 1, at 600 MHz. After a supply change, the goal is to pass all 4 steps:

1. Pass all 4 steps at 600 MHz.
2. Switch the governor back to `ondemand`.
3. Pass all 4 steps at 1.2 GHz.

### 3. Measure the voltage directly

The Pi 3B cannot measure its own input voltage, and no software can. Two physical options:

- **Multimeter:** red probe on GPIO pin 2 or 4 (5 V), black on pin 6 (GND), while the step test runs. A good supply stays around 4.9-5.1 V under full load. Readings near 4.7 V or lower confirm the supply path.
- **USB inline meter:** only fits if the cable is separate from the adapter (USB-A at the adapter end). It shows the voltage before the cable, so the drop on the cable is not included.

### 4. Look at the red LED

The red power LED's trigger is `input`: it follows a hardware signal, not software activity. On these boards it goes off or flickers when the input is too low. Watching it while a big appliance switches on is a quick test of the "strip disturbance" idea.

### 5. Existing tools and gaps

| Tool | State | Notes |
| --- | --- | --- |
| Kernel log + persistent journal (50 MB cap) | Working | 25 MB used. The journal only starts at this boot (17 Nov 13:25). |
| `uv-snapshot` service | Active | Snapshot after each event, 60 s rate limit |
| `uv-web` phone page (port 8099) | Active | |
| `pi-health` CSV timer (from the monitoring doc) | **Not installed** | The flight recorder covers the same need with better timing |
| Push alert on a drop | Not set up | Could be added to the flight recorder with ntfy (see [06-ideas.md](06-ideas.md)) |
