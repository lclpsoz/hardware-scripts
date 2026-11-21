# Sedentary reminders (steps)

A small container, `steps`, nags you through ntfy when your phone's step counter shows you have not walked for a while. It runs next to the notification setup in 11-notifications (own topic, own port; it does not touch the ntfy or alerts containers).

## How it works

- The phone sends its cumulative step counter to `http://192.168.0.250:8097/steps` from a loop in Termux ([pi/steps/steps-loop.sh](pi/steps/steps-loop.sh)). **The Pi answers each report with `next_s`, how long to wait**: 60 s normally and 10 s while you walk (20 steps per minute or more, kept for 60 s after the last walking). Both intervals and the walking threshold are settings on the web page; the loop never needs editing.
- "Moved" means at least 200 steps within 5 minutes. That resets the sitting timer.
- Escalation by minutes sitting, with a rising sound (all of it editable on the page, defaults below):

  | Level | Sitting | ntfy priority | Sound | Repeats |
  | --- | --- | --- | --- | --- |
  | 1 | 50 min | 2 (low) | none, only in the notification tray | once |
  | 2 | 60 min | 3 (default) | the app's default sound | every 5 min |
  | 3 | 70 min | 4 (high) | sound and vibration | every 2 min |
  | 4 | 80 min and up | 5 (max) | loudest; plus the phone alarm below | every 20 s |

  Android lets you give each priority its own sound (Settings → Apps → ntfy → Notifications): soft for 3, a clear two-tone for 4, the alarm tone for 5, with Do Not Disturb override on 5.
- **Phone alarm** (level 4, switch on the page): the Pi's reply to each phone report says `"alarm": 1`, with the phrase and volume to use, while level 4 is active and nothing is snoozed. The Termux loop then sets the alarm stream to that percentage of its maximum (default 50 %), vibrates and speaks the phrase (default "Time to walk") on the alarm stream (`termux-volume`, `termux-vibrate`, `termux-tts-speak -s ALARM`), so it is heard with the ringer silent. The phone can only react when it reports, so from level 3 (and 2 minutes before level 4) the phone is asked to report every 20 s. Phrase and volume are settings; Debug → *Play the phone alarm once* tests them. Verified on the owner's phone: it speaks on the alarm volume.
- **Buttons.** Every reminder has snooze buttons of **30, 5 and 1 min**, but the long ones go away as you sit longer: 30 min only below 90 min of sitting (1 h 30), 5 min below 110 (1 h 50), and 1 min always. These limits are settings. The page shows the ones currently offered, and the Pi refuses a snooze that is not offered. Every snooze PAUSES the sitting timer: it is frozen and carries on from the same value afterwards (it is not reset). Real walking during a snooze still resets it.
- **Notifications.** Each type has its own title, message, priority and (for levels) repeat interval on the page, with a **Test** button: the four levels, "Moved enough, timer reset", snoozed, snooze ended and phone silent. Texts can use `{min}`, `{snooze}`, `{steps}` and `{stale}`. "Moved enough" is sent when you walk enough while a reminder was active.
- No reminders from 22:00 to 07:00 (America/Sao_Paulo), and none while the phone has been silent for 30 min (you get one low-priority notice).

All numbers are settings. They start from the optional values in `~/steps/config.env` (`LEVELS_MIN`, `REPEAT_S` for level 4, `MOVE_STEPS`, `QUIET_HOURS`) and are changed in the web page below, which wins once saved.

## Web page: http://steps.lan

Shows the sitting timer with the reminder levels, the mode (watching, snoozed, quiet hours, no phone data), snooze buttons, today's steps, reminders, longest sitting and how many times you got up, steps per hour, the last 14 days, and every setting (levels, step threshold and window, quiet hours, snooze lengths and when they are offered, the text, priority and repeat of every notification with a Test button, on/off).

