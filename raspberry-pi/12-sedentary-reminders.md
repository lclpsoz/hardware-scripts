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
- **Phone alarm per level.** Every level has its own phone alarm (a master switch turns them all off): a type (*off*, *Clock timer* or *spoken*), a label or phrase, a volume (% of the alarm stream's maximum) and an *at most once every* interval, so the earlier levels can be quieter and rarer than the last. Defaults: levels 1 and 2 off; **level 3** a timer at 25 % at most every 3 min (`🚶 Walk soon: sitting {min} min`); **level 4** a timer at 50 % at most every minute (`🚶 Time to walk! Sitting {min} min`). A new level sounds at once; the same level repeats after its interval. The *Test* button of a level sends its notification **and** its alarm.
  - The Pi's reply to a phone report says `"alarm": 1` with the level, type, label and volume. The Termux loop sets the alarm stream to that percentage and then either asks the Clock app for a 1-second timer labelled with the text (`am start -a android.intent.action.SET_TIMER --ei android.intent.extra.alarm.LENGTH 1 --es android.intent.extra.alarm.MESSAGE "..." --ez android.intent.extra.alarm.SKIP_UI true`; it rings like any timer until dismissed, so each request makes a new timer and the interval matters; Android needs *Display over other apps* for Termux; verified on the owner's phone) or vibrates and speaks the phrase (`termux-tts-speak -s ALARM`, emojis dropped).
  - **Timer volume is up to the Clock app.** The Pi sets the phone's *alarm* volume, which the spoken alarm and Google's Clock follow, but some Clock apps play timers at a volume of their own, set inside the app (the owner's Samsung Clock seems to). The setting *Clock app for the timers* names the app (an Android package name, for example `com.google.android.deskclock`); the loop then adds `-p <package>` to the timer request, since Android has no global choice of timer app.
  - Labels can have emojis and `{min}` (minutes sitting) and `{time}` (clock time); quotes and backslashes are not allowed.
  - The phone can only react when it reports, so from 2 minutes before the first level with an alarm the phone is asked to report every 20 s.
- **Buttons.** Every reminder has snooze buttons of **30, 5 and 1 min**, but the long ones go away as you sit longer: 30 min only below 90 min of sitting (1 h 30), 5 min below 110 (1 h 50), and 1 min always. These limits are settings. The page shows the ones currently offered, and the Pi refuses a snooze that is not offered. Every snooze PAUSES the sitting timer: it is frozen and carries on from the same value afterwards (it is not reset). Real walking during a snooze still resets it.
- **Notifications.** Each type has its own title, message, priority and (for levels) repeat interval on the page, with a **Test** button: the four levels, "Moved enough, timer reset", snoozed, snooze ended and phone silent. Texts can use `{min}`, `{snooze}`, `{steps}` and `{stale}`. "Moved enough" is sent when you walk enough while a reminder was active; it is priority 4 like level 2 (sound and a pop-up over other apps, so you can stop walking), with the timers dismissed at the same moment.
- No reminders from 22:00 to 07:00 (America/Sao_Paulo), and none while the phone has been silent for 30 min (you get one low-priority notice).

All numbers are settings. They start from the optional values in `~/steps/config.env` (`LEVELS_MIN`, `REPEAT_S` for level 4, `MOVE_STEPS`, `QUIET_HOURS`) and are changed in the web page below, which wins once saved.

## Web page: http://steps.lan

Shows the sitting timer with the reminder levels, the mode (watching, snoozed, quiet hours, no phone data), snooze buttons, today's steps, reminders, longest sitting and how many times you got up, steps per hour, the last 14 days, and every setting (levels, step threshold and window, quiet hours, snooze lengths and when they are offered, the text, priority and repeat of every notification with a Test button, on/off).

- The page asks for the `STEPS_TOKEN` once and keeps it in the browser. The settings are saved in `~/steps/data/settings.json` and apply at once.
- Stats are kept for a year in `~/steps/data/stats.json` and written at every phone report (and every 15 minutes).
- **Phone data** (1 h by default, with a 1 h / 3 h / 12 h / 24 h switch; long ranges are thinned to about 900 points): *Steps per minute* shows real steps per minute (one bar per minute for 1 h, wider buckets for longer ranges) against the moving rate (green bars count as moving), and *When the phone reported* plots one dot per report with the step counter value, red bands where reports were missing for 6 min or more, and a table of the last 8 reports. A counter that drops means the phone restarted.
- Every report (time and counter value) is kept for 24 hours in `~/steps/data/reports.jsonl`: one line per report, written as it arrives, so a crash or restart of the container loses nothing (tested with `kill -9` and a half-written last line). There is no fsync per report (with reports every 10 s that would wear the SD card), so a power cut can lose the last few seconds. Stats are written at most once a minute. A third value `1` marks a point added by hand; it is drawn as a hollow dot and never counts as the phone reporting. The sitting timer is kept in `state.json` (with a `saved_at` time) and restored on start however old it is; it is dropped only if the service itself was down longer than the phone-silent limit (for example the Pi was off for hours).
- **Debug tools** (collapsed, at the bottom): *Add steps* sends a fake phone report with the counter raised by N (drawn as a hollow dot, so walking detection and the timer reset run for real), and *Set sitting time* pretends you have been sitting M minutes so the reminder levels fire without waiting, (the Test button of each level plays that level's phone alarm). Both act on the live service.
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

Token in `X-Token` or `?t=`. `/steps?steps=N`, `POST /snooze?min=5` (only lengths offered right now), `/resume`, `/status`, `/api/state`, `POST /api/settings`, `POST /api/test` (JSON `{"type": "level2"}`), `POST /api/debug/steps`, `POST /api/debug/sitting`, `POST /api/debug/dismiss` and `POST /api/debug/alarm` (JSON `{"n": N}` and `{"min": M}`); `/`, `/health` and `/phone.sh` need no token.

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

## Away from home (phone presence)

The phone's Wi-Fi stays on at home, and the Pi pings it every 60 s (`presence_every_s`; the Pi pinging costs the phone less battery than the phone sending). The phone is found by its Wi-Fi MAC address (`presence_mac`), because its IP changes (it changed once): the Pi tries the address it last had, the optional `presence_ip`, and every address its neighbour table lists for that MAC, and a reply only counts if the table shows the phone's MAC for that address. The phone that sends the step reports is **a different phone** (the main one, which stays home), so its reports do not count as presence; only pings to the street phone do.

- **Away** = no ping answer for `presence_away_min` (default 8) minutes. The sitting timer is then **paused from the last time the phone was seen**, like a snooze: reminders and phone alarms stop and the Clock timers are dismissed at the next report. When the phone is back the timer **carries on from the same value** (not reset). A snooze active at that moment ends when you leave.
- The state is kept in `state.json`, so a restart keeps you away until the next ping answers.
- Every ping is kept for a week in `~/steps/data/pings.jsonl` (about 60 KB a day, no fsync, thinned on the page for long ranges), so a restart does not empty the chart.
- The page shows a banner and a grayed timer while away, and a *Phone at home* card with the last ping, the answered/missed pings of the last 1 to 12 h and the last 6 results. `GET /api/pings?hours=3` gives the data. Settings are on the page (name, MAC, address to try first, ping interval, minutes before away).
- Needs the phone's Wi-Fi on at home and `ping` in the container (the Alpine image has it; it works as uid 1000 because of the host's `ping_group_range`). Phones in power-save answer slowly (about 300 ms) and may miss a ping, which is why one miss is not enough to be away.

### Steps from the street phone

The street phone (also yours) does no looping and holds no wake-lock. Android's job scheduler starts [pi/steps/street-steps.sh](pi/steps/street-steps.sh) (installed by [pi/steps/street-setup.sh](pi/steps/street-setup.sh)): one job every 15 min on any network, and one every 15 min only on an unmetered network (Wi-Fi), which Android runs soon after the phone joins the home Wi-Fi, so nothing polls for Wi-Fi. Each run appends `time counter` to `~/.steps-queue` and tries to send the whole queue in one `POST /steps/batch?device=street`; out of the house the send fails quickly and the samples wait (up to about a week).

- The Pi credits the steps between samples to the day and hour they happened (15-minute detail), keeps the last counter in `street.json` and shows today's street steps and the last upload on the *Phone at home* card.
- **Walking outside resets the sitting timer**: a batch with at least *Steps that count as moving* (200) steps resets it, as real walking does (and sends the "moved" notice if a reminder was active). Steps are only counted by the phone that is carried; if both phones are on you, they are added up.
- **Battery warning**: each upload also carries the phone's battery (`termux-battery-status`, read only when the Pi is reachable, so nothing extra while out). At or below `battery_warn_pct` (40) and not charging, the Pi sends the `battery` notification to the main phone, at most once per its `repeat_s` (840 s, so about every upload, roughly 15 min on the home Wi-Fi), never in quiet hours. The last value shows in `/api/state` (`presence.street.battery`). To change the phone's script: `curl -fsS http://192.168.0.250:8097/street.sh -o ~/street-steps.sh`.
- Presence is still only the Pi's ping, which costs the phone almost nothing (the Wi-Fi chip answers; a phone sending would have to wake the CPU and radio).
- Setup on the phone: install Termux and Termux:API from F-Droid, `pkg install termux-api curl`, put `PI=` and `TOKEN=` in `~/.steps-env`, then `curl -fsS http://192.168.0.250:8097/street.sh -o ~/street-steps.sh` and run `bash ~/street-setup.sh` (it fetches the script and schedules both jobs). Leave Termux's battery setting on *Optimized* (not *Restricted*), or the jobs may never run. Check `tail ~/street-steps.log`.

## Long periods (phone left at home)

If nobody moves for hours, level 4 would repeat forever (with the first settings: 180 ntfy sounds and about 54 timers per hour, about 1,400 notifications and 420 timers over a 9-hour day). The rules below prevent it (settings on the page):

- **A Clock timer keeps ringing until the Clock app stops it** (the owner's Clock app: 10 minutes, a setting inside the Clock app that the Pi cannot change), and every timer request makes a new timer, so they pile up. When no Clock app is named on the page they cannot be cleared, so a new timer then waits *Timer ring time* (default 600 s) after the previous one, whatever the level. With a Clock app named they are cleared instead (next rule) and the alarm interval alone decides, which also fits you having dismissed the timer yourself.
- **Timers are dismissed** from the phone so they cannot pile up: the loop runs `am start -p <clock app> -a android.intent.action.DISMISS_TIMER` before starting a new timer, and the Pi asks it to dismiss them (once, in its reply to the next report) when you walk enough, press a snooze, quiet hours begin, you press the debug button, or the loud phase of level 4 ends. This needs *Clock app for the timers* set on the page (verified with `com.google.android.deskclock`): without a package name Android asks which Clock app to use, so dismissal is skipped.
- **Level 4 backs off**: each repeat waits twice as long as the last, notifications up to *Notification waits grow up to* (default 120 s) and phone alarms up to *Alarm waits grow up to* (default 900 s).
- **Level 4 stays loud for 30 minutes** (*Level 4 stays loud for*, 0 = no limit). After that the phone alarms and loud notifications stop, and only the quiet *Still sitting* notification (priority 2, every 15 min, editable under Notifications) repeats until you move. Moving enough and then sitting again re-arms everything.

Simulated with a fake clock and the owner's settings, a 9-hour day away gives 25 loud notifications (8 from levels 1-3 and 17 in the 30 loud minutes of level 4), 29 quiet ones and 8 timers (3 at level 3, 5 at level 4, each replacing the previous) with a Clock app named, or 3 timers at least 10 minutes apart without one, instead of about 1,390 notifications and 420 timers. The loud phase restarts if the `steps` container is restarted.

## Lock screen

Whether a notification shows on the lock screen is decided by Android, per ntfy priority (each priority is a separate notification category). Priorities 1 and 2 are *silent* and are hidden on the lock screen when Android's *Hide silent notifications* is on; when the phone's sound mode is vibrate or mute, ordinary sound notifications count as silent too. So the defaults avoid priorities 1 and 2 for reminders: level 1 is priority 3, level 2 is 4, levels 3 and 4 are 5 (the confirmations are still 1 and 2). Check Settings → Notifications → Lock screen notifications (show content, *Hide silent notifications* off) and Apps → ntfy → Notifications → each category (lock screen: show, pop-up on). The Clock timer's own ring screen also appears over the lock screen.

## Backup and durability

- The laptop's daily backup ([scripts/backup-pi.sh](scripts/backup-pi.sh), run by the `pi-backup` timer) includes `~/steps`: the code, `config.env` (the secrets, so keep the backup folder private) and all the data. Restore: copy the folder back to `~` on the Pi and run `docker compose up -d`.
- At most one day of data is lost if the SD card dies, nothing if only the container or the Pi restarts or loses power.
- The first start after a restart takes about 45 seconds (0.15 CPU on a 600 MHz Pi), and phone reports sent in that window are refused. The loop sends the next one at its next interval.

## Limits

- Only the phone is measured: if you leave it on the desk while walking, you get reminders anyway (press a snooze, or carry it).
- The port is plain HTTP on the LAN, protected by the token only.
- Undo: `cd ~/steps && docker compose down`.
