#!/usr/bin/env python3
"""steps: nags you through ntfy when you sit too long. Standard library only.

The phone posts its cumulative step counter to /steps. If fewer than move_steps
steps happen within move_window_s seconds, the "sitting" timer keeps running.
Escalation (minutes sitting): level 1 light (ntfy prio 3), 2 mid (4), 3 high (5),
4 constant (prio 5 every repeat_s seconds) until you walk.
Snooze (30/5/1 min, the long ones only while you have not sat too long) PAUSES the
sitting timer; it carries on from where it was when the snooze ends.

Presence: the service pings the street phone (found by its Wi-Fi MAC address) every 60 s. This is not
the phone that sends the step reports (that one stays home). If no ping reply has been seen for presence_away_min minutes, you are "away": the sitting
timer is PAUSED (like a snooze) and nothing is sent until the phone is seen again.

Settings (editable in the web page, saved to /data/settings.json) start from the
environment variables below. Daily stats are kept in /data/stats.json (30 days),
written every 15 minutes when changed, so the SD card sees very few writes.

Endpoints (token in header X-Token or ?t=):
  GET  /  or /ui              web page (no token; it asks for the token itself)
  GET  /steps?steps=N         phone reports its cumulative step counter; the reply says
                              {"next_s": seconds to wait} (normal or walking interval)
  POST /steps/batch?device=street  the street phone's backlog {"samples": [[unix time, counter], ...]}, sent when it is
                              home again; the steps are credited to the stats and enough of them reset the timer
  POST /snooze?min=15         pause for one of the snooze lengths offered right now
  GET  /resume                cancel the snooze
  GET  /status                small JSON state
  GET  /api/state             everything the page shows
  GET  /api/samples?hours=1   phone reports (time, counter) of the last 1-24 h, thinned to ~900 points
  GET  /phone.sh              the Termux loop script (no token)
  POST /api/settings          JSON body with any settings, validated
  POST /api/test              {"type": "level2"|"moved"|...} send that notification
  GET  /api/pings?hours=3     ping results of the last 1-168 h (kept in /data/pings.jsonl, so a restart keeps them)
  POST /api/debug/steps       {"n": N} fake report with the counter raised by N (hollow dot)
  POST /api/debug/sitting     {"min": M} pretend you have been sitting M minutes
  POST /api/debug/dismiss     the phone dismisses its Clock timers at the next report
  POST /api/debug/alarm       {"level": 3} play that level's phone alarm once, at the next report
  GET  /health                no token
"""
import copy
import hmac
import re
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

E = os.environ.get
NTFY_URL = E("NTFY_URL", "http://127.0.0.1:2586").rstrip("/")
TOPIC = E("NTFY_TOPIC", "")
TOKEN = E("STEPS_TOKEN", "")
PORT = int(E("STEPS_PORT", "8097"))
PUBLIC = E("STEPS_PUBLIC_URL", "http://192.168.0.250:8097")  # used by the snooze buttons
DATA = E("DATA_DIR", "/data")
UI_FILE = E("UI_FILE", "/app/ui.html")
PHONE_FILE = E("PHONE_FILE", "/app/phone.sh")
STREET_FILE = E("STREET_FILE", "/app/street.sh")
KEEP_DAYS = 365
KEEP_REPORTS_H = 24
KEEP_PINGS_H = 168          # a week of pings: about 1,440 lines (60 KB) a day
ARP_FILE = E("ARP_FILE", "/proc/net/arp")


def _quiet_default():
    a, _, b = E("QUIET_HOURS", "22:00-07:00").partition("-")
    return a or "22:00", b or "07:00"


QS, QE = _quiet_default()
# Every notification the service can send. {min} = minutes sitting, {snooze} = snooze minutes,
# {stale} = phone-silent limit, {steps} = steps counted. Priorities: 1 min, 2 low (silent),
# 3 default, 4 high, 5 max. repeat_s (levels only): 0 = send once, else repeat while the level lasts.
NOTIF_DEFAULTS = {
    # Levels 1-4 also have a phone alarm: alarm_mode off | timer (a 1-second Clock-app timer labelled with
    # alarm_text) | speech (text-to-speech); alarm_volume in % of the alarm stream; alarm_repeat_s = at most
    # one alarm per this many seconds. {min} = minutes sitting, {time} = clock time in the label.
    "level1": {"title": "Stand up", "msg": "Sitting for {min} min. Walk a bit.", "prio": 3, "repeat_s": 0,
               "alarm_mode": "off", "alarm_text": "\U0001F6B6 Stand up, {min} min sitting", "alarm_volume": 20, "alarm_repeat_s": 300},
    "level2": {"title": "Move now", "msg": "Sitting for {min} min. Walk a bit.", "prio": 4, "repeat_s": 300,
               "alarm_mode": "off", "alarm_text": "\U0001F6B6 Walk a bit, {min} min sitting", "alarm_volume": 25, "alarm_repeat_s": 300},
    "level3": {"title": "MOVE NOW", "msg": "Sitting for {min} min. Get up and walk.", "prio": 5, "repeat_s": 120,
               "alarm_mode": "timer", "alarm_text": "\U0001F6B6 Walk soon: sitting {min} min", "alarm_volume": 25, "alarm_repeat_s": 180},
    "level4": {"title": "MOVE NOW", "msg": "{min} min without moving. Get up!", "prio": 5,
               "repeat_s": int(E("REPEAT_S", "20")),
               "alarm_mode": "timer", "alarm_text": "\U0001F6B6 Time to walk! Sitting {min} min", "alarm_volume": 50, "alarm_repeat_s": 60},
    "moved": {"title": "Good", "msg": "Moved enough, timer reset. You can stop walking.", "prio": 4},
    "snoozed": {"title": "Snoozed", "msg": "Snoozed {snooze} min. The sitting timer is paused.", "prio": 1},
    # After the loud phase of level 4 (loud_limit_min) all sound stops and this quiet one repeats instead.
    "giveup": {"title": "Still sitting", "msg": "{min} min without moving. Sounds are paused until you move.", "prio": 2, "repeat_s": 900},
    "snooze_end": {"title": "Snooze ended", "msg": "Snooze over, the sitting timer continues at {min} min.", "prio": 2},
    # Street phone battery: sent when an upload shows it at or below battery_warn_pct and unplugged; repeat_s = min gap.
    "battery": {"title": "Charge the street phone", "msg": "Street phone battery is at {battery}%. Plug it in.", "prio": 4, "repeat_s": 840},
    "silent": {"title": "Steps: phone silent", "msg": "No step data from the phone for {stale} min; reminders paused.", "prio": 2},
}
TAGS = {"level1": "walking", "level2": "warning", "level3": "rotating_light", "level4": "rotating_light",
        "moved": "tada", "snoozed": "zzz", "snooze_end": "alarm_clock", "giveup": "hourglass_flowing_sand", "silent": "warning", "battery": "battery"}
