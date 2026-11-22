#!/usr/bin/env python3
"""alerts: watches the Pi and sends push notifications through ntfy.

Standard library only. Runs in the `alerts` container (python:3.13-alpine, host
network, read-only). See raspberry-pi/11-notifications.md.

How it works
  - Every INTERVAL seconds it runs a list of checks. A check reports a condition
    ("is this bad right now?"), not a message. The engine sends ONE notification
    when a condition has been bad for `after` seconds, and ONE "resolved"
    notification when it clears. No repeats while it stays bad.
  - Event checks (new Pi-hole message, flight-recorder event, reboot) send once per
    new item.
  - State (what was already announced) is kept in /data/state.json and written only
    when it changes, so the SD card sees almost no writes.

Add a check: write a function that calls observe(...) or event(...), and list it in CHECKS.

Usage
  alerts.py              run forever
  alerts.py --selftest   run every check once, print results, send nothing
  alerts.py --send-test  send one test notification and exit
"""
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

NTFY_URL = os.environ.get("NTFY_URL", "http://127.0.0.1:2586").rstrip("/")
TOPIC = os.environ.get("NTFY_TOPIC", "")
INTERVAL = int(os.environ.get("ALERTS_INTERVAL", "60"))
STATE_FILE = os.environ.get("ALERTS_STATE", "/data/state.json")
HEARTBEAT_FILE = os.environ.get("ALERTS_HEARTBEAT", "/data/heartbeat")
PIHOLE_URL = os.environ.get("PIHOLE_URL", "http://127.0.0.1:8080")
PIHOLE_PW_FILE = os.environ.get("PIHOLE_PW_FILE", "/run/secrets/pihole_cli_pw")
NETQ_URL = os.environ.get("NETQ_URL", "http://127.0.0.1:8098")
BACKUP_STAMP = os.environ.get("BACKUP_STAMP", "/ops-state/last-backup.json")
UV_EVENTS = os.environ.get("UV_EVENTS", "/uv-flight/events.jsonl")
BACKUP_MAX_AGE_H = float(os.environ.get("BACKUP_MAX_AGE_H", "36"))
DRY = "--selftest" in sys.argv

# Priorities in ntfy: 1 min, 2 low (silent), 3 default, 4 high, 5 urgent
STATE = {"p": {}, "pihole_seen": [], "uv_offset": None, "uv_last": 0, "uv_suppressed": 0,
         "boot_id": None, "first_seen": None, "versions": {}}
_dirty = False
_outbox = []          # notifications that could not be delivered yet
_results = []         # for --selftest


def now():
    return time.time()


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            STATE.update(json.load(f))
    except (OSError, ValueError):
        pass


def save_state():
    global _dirty
    if not _dirty or DRY:
        return
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(STATE, f)
    os.replace(tmp, STATE_FILE)
    _dirty = False


def dirty():
    global _dirty
    _dirty = True


# ---------------------------------------------------------------- sending
def notify(title, message, prio=3, tags=None):
    """Queue one notification; delivery is retried on the next cycle if ntfy is down."""
    _outbox.append({"topic": TOPIC, "title": title, "message": message,
                    "priority": prio, "tags": tags or []})
    del _outbox[:-50]


def flush_outbox():
    while _outbox and not DRY:
        body = json.dumps(_outbox[0]).encode()
        req = urllib.request.Request(NTFY_URL, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except (urllib.error.URLError, OSError) as e:
            log("ntfy send failed:", e)
            return
        _outbox.pop(0)
    if DRY:
        for n in _outbox:
            log("WOULD SEND", n["priority"], n["title"], "|", n["message"][:100])
        _outbox.clear()


# ---------------------------------------------------------------- engine
def human(sec):
    sec = int(sec)
    if sec < 90:
        return f"{sec} s"
    if sec < 5400:
        return f"{sec // 60} min"
    return f"{sec / 3600:.1f} h"


def observe(key, bad, title, message="", prio=3, after=120, tags=None):
    """Report a condition. Notifies once after `after` s of badness, and once when it clears."""
    _results.append((key, "BAD" if bad else "ok", message))
    st = STATE["p"].setdefault(key, {"since": None, "alerted": False})
    if bad:
        if st["since"] is None:
            st["since"] = now()
            dirty()
        if not st["alerted"] and now() - st["since"] >= after:
            st["alerted"] = True
            dirty()
            notify(title, message, prio, tags or ["warning"])
    else:
        if st["alerted"]:
            notify("Resolved: " + title, f"Back to normal after {human(now() - st['since'])}.", 2,
                   ["white_check_mark"])
        if st["since"] is not None or st["alerted"]:
            st["since"], st["alerted"] = None, False
            dirty()


def event(title, message, prio=3, tags=None):
    _results.append(("event", "EVENT", title))
    notify(title, message, prio, tags or ["information_source"])


# ---------------------------------------------------------------- probes
def get(url, host=None, timeout=5, headers=None):
    h = dict(headers or {})
    if host:
        h["Host"] = host
    req = urllib.request.Request(url, headers=h)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError):
        return None, b""


