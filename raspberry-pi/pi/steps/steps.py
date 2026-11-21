#!/usr/bin/env python3
"""steps: nags you through ntfy when you sit too long. Standard library only.

The phone posts its cumulative step counter to /steps. If fewer than MOVE_STEPS
steps happen within MOVE_WINDOW seconds, the "sitting" timer keeps running.
Escalation (minutes sitting): 50 light (ntfy prio 3), 60 mid (4), 70 high (5),
80+ constant (prio 5 every REPEAT_S seconds) until you walk.
Snooze (meeting / nap) pauses everything; the timer restarts when it ends.

Endpoints (token in header X-Token or ?t=):
  /steps?steps=N        phone reports its cumulative step counter
  /snooze?min=60&kind=meeting   (kind: meeting|nap)
  /resume               cancel the snooze
  /status               JSON state
  /health               no token
"""
import hmac
import json
import os
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
LEVELS = [float(x) for x in E("LEVELS_MIN", "50,60,70,80").split(",")]  # light, mid, high, constant
REPEAT_S = int(E("REPEAT_S", "20"))
MOVE_STEPS = int(E("MOVE_STEPS", "200"))
MOVE_WINDOW = int(E("MOVE_WINDOW_S", "300"))
STALE_S = int(E("STALE_S", "1800"))
QUIET = E("QUIET_HOURS", "22:00-07:00")
MEETING_MIN = int(E("MEETING_MIN", "60"))
NAP_MIN = int(E("NAP_MIN", "30"))
STATE_FILE = E("STATE_FILE", "/data/state.json")

lock = threading.Lock()
S = {"last_move": time.time(), "snooze_until": 0.0, "snooze_kind": "", "last_sample": 0.0,
     "stale_told": False, "level_sent": 0, "last_repeat": 0.0, "steps": None}
samples = deque()


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def save():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"snooze_until": S["snooze_until"], "snooze_kind": S["snooze_kind"]}, f)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log("state not saved:", e)


def load():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
        S["snooze_until"] = float(d.get("snooze_until", 0))
        S["snooze_kind"] = d.get("snooze_kind", "")
    except (OSError, ValueError):
        pass


