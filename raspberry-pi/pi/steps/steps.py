#!/usr/bin/env python3
"""steps: nags you through ntfy when you sit too long. Standard library only.

The phone posts its cumulative step counter to /steps. If fewer than move_steps
steps happen within move_window_s seconds, the "sitting" timer keeps running.
Escalation (minutes sitting): level 1 light (ntfy prio 3), 2 mid (4), 3 high (5),
4 constant (prio 5 every repeat_s seconds) until you walk.
Snooze (meeting / nap) pauses everything; the timer restarts when it ends.

Settings (editable in the web page, saved to /data/settings.json) start from the
environment variables below. Daily stats are kept in /data/stats.json (30 days),
written every 15 minutes when changed, so the SD card sees very few writes.

Endpoints (token in header X-Token or ?t=):
  GET  /  or /ui              web page (no token; it asks for the token itself)
  GET  /steps?steps=N         phone reports its cumulative step counter
  POST /snooze?min=60&kind=meeting   (kind: meeting|nap)
  GET  /resume                cancel the snooze
  GET  /status                small JSON state
  GET  /api/state             everything the page shows
  GET  /api/samples?hours=3   every phone report (time, counter) of the last 1-24 h
  POST /api/settings          JSON body with any settings, validated
  POST /api/test              send a test notification
  GET  /health                no token
"""
import hmac
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
KEEP_DAYS = 365
KEEP_REPORTS_H = 24


def _quiet_default():
    a, _, b = E("QUIET_HOURS", "22:00-07:00").partition("-")
    return a or "22:00", b or "07:00"


QS, QE = _quiet_default()
DEFAULTS = {
    "enabled": True,
    "levels_min": [float(x) for x in E("LEVELS_MIN", "50,60,70,80").split(",")],
    "repeat_s": int(E("REPEAT_S", "20")),
    "move_steps": int(E("MOVE_STEPS", "200")),
    "move_window_s": int(E("MOVE_WINDOW_S", "300")),
    "stale_min": int(E("STALE_S", "1800")) // 60,
    "quiet_enabled": True,
    "quiet_start": QS,
    "quiet_end": QE,
    "meeting_min": int(E("MEETING_MIN", "60")),
    "nap_min": int(E("NAP_MIN", "30")),
}
SET = dict(DEFAULTS)

lock = threading.Lock()
S = {"last_move": time.time(), "snooze_until": 0.0, "snooze_kind": "", "last_sample": 0.0,
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
                              "last_move": S["last_move"]})


def save_settings():
    write_json("settings.json", SET)