DEFAULTS = {
    "enabled": True,
    "levels_min": [float(x) for x in E("LEVELS_MIN", "50,60,70,80").split(",")],
    "move_steps": int(E("MOVE_STEPS", "200")),
    "move_window_s": int(E("MOVE_WINDOW_S", "300")),
    "stale_min": int(E("STALE_S", "1800")) // 60,
    "quiet_enabled": True,
    "quiet_start": QS,
    "quiet_end": QE,
    # snooze buttons: [minutes, offered only while sitting under this many minutes (0 = always)]
    "snooze_opts": [[30, 90], [5, 110], [1, 0]],
    "phone_alarm": True,                # master switch for the phone alarms of the levels
    "alarm_ring_s": 600,                # how long a Clock timer rings by itself (Clock app: 10 min); only used when no Clock app is named
    "loud_limit_min": 30,               # level 4 stays loud this long (0 = no limit), then only the quiet notification
    "backoff_max_s": 900,               # level 4 phone alarms double their wait each time, up to this many seconds
    "notif_backoff_max_s": 120,         # level 4 notifications double their wait each time, up to this many seconds
    "alarm_package": "",                # Clock app that gets the timer request (blank = the phone's choice), e.g. com.google.android.deskclock
    "notif": NOTIF_DEFAULTS,
    "report_idle_s": int(E("REPORT_IDLE_S", "60")),     # how often the phone should report normally
    "report_walk_s": int(E("REPORT_WALK_S", "10")),     # ... while you are walking
    "walk_spm": int(E("WALK_SPM", "20")),               # steps per minute that count as walking
    "presence_enabled": True,                           # pause the timer while the phone is not on the home network
    "presence_name": E("PRESENCE_NAME", "Street phone"),
    "presence_mac": E("PRESENCE_MAC", "").lower(),      # the street phone's Wi-Fi address (its IP changes); empty = presence check off
    "presence_ip": E("PRESENCE_IP", ""),                # optional extra address to try first
    "presence_every_s": 60,                             # ping the phone this often
    "battery_warn_pct": 40,                             # street phone: warn at or below this battery % (when not charging)
    "presence_away_min": 8,                             # no ping reply for this long = away
}
WALK_HOLD_S = 60      # keep reporting fast this long after the last walking seen
SET = copy.deepcopy(DEFAULTS)

lock = threading.Lock()
S = {"last_move": time.time(), "snooze_start": 0.0, "snooze_until": 0.0, "snooze_kind": "", "last_sample": 0.0, "walk_until": 0.0, "stats_flush": 0.0, "alarm_test": 0, "last_alarm": 0.0, "last_alarm_level": 0,
     "l4_since": 0.0, "l4_repeat": 0, "l4_alarm_repeat": 0, "quiet_phase": False,
     "timers_out": False, "dismiss": False,
     "street": {"last_t": 0.0, "last_n": None, "uploaded_at": 0.0, "battery": None, "plugged": None},
     "batt_told": 0.0,
     "away": False, "away_start": 0.0, "last_home": time.time(), "phone_ip": "", "last_ping": None,
     "stale_told": False, "level_sent": 0, "last_repeat": 0.0, "steps": None, "started": time.time()}
samples = deque()
stats = {}            # "YYYY-MM-DD" -> day record
stats_dirty = False
reports = deque()     # [unix time, counter value(, 1 if added by hand)] of every phone report, last 24 h
pings = deque()       # [unix time, 1 if the phone answered else 0, round-trip ms or None, address tried] every presence ping, last 7 days
pings_appended = 0    # lines appended to pings.jsonl since it was last rewritten
appended = 0          # lines appended to reports.jsonl since it was last rewritten compactly


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ---------- persistence ----------
def write_json(name, obj):
    path = os.path.join(DATA, name)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        log("not saved:", name, e)


def read_json(name):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state():
    """Snooze and the time of the last movement, so a restart does not reset the sitting timer."""
    write_json("state.json", {"snooze_until": S["snooze_until"], "snooze_kind": S["snooze_kind"],
                              "snooze_start": S["snooze_start"],
                              "last_move": S["last_move"], "saved_at": time.time(),
                              "away": S["away"], "away_start": S["away_start"], "last_home": S["last_home"],
                              "phone_ip": S["phone_ip"]})


def save_settings():
    write_json("settings.json", SET)


