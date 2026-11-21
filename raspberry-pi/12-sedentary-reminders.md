# Sedentary reminders (steps)

A small container, `steps`, nags you through ntfy when your phone's step counter shows you have not walked for a while. It runs next to the notification setup in 11-notifications (own topic, own port; it does not touch the ntfy or alerts containers).

## How it works

- The phone sends its cumulative step counter to `http://192.168.0.250:8097/steps` from a loop in Termux ([pi/steps/steps-loop.sh](pi/steps/steps-loop.sh)). **The Pi answers each report with `next_s`, how long to wait**: 60 s normally and 10 s while you walk (20 steps per minute or more, kept for 60 s after the last walking). Both intervals and the walking threshold are settings on the web page; the loop never needs editing.
- "Moved" means at least 200 steps within 5 minutes. That resets the sitting timer.
- Escalation by minutes sitting: **50** light (ntfy priority 3), **60** mid (4), **70** high (5), **80+** constant (priority 5 every 20 s) until you walk.
- Every reminder has two buttons: **Meeting 60m** and **Nap 30m**. Both PAUSE the sitting timer: it is frozen during the snooze and carries on from the same value afterwards (it is not reset). Real walking during a snooze still resets it.
- No reminders from 22:00 to 07:00 (America/Sao_Paulo), and none while the phone has been silent for 30 min (you get one low-priority notice).

All numbers are settings. They start from the optional values in `~/steps/config.env` (`LEVELS_MIN`, `REPEAT_S`, `MOVE_STEPS`, `QUIET_HOURS`, `MEETING_MIN`, `NAP_MIN`) and are changed in the web page below, which wins once saved.

## Web page: http://steps.lan

Shows the sitting timer with the reminder levels, the mode (watching, snoozed, quiet hours, no phone data), Meeting / Nap / Pause buttons, today's steps, reminders, longest sitting and how many times you got up, steps per hour, the last 14 days, and every setting (levels, repeat interval, step threshold and window, quiet hours, snooze lengths, on/off, a test notification).

- The page asks for the `STEPS_TOKEN` once and keeps it in the browser. The settings are saved in `~/steps/data/settings.json` and apply at once.
- Stats are kept for a year in `~/steps/data/stats.json` and written at every phone report (and every 15 minutes).
- **Phone data** (1 h by default, with a 1 h / 3 h / 12 h / 24 h switch; long ranges are thinned to about 900 points): *Steps per minute* shows real steps per minute (one bar per minute for 1 h, wider buckets for longer ranges) against the moving rate (green bars count as moving), and *When the phone reported* plots one dot per report with the step counter value, red bands where reports were missing for 6 min or more, and a table of the last 8 reports. A counter that drops means the phone restarted.
- Every report (time and counter value) is kept for 24 hours in `~/steps/data/reports.jsonl`: one line per report, written as it arrives, so a crash or restart of the container loses nothing (tested with `kill -9` and a half-written last line). There is no fsync per report (with reports every 10 s that would wear the SD card), so a power cut can lose the last few seconds. Stats are written at most once a minute. A third value `1` marks a point added by hand; it is drawn as a hollow dot and never counts as the phone reporting. The sitting timer is kept in `state.json` (with a `saved_at` time) and restored on start however old it is; it is dropped only if the service itself was down longer than the phone-silent limit (for example the Pi was off for hours).
- The card on [home.lan](http://home.lan) shows whether it answers.

## Files

| File | Purpose |
| --- | --- |
| [pi/steps/steps.py](pi/steps/steps.py) | The service (standard library only) |
| [pi/steps/ui.html](pi/steps/ui.html) | The web page |
| [pi/steps/docker-compose.yml](pi/steps/docker-compose.yml) | Container: 32 MB, 0.15 CPU, read-only |
| `~/steps/config.env` (Pi only) | `NTFY_TOPIC` and `STEPS_TOKEN`, both secret |
| [pi/steps/steps-loop.sh](pi/steps/steps-loop.sh) | The Termux loop; the Pi serves it at `http://192.168.0.250:8097/phone.sh` |
| [phone/steps-report.sh](phone/steps-report.sh) | One-shot version of the same, for testing |

## Endpoints

Token in `X-Token` or `?t=`. `/steps?steps=N`, `/snooze?min=60&kind=meeting|nap`, `/resume`, `/status`, `/api/state`, `POST /api/settings`, `POST /api/test`; `/`, `/health` and `/phone.sh` need no token.

## Phone setup

1. Install the ntfy app, add the server `http://ntfy.lan`, subscribe to the `steps` topic, and in the topic settings let max priority override Do Not Disturb.
2. Install Termux and Termux:API, run `pkg install termux-api curl`, and put `PI=http://192.168.0.250:8097` and `TOKEN=...` in `~/.steps-env`.
3. Check `termux-sensor -l` lists a step counter and that `bash steps-report.sh` makes `/status` show your steps. Verified on the owner's phone (a Samsung): the sensor is called `Step Counter` and a read takes about 2 s.
4. Install the loop and start it on boot (Termux:Boot app):
   `curl -fsS http://192.168.0.250:8097/phone.sh -o ~/steps-loop.sh && mkdir -p ~/.termux/boot && printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nnohup bash ~/steps-loop.sh >/dev/null 2>&1 &\n' > ~/.termux/boot/steps-loop && chmod +x ~/.termux/boot/steps-loop`
   Start it now: `pkill -f steps-report; termux-wake-lock; nohup bash ~/steps-loop.sh >/dev/null 2>&1 &`
   Exempt Termux and Termux:Boot from battery optimisation.

## Backup and durability

- The laptop's daily backup ([scripts/backup-pi.sh](scripts/backup-pi.sh), run by the `pi-backup` timer) includes `~/steps`: the code, `config.env` (the secrets, so keep the backup folder private) and all the data. Restore: copy the folder back to `~` on the Pi and run `docker compose up -d`.
- At most one day of data is lost if the SD card dies, nothing if only the container or the Pi restarts or loses power.
- The first start after a restart takes about 45 seconds (0.15 CPU on a 600 MHz Pi), and phone reports sent in that window are refused. The loop sends the next one at its next interval.

## Limits

- Only the phone is measured: if you leave it on the desk while walking, you get reminders anyway (press Meeting or Nap, or carry it).
- The port is plain HTTP on the LAN, protected by the token only.
- Undo: `cd ~/steps && docker compose down`.
