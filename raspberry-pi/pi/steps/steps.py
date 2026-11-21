#!/usr/bin/env python3
"""steps: nags you through ntfy when you sit too long. Standard library only.

The phone posts its cumulative step counter to /steps. If fewer than move_steps
steps happen within move_window_s seconds, the "sitting" timer keeps running.
Escalation (minutes sitting): level 1 light (ntfy prio 3), 2 mid (4), 3 high (5),
4 constant (prio 5 every repeat_s seconds) until you walk.
Snooze (30/5/1 min, the long ones only while you have not sat too long) PAUSES the
sitting timer; it carries on from where it was when the snooze ends.

Settings (editable in the web page, saved to /data/settings.json) start from the
environment variables below. Daily stats are kept in /data/stats.json (30 days),
written every 15 minutes when changed, so the SD card sees very few writes.

Endpoints (token in header X-Token or ?t=):
  GET  /  or /ui              web page (no token; it asks for the token itself)
  GET  /steps?steps=N         phone reports its cumulative step counter; the reply says
                              {"next_s": seconds to wait} (normal or walking interval)
  POST /snooze?min=15         pause for one of the snooze lengths offered right now
  GET  /resume                cancel the snooze
  GET  /status                small JSON state
  GET  /api/state             everything the page shows
  GET  /api/samples?hours=1   phone reports (time, counter) of the last 1-24 h, thinned to ~900 points
  GET  /phone.sh              the Termux loop script (no token)
  POST /api/settings          JSON body with any settings, validated
  POST /api/test              {"type": "level2"|"moved"|...} send that notification
  POST /api/debug/steps       {"n": N} fake report with the counter raised by N (hollow dot)
  POST /api/debug/sitting     {"min": M} pretend you have been sitting M minutes
  POST /api/debug/alarm       {"level": 3} play that level's phone alarm once, at the next report
  GET  /health                no token
"""
import copy
import hmac
import re
import json
import os
import signal
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
KEEP_DAYS = 365
KEEP_REPORTS_H = 24


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
    "moved": {"title": "Good", "msg": "Moved enough, timer reset.", "prio": 1},
    "snoozed": {"title": "Snoozed", "msg": "Snoozed {snooze} min. The sitting timer is paused.", "prio": 1},
    # After the loud phase of level 4 (loud_limit_min) all sound stops and this quiet one repeats instead.
    "giveup": {"title": "Still sitting", "msg": "{min} min without moving. Sounds are paused until you move.", "prio": 2, "repeat_s": 900},
    "snooze_end": {"title": "Snooze ended", "msg": "Snooze over, the sitting timer continues at {min} min.", "prio": 2},
    "silent": {"title": "Steps: phone silent", "msg": "No step data from the phone for {stale} min; reminders paused.", "prio": 2},
}
TAGS = {"level1": "walking", "level2": "warning", "level3": "rotating_light", "level4": "rotating_light",
        "moved": "tada", "snoozed": "zzz", "snooze_end": "alarm_clock", "giveup": "hourglass_flowing_sand", "silent": "warning"}
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
    "alarm_ring_s": 900,                # how long a Clock timer rings before it stops by itself: no new one starts sooner
    "loud_limit_min": 30,               # level 4 stays loud this long (0 = no limit), then only the quiet notification
    "backoff_max_s": 900,               # level 4 repeats double each time, up to this many seconds
    "alarm_package": "",                # Clock app that gets the timer request (blank = the phone's choice), e.g. com.google.android.deskclock
    "notif": NOTIF_DEFAULTS,
    "report_idle_s": int(E("REPORT_IDLE_S", "60")),     # how often the phone should report normally
    "report_walk_s": int(E("REPORT_WALK_S", "10")),     # ... while you are walking
    "walk_spm": int(E("WALK_SPM", "20")),               # steps per minute that count as walking
}
WALK_HOLD_S = 60      # keep reporting fast this long after the last walking seen
SET = copy.deepcopy(DEFAULTS)

lock = threading.Lock()
S = {"last_move": time.time(), "snooze_start": 0.0, "snooze_until": 0.0, "snooze_kind": "", "last_sample": 0.0, "walk_until": 0.0, "stats_flush": 0.0, "alarm_test": 0, "last_alarm": 0.0, "last_alarm_level": 0,
     "l4_since": 0.0, "l4_repeat": 0, "l4_alarm_repeat": 0, "quiet_phase": False,
     "stale_told": False, "level_sent": 0, "last_repeat": 0.0, "steps": None, "started": time.time()}