def append_report(rec):
    """One line per report, flushed to disk at once: a crash or power cut loses nothing."""
    global appended
    try:
        with open(os.path.join(DATA, "reports.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
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
    saved = read_json("settings.json") or {}
    ok, _ = validate(saved)
    SET.update(ok)
    lm, t = d.get("last_move"), time.time()
    # keep the old timer unless it is in the future or older than the "phone silent" limit
    # (the Pi was off for a long time: do not greet you with constant alarms)
    if isinstance(lm, (int, float)) and 0 < t - lm <= SET["stale_min"] * 60:
        S["last_move"] = lm
        log("sitting timer restored:", round((t - lm) / 60, 1), "min")
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

    for key in ("enabled", "quiet_enabled"):
        if key in d:
            out[key] = bool(d[key])
    num("repeat_s", 5, 600)
    num("move_steps", 10, 5000)
    num("move_window_s", 60, 3600)
    num("stale_min", 5, 240)
    num("meeting_min", 1, 600)
    num("nap_min", 1, 600)
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
def notify(msg, prio=3, title="Move!", tags="walking", actions=True):
    if not TOPIC:
        log("NO TOPIC, would send:", prio, msg)
        return
    h = {"Priority": str(prio), "Title": title, "Tags": tags}
    if actions:
        a = []
        for label, mins, kind in (("Meeting", SET["meeting_min"], "meeting"), ("Nap", SET["nap_min"], "nap")):
            a.append(f"http, {label} {mins}m, {PUBLIC}/snooze?min={mins}&kind={kind}, "
                     f"method=POST, headers.X-Token={TOKEN}, clear=true")
        h["Actions"] = "; ".join(a)
    try:
        req = urllib.request.Request(f"{NTFY_URL}/{TOPIC}", data=msg.encode(), headers=h, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:  # ntfy down: log and carry on
        log("ntfy failed:", e)


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
    save_state()
    log("timer reset:", why)


def add_sample(n):
    t = time.time()
    with lock:
        reports.append([int(t), n])
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
                notify("Moving again, timer reset.", 1, "Good", "tada", actions=False)
            bump(lambda d: d.__setitem__("moves", d["moves"] + 1))
            reset(t, f"{n - first} steps in {int(t - samples[0][0])}s")
            samples.clear()
            samples.append((t, n))


def tick():
    t = time.time()
    with lock:
        if S["snooze_until"]:
            if t < S["snooze_until"]:
                S["last_move"] = t
                return
            S["snooze_until"], S["snooze_kind"] = 0.0, ""
            save_state()
            reset(t, "snooze ended")
            notify("Snooze over, the timer starts now.", 2, "Snooze ended", "alarm_clock", actions=False)
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
                notify(f"No step data from the phone for {SET['stale_min']} min; reminders paused.", 2,
                       "Steps: phone silent", "warning", actions=False)
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
        if lvl < 4:
            if lvl > S["level_sent"]:
                S["level_sent"] = lvl
                bump(lambda d: d["reminders"].__setitem__(lvl - 1, d["reminders"][lvl - 1] + 1))
                notify(f"Sitting for {int(mins)} min. Walk a bit.", (3, 4, 5)[lvl - 1],
                       ("Stand up", "Move now", "MOVE NOW")[lvl - 1],
                       ("walking", "warning", "rotating_light")[lvl - 1])
        else:
            if S["level_sent"] < 4:
                bump(lambda d: d["reminders"].__setitem__(3, d["reminders"][3] + 1))
            S["level_sent"] = 4
            if t - S["last_repeat"] >= SET["repeat_s"]:
                S["last_repeat"] = t
                notify(f"{int(mins)} min without moving. Get up!", 5, "MOVE NOW", "rotating_light")


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
    return {
        "mode": mode, "sitting_min": round((t - S["last_move"]) / 60, 1), "level": S["level_sent"],
        "steps": S["steps"], "last_sample_age_s": round(t - S["last_sample"]) if S["last_sample"] else None,
        "snooze_left_min": round(max(0, S["snooze_until"] - t) / 60, 1), "snooze_kind": S["snooze_kind"],
        "quiet_now": quiet, "uptime_min": round((t - S["started"]) / 60)}


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
        b = body if isinstance(body, bytes) else (json.dumps(body) + "\n").encode()
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
        tok = self.headers.get("X-Token") or (q.get("t") or [""])[0]
        if not TOKEN or not hmac.compare_digest(tok, TOKEN):
            return self.reply(403, {"error": "forbidden"})
        try:
            if u.path == "/steps":
                add_sample(int(float(q["steps"][0])))
                with lock:
                    flush_stats()
                return self.reply(200, {"ok": True})
            if u.path == "/snooze":
                m = max(1, min(600, int((q.get("min") or [SET["meeting_min"]])[0])))
                kind = (q.get("kind") or ["meeting"])[0][:10]
                with lock:
                    S["snooze_until"], S["snooze_kind"] = time.time() + m * 60, kind
                    save_state()
                    if kind in ("meeting", "nap"):
                        bump(lambda d: d["snoozes"].__setitem__(kind, d["snoozes"].get(kind, 0) + 1))
                notify(f"Snoozed {m} min ({kind}).", 1, "Snoozed", "zzz", actions=False)
                return self.reply(200, {"snoozed_min": m, "kind": kind})
            if u.path == "/resume":
                with lock:
                    S["snooze_until"], S["snooze_kind"] = 0.0, ""
                    save_state()
                    reset(time.time(), "snooze cancelled")
                return self.reply(200, {"ok": True})
            if u.path == "/status":
                with lock:
                    return self.reply(200, state(time.time()))
            if u.path == "/api/state":
                return self.reply(200, full_state())
            if u.path == "/api/samples":
                h = max(1, min(KEEP_REPORTS_H, int((q.get("hours") or [3])[0])))
                t = time.time()
                with lock:
                    out = [x for x in reports if x[0] >= t - h * 3600]
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
            if u.path == "/api/test" and self.command == "POST":
                notify("Test notification. Try the Meeting and Nap buttons.", 3, "Steps test", "test_tube")
                return self.reply(200, {"ok": True})
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
