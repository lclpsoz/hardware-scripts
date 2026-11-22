#!/usr/bin/env python3
"""Timed unblock for the work laptop's focus groups in Pi-hole.

Each site (reddit, globo, uol, bbc) has a Pi-hole group `focus-<site>` that carries its deny rule. The group is
enabled (= blocked) by default. This service disables one for N minutes, then enables it again. State is kept in
/data/state.json so a restart does not forget an open window, and every open/close is appended to /data/log.csv.
Docs: raspberry-pi/15-focus-blocking.md
"""
import csv
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PIHOLE_URL = os.environ.get("PIHOLE_URL", "http://127.0.0.1:8080")
PW_FILE = os.environ.get("PIHOLE_PW_FILE", "/run/secrets/pihole_cli_pw")
SITES = [s for s in os.environ.get("FOCUS_SITES", "reddit,globo,uol,bbc").split(",") if s]
PORT = int(os.environ.get("FOCUS_PORT", "8095"))
MAX_MIN = int(os.environ.get("FOCUS_MAX_MINUTES", "120"))
STATE = "/data/state.json"
LOG = "/data/log.csv"
LOCK = threading.Lock()
open_until = {}  # site -> epoch when it must be blocked again


def now():
    return time.time()


def api(method, path, sid=None, body=None):
    h = {"Content-Type": "application/json"}
    if sid:
        h["sid"] = sid
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PIHOLE_URL + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def login():
    pw = open(PW_FILE).read().strip()
    return api("POST", "/api/auth", body={"password": pw})["session"]["sid"]


def logout(sid):
    try:
        api("DELETE", "/api/auth", sid)
    except (OSError, urllib.error.URLError, ValueError):
        pass


def set_groups(want_blocked):
    """want_blocked: {site: bool}. Returns True if Pi-hole now matches."""
    sid = login()
    try:
        for site, blocked in want_blocked.items():
            name = "focus-" + site
            cur = api("GET", "/api/groups/" + name, sid)["groups"][0]
            if bool(cur.get("enabled")) != blocked:
                api("PUT", "/api/groups/" + name, sid,
                    {"name": name, "comment": cur.get("comment"), "enabled": blocked})
        return True
    finally:
        logout(sid)


def save():
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(open_until, f)
    os.replace(tmp, STATE)


def log(site, action, minutes):
    try:
        with open(LOG, "a", newline="") as f:
            csv.writer(f).writerow([time.strftime("%Y-%m-%d %H:%M:%S"), site, action, minutes])
    except OSError:
        pass


def desired():
    return {s: not (s in open_until and open_until[s] > now()) for s in SITES}


def reconcile():
    for s in [s for s, t in open_until.items() if t <= now()]:
        del open_until[s]
        log(s, "expired", 0)
    save()
    return set_groups(desired())


def do_open(site, minutes):
    if site not in SITES:
        raise ValueError("unknown site")
    minutes = max(1, min(int(minutes), MAX_MIN))
    with LOCK:
        open_until[site] = now() + minutes * 60
        log(site, "open", minutes)
        save()
        set_groups(desired())


def do_close(site):
    with LOCK:
        for s in SITES if site == "all" else [site]:
            if s in open_until:
                del open_until[s]
                log(s, "close", 0)
        save()
        set_groups(desired())


def status():
    t = now()
    return {"sites": [{"site": s, "open": open_until.get(s, 0) > t,
                       "seconds_left": max(0, int(open_until.get(s, 0) - t))} for s in SITES],
            "max_minutes": MAX_MIN}


def loop():
    last_full = 0.0
    while True:
        try:
            with LOCK:
                expired = any(t <= now() for t in open_until.values())
                if expired or now() - last_full > 300:
                    reconcile()
                    last_full = now()
        except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
            print("reconcile failed:", e, flush=True)
            time.sleep(10)
        time.sleep(5)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/status":
            with LOCK:
                return self.send(200, status())
        if self.path in ("/", "/index.html"):
            return self.send(200, open("/app/ui.html", "rb").read(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/open":
                do_open(req["site"], req["minutes"])
            elif self.path == "/api/close":
                do_close(req.get("site", "all"))
            else:
                return self.send(404, {"error": "not found"})
            with LOCK:
                self.send(200, status())
        except (KeyError, ValueError, TypeError) as e:
            self.send(400, {"error": str(e)})
        except (OSError, urllib.error.URLError) as e:
            self.send(502, {"error": "Pi-hole API: %s" % e})


if __name__ == "__main__":
    try:
        open_until.update({k: float(v) for k, v in json.load(open(STATE)).items() if k in SITES})
    except (OSError, ValueError):
        pass
    for attempt in range(30):  # Pi-hole may still be starting
        try:
            reconcile()
            break
        except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
            print("startup reconcile failed:", e, flush=True)
            time.sleep(5)
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