def append_report(rec):
    """One line per report, flushed to disk at once: a crash or power cut loses nothing."""
    global appended
    try:
        with open(os.path.join(DATA, "reports.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")   # no fsync: one per report would wear the SD card
        appended += 1
    except OSError as e:
        log("report not saved:", e)


def append_ping(rec):
    """One line per ping, no fsync (a power cut can lose the last few)."""
    global pings_appended
    try:
        with open(os.path.join(DATA, "pings.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
        pings_appended += 1
    except OSError as e:
        log("ping not saved:", e)


def compact_pings():
    """Rewrite pings.jsonl with only the pings still kept."""
    global pings_appended
    path = os.path.join(DATA, "pings.jsonl")
    try:
        with open(path + ".tmp", "w") as f:
            for x in pings:
                f.write(json.dumps(x) + "\n")
        os.replace(path + ".tmp", path)
        pings_appended = 0
    except OSError as e:
        log("pings not compacted:", e)


def compact_reports():
    """Rewrite reports.jsonl with only the reports still kept (last 24 h)."""
    global appended
    path = os.path.join(DATA, "reports.jsonl")
    try:
        with open(path + ".tmp", "w") as f:
            for r in reports:
                f.write(json.dumps(r) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(path + ".tmp", path)
        appended = 0
    except OSError as e:
        log("reports not compacted:", e)


def flush_stats(force=False):
    global stats_dirty
    S["stats_flush"] = time.time()
    if stats_dirty or force:
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
        for k in [k for k in stats if k < cutoff]:
            del stats[k]
        write_json("stats.json", stats)
        stats_dirty = False


def load():
    d = read_json("state.json") or {}
    S["snooze_until"] = float(d.get("snooze_until", 0))
    S["snooze_kind"] = d.get("snooze_kind", "")
    S["snooze_start"] = float(d.get("snooze_start", 0))
    saved = read_json("settings.json") or {}
    ok, _ = validate(saved)
    SET.update(ok)
    S["phone_ip"] = str(d.get("phone_ip") or "")
    lh = float(d.get("last_home") or 0)
    if 0 < lh <= time.time() and time.time() - lh < 86400:     # when the phone was last seen, so a restart does not give 5 min of grace
        S["last_home"] = lh
    if d.get("away") and float(d.get("away_start") or 0) > 0:      # stay away across a restart; the next ping decides
        S["away"], S["away_start"], S["last_home"] = True, float(d["away_start"]), float(d.get("last_home") or d["away_start"])
        log("restored: away since", time.strftime("%H:%M", time.localtime(S["away_start"])))
    lm, t = d.get("last_move"), time.time()
    try:
        saved = float(d.get("saved_at") or os.path.getmtime(os.path.join(DATA, "state.json")))
    except OSError:
        saved = 0.0
    # Keep the old timer however old it is (you may have sat for an hour). Drop it only when the
    # service itself was down longer than the phone-silent limit (Pi off for hours): then we know nothing.
    if isinstance(lm, (int, float)) and 0 < t - lm and 0 <= t - saved <= SET["stale_min"] * 60:
        S["last_move"] = lm
        log("sitting timer restored:", round((t - lm) / 60, 1), "min")
    else:
        log("sitting timer not restored (service was down", round((t - saved) / 60), "min)")
    rp = []
    try:
        with open(os.path.join(DATA, "reports.jsonl")) as f:
            for line in f:
                try:
                    rp.append(json.loads(line))
                except ValueError:
                    pass                  # a half-written last line after a power cut
    except OSError:
        rp = read_json("reports.json") or []   # older format
    cut = time.time() - KEEP_REPORTS_H * 3600
    good = sorted((x for x in rp if isinstance(x, list) and len(x) in (2, 3) and x[0] >= cut),
                  key=lambda x: x[0])
    reports.extend(good)
    if reports:
        compact_reports()
    real = [x for x in reports if len(x) == 2]       # reports added by hand do not count as the phone
    if real:                                          # a restart must not make the phone look silent
        S["last_sample"], S["steps"] = float(real[-1][0]), real[-1][1]
    try:
        with open(os.path.join(DATA, "pings.jsonl")) as f:
            for line in f:
                try:
                    x = json.loads(line)
                    if isinstance(x, list) and len(x) == 4 and x[0] >= time.time() - KEEP_PINGS_H * 3600:
                        pings.append(x)
                except ValueError:
                    pass                  # a half-written last line after a power cut
    except OSError:
        pass
    if pings:
        compact_pings()
        S["last_ping"] = pings[-1]
    sj = read_json("street.json")
    if isinstance(sj, dict) and isinstance(sj.get("last_n"), int):
        S["street"].update({k: sj[k] for k in ("last_t", "last_n", "uploaded_at", "battery", "plugged") if k in sj})
    st = read_json("stats.json")
    if isinstance(st, dict):
        stats.update(st)


# ---------- settings ----------
def validate(d):
    """Return (clean dict of valid keys, list of error strings)."""
    out, err = {}, []

    def num(key, lo, hi, cast=int):
        if key in d:
            try:
                v = cast(d[key])
                if not lo <= v <= hi:
                    raise ValueError
                out[key] = v
            except (TypeError, ValueError):
                err.append(f"{key} must be between {lo} and {hi}")

    def hhmm(key):
        if key in d:
            v = str(d[key])
            try:
                h, m = v.split(":")
                if not (0 <= int(h) < 24 and 0 <= int(m) < 60 and len(v) == 5):
                    raise ValueError
                out[key] = v
            except ValueError:
                err.append(f"{key} must look like 07:30")

    for key in ("enabled", "quiet_enabled", "phone_alarm", "presence_enabled"):
        if key in d:
            out[key] = bool(d[key])
    num("move_steps", 10, 5000)
    num("move_window_s", 60, 3600)
    num("stale_min", 5, 240)
    if "alarm_package" in d:
        pk = str(d["alarm_package"]).strip()
        if re.fullmatch(r"[A-Za-z0-9._]{0,80}", pk):
            out["alarm_package"] = pk
        else:
            err.append("alarm_package must be an Android package name such as com.google.android.deskclock (or empty)")
    num("alarm_ring_s", 10, 1800)
    num("loud_limit_min", 0, 1440)
    num("backoff_max_s", 20, 3600)
    num("notif_backoff_max_s", 20, 3600)
    num("report_idle_s", 5, 600)
    num("report_walk_s", 2, 120)
    num("walk_spm", 5, 200)
    num("presence_every_s", 10, 300)
    num("presence_away_min", 1, 240)
    num("battery_warn_pct", 5, 90)
    if "presence_mac" in d:
        mac = str(d["presence_mac"]).strip().lower()
        if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            out["presence_mac"] = mac
        else:
            err.append("presence_mac must look like aa:bb:cc:dd:ee:ff")
    if "presence_ip" in d:
        ip = str(d["presence_ip"]).strip()
        if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}|", ip) and all(int(x) < 256 for x in ip.split(".") if x):
            out["presence_ip"] = ip
        else:
            err.append("presence_ip must be an IPv4 address such as 192.168.0.6 (or empty)")
    if "presence_name" in d:
        nm = str(d["presence_name"]).strip()
        if 1 <= len(nm) <= 40:
            out["presence_name"] = nm
        else:
            err.append("presence_name must be 1-40 characters")
    hhmm("quiet_start")
    hhmm("quiet_end")
    if "levels_min" in d:
        try:
            lv = [float(x) for x in d["levels_min"]]
            if len(lv) != 4 or lv != sorted(lv) or len(set(lv)) != 4 or lv[0] < 1 or lv[3] > 600:
                raise ValueError
            out["levels_min"] = lv
        except (TypeError, ValueError):
            err.append("levels_min needs 4 increasing minutes, each between 1 and 600")
    if "snooze_opts" in d:
        try:
            ops = [[int(m), int(b)] for m, b in d["snooze_opts"]]
            if not 1 <= len(ops) <= 6 or any(not 1 <= m <= 600 or not 0 <= b <= 1000 for m, b in ops):
                raise ValueError
            out["snooze_opts"] = ops
        except (TypeError, ValueError):
            err.append("snooze_opts needs 1-6 pairs: minutes 1-600 and a sitting limit 0-1000 (0 = no limit)")
    if "notif" in d:
        base = copy.deepcopy(SET["notif"])
        try:
            if not isinstance(d["notif"], dict):
                raise ValueError("notif must be an object")
            for typ, v in d["notif"].items():
                if typ not in base or not isinstance(v, dict):
                    raise ValueError(f"unknown notification {typ}")
                for k, lo, hi in (("title", 1, 60), ("msg", 1, 200)):
                    if k in v:
                        txt = str(v[k]).strip()
                        if not lo <= len(txt) <= hi:
                            raise ValueError(f"{typ} {k} must be {lo}-{hi} characters")
                        base[typ][k] = txt
                if "prio" in v:
                    p = int(v["prio"])
                    if not 1 <= p <= 5:
                        raise ValueError(f"{typ} priority must be 1-5")
                    base[typ]["prio"] = p
                if "alarm_mode" in base[typ]:
                    if "alarm_mode" in v:
                        if v["alarm_mode"] not in ("off", "timer", "speech"):
                            raise ValueError(f"{typ} alarm type must be off, timer or speech")
                        base[typ]["alarm_mode"] = v["alarm_mode"]
                    if "alarm_text" in v:
                        txt = str(v["alarm_text"]).strip()
                        # emojis allowed; no quotes, backslashes or control characters (the phone script passes it on)
                        if not (1 <= len(txt) <= 60) or re.search(r'["\\`]|[\x00-\x1f]', txt):
                            raise ValueError(f"{typ} alarm label must be 1-60 characters without quotes, backslashes or line breaks")
                        base[typ]["alarm_text"] = txt
                    for k, lo, hi in (("alarm_volume", 10, 100), ("alarm_repeat_s", 10, 3600)):
                        if k in v:
                            x = int(v[k])
                            if not lo <= x <= hi:
                                raise ValueError(f"{typ} {k} must be {lo}-{hi}")
                            base[typ][k] = x
                if "repeat_s" in v and "repeat_s" in base[typ]:
                    r = int(v["repeat_s"])
                    if not 0 <= r <= 3600:
                        raise ValueError(f"{typ} repeat must be 0-3600 s (0 = once)")
                    base[typ]["repeat_s"] = r
            out["notif"] = base
        except (TypeError, ValueError) as e:
            err.append(str(e))
    return out, err


# ---------- stats ----------
def day(t=None):
    k = time.strftime("%Y-%m-%d", time.localtime(t or time.time()))
    if k not in stats:
        stats[k] = {"steps": 0, "hourly": [0] * 24, "reminders": [0, 0, 0, 0],
                    "snoozes": {"meeting": 0, "nap": 0}, "moves": 0, "longest_min": 0.0}
    return stats[k]


def bump(fn):
    global stats_dirty
    fn(day())
    stats_dirty = True


# ---------- notifications ----------
def sitting_min(t=None):
    """Minutes sitting; frozen while a snooze or being away pauses the timer."""
    t = t or time.time()
    snoozed = S["snooze_until"] and t < S["snooze_until"] and S["snooze_start"]
    end = S["away_start"] if S["away"] else S["snooze_start"] if snoozed else t
    return max(0.0, end - S["last_move"]) / 60


def avail_snoozes(mins):
    """Snooze lengths offered at this sitting time, longest first: long snoozes disappear as you sit longer."""
    return sorted({m for m, below in SET["snooze_opts"] if not below or mins < below}, reverse=True)


def action_buttons(mins):
    """ntfy allows 3 buttons: the (up to 3) longest snoozes available right now."""
    def mk(label, path):
        return f"http, {label}, {PUBLIC}{path}, method=POST, headers.X-Token={TOKEN}, clear=true"
    return "; ".join(mk(f"{m} min", f"/snooze?min={m}") for m in avail_snoozes(mins)[:3])


def notify(msg, prio=3, title="Move!", tags="walking", mins=None):
    """Send to ntfy. mins is given for reminders: they get the buttons."""
    if not TOPIC:
        log("NO TOPIC, would send:", prio, title, "|", msg)
        return
    h = {"Priority": str(prio), "Title": title, "Tags": tags}
    if mins is not None:
        h["Actions"] = action_buttons(mins)
    try:
        req = urllib.request.Request(f"{NTFY_URL}/{TOPIC}", data=msg.encode(), headers=h, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:  # ntfy down: log and carry on
        log("ntfy failed:", e)


class _Fmt(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def send(typ, **ctx):
    """Send the notification of this type with the texts and priority from the settings."""
    n = SET["notif"][typ]
    mins = sitting_min()
    ctx.setdefault("min", int(mins))
    ctx.setdefault("stale", SET["stale_min"])
    ctx.setdefault("steps", S["steps"] or 0)
    try:
        msg, title = n["msg"].format_map(_Fmt(ctx)), n["title"].format_map(_Fmt(ctx))
    except (ValueError, IndexError, AttributeError):
        msg, title = n["msg"], n["title"]
    notify(msg, n["prio"], title, TAGS.get(typ, "walking"), mins if typ.startswith("level") else None)


def alarm_label(lvl):
    """The timer label / spoken phrase of this level with {min} and {time} filled in. Emojis are dropped for speech."""
    cfg = SET["notif"][f"level{lvl}"]
    ctx = _Fmt(min=int(sitting_min()), time=time.strftime("%H:%M"))
    try:
        txt = cfg["alarm_text"].format_map(ctx)
    except (ValueError, IndexError, AttributeError):
        txt = cfg["alarm_text"]
    if cfg["alarm_mode"] == "speech":
        txt = re.sub(r"[^\w .,!?'\-:]", "", txt).strip() or "Time to walk"
    return txt


def alarm_levels():
    """Numbers (1-4) of the levels that have a phone alarm."""
    return [i for i in range(1, 5) if SET["notif"][f"level{i}"]["alarm_mode"] != "off"]


def alarm_active(t):
    """True while the current level has a phone alarm and nothing pauses it."""
    snoozed = S["snooze_until"] and t < S["snooze_until"]
    lvl = S["level_sent"]
    return bool(SET["phone_alarm"] and SET["enabled"] and lvl >= 1 and not snoozed and not S["away"] and not in_quiet(t)
                and not (lvl == 4 and S["quiet_phase"])
                and SET["notif"][f"level{lvl}"]["alarm_mode"] != "off")


def alarm_due(now):
    """Which level's alarm the phone should play on this report (0 = none). Called once per report.
    A new level sounds at once; the same level repeats after its alarm_repeat_s."""
    test, S["alarm_test"] = S["alarm_test"], 0              # a Test button or debug call plays once
    if test:
        S["timers_out"] = True
        return test
    if alarm_active(now):
        lvl = S["level_sent"]
        # never sooner than the timer needs to finish ringing (a Clock timer keeps going until it stops by itself)
        # With a Clock app named the phone dismisses the old timers before each new one, so the alarm interval alone
        # decides (you may well have dismissed it yourself). Without one old timers cannot be cleared and keep ringing
        # for the Clock app's own time, so wait that long.
        floor = 0 if SET["alarm_package"] else SET["alarm_ring_s"] + 10
        base = max(SET["notif"][f"level{lvl}"]["alarm_repeat_s"], floor)
        gap = max(S["l4_alarm_repeat"], base) if lvl == 4 else base
        if floor and S["last_alarm"] and now - S["last_alarm"] < floor:
            return 0                                  # the previous timer may still be ringing, whatever the level
        if lvl != S["last_alarm_level"] or now - S["last_alarm"] >= gap:
            S["last_alarm"], S["last_alarm_level"] = now, lvl
            S["timers_out"] = True
            if lvl == 4:                              # back off: each alarm waits twice as long as the last
                S["l4_alarm_repeat"] = max(base, min(gap * 2, SET["backoff_max_s"]))
            return lvl
    return 0


def begin_snooze(minutes, kind, t):
    """Start (or replace) a snooze. It pauses the sitting timer; extending keeps the original start."""
    want_dismiss()
    if not (S["snooze_until"] and t < S["snooze_until"]):
        S["snooze_start"] = t
    S["snooze_until"], S["snooze_kind"] = t + minutes * 60, kind
    save_state()
    bump(lambda d: d["snoozes"].__setitem__(kind, d["snoozes"].get(kind, 0) + 1))


def in_quiet(t):
    if not SET["quiet_enabled"]:
        return False
    try:
        a, b = SET["quiet_start"], SET["quiet_end"]
        lt = time.localtime(t)
        cur = lt.tm_hour * 60 + lt.tm_min
        s = int(a[:2]) * 60 + int(a[3:])
        e = int(b[:2]) * 60 + int(b[3:])
        if s == e:
            return False
        return (s <= cur < e) if s < e else (cur >= s or cur < e)
    except Exception:
        return False


def want_dismiss():
    """Timers were started on the phone and are no longer wanted: ask the phone to dismiss them at its next report."""
    if S["timers_out"]:
        S["dismiss"], S["timers_out"] = True, False


def reset(t, why):
    want_dismiss()
    S["last_move"] = t
    S["level_sent"] = 0
    S["last_repeat"] = 0.0
    S["last_alarm_level"] = 0
    S["l4_since"], S["l4_repeat"], S["l4_alarm_repeat"], S["quiet_phase"] = 0.0, 0, 0, False
    save_state()
    log("timer reset:", why)


# ---------- presence ----------
def presence_on():
    return bool(SET["presence_enabled"] and SET["presence_mac"])


def mark_home(t, ip=""):
    """The phone was seen on the home network (a ping reply or a report). Caller holds the lock."""
    S["last_home"] = t
    if ip:
        S["phone_ip"] = ip
    if S["away"]:
        resume_home(t)


def go_away():
    """Nothing seen for presence_away_min: pause the timer from the last time the phone was seen."""
    start = S["last_home"]
    if S["snooze_until"]:
        end_snooze(start)                   # the snooze ends here; being away keeps the pause going
    want_dismiss()                          # timers still ringing on the phone are cleared at its next report
    S["away"], S["away_start"] = True, start
    save_state()
    log("away: phone not seen since", time.strftime("%H:%M:%S", time.localtime(start)), "- timer paused at",
        round((start - S["last_move"]) / 60, 1), "min")


def resume_home(t):
    """Back home: the sitting timer carries on from where it stopped (it is not reset)."""
    shift = max(0.0, t - S["away_start"])
    if S["last_move"] < S["away_start"]:
        S["last_move"] += shift
        for k in ("l4_since", "last_repeat", "last_alarm"):
            if S[k]:
                S[k] += shift
    S["away"], S["away_start"] = False, 0.0
    S["level_sent"] = sum(1 for v in SET["levels_min"] if (t - S["last_move"]) / 60 >= v)   # levels already passed while paused do not sound again
    S["last_alarm_level"] = S["level_sent"]
    save_state()
    log("home again after", round(shift / 60, 1), "min, timer continues at", round((t - S["last_move"]) / 60, 1), "min")


def arp_table():
    """[(ip, mac, complete)] from the host's neighbour table (the container shares the host network)."""
    rows = []
    try:
        with open(ARP_FILE) as f:
            next(f, None)
            for line in f:
                c = line.split()
                if len(c) >= 4:
                    rows.append((c[0], c[3].lower(), c[2] != "0x0"))
    except OSError:
        pass
    return rows


def ping_once(ip):
    """Two ICMP echoes with a 1 s wait each. Returns the average round trip in ms, or None."""
    try:
        r = subprocess.run(["ping", "-c", "2", "-W", "1", ip], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"= [\d.]+/([\d.]+)/", r.stdout)
    return float(m.group(1)) if m else 0.0


def probe_phone():
    """Ping the phone at the address it had last, then at any other address the neighbour table lists for its MAC.
    A reply only counts if the table then shows the phone's MAC for that address (the address may have been reused)."""
    with lock:
        mac, hint, last = SET["presence_mac"], SET["presence_ip"], S["phone_ip"]
    cands = [x for x in (last, hint) if x]
    cands += [ip for ip, m, ok in arp_table() if m == mac and ok]
    cands += [ip for ip, m, ok in arp_table() if m == mac]
    seen, tried = set(), None
    for ip in cands:
        if ip in seen:
            continue
        seen.add(ip)
        if len(seen) > 4:
            break
        tried = ip
        rtt = ping_once(ip)
        if rtt is not None:
            owner = [m for a, m, ok in arp_table() if a == ip and ok]
            if not owner or owner[0] == mac:
                return ip, rtt
    return tried, None


def presence_loop():
    time.sleep(10)
    while True:
        every = 30
        try:
            with lock:
                on, every = presence_on(), SET["presence_every_s"]
            if on:
                ip, rtt = probe_phone()
                t = time.time()
                with lock:
                    rec = [int(t), 1 if rtt is not None else 0, round(rtt, 1) if rtt is not None else None, ip or ""]
                    pings.append(rec)
                    append_ping(rec)
                    S["last_ping"] = rec
                    while pings and pings[0][0] < t - KEEP_PINGS_H * 3600:
                        pings.popleft()
                    if pings_appended >= 1440:
                        compact_pings()
                    if rtt is not None:
                        mark_home(t, ip)
        except Exception as e:
            log("presence error:", e)
        time.sleep(every)


def add_street(rows):
    """The street phone sends its backlog of [time, counter] samples when it is home again. The steps between
    samples are credited to the day and hour they happened. Enough steps in the batch reset the sitting timer
    (walking outside counts). Caller holds the lock. Returns (steps credited, timer reset)."""
    global stats_dirty
    t = time.time()
    st = S["street"]
    good = sorted({(int(a), int(b)) for a, b in rows if 0 < a <= t + 60 and b >= 0})
    good = [x for x in good if x[0] >= st["last_t"]]
    total, prev = 0, st["last_n"]
    for ts, n in good:
        if prev is not None:
            d = n - prev if n >= prev else n                 # a lower counter means the phone restarted
            if d:
                total += d
                rec = day(ts)
                rec["steps"] += d
                rec["street"] = rec.get("street", 0) + d
                rec["hourly"][time.localtime(ts).tm_hour] += d
                stats_dirty = True
        prev = n
    if good:
        st["last_t"], st["last_n"] = float(good[-1][0]), good[-1][1]
    st["uploaded_at"] = t
    write_json("street.json", st)
    did = total >= SET["move_steps"]
    if did:
        if S["level_sent"] >= 1:
            send("moved", steps=total)
        bump(lambda d: d.__setitem__("moves", d["moves"] + 1))
        reset(t, f"{total} steps from the street phone")
        samples.clear()
    log("street phone:", len(good), "samples,", total, "steps credited" + (", timer reset" if did else ""))
    return total, did


def street_battery(pct, plugged, t):
    """The street phone reports its battery with each upload (so only while home, when you can charge it).
    Warn the main phone at or below battery_warn_pct unless it is charging or in quiet hours, at most once per repeat_s.
    Caller holds the lock."""
    st = S["street"]
    st["battery"], st["plugged"] = pct, plugged
    write_json("street.json", st)
    gap = SET["notif"]["battery"]["repeat_s"]
    if (pct <= SET["battery_warn_pct"] and not plugged and SET["enabled"] and not in_quiet(t)
            and t - S["batt_told"] >= gap):
        S["batt_told"] = t
        send("battery", battery=pct)
        log("street phone battery", pct, "%: warned")


def add_sample(n, manual=False):
    """Record a report. Returns how many seconds the phone should wait before the next one.
    manual=True marks a report made from the debug tools (drawn as a hollow dot)."""
    t = time.time()
    with lock:
        pt, pn = S["last_sample"], S["steps"]
        if pt and pn is not None and 0 < t - pt <= 300 and n >= pn and (n - pn) / (t - pt) * 60 >= SET["walk_spm"]:
            S["walk_until"] = t + WALK_HOLD_S
        reports.append([int(t), n, 1] if manual else [int(t), n])
        append_report(reports[-1])
        while reports and reports[0][0] < t - KEEP_REPORTS_H * 3600:
            reports.popleft()
        if appended >= 720:
            compact_reports()
        prev = samples[-1][1] if samples else S["steps"]
        if samples and n < samples[-1][1]:       # phone rebooted: counter restarted
            samples.clear()
            prev = None
        delta = max(0, n - prev) if prev is not None else 0
        if delta:
            def f(d):
                d["steps"] += delta
                d["hourly"][time.localtime(t).tm_hour] += delta
            bump(f)
        samples.append((t, n))
        while samples and samples[0][0] < t - SET["move_window_s"]:
            samples.popleft()
        first = samples[0][1]
        was_stale = S["last_sample"] and t - S["last_sample"] > SET["stale_min"] * 60
        S["last_sample"], S["steps"], S["stale_told"] = t, n, False
        if was_stale:
            reset(t, "phone back after a gap")
        elif n - first >= SET["move_steps"]:
            if S["level_sent"] >= 1:
                send("moved", steps=n - first)
            bump(lambda d: d.__setitem__("moves", d["moves"] + 1))
            reset(t, f"{n - first} steps in {int(t - samples[0][0])}s")
            samples.clear()
            samples.append((t, n))
        return next_report_s(t)


def next_report_s(t):
    n = SET["report_walk_s"] if t < S["walk_until"] else SET["report_idle_s"]
    snoozed = S["snooze_until"] and t < S["snooze_until"]
    first = (alarm_levels() or [0])[0]
    # the alarm can only sound when the phone reports: ask for fresh reports from 2 min before the first alarm level
    if (first and SET["phone_alarm"] and SET["enabled"] and not snoozed and not in_quiet(t) and not S["quiet_phase"]
            and (S["level_sent"] >= first - 1 or sitting_min(t) >= SET["levels_min"][first - 1] - 2)):
        n = min(n, 20)
    return n


def end_snooze(t):
    """A snooze PAUSES the sitting timer: the time spent snoozed is added back, so the timer carries
    on from where it was. (If you really walked during the snooze, the timer was already reset.)"""
    if S["snooze_start"] and S["last_move"] < S["snooze_start"]:
        S["last_move"] += max(0.0, t - S["snooze_start"])
        if S["l4_since"]:
            S["l4_since"] += max(0.0, t - S["snooze_start"])
    S["snooze_until"], S["snooze_kind"], S["snooze_start"] = 0.0, "", 0.0
    save_state()


def tick():
    t = time.time()
    with lock:
        if presence_on():
            if not S["away"] and t - S["last_home"] > SET["presence_away_min"] * 60:
                go_away()
            if S["away"]:
                want_dismiss()
                return                       # away: paused, nothing is sent
        elif S["away"]:
            resume_home(t)                   # presence switched off while away
        if S["snooze_until"]:
            if t < S["snooze_until"]:
                return                       # paused: the timer neither runs nor resets
            end_snooze(S["snooze_until"])
            log("snooze ended, timer continues at", round((t - S["last_move"]) / 60, 1), "min")
            send("snooze_end")
            return
        if not SET["enabled"] or in_quiet(t):
            want_dismiss()
            S["last_move"] = t
            S["level_sent"] = 0
            return
        if S["last_sample"] and t - S["last_sample"] > SET["stale_min"] * 60:
            S["last_move"] = t
            S["level_sent"] = 0
            if not S["stale_told"]:
                S["stale_told"] = True
                send("silent")
            return
        mins = (t - S["last_move"]) / 60
        if mins > day(t)["longest_min"]:
            day(t)["longest_min"] = round(mins, 1)
            global stats_dirty
            stats_dirty = True
        lv = SET["levels_min"]
        lvl = 0
        for i in range(3, -1, -1):
            if mins >= lv[i]:
                lvl = i + 1
                break
        if not lvl:
            return
        cfg = SET["notif"][f"level{lvl}"]
        if lvl > S["level_sent"]:                    # first time this level is reached
            S["level_sent"] = lvl
            S["last_repeat"] = t
            if lvl == 4:                             # the open-ended level: start the back-off clock
                S["l4_since"], S["l4_repeat"], S["l4_alarm_repeat"], S["quiet_phase"] = t, cfg["repeat_s"], 0, False
            bump(lambda d: d["reminders"].__setitem__(lvl - 1, d["reminders"][lvl - 1] + 1))
            send(f"level{lvl}", min=int(mins))
        elif lvl == 4:
            limit = SET["loud_limit_min"] * 60
            if S["quiet_phase"]:
                gap = SET["notif"]["giveup"]["repeat_s"]
                if gap and t - S["last_repeat"] >= gap:
                    S["last_repeat"] = t
                    send("giveup", min=int(mins))
            elif limit and S["l4_since"] and t - S["l4_since"] >= limit:
                S["quiet_phase"], S["last_repeat"] = True, t       # enough noise: only quiet notifications from now
                want_dismiss()                                       # and the last timer stops ringing
                log("level 4 loud phase over, sounds stop until you move")
                send("giveup", min=int(mins))
            elif S["l4_repeat"] and t - S["last_repeat"] >= S["l4_repeat"]:
                S["last_repeat"] = t
                send("level4", min=int(mins))
                S["l4_repeat"] = min(S["l4_repeat"] * 2, max(SET["notif_backoff_max_s"], cfg["repeat_s"]))
        elif cfg["repeat_s"] and t - S["last_repeat"] >= cfg["repeat_s"]:
            S["last_repeat"] = t
            send(f"level{lvl}", min=int(mins))


def loop():
    n = 0
    while True:
        try:
            tick()
            n += 1
            if n % 180 == 0:          # every 15 minutes
                with lock:
                    flush_stats()
                    save_state()
        except Exception as e:
            log("tick error:", e)
        time.sleep(5)


# ---------- state for the web page ----------
def presence_state(t):
    on = presence_on()
    lp = S["last_ping"]
    recent = [x for x in pings if x[0] >= t - 3600]
    return {"enabled": on, "name": SET["presence_name"], "mac": SET["presence_mac"],
            "state": "off" if not on else "away" if S["away"] else "home",
            "ip": S["phone_ip"], "away_min": SET["presence_away_min"], "every_s": SET["presence_every_s"],
            "seen_age_s": round(t - S["last_home"]), "away_since": S["away_start"] if S["away"] else 0,
            "away_for_min": round((t - S["away_start"]) / 60, 1) if S["away"] else 0,
            "last_ping": lp, "ok_1h": sum(x[1] for x in recent), "n_1h": len(recent),
            "street": {"upload_age_s": round(t - S["street"]["uploaded_at"]) if S["street"]["uploaded_at"] else None,
                       "counter": S["street"]["last_n"], "battery": S["street"]["battery"], "plugged": S["street"]["plugged"], "steps_today": day(t).get("street", 0)}}


def state(t):
    quiet = in_quiet(t)
    stale = bool(S["last_sample"] and t - S["last_sample"] > SET["stale_min"] * 60)
    snoozed = bool(S["snooze_until"] and t < S["snooze_until"])
    mode = ("off" if not SET["enabled"] else "away" if S["away"] else "snoozed" if snoozed else "quiet" if quiet
            else "no-data" if stale or not S["last_sample"] else "watching")
    mins = sitting_min(t)
    return {
        "mode": mode, "sitting_min": round(mins, 1), "level": S["level_sent"],
        "steps": S["steps"], "last_sample_age_s": round(t - S["last_sample"]) if S["last_sample"] else None,
        "snooze_left_min": round(max(0, S["snooze_until"] - t) / 60, 1), "snooze_kind": S["snooze_kind"],
        "snooze_available": avail_snoozes(mins), "alarm": alarm_active(t),
        "quiet_now": quiet, "uptime_min": round((t - S["started"]) / 60),
        "walking": t < S["walk_until"], "report_every_s": next_report_s(t), "presence": presence_state(t)}


def full_state():
    t = time.time()
    with lock:
        days = sorted(stats)[-14:]
        return {"status": state(t), "settings": SET, "defaults": DEFAULTS, "today": day(t),
                "history": [dict(stats[k], date=k) for k in days], "now": time.strftime("%H:%M")}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def body_json(self, limit=4096):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError
        return json.loads(self.rfile.read(n) or b"{}")

    def handle_any(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/health":
            return self.reply(200, {"ok": True})
        if u.path in ("/", "/ui"):
            try:
                with open(UI_FILE, "rb") as f:
                    return self.reply(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self.reply(404, {"error": "no ui"})
        if u.path == "/street.sh":       # the street phone's job script; holds no secrets
            try:
                with open(STREET_FILE, "rb") as f:
                    return self.reply(200, f.read(), "text/plain; charset=utf-8")
            except OSError:
                return self.reply(404, {"error": "no script"})
        if u.path == "/phone.sh":        # the Termux loop; holds no secrets
            try:
                with open(PHONE_FILE, "rb") as f:
                    return self.reply(200, f.read(), "text/plain; charset=utf-8")
            except OSError:
                return self.reply(404, {"error": "no script"})
        tok = self.headers.get("X-Token") or (q.get("t") or [""])[0]
        if not TOKEN or not hmac.compare_digest(tok, TOKEN):
            return self.reply(403, {"error": "forbidden"})
        try:
            if u.path == "/steps":
                nxt = add_sample(int(float(q["steps"][0])))
                with lock:
                    if time.time() - S["stats_flush"] >= 60:      # at most once a minute
                        flush_stats()
                    now = time.time()
                    due = alarm_due(now)
                    nxt = min(nxt, next_report_s(now))            # fast reports as the alarm levels near
                out = {"ok": True, "next_s": nxt, "alarm": int(bool(due)), "pkg": SET["alarm_package"]}
                if S["dismiss"]:
                    out["dismiss"], S["dismiss"] = 1, False       # once: the phone dismisses the timers we started
                if due:
                    cfg = SET["notif"][f"level{due}"]
                    out.update(level=due, mode=cfg["alarm_mode"], say=alarm_label(due), vol=cfg["alarm_volume"])
                return self.reply(200, out)
            if u.path == "/steps/batch" and self.command == "POST":
                if (q.get("device") or [""])[0] != "street":
                    return self.reply(400, {"error": "device must be street"})
                body = self.body_json(32768)
                rows = body["samples"]
                if not isinstance(rows, list) or len(rows) > 2000:
                    return self.reply(400, {"error": "samples must be a list of up to 2000 [time, counter]"})
                with lock:
                    total, did = add_street(rows)
                    b = body.get("battery")        # optional: {"pct": 0-100, "plugged": bool}
                    if isinstance(b, dict) and isinstance(b.get("pct"), (int, float)) and 0 <= b["pct"] <= 100:
                        street_battery(int(b["pct"]), bool(b.get("plugged")), time.time())
                    flush_stats()
                return self.reply(200, {"ok": True, "credited": total, "reset": did})
            if u.path == "/snooze":
                m = int((q.get("min") or [0])[0])
                with lock:
                    now = time.time()
                    ok = avail_snoozes(sitting_min(now))
                    if m not in ok:
                        return self.reply(400, {"error": f"{m} min is not available at this sitting time",
                                                "available": ok})
                    begin_snooze(m, "snooze", now)
                    send("snoozed", snooze=m)
                return self.reply(200, {"snoozed_min": m})
            if u.path == "/resume":
                with lock:
                    if S["snooze_until"]:
                        end_snooze(time.time())          # the timer carries on, it is not reset
                return self.reply(200, {"ok": True})
            if u.path == "/status":
                with lock:
                    return self.reply(200, state(time.time()))
            if u.path == "/api/state":
                return self.reply(200, full_state())
            if u.path == "/api/samples":
                h = max(1, min(KEEP_REPORTS_H, int((q.get("hours") or [3])[0])))
                t = time.time()
                gap, out, last = h * 3600 / 900, [], 0   # about 900 points at most
                with lock:
                    rows = [x for x in reports if x[0] >= t - h * 3600]
                for i, x in enumerate(rows):
                    if i == len(rows) - 1 or len(x) == 3 or x[0] - last >= gap:
                        out.append(x)
                        last = x[0]
                return self.reply(200, {"now": int(t), "hours": h, "samples": out})
            if u.path == "/api/pings":
                h = max(1, min(KEEP_PINGS_H, int((q.get("hours") or [3])[0])))
                t = time.time()
                with lock:
                    rows = [x for x in pings if x[0] >= t - h * 3600]
                if len(rows) > 900:                    # thin the answered ones, keep every miss
                    k = len(rows) // 900 + 1
                    rows = [x for i, x in enumerate(rows) if not x[1] or i % k == 0 or i == len(rows) - 1]
                return self.reply(200, {"now": int(t), "hours": h, "pings": rows})
            if u.path == "/api/settings" and self.command == "POST":
                ok, err = validate(self.body_json())
                if err:
                    return self.reply(400, {"errors": err})
                with lock:
                    SET.update(ok)
                    save_settings()
                log("settings changed:", ok)
                return self.reply(200, {"ok": True, "settings": SET})
            if u.path == "/api/debug/steps" and self.command == "POST":
                n = int(self.body_json()["n"])
                if not 1 <= n <= 20000:
                    return self.reply(400, {"errors": ["n must be between 1 and 20000"]})
                with lock:
                    base = S["steps"] if S["steps"] is not None else (reports[-1][1] if reports else 0)
                add_sample(base + n, manual=True)
                with lock:
                    flush_stats()
                log("debug: added", n, "steps, counter now", base + n)
                return self.reply(200, {"ok": True, "counter": base + n})
            if u.path == "/api/debug/sitting" and self.command == "POST":
                m = float(self.body_json()["min"])
                if not 0 <= m <= 600:
                    return self.reply(400, {"errors": ["min must be between 0 and 600"]})
                with lock:
                    S["last_move"] = time.time() - m * 60
                    S["level_sent"], S["last_repeat"] = 0, 0.0
                    save_state()
                log("debug: sitting timer set to", m, "min")
                return self.reply(200, {"ok": True})
            if u.path == "/api/debug/dismiss" and self.command == "POST":
                with lock:
                    S["dismiss"] = True
                log("debug: the phone will dismiss its timers at the next report")
                return self.reply(200, {"ok": True})
            if u.path == "/api/debug/alarm" and self.command == "POST":
                lvl = int(self.body_json().get("level", 4))
                if lvl not in alarm_levels():
                    return self.reply(400, {"errors": [f"level {lvl} has its phone alarm off"]})
                with lock:
                    S["alarm_test"] = lvl
                    S["walk_until"] = time.time() + 60    # fast reports so it plays soon
                log("debug: phone alarm of level", lvl, "will play at the next report")
                return self.reply(200, {"ok": True})
            if u.path == "/api/test" and self.command == "POST":
                typ = str(self.body_json().get("type", ""))
                if typ not in SET["notif"]:
                    return self.reply(400, {"errors": ["unknown notification type"]})
                alarm = False
                with lock:
                    send(typ, snooze=5)
                    if typ.startswith("level") and SET["phone_alarm"] and int(typ[5:]) in alarm_levels():
                        S["alarm_test"] = int(typ[5:])             # the phone plays this level's alarm too
                        S["walk_until"] = time.time() + 60         # fast reports so it plays soon
                        alarm = True
                return self.reply(200, {"ok": True, "alarm": alarm})
        except (KeyError, ValueError, IndexError, TypeError):
            return self.reply(400, {"error": "bad request"})
        self.reply(404, {"error": "not found"})

    do_GET = do_POST = handle_any


def shutdown(*_):
    with lock:
        flush_stats(True)
        save_state()
    sys.exit(0)


if __name__ == "__main__":
    load()
    signal.signal(signal.SIGTERM, shutdown)
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=presence_loop, daemon=True).start()
    log("steps listening on", PORT, "topic set:", bool(TOPIC))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