samples = deque()
stats = {}            # "YYYY-MM-DD" -> day record
stats_dirty = False
reports = deque()     # [unix time, counter value(, 1 if added by hand)] of every phone report, last 24 h
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
                              "last_move": S["last_move"], "saved_at": time.time()})


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

    for key in ("enabled", "quiet_enabled", "phone_alarm"):
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
    num("report_idle_s", 5, 600)
    num("report_walk_s", 2, 120)
    num("walk_spm", 5, 200)
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
    """Minutes sitting; frozen while a snooze pauses the timer."""
    t = t or time.time()
    snoozed = S["snooze_until"] and t < S["snooze_until"] and S["snooze_start"]
    return ((S["snooze_start"] if snoozed else t) - S["last_move"]) / 60


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
    return bool(SET["phone_alarm"] and SET["enabled"] and lvl >= 1 and not snoozed and not in_quiet(t)
                and not (lvl == 4 and S["quiet_phase"])
                and SET["notif"][f"level{lvl}"]["alarm_mode"] != "off")


def alarm_due(now):
    """Which level's alarm the phone should play on this report (0 = none). Called once per report.
    A new level sounds at once; the same level repeats after its alarm_repeat_s."""
    test, S["alarm_test"] = S["alarm_test"], 0              # a Test button or debug call plays once
    if test:
        return test
    if alarm_active(now):
        lvl = S["level_sent"]
        # never sooner than the timer needs to finish ringing (a Clock timer keeps going until it stops by itself)
        floor = SET["alarm_ring_s"] + 10
        base = max(SET["notif"][f"level{lvl}"]["alarm_repeat_s"], floor)
        gap = max(S["l4_alarm_repeat"], base) if lvl == 4 else base
        if S["last_alarm"] and now - S["last_alarm"] < floor:
            return 0                                  # the previous timer may still be ringing, whatever the level
        if lvl != S["last_alarm_level"] or now - S["last_alarm"] >= gap:
            S["last_alarm"], S["last_alarm_level"] = now, lvl
            if lvl == 4:                              # back off: each alarm waits twice as long as the last
                S["l4_alarm_repeat"] = max(base, min(gap * 2, SET["backoff_max_s"]))
            return lvl
    return 0


def begin_snooze(minutes, kind, t):
    """Start (or replace) a snooze. It pauses the sitting timer; extending keeps the original start."""
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


def reset(t, why):
    S["last_move"] = t
    S["level_sent"] = 0
    S["last_repeat"] = 0.0
    S["last_alarm_level"] = 0
    S["l4_since"], S["l4_repeat"], S["l4_alarm_repeat"], S["quiet_phase"] = 0.0, 0, 0, False
    save_state()
    log("timer reset:", why)


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
        if S["snooze_until"]:
            if t < S["snooze_until"]:
                return                       # paused: the timer neither runs nor resets
            end_snooze(S["snooze_until"])
            log("snooze ended, timer continues at", round((t - S["last_move"]) / 60, 1), "min")
            send("snooze_end")
            return
        if not SET["enabled"] or in_quiet(t):
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
                log("level 4 loud phase over, sounds stop until you move")
                send("giveup", min=int(mins))
            elif S["l4_repeat"] and t - S["last_repeat"] >= S["l4_repeat"]:
                S["last_repeat"] = t
                send("level4", min=int(mins))
                S["l4_repeat"] = min(S["l4_repeat"] * 2, max(SET["backoff_max_s"], cfg["repeat_s"]))
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
def state(t):
    quiet = in_quiet(t)
    stale = bool(S["last_sample"] and t - S["last_sample"] > SET["stale_min"] * 60)
    snoozed = bool(S["snooze_until"] and t < S["snooze_until"])
    mode = ("off" if not SET["enabled"] else "snoozed" if snoozed else "quiet" if quiet
            else "no-data" if stale or not S["last_sample"] else "watching")
    mins = sitting_min(t)
    return {
        "mode": mode, "sitting_min": round(mins, 1), "level": S["level_sent"],
        "steps": S["steps"], "last_sample_age_s": round(t - S["last_sample"]) if S["last_sample"] else None,
        "snooze_left_min": round(max(0, S["snooze_until"] - t) / 60, 1), "snooze_kind": S["snooze_kind"],
        "snooze_available": avail_snoozes(mins), "alarm": alarm_active(t),
        "quiet_now": quiet, "uptime_min": round((t - S["started"]) / 60),
        "walking": t < S["walk_until"], "report_every_s": next_report_s(t)}


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

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4096:
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
                out = {"ok": True, "next_s": nxt, "alarm": int(bool(due))}
                if due:
                    cfg = SET["notif"][f"level{due}"]
                    out.update(level=due, mode=cfg["alarm_mode"], say=alarm_label(due), vol=cfg["alarm_volume"],
                               pkg=SET["alarm_package"])
                return self.reply(200, out)
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
        except (KeyError, ValueError, IndexError):
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
    log("steps listening on", PORT, "topic set:", bool(TOPIC))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
