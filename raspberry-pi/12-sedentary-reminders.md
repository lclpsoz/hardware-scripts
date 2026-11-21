# Sedentary reminders (steps)

A small container, `steps`, nags you through ntfy when your phone's step counter shows you have not walked for a while. It runs next to the notification setup in 11-notifications (own topic, own port; it does not touch the ntfy or alerts containers).

## How it works

- The phone sends its cumulative step counter to `http://192.168.0.250:8097/steps` every 2 minutes ([phone/steps-report.sh](phone/steps-report.sh), run in Termux).
- "Moved" means at least 200 steps within 5 minutes. That resets the sitting timer.
- Escalation by minutes sitting: **50** light (ntfy priority 3), **60** mid (4), **70** high (5), **80+** constant (priority 5 every 20 s) until you walk.
- Every reminder has two buttons: **Meeting 60m** and **Nap 30m**. Both pause reminders; the timer restarts when the pause ends.
- No reminders from 22:00 to 07:00 (America/Sao_Paulo), and none while the phone has been silent for 30 min (you get one low-priority notice).

All numbers are settings. They start from the optional values in `~/steps/config.env` (`LEVELS_MIN`, `REPEAT_S`, `MOVE_STEPS`, `QUIET_HOURS`, `MEETING_MIN`, `NAP_MIN`) and are changed in the web page below, which wins once saved.

## Web page: http://steps.lan

Shows the sitting timer with the reminder levels, the mode (watching, snoozed, quiet hours, no phone data), Meeting / Nap / Pause buttons, today's steps, reminders, longest sitting and how many times you got up, steps per hour, the last 14 days, and every setting (levels, repeat interval, step threshold and window, quiet hours, snooze lengths, on/off, a test notification).

- The page asks for the `STEPS_TOKEN` once and keeps it in the browser. The settings are saved in `~/steps/data/settings.json` and apply at once.
- Stats are kept for 30 days in `~/steps/data/stats.json`, written every 15 minutes (and on stop), so the SD card sees few writes. A restart loses at most 15 minutes of stats.
- The card on [home.lan](http://home.lan) shows whether it answers.

## Files

| File | Purpose |
| --- | --- |
| [pi/steps/steps.py](pi/steps/steps.py) | The service (standard library only) |
| [pi/steps/ui.html](pi/steps/ui.html) | The web page |
| [pi/steps/docker-compose.yml](pi/steps/docker-compose.yml) | Container: 32 MB, 0.15 CPU, read-only |
| `~/steps/config.env` (Pi only) | `NTFY_TOPIC` and `STEPS_TOKEN`, both secret |
| [phone/steps-report.sh](phone/steps-report.sh) | Termux script that reports the step counter |

## Endpoints

Token in `X-Token` or `?t=`. `/steps?steps=N`, `/snooze?min=60&kind=meeting|nap`, `/resume`, `/status`, `/api/state`, `POST /api/settings`, `POST /api/test`; `/` and `/health` need no token.

## Phone setup

1. Install the ntfy app, add the server `http://ntfy.lan`, subscribe to the `steps` topic, and in the topic settings let max priority override Do Not Disturb.
2. Install Termux and Termux:API, run `pkg install termux-api curl`, and put `PI=http://192.168.0.250:8097` and `TOKEN=...` in `~/.steps-env`.
3. Check `termux-sensor -l` lists a step counter and that `bash steps-report.sh` makes `/status` show your steps. Verified on the owner's phone (a Samsung): the sensor is called `Step Counter` and a read takes about 2 s.
4. Schedule it every 2 minutes and exempt Termux from battery optimisation.

## Limits

- Only the phone is measured: if you leave it on the desk while walking, you get reminders anyway (press Meeting or Nap, or carry it).
- The port is plain HTTP on the LAN, protected by the token only.
- Undo: `cd ~/steps && docker compose down`.