def notify(msg, prio=3, title="Move!", tags="walking", actions=True):
    if not TOPIC:
        log("NO TOPIC, would send:", prio, msg)
        return
    h = {"Priority": str(prio), "Title": title, "Tags": tags}
    if actions:
        a = []
        for label, mins, kind in (("Meeting", MEETING_MIN, "meeting"), ("Nap", NAP_MIN, "nap")):
            a.append(f"http, {label} {mins}m, {PUBLIC}/snooze?min={mins}&kind={kind}, "
                     f"method=POST, headers.X-Token={TOKEN}, clear=true")
        h["Actions"] = "; ".join(a)
    try:
        req = urllib.request.Request(f"{NTFY_URL}/{TOPIC}", data=msg.encode(), headers=h, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:  # ntfy down: log and carry on
        log("ntfy failed:", e)


def in_quiet(t):
    try:
        a, b = QUIET.split("-")
        lt = time.localtime(t)
        cur = lt.tm_hour * 60 + lt.tm_min
        s = int(a[:2]) * 60 + int(a[3:])
        e = int(b[:2]) * 60 + int(b[3:])
        return (s <= cur < e) if s < e else (cur >= s or cur < e)
    except Exception:
        return False


def reset(t, why):
    S["last_move"] = t
    S["level_sent"] = 0
    S["last_repeat"] = 0.0
    log("timer reset:", why)


def add_sample(n):
    t = time.time()
    with lock:
        if samples and n < samples[-1][1]:       # phone rebooted: counter restarted
            samples.clear()
        samples.append((t, n))
        while samples and samples[0][0] < t - MOVE_WINDOW:
            samples.popleft()
        first = samples[0][1]
        was_stale = S["last_sample"] and t - S["last_sample"] > STALE_S
        S["last_sample"], S["steps"], S["stale_told"] = t, n, False
        if was_stale:
            reset(t, "phone back after a gap")
        elif n - first >= MOVE_STEPS:
            if S["level_sent"] >= 1:
                notify("Moving again, timer reset.", 1, "Good", "tada", actions=False)
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
            save()
            reset(t, "snooze ended")
            notify("Snooze over, the timer starts now.", 2, "Snooze ended", "alarm_clock", actions=False)
            return
        if in_quiet(t):
            S["last_move"] = t
            S["level_sent"] = 0
            return
        if S["last_sample"] and t - S["last_sample"] > STALE_S:
            S["last_move"] = t
            S["level_sent"] = 0
            if not S["stale_told"]:
                S["stale_told"] = True
                notify("No step data from the phone for 30 min; reminders paused.", 2,
                       "Steps: phone silent", "warning", actions=False)
            return
        mins = (t - S["last_move"]) / 60
        for i in range(len(LEVELS) - 1, -1, -1):
            if mins >= LEVELS[i]:
                lvl = i + 1
                break
        else:
            return
        if lvl < 4:
            if lvl > S["level_sent"]:
                S["level_sent"] = lvl
                notify(f"Sitting for {int(mins)} min. Walk a bit.", (3, 4, 5)[lvl - 1],
                       ("Stand up", "Move now", "MOVE NOW")[lvl - 1],
                       ("walking", "warning", "rotating_light")[lvl - 1])
        else:
            S["level_sent"] = 4
            if t - S["last_repeat"] >= REPEAT_S:
                S["last_repeat"] = t
                notify(f"{int(mins)} min without moving. Get up!", 5, "MOVE NOW", "rotating_light")


def loop():
    while True:
        try:
            tick()
        except Exception as e:
            log("tick error:", e)
        time.sleep(5)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body):
        b = (json.dumps(body) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def handle_any(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/health":
            return self.reply(200, {"ok": True})
        tok = self.headers.get("X-Token") or (q.get("t") or [""])[0]
        if not TOKEN or not hmac.compare_digest(tok, TOKEN):
            return self.reply(403, {"error": "forbidden"})
        try:
            if u.path == "/steps":
                add_sample(int(float(q["steps"][0])))
                return self.reply(200, {"ok": True})
            if u.path == "/snooze":
                m = max(1, min(600, int((q.get("min") or [MEETING_MIN])[0])))
                kind = (q.get("kind") or ["meeting"])[0][:10]
                with lock:
                    S["snooze_until"], S["snooze_kind"] = time.time() + m * 60, kind
                    save()
                notify(f"Snoozed {m} min ({kind}).", 1, "Snoozed", "zzz", actions=False)
                return self.reply(200, {"snoozed_min": m, "kind": kind})
            if u.path == "/resume":
                with lock:
                    S["snooze_until"], S["snooze_kind"] = 0.0, ""
                    save()
                    reset(time.time(), "snooze cancelled")
                return self.reply(200, {"ok": True})
            if u.path == "/status":
                t = time.time()
                with lock:
                    return self.reply(200, {
                        "sitting_min": round((t - S["last_move"]) / 60, 1),
                        "level": S["level_sent"], "steps": S["steps"],
                        "last_sample_age_s": round(t - S["last_sample"]) if S["last_sample"] else None,
                        "snooze_left_min": round(max(0, S["snooze_until"] - t) / 60, 1),
                        "snooze_kind": S["snooze_kind"], "quiet_now": in_quiet(t)})
        except (KeyError, ValueError, IndexError):
            return self.reply(400, {"error": "bad request"})
        self.reply(404, {"error": "not found"})

    do_GET = do_POST = handle_any


if __name__ == "__main__":
    load()
    threading.Thread(target=loop, daemon=True).start()
    log("steps listening on", PORT, "topic set:", bool(TOPIC))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