def get_json(url, **kw):
    code, body = get(url, **kw)
    if code != 200:
        raise OSError(f"HTTP {code} from {url}")
    return json.loads(body)


def dns_ok(server, port, name, timeout=3.0):
    tid = os.urandom(2)
    q = tid + struct.pack("!HHHHH", 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack("!HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, port))
        data, _ = s.recvfrom(2048)
        return data[:2] == tid and (data[3] & 0x0F) in (0, 3)
    except OSError:
        return False
    finally:
        s.close()


# ---------------------------------------------------------------- checks
def check_services():
    """Is every service answering? Probes, not the Docker API, so no Docker socket is needed."""
    web = [("Pi-hole web", "http://127.0.0.1:8080/admin/", None, 4),
           ("NetAlertX", "http://127.0.0.1:20211/", None, 3),
           ("Beszel", "http://127.0.0.1:8090/", None, 3),
           ("Proxy (.lan names)", "http://127.0.0.1/", "home.lan", 3),
           ("Power page", "http://127.0.0.1:8099/json", None, 2),
           ("ntfy", "http://127.0.0.1:2586/v1/health", None, 3),
           ("netq", NETQ_URL + "/api/summary", None, 3)]
    for name, url, host, prio in web:
        code, _ = get(url, host=host)
        observe("svc:" + name, code is None or code >= 500, f"{name} is not answering",
                f"No valid answer from {url} (HTTP {code}). Check: docker ps; docker logs <container>.", prio,
                after=120, tags=["whale"])
    ok = dns_ok("127.0.0.1", 53, "home.lan")
    observe("svc:Pi-hole DNS", not ok, "Pi-hole DNS is not answering",
            "127.0.0.1:53 gave no answer for home.lan. Devices cannot resolve names.", 5, after=60,
            tags=["rotating_light"])


def check_netq():
    try:
        s = get_json(NETQ_URL + "/api/summary")
    except (OSError, ValueError):
        return                                   # netq itself is covered by check_services
    age = now() - (s.get("last_sample") or 0)
    observe("netq:stale", age > 300, "netq stopped sampling",
            f"Last sample {human(age)} ago. The internet-quality history has a gap.", 3, after=60,
            tags=["chart_with_downwards_trend"])
    status = s.get("status")
    observe("internet", status in ("serious", "critical"), "Internet problem: " + str(s.get("status_text")),
            f"netq status is '{status}': {s.get('status_text')}. Latency {s.get('latency_15m')}; "
            f"loss last hour {s.get('loss_1h_pct')}. Details: http://net.lan", 4 if status == "critical" else 3,
            after=180, tags=["globe_with_meridians"])
    fw = (s.get("dns_15m") or {}).get("forwarder") or {}
    fail = fw.get("fail_pct")
    observe("forwarder", fail is not None and fail >= 50, "Encrypted DNS forwarder is failing",
            f"{fail}% of the lookups through dnsproxy failed in the last 15 min. If it stays down, new names "
            "stop resolving. Check: docker logs dnsproxy.", 4, after=180, tags=["lock"])


def check_laptop_wifi():
    """The laptop's pings to the router are slow or lossy while the Pi sees a healthy line: the problem is
    the laptop's Wi-Fi, not the ISP. netq decides (see laptop_wifi_diag); we only add the 15 min wait."""
    try:
        w = (get_json(NETQ_URL + "/api/summary").get("laptop_wifi") or {})
    except (OSError, ValueError):
        return
    observe("laptop:wifi", bool(w.get("bad")), "The laptop's Wi-Fi is the weak link, not the internet",
            "; ".join(w.get("reasons") or []) + ". The Pi sees the line as healthy. Move closer to the router, "
            "check the band, or use the cable. Details: http://net.lan", 3, after=900, tags=["signal_strength"])


def check_weekly():
    """Sunday evening: one low-priority summary of the week's laptop speed tests (by hour of day)."""
    t = time.localtime()
    day = time.strftime("%Y-%m-%d", t)
    if t.tm_wday != 6 or t.tm_hour < 20 or STATE.get("weekly_last") == day:
        return
    try:
        w = get_json(NETQ_URL + "/api/weekly")
    except (OSError, ValueError):
        return
    notify(w.get("title", "Weekly internet summary"), w.get("text", ""), 2, ["bar_chart"])
    STATE["weekly_last"] = day
    dirty()


def check_system():
    try:
        with open("/proc/meminfo") as f:
            mem = {l.split(":")[0]: int(l.split()[1]) for l in f}
        avail = mem["MemAvailable"] // 1024
        observe("mem", avail < 80, "Pi is low on memory", f"Only {avail} MiB available (limit 80).", 3, after=300,
                tags=["brain"])
    except (OSError, KeyError, ValueError):
        pass
    try:
        t = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
        observe("temp", t >= 75, "Pi is running hot", f"CPU {t:.0f} °C (limit 75).", 3, after=120,
                tags=["thermometer"])
    except (OSError, ValueError):
        pass
    try:
        v = os.statvfs("/ops-state")
        used = 100 * (1 - v.f_bavail / v.f_blocks)
        observe("disk", used >= 85, "SD card is getting full", f"{used:.0f}% used (limit 85%).", 3, after=600,
                tags=["floppy_disk"])
    except OSError:
        pass
    try:
        boot = open("/proc/sys/kernel/random/boot_id").read().strip()
        if STATE["boot_id"] and boot != STATE["boot_id"]:
            event("Pi rebooted", "The Pi restarted. Services should be back within a few minutes.", 3,
                  ["arrows_counterclockwise"])
        if boot != STATE["boot_id"]:
            STATE["boot_id"] = boot
            dirty()
    except OSError:
        pass


def check_flight_recorder():
    running = False
    try:
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        if b"uv-flight-recorder" in f.read():
                            running = True
                            break
                except OSError:
                    continue
    except OSError:
        return
    observe("flight_recorder", not running, "Undervoltage flight recorder is not running",
            "The service uv-flight-recorder is gone, so undervoltage events are no longer explained. "
            "Check: systemctl status uv-flight-recorder", 3, after=180, tags=["airplane"])
    try:
        size = os.path.getsize(UV_EVENTS)
    except OSError:
        if STATE["uv_offset"] is None:           # no file yet: the first event must not be skipped
            STATE["uv_offset"] = 0
            dirty()
        return                                   # no events yet
    if STATE["uv_offset"] is None or size < STATE["uv_offset"]:
        STATE["uv_offset"] = 0 if STATE["uv_offset"] is not None else size   # start after history
        dirty()
    if size > STATE["uv_offset"]:
        with open(UV_EVENTS, "rb") as f:
            f.seek(STATE["uv_offset"])
            lines = f.read().decode("utf-8", "replace").splitlines()
        STATE["uv_offset"] = size
        dirty()
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if now() - STATE["uv_last"] < 1800:      # batch bursts: one message per 30 min
                STATE["uv_suppressed"] += 1
                continue
            top = ", ".join(f"{n} {ms} ms" for n, ms in (r.get("prev10s_top_cpu_ms") or [])[:3]) or "nothing notable"
            extra = f" (+{STATE['uv_suppressed']} more since the last message)" if STATE["uv_suppressed"] else ""
            event("Undervoltage event" + extra,
                  f"{r.get('event')}: the Pi was '{r.get('class')}' before it (CPU peak {r.get('prev10s_cpu_peak')}%, "
                  f"busiest core {r.get('prev10s_busiest_core_peak')}%). Most CPU in the last 10 s: {top}.",
                  3, ["zap"])
            STATE["uv_last"], STATE["uv_suppressed"] = now(), 0
        dirty()


def check_backup():
    if STATE["first_seen"] is None:
        STATE["first_seen"] = now()
        dirty()
    try:
        with open(BACKUP_STAMP) as f:
            stamp = json.load(f)
        ts = float(stamp["ts"])
        age_h = (now() - ts) / 3600
        observe("backup_age", age_h > BACKUP_MAX_AGE_H, "No recent backup",
                f"The last successful backup was {age_h:.0f} h ago (limit {BACKUP_MAX_AGE_H:.0f} h). "
                "Is the laptop off? Run: bash raspberry-pi/laptop/services/backup/run.sh", 3, after=0,
                tags=["floppy_disk"])
    except (OSError, ValueError, KeyError):
        never = now() - STATE["first_seen"] > 48 * 3600
        observe("backup_age", never, "No backup has ever been recorded",
                "The laptop has not reported a backup in 48 h since alerts started.", 3, after=0, tags=["floppy_disk"])


_pihole_next = 0.0


def check_pihole_api():
    """Pi-hole's own diagnostic messages (DNS limit, rate limit, ...), updates, gravity age, blocking."""
    global _pihole_next
    if now() < _pihole_next and not DRY:
        return
    _pihole_next = now() + 300
    try:
        pw = open(PIHOLE_PW_FILE).read().strip()
        req = urllib.request.Request(PIHOLE_URL + "/api/auth", data=json.dumps({"password": pw}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            sid = json.load(r)["session"]["sid"]
    except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
        observe("pihole_api", True, "Pi-hole API check is failing",
                f"Could not log in to the Pi-hole API ({e}). Pi-hole messages are not being watched.", 2,
                after=1800, tags=["shield"])
        return
    observe("pihole_api", False, "Pi-hole API check is failing")
    h = {"sid": sid}
    try:
        msgs = get_json(PIHOLE_URL + "/api/info/messages", headers=h).get("messages", [])
        keys = [f"{m.get('id')}:{m.get('timestamp')}" for m in msgs]
        first = not STATE["pihole_seen"] and "pihole_init" not in STATE
        new = [m for m, k in zip(msgs, keys) if k not in STATE["pihole_seen"]]
        if first:
            STATE["pihole_init"] = True
        elif new:
            for m in new[:5]:
                plain = (m.get("plain") or m.get("type") or "Pi-hole message").strip()
                event("Pi-hole: " + str(m.get("type", "message")), plain[:400], 3, ["shield"])
            if len(new) > 5:
                event("Pi-hole: more messages", f"{len(new) - 5} more new messages. See http://pihole.lan (bell icon).", 2,
                      ["shield"])
        STATE["pihole_seen"] = (STATE["pihole_seen"] + [k for k in keys if k not in STATE["pihole_seen"]])[-200:]
        dirty()
    except (OSError, ValueError, KeyError):
        pass
    try:
        v = get_json(PIHOLE_URL + "/api/info/version", headers=h).get("version", {})
        for comp in ("core", "web", "ftl"):
            loc = ((v.get(comp) or {}).get("local") or {}).get("version")
            rem = ((v.get(comp) or {}).get("remote") or {}).get("version")
            if loc and rem and loc != rem and STATE["versions"].get(comp) != rem:
                event(f"Pi-hole update available ({comp} {rem})",
                      f"Installed {loc}. Update with: cd ~/pihole && docker compose pull && docker compose up -d "
                      "(DNS pauses for about 20 s). Back up first.", 2, ["arrow_up"])
                STATE["versions"][comp] = rem
                dirty()
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    try:
        g = get_json(PIHOLE_URL + "/api/stats/summary", headers=h).get("gravity", {})
        last = g.get("last_update")
        if last:
            days = (now() - float(last)) / 86400
            observe("gravity_old", days > 10, "Blocklists have not updated",
                    f"Gravity was last updated {days:.0f} days ago (it should refresh weekly).", 3, after=0,
                    tags=["shield"])
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    try:
        b = get_json(PIHOLE_URL + "/api/dns/blocking", headers=h).get("blocking")
        observe("pihole_blocking_off", b not in ("enabled", None), "Pi-hole blocking is switched off",
                f"Blocking is '{b}'. Ads and trackers are not being blocked for any device.", 3, after=600,
                tags=["shield"])
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    try:
        req = urllib.request.Request(PIHOLE_URL + "/api/auth", headers=h, method="DELETE")
        urllib.request.urlopen(req, timeout=5).close()
    except (OSError, urllib.error.URLError):
        pass


CHECKS = [check_services, check_netq, check_laptop_wifi, check_weekly, check_system, check_flight_recorder, check_backup, check_pihole_api]


def heartbeat():
    if DRY:
        return
    try:
        if now() - os.path.getmtime(HEARTBEAT_FILE) < 540:
            return
    except OSError:
        pass
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump({"ts": now()}, f)


def run_once():
    for chk in CHECKS:
        try:
            chk()
        except Exception as e:                  # a broken check must never stop the others
            log("check failed:", chk.__name__, repr(e))
            _results.append((chk.__name__, "ERROR", repr(e)))
    save_state()
    flush_outbox()
    heartbeat()


def main():
    if not TOPIC:
        sys.exit("NTFY_TOPIC is not set (see config.env.example)")
    if "--send-test" in sys.argv:
        notify("Pi alerts: test", "If you can read this, notifications from the Pi work.", 3, ["white_check_mark"])
        flush_outbox()
        return
    load_state()
    if "--selftest" in sys.argv:
        run_once()
        for r in _results:
            print(f"{r[0]:28} {r[1]:6} {r[2][:90]}")
        return
    first_run = STATE["boot_id"] is None and not os.path.exists(STATE_FILE)
    log("alerts started; topic", TOPIC[:3] + "…", "interval", INTERVAL)
    if first_run:
        notify("Pi alerts started", "Watching services, internet quality, backups, undervoltage, Pi-hole and "
               "system health. You will get one message when something breaks and one when it recovers.", 2,
               ["white_check_mark"])
    while True:
        t0 = time.time()
        _results.clear()
        run_once()
        time.sleep(max(5, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    main()