- The page asks for the `STEPS_TOKEN` once and keeps it in the browser. The settings are saved in `~/steps/data/settings.json` and apply at once.
- Stats are kept for a year in `~/steps/data/stats.json` and written at every phone report (and every 15 minutes).
- **Phone data** (1 h by default, with a 1 h / 3 h / 12 h / 24 h switch; long ranges are thinned to about 900 points): *Steps per minute* shows real steps per minute (one bar per minute for 1 h, wider buckets for longer ranges) against the moving rate (green bars count as moving), and *When the phone reported* plots one dot per report with the step counter value, red bands where reports were missing for 6 min or more, and a table of the last 8 reports. A counter that drops means the phone restarted.
- Every report (time and counter value) is kept for 24 hours in `~/steps/data/reports.jsonl`: one line per report, written as it arrives, so a crash or restart of the container loses nothing (tested with `kill -9` and a half-written last line). There is no fsync per report (with reports every 10 s that would wear the SD card), so a power cut can lose the last few seconds. Stats are written at most once a minute. A third value `1` marks a point added by hand; it is drawn as a hollow dot and never counts as the phone reporting. The sitting timer is kept in `state.json` (with a `saved_at` time) and restored on start however old it is; it is dropped only if the service itself was down longer than the phone-silent limit (for example the Pi was off for hours).
- **Debug tools** (collapsed, at the bottom): *Add steps* sends a fake phone report with the counter raised by N (drawn as a hollow dot, so walking detection and the timer reset run for real), and *Set sitting time* pretends you have been sitting M minutes so the reminder levels fire without waiting, and *Play the phone alarm once* sounds the alarm at the phone's next report. Both act on the live service.
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

Token in `X-Token` or `?t=`. `/steps?steps=N`, `POST /snooze?min=5` (only lengths offered right now), `/resume`, `/status`, `/api/state`, `POST /api/settings`, `POST /api/test` (JSON `{"type": "level2"}`), `POST /api/debug/steps`, `POST /api/debug/sitting` and `POST /api/debug/alarm` (JSON `{"n": N}` and `{"min": M}`); `/`, `/health` and `/phone.sh` need no token.

## Phone setup

1. Install the ntfy app, add the server `http://ntfy.lan`, subscribe to the `steps` topic, and in the topic settings let max priority override Do Not Disturb.
2. Install Termux and Termux:API, run `pkg install termux-api curl`, and put `PI=http://192.168.0.250:8097` and `TOKEN=...` in `~/.steps-env`.
3. Check `termux-sensor -l` lists a step counter and that `bash steps-report.sh` makes `/status` show your steps. Verified on the owner's phone (a Samsung): the sensor is called `Step Counter` and a read takes about 2 s.
4. Install the loop and start it on boot (Termux:Boot app):
   `curl -fsS http://192.168.0.250:8097/phone.sh -o ~/steps-loop.sh && mkdir -p ~/.termux/boot && printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nnohup bash ~/steps-loop.sh >/dev/null 2>&1 &\n' > ~/.termux/boot/steps-loop && chmod +x ~/.termux/boot/steps-loop`
   Start it now: `pkill -f steps-report; termux-wake-lock; nohup bash ~/steps-loop.sh >/dev/null 2>&1 &`
   Exempt Termux and Termux:Boot from battery optimisation.

## If the phone loop misbehaves

The loop writes what it does to `~/steps-loop.log` on the phone (kept under about 200 KB): each report with the Pi's reply, and for an alarm the volume it set, the phrase and the exit code of the text-to-speech command (124 = it hung and was stopped after 25 s). Every Termux command has a timeout, so one stuck command cannot freeze the loop. Look with `tail -20 ~/steps-loop.log`; run it in the foreground with `bash -x ~/steps-loop.sh` to see every command. The Pi side shows when the phone last reported on `steps.lan` (Phone data).

## Backup and durability

- The laptop's daily backup ([scripts/backup-pi.sh](scripts/backup-pi.sh), run by the `pi-backup` timer) includes `~/steps`: the code, `config.env` (the secrets, so keep the backup folder private) and all the data. Restore: copy the folder back to `~` on the Pi and run `docker compose up -d`.
- At most one day of data is lost if the SD card dies, nothing if only the container or the Pi restarts or loses power.
- The first start after a restart takes about 45 seconds (0.15 CPU on a 600 MHz Pi), and phone reports sent in that window are refused. The loop sends the next one at its next interval.

## Limits

- Only the phone is measured: if you leave it on the desk while walking, you get reminders anyway (press a snooze, or carry it).
- The port is plain HTTP on the LAN, protected by the token only.
- Undo: `cd ~/steps && docker compose down`.
