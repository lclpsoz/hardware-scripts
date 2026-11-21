#!/usr/bin/env python3
"""netq: a small internet-quality logger with a web page, for the Raspberry Pi.

Runs in the `netq` container (see docker-compose.yml) and is served at
http://net.lan through the proxy. Docs: raspberry-pi/08-internet-quality.md

Every minute it measures, from the Pi:
  - ping (ICMP) to the router, the ISP's first hop, Cloudflare and Google
  - DNS lookup time through Pi-hole, and straight to Cloudflare and Google
Every 5 minutes: HTTPS connection time to Cloudflare (TCP + TLS + first byte).
Every 6 hours, and on demand: a download/upload test against Cloudflare's speed
endpoints, with latency measured during the download.

Standard library only. Samples are kept in RAM and written to SQLite every
5 minutes, so the SD card sees one small write burst per 5 minutes.
"""
import glob
import http.client
import json
import os
import random
import select
import signal
import socket
import sqlite3
import ssl
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from statistics import median
from urllib.parse import parse_qs, urlparse


def env(name, default):
    return os.environ.get(name, default)


def pairs(text):
    out = []
    for item in text.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            out.append((k.strip(), v.strip()))
    return out


BIND = env("NETQ_BIND", "127.0.0.1")
PORT = int(env("NETQ_PORT", "8098"))
DB_PATH = env("NETQ_DB", "/data/netq.db")
INTERVAL = int(env("NETQ_INTERVAL", "60"))
PING_TARGETS = pairs(env("NETQ_PING", "router=192.168.0.1,isp=100.66.64.1,cloudflare=1.1.1.1,google=8.8.8.8"))
DNS_TARGETS = pairs(env("NETQ_DNS", "pihole=127.0.0.1,cloudflare=1.1.1.1,google=8.8.8.8"))
PIHOLE_NAME = env("NETQ_PIHOLE_NAME", "www.google.com")
HTTPS_HOST = env("NETQ_HTTPS_HOST", "1.1.1.1")
HTTPS_EVERY = int(env("NETQ_HTTPS_EVERY", "300"))
SPEED_HOST = env("NETQ_SPEED_HOST", "speed.cloudflare.com")
SPEED_EVERY_H = float(env("NETQ_SPEED_EVERY_H", "6"))
SPEED_DOWN = int(env("NETQ_SPEED_DOWN_BYTES", "10000000"))
SPEED_UP = int(env("NETQ_SPEED_UP_BYTES", "2000000"))
SPEED_MIN_GAP = int(env("NETQ_SPEED_MIN_GAP", "600"))
KEEP_DAYS = int(env("NETQ_KEEP_DAYS", "60"))
FLUSH_EVERY = int(env("NETQ_FLUSH_EVERY", "300"))
UA = "netq/1.0 (home network quality logger)"

LABELS = {"router": "Router", "isp": "ISP gateway", "cloudflare": "Cloudflare",
          "google": "Google", "pihole": "Pi-hole"}
RANGES = {"6h": (6 * 3600, 60, 900), "24h": (86400, 300, 3600),
          "7d": (7 * 86400, 1800, 6 * 3600), "30d": (30 * 86400, 7200, 86400)}
STOP = threading.Event()
STATE = {"speed_running": False, "speed_last_start": 0.0, "started": time.time()}


# --------------------------------------------------------------------------- probes

def _checksum(data):
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def ping(host, count=5, gap=0.2, timeout=1.0):
    """ICMP echo. Returns {"rtt": median ms or None, "loss": 0..1, "min", "max"}."""
    raw = False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except OSError:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        raw = True
    ident = random.randint(1, 0xFFFF)
    sent, rtts = {}, {}
    try:
        for seq in range(count):
            payload = b"netq" + struct.pack("!d", time.time()) + b"\0" * 20
            header = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
            packet = struct.pack("!BBHHH", 8, 0, _checksum(header + payload), ident, seq) + payload
            sent[seq] = time.monotonic()
            sock.sendto(packet, (host, 0))
            wait_until = time.monotonic() + (gap if seq < count - 1 else timeout)
            while True:
                left = wait_until - time.monotonic()
                if left <= 0:
                    break
                ready, _, _ = select.select([sock], [], [], left)
                if not ready:
                    break
                data, addr = sock.recvfrom(2048)
                if raw:
                    data = data[(data[0] & 0x0F) * 4:]
                if len(data) < 8 or addr[0] != host:
                    continue
                kind, _, _, rid, rseq = struct.unpack("!BBHHH", data[:8])
                if kind != 0 or (raw and rid != ident):
                    continue
                if rseq in sent and rseq not in rtts:
                    rtts[rseq] = (time.monotonic() - sent[rseq]) * 1000
    except OSError:
        pass
    finally:
        sock.close()
    got = list(rtts.values())
    return {"rtt": round(median(got), 2) if got else None,
            "loss": round(1 - len(got) / count, 3),
            "min": round(min(got), 2) if got else None,
            "max": round(max(got), 2) if got else None}


def dns_query(server, name, timeout=2.0):
    """One A query over UDP. Returns (answered, ms, rcode)."""
    tid = random.randint(0, 0xFFFF)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.strip(".").split(".")) + b"\0"
    query = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    start = time.monotonic()
    try:
        sock.sendto(query, (server, 53))
        while True:
            data, _ = sock.recvfrom(4096)
            if len(data) >= 4 and data[:2] == query[:2]:
                break
        ms = (time.monotonic() - start) * 1000
        rcode = data[3] & 0x0F
        return rcode in (0, 3), round(ms, 2), rcode   # NOERROR or NXDOMAIN = answered
    except OSError:
        return False, None, None
    finally:
        sock.close()


def https_probe(host):
    """TCP connect, TLS handshake and first byte of /cdn-cgi/trace, in ms."""
    ctx = ssl.create_default_context()
    out = {}
    start = time.monotonic()
    try:
        raw = socket.create_connection((host, 443), timeout=5)
        out["tcp"] = round((time.monotonic() - start) * 1000, 1)
        tls = ctx.wrap_socket(raw, server_hostname=host)
        out["tls"] = round((time.monotonic() - start) * 1000 - out["tcp"], 1)
        t_req = time.monotonic()
        tls.sendall(f"GET /cdn-cgi/trace HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {UA}\r\n"
                    "Connection: close\r\n\r\n".encode())
        body = b""
        while True:
            chunk = tls.recv(4096)
            if not chunk:
                break
            if "ttfb" not in out:
                out["ttfb"] = round((time.monotonic() - t_req) * 1000, 1)
            body += chunk
        tls.close()
        out["total"] = round((time.monotonic() - start) * 1000, 1)
        for line in body.decode("utf-8", "replace").splitlines():
            if line.startswith("colo="):
                out["colo"] = line[5:].strip()
        return True, out
    except (OSError, ssl.SSLError) as exc:
        out["error"] = type(exc).__name__
        return False, out


def uv_flag():
    """Raspberry Pi undervoltage flag (1 = low right now), or None if unavailable."""
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(os.path.join(d, "name")).read().strip() == "rpi_volt":
                return int(open(os.path.join(d, "in0_lcrit_alarm")).read().strip())
        except (OSError, ValueError):
            pass
    return None


def speedtest(trigger):
    """Download SPEED_DOWN and upload SPEED_UP bytes over one pre-opened HTTPS
    connection each, so the handshake is not counted in the transfer time."""
    result = {"trigger": trigger}
    idle = ping("1.1.1.1", count=5)
    result["idle_ms"] = idle["rtt"]
    loaded, uv_seen, stop = [], [uv_flag() or 0], threading.Event()

    def pinger():
        while not stop.is_set():
            r = ping("1.1.1.1", count=1, timeout=1.0)
            if r["rtt"] is not None:
                loaded.append(r["rtt"])
            uv_seen.append(uv_flag() or 0)
            stop.wait(0.3)

    ctx = ssl.create_default_context()
    headers = {"User-Agent": UA}
    thread = threading.Thread(target=pinger, daemon=True)
    try:
        conn = http.client.HTTPSConnection(SPEED_HOST, 443, timeout=30, context=ctx)
        conn.connect()
        thread.start()
        conn.request("GET", f"/__down?bytes={SPEED_DOWN}", headers=headers)
        resp = conn.getresponse()
        first, size = None, 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            if first is None:
                first = time.monotonic()
            size += len(chunk)
        seconds = time.monotonic() - (first or time.monotonic())
        conn.close()
        if resp.status == 200 and size and seconds > 0:
            result["down_mbps"] = round(size * 8 / seconds / 1e6, 1)
        else:
            result["down_error"] = f"HTTP {resp.status}"
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        result["down_error"] = type(exc).__name__
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=3)
    try:
        body = os.urandom(SPEED_UP)          # random bytes: compression cannot help
        conn = http.client.HTTPSConnection(SPEED_HOST, 443, timeout=30, context=ctx)
        conn.connect()
        start = time.monotonic()
        conn.request("POST", "/__up", body=body,
                     headers=dict(headers, **{"Content-Type": "application/octet-stream"}))
        resp = conn.getresponse()
        resp.read()
        seconds = time.monotonic() - start
        conn.close()
        if resp.status == 200 and seconds > 0:
            result["up_mbps"] = round(SPEED_UP * 8 / seconds / 1e6, 1)
        else:
            result["up_error"] = f"HTTP {resp.status}"
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        result["up_error"] = type(exc).__name__
    result["loaded_ms"] = round(median(loaded), 1) if loaded else None
    result["uv"] = bool(max(uv_seen + [uv_flag() or 0]))
    return result


# --------------------------------------------------------------------------- storage

class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        self.buf = []
        self.last_flush = time.time()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("CREATE TABLE IF NOT EXISTS sample (ts INTEGER NOT NULL, kind TEXT NOT NULL,"
                            " target TEXT NOT NULL, ok INTEGER NOT NULL, ms REAL, loss REAL, extra TEXT)")
            self.db.execute("CREATE INDEX IF NOT EXISTS sample_kind_ts ON sample (kind, ts)")

    def add(self, ts, kind, target, ok, ms=None, loss=None, extra=None):
        with self.lock:
            self.buf.append((int(ts), kind, target, int(bool(ok)), ms, loss,
                             json.dumps(extra, separators=(",", ":")) if extra else None))

    def flush(self):
        with self.lock:
            rows, self.buf = self.buf, []
            if rows:
                self.db.execute("BEGIN")
                self.db.executemany("INSERT INTO sample VALUES (?,?,?,?,?,?,?)", rows)
                self.db.execute("COMMIT")
            self.last_flush = time.time()

    def prune(self):
        with self.lock:
            self.db.execute("DELETE FROM sample WHERE ts < ?", (int(time.time()) - KEEP_DAYS * 86400,))

    def rows(self, sql, args=()):
        self.flush()                          # include the samples still in RAM
        with self.lock:
            return self.db.execute(sql, args).fetchall()


STORE = None


# --------------------------------------------------------------------------- probe loop

def run_speedtest(trigger):
    STATE["speed_running"] = True
    STATE["speed_last_start"] = time.time()
    ts = time.time()
    try:
        res = speedtest(trigger)
        STORE.add(ts, "speed", "cloudflare", "down_mbps" in res, res.get("down_mbps"), None, res)
    finally:
        STATE["speed_running"] = False


def probe_loop():
    next_https = 0.0
    next_speed = time.time() + 300 if SPEED_EVERY_H > 0 else float("inf")
    next_prune = 0.0
    while not STOP.is_set():
        start = time.time()
        for name, host in PING_TARGETS:
            r = ping(host)
            STORE.add(start, "ping", name, r["rtt"] is not None, r["rtt"], r["loss"],
                      {"min": r["min"], "max": r["max"]})
        for name, server in DNS_TARGETS:
            qname = PIHOLE_NAME if name == "pihole" else "netq-%08x.example.com" % random.getrandbits(32)
            ok, ms, rcode = dns_query(server, qname)
            STORE.add(start, "dns", name, ok, ms, None, {"rcode": rcode})
        if start >= next_https:
            ok, info = https_probe(HTTPS_HOST)
            STORE.add(start, "https", "cloudflare", ok, info.get("total"), None, info)
            next_https = start + HTTPS_EVERY
        if start >= next_speed:
            if not STATE["speed_running"]:
                STATE["speed_running"] = True
                threading.Thread(target=run_speedtest, args=("schedule",), daemon=True).start()
            next_speed = start + SPEED_EVERY_H * 3600
        if time.time() - STORE.last_flush >= FLUSH_EVERY:
            STORE.flush()
        if start >= next_prune:
            STORE.prune()
            next_prune = start + 3600
        STOP.wait(max(1.0, INTERVAL - (time.time() - start)))
    STORE.flush()


# --------------------------------------------------------------------------- aggregation

_cache = {}


def _bucket(sql_rows, t0, step, n):
    """rows of (bucket_ts, target, value) -> {target: [value or None] * n}"""
    out = {}
    for b, target, value in sql_rows:
        i = int((b - t0) // step)
        if 0 <= i < n:
            out.setdefault(target, [None] * n)[i] = None if value is None else round(value, 2)
    return out


def incidents(since):
    rows = STORE.rows("SELECT ts, target, loss FROM sample WHERE kind='ping' AND ts>=? "
                      "AND target IN ('router','cloudflare','google') ORDER BY ts", (since,))
    minutes = {}
    for ts, target, loss in rows:
        minutes.setdefault(ts // 60 * 60, {})[target] = loss
    events, current = [], None
    for minute in sorted(minutes):
        m = minutes[minute]
        if m.get("router", 0) >= 0.8:
            kind = "lan"
        elif m.get("cloudflare", 0) >= 0.8 and m.get("google", 0) >= 0.8:
            kind = "internet"
        else:
            kind = None
        if current and (kind != current["kind"] or minute - current["end"] > 2 * INTERVAL):
            events.append(current)
            current = None
        if kind:
            if current is None:
                current = {"kind": kind, "start": minute, "end": minute}
            current["end"] = minute
    if current:
        events.append(current)
    for e in events:
        e["minutes"] = (e["end"] - e["start"]) // 60 + 1
    return sorted(events, key=lambda e: -e["start"])[:50]


def summary():
    now = int(time.time())

    def recent(kind, target, seconds):
        rows = STORE.rows("SELECT ms, loss, ok FROM sample WHERE kind=? AND target=? AND ts>=?",
                          (kind, target, now - seconds))
        return rows

    lat, loss1h, dns = {}, {}, {}
    for name, _ in PING_TARGETS:
        rows = recent("ping", name, 900)
        vals = [r[0] for r in rows if r[0] is not None]
        lat[name] = round(median(vals), 1) if vals else None
        rows = recent("ping", name, 3600)
        loss1h[name] = round(100 * sum(r[1] or 0 for r in rows) / len(rows), 1) if rows else None
    for name, _ in DNS_TARGETS:
        rows = recent("dns", name, 900)
        vals = [r[0] for r in rows if r[0] is not None]
        fails = sum(1 for r in rows if not r[2])
        dns[name] = {"ms": round(median(vals), 1) if vals else None,
                     "fail_pct": round(100 * fails / len(rows), 1) if rows else None}
    last5 = {name: recent("ping", name, 300) for name, _ in PING_TARGETS}

    def loss5(name):
        rows = last5.get(name) or []
        return sum(r[1] or 0 for r in rows) / len(rows) if rows else None

    internet = [v for v in (loss5("cloudflare"), loss5("google")) if v is not None]
    router = loss5("router")
    if router is None and not internet:
        status, text = "warning", "No samples yet"
    elif router is not None and router >= 0.8:
        status, text = "critical", "Router not answering"
    elif internet and min(internet) >= 0.8:
        status, text = "critical", "Internet down"
    elif (internet and max(internet) >= 0.1) or (dns.get("pihole", {}).get("fail_pct") or 0) >= 10:
        status, text = "serious", "Packet loss or DNS failures"
    elif (internet and max(internet) > 0) or (lat.get("cloudflare") or 0) > 100:
        status, text = "warning", "Some loss or high latency"
    else:
        status, text = "good", "Internet OK"
    speed = STORE.rows("SELECT ts, extra FROM sample WHERE kind='speed' ORDER BY ts DESC LIMIT 1")
    https = STORE.rows("SELECT ts, ms, extra FROM sample WHERE kind='https' ORDER BY ts DESC LIMIT 1")
    last = STORE.rows("SELECT max(ts) FROM sample")
    return {
        "now": now, "last_sample": last[0][0] if last else None,
        "status": status, "status_text": text,
        "latency_15m": lat, "loss_1h_pct": loss1h, "dns_15m": dns,
        "https_last": ({"ts": https[0][0], "ms": https[0][1],
                        "colo": json.loads(https[0][2] or "{}").get("colo")} if https else None),
        "speed_last": ({"ts": speed[0][0], **json.loads(speed[0][1] or "{}")} if speed else None),
        "speed_running": STATE["speed_running"],
        "labels": LABELS,
    }


def data(range_key):
    span, step, strip_step = RANGES.get(range_key, RANGES["24h"])
    key = (range_key, int(time.time() // 30))
    if key in _cache:
        return _cache[key]
    now = int(time.time())
    t0 = (now - span) // step * step
    n = (now - t0) // step + 1
    agg = STORE.rows(
        "SELECT ts/?*? b, kind, target, avg(ms), avg(loss), avg(ok), max(loss) FROM sample "
        "WHERE ts>=? AND kind IN ('ping','dns','https') GROUP BY b, kind, target", (step, step, t0))
    pick = lambda kind, col: [(r[0], r[2], r[col]) for r in agg if r[1] == kind]
    dns_fail = [(b, t, None if ok is None else 100 * (1 - ok)) for b, t, ok in pick("dns", 5)]
    s0 = (now - span) // strip_step * strip_step
    sn = (now - s0) // strip_step + 1
    strip = STORE.rows(
        "SELECT ts/?*? b, target, avg(loss)*100, max(loss)*100, sum(loss>0), count(*) FROM sample "
        "WHERE kind='ping' AND ts>=? GROUP BY b, target", (strip_step, strip_step, s0))
    loss_strip = {}
    for b, target, mean_loss, worst, lossy, count in strip:
        i = int((b - s0) // strip_step)
        if 0 <= i < sn:
            loss_strip.setdefault(target, [None] * sn)[i] = [round(mean_loss, 2), round(worst, 1), lossy, count]
    speed = [dict({"ts": ts}, **json.loads(extra or "{}")) for ts, extra in
             STORE.rows("SELECT ts, extra FROM sample WHERE kind='speed' AND ts>=? ORDER BY ts", (now - span,))]
    out = {
        "range": range_key, "now": now, "step": step, "t0": t0, "n": n,
        "ping_ms": _bucket(pick("ping", 3), t0, step, n),
        "dns_ms": _bucket(pick("dns", 3), t0, step, n),
        "dns_fail": _bucket(dns_fail, t0, step, n),
        "https_ms": _bucket(pick("https", 3), t0, step, n),
        "strip": {"t0": s0, "step": strip_step, "n": sn, "rows": loss_strip},
        "speed": speed,
        "incidents": incidents(now - span),
        "summary": summary(),
    }
    _cache.clear()
    _cache[key] = out
    return out


# --------------------------------------------------------------------------- web

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internet quality</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%232a78d6'/%3E%3Cpath d='M6 22 L12 14 L18 18 L26 8' fill='none' stroke='white' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
:root { color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --isp: #2a78d6; --router: #eb6834; --cloudflare: #1baf7a; --google: #eda100; --pihole: #6250d6;
  --down: #e87ba4; --up: #008300;
  --l1: #86b6ef; --l2: #5598e7; --l3: #2a78d6; --l4: #1c5cab; --l5: #104281;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) { color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
  --isp: #3987e5; --router: #d95926; --cloudflare: #199e70; --google: #c98500; --pihole: #9085e9;
  --down: #d55181; --up: #0ca30c;
  --l1: #184f95; --l2: #256abf; --l3: #3987e5; --l4: #6da7ec; --l5: #b7d3f6; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 22px 16px 40px; }
h1 { font-size: 20px; margin: 0; } h2 { font-size: 15px; margin: 0 0 2px; }
.sub { color: var(--ink2); font-size: 13px; margin: 2px 0 0; }
.bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 16px 0; }
.bar a, .bar button { font: inherit; font-size: 14px; padding: 5px 12px; border-radius: 8px;
  border: 1px solid var(--ring); background: var(--surface); color: var(--ink); text-decoration: none; cursor: pointer; }
.bar a.on { font-weight: 600; box-shadow: inset 0 -2px 0 var(--ink); }
.bar .sp { flex: 1; } .bar button:disabled { opacity: .55; cursor: default; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.tile, .card { background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 12px 14px; }
.tile .k { color: var(--ink2); font-size: 13px; } .tile .v { font-size: 24px; font-weight: 600; margin-top: 2px; }
.tile .v .status { font-size: 19px; }
.tile .d { color: var(--muted); font-size: 12px; margin-top: 2px; }
.status { display: inline-flex; align-items: center; gap: 7px; }
.badge { width: 18px; height: 18px; border-radius: 50%; display: inline-grid; place-items: center;
  color: #fff; font-size: 12px; font-weight: 700; }
.badge.warning { color: #0b0b0b; }
.card { margin-top: 12px; } .cap { color: var(--ink2); font-size: 13px; margin: 0 0 8px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: var(--ink2); margin: 4px 0 6px; }
.key { display: inline-block; width: 14px; height: 2px; vertical-align: middle; margin-right: 6px; border-radius: 1px; }
.sw { display: inline-block; width: 12px; height: 12px; vertical-align: -1px; margin-right: 5px; border-radius: 3px; }
.chart { position: relative; } svg { display: block; width: 100%; overflow: visible; }
svg text { font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--muted); font-variant-numeric: tabular-nums; }
svg .lbl { fill: var(--ink2); font-size: 12px; }
.tip { position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--ring);
  border-radius: 8px; padding: 7px 9px; font-size: 12px; box-shadow: 0 4px 14px rgba(0,0,0,.12); display: none;
  min-width: 140px; z-index: 2; }
.tip .t { color: var(--muted); margin-bottom: 3px; } .tip .r { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.tip .r b { min-width: 52px; text-align: right; font-variant-numeric: tabular-nums; } .tip .r span { color: var(--ink2); }
details { margin-top: 6px; font-size: 13px; } summary { color: var(--ink2); cursor: pointer; }
.tbl { max-height: 260px; overflow: auto; margin-top: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; white-space: nowrap; }
th:first-child, td:first-child, th.txt, td.txt { text-align: left; } th { color: var(--ink2); font-weight: 600; }
.note { color: var(--ink2); font-size: 13px; margin-top: 18px; } .note li { margin: 3px 0; }
.empty { color: var(--muted); font-size: 13px; padding: 18px 0; }
</style></head><body><main>
<h1>Internet quality</h1>
<p class="sub">Measured from the Raspberry Pi every minute · <span id="last"></span></p>
<div class="bar" id="ranges"></div>
<div class="tiles" id="tiles"></div>
<div id="charts"></div>
<div class="card"><h2>Speed tests</h2>
  <p class="cap">Cloudflare download and upload, every 6 hours and on demand. The Pi's 100&nbsp;Mbps Ethernet caps results near 94&nbsp;Mbps, so a faster plan reads as the cap.</p>
  <div id="speed"></div></div>
<div class="card"><h2>Incidents</h2>
  <p class="cap">Minutes when the router (LAN) or both Cloudflare and Google (internet) lost at least 80% of pings.</p>
  <div id="inc"></div></div>
<ul class="note">
  <li><b>Router</b> is the LAN hop. <b>ISP gateway</b> is the first hop inside the ISP's network. <b>Cloudflare</b> and <b>Google</b> are the far end.</li>
  <li>DNS through <b>Pi-hole</b> asks for a popular name (usually cached). DNS to Cloudflare and Google asks for a new random name each time, so it measures a full lookup.</li>
  <li>Charts show the average of each period's per-minute medians; hover or use the arrow keys for values, or open the table under each chart.</li>
</ul>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById('data').textContent);
const S = D.summary, L = S.labels || {};
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const el = (tag, attrs, text) => { const e = document.createElement(tag); for (const k in (attrs || {})) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; };
const svgEl = (tag, attrs) => { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
const fmtTime = (ts, long) => { const d = new Date(ts * 1000);
  return long ? d.toLocaleString([], {weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'})
              : d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); };
// short axis labels: time of day for 6h/24h, weekday + hour for 7d, date for 30d
const fmtAxis = ts => { const d = new Date(ts * 1000);
  if (D.range === '30d') return d.toLocaleDateString([], {day: 'numeric', month: 'short'});
  if (D.range === '7d') return d.toLocaleDateString([], {weekday: 'short'}) + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); };
const tickCount = pw => Math.max(2, Math.min(6, Math.floor(pw / 120)));
const fmtMs = v => v == null ? '–' : (v < 10 ? v.toFixed(1) : Math.round(v)) + ' ms';
const ago = ts => { if (!ts) return 'never'; const s = Math.max(0, D.now - ts);
  return s < 90 ? Math.round(s) + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 172800 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago'; };
const longRange = D.range === '7d' || D.range === '30d';

document.getElementById('last').textContent = 'last sample ' + ago(S.last_sample);

// range filter row, plus the speed test button
const bar = document.getElementById('ranges');
for (const r of ['6h', '24h', '7d', '30d']) { const a = el('a', {href: '?range=' + r}, r === '6h' ? 'Last 6 hours' : r === '24h' ? 'Last 24 hours' : r === '7d' ? 'Last 7 days' : 'Last 30 days'); if (r === D.range) a.className = 'on'; bar.append(a); }
bar.append(el('span', {class: 'sp'}));
const btn = el('button', {type: 'button'}, S.speed_running ? 'Speed test running…' : 'Run speed test');
btn.disabled = !!S.speed_running; bar.append(btn);
btn.onclick = async () => { btn.disabled = true; btn.textContent = 'Starting…';
  try { const r = await fetch('api/speedtest', {method: 'POST'}); const j = await r.json();
    btn.textContent = j.started ? 'Running… page reloads in 25 s' : (j.reason || 'Not started');
    if (j.started) setTimeout(() => location.reload(), 25000); } catch (e) { btn.textContent = 'Failed to start'; } };

// stat tiles
const tiles = document.getElementById('tiles');
function tile(k, v, d, status) { const t = el('div', {class: 'tile'}); t.append(el('div', {class: 'k'}, k));
  const vv = el('div', {class: 'v'}); if (status) { const s = el('span', {class: 'status'});
    const icon = {good: '✓', warning: '!', serious: '!', critical: '×'}[status];
    const b = el('span', {class: 'badge ' + status}, icon); b.style.background = css('--' + status); s.append(b, document.createTextNode(v)); vv.append(s); }
  else vv.textContent = v; t.append(vv); if (d) t.append(el('div', {class: 'd'}, d)); tiles.append(t); }
tile('Now', S.status_text, 'based on the last 5 minutes', S.status);
tile('Cloudflare latency', fmtMs(S.latency_15m.cloudflare), 'median, last 15 min');
const lossNet = ['cloudflare', 'google'].map(k => S.loss_1h_pct[k]).filter(v => v != null);
tile('Packet loss', lossNet.length ? Math.max(...lossNet).toFixed(1) + '%' : '–', 'worst internet target, last hour');
tile('DNS via Pi-hole', fmtMs(S.dns_15m.pihole && S.dns_15m.pihole.ms), 'median, last 15 min');
const sp = S.speed_last; tile('Last speed test', sp && sp.down_mbps != null ? sp.down_mbps + ' / ' + (sp.up_mbps != null ? sp.up_mbps : '–') + ' Mbps' : '–',
  sp ? 'down / up, ' + ago(sp.ts) : 'none yet');

// line chart with crosshair tooltip and a table view
function lineChart(parent, title, caption, unit, series, opts) {
  opts = opts || {};
  const card = el('div', {class: 'card'}); parent.append(card); card.append(el('h2', {}, title + ' (' + unit + ')'), el('p', {class: 'cap'}, caption));
  const has = series.filter(s => s.values && s.values.some(v => v != null));
  if (!has.length) { card.append(el('div', {class: 'empty'}, 'No data in this range yet.')); return card; }
  if (has.length > 1) { const lg = el('div', {class: 'legend'}); for (const s of has) { const i = el('span'); const k = el('span', {class: 'key'}); k.style.background = css(s.color); i.append(k, document.createTextNode(s.label)); lg.append(i); } card.append(lg); }
  const box = el('div', {class: 'chart'}); card.append(box);
  const tip = el('div', {class: 'tip'}); box.append(tip);
  const n = D.n, t = i => D.t0 + i * D.step;
  function draw() {
    box.querySelectorAll('svg').forEach(e => e.remove());
    const W = Math.max(300, box.clientWidth), H = 190, m = {l: 44, r: has.length > 1 ? 96 : 16, t: 8, b: 22};
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    let max = 0; for (const s of has) for (const v of s.values) if (v != null && v > max) max = v;
    if (opts.cap && max > opts.cap) max = opts.cap;
    const nice = x => { const p = Math.pow(10, Math.floor(Math.log10(x || 1))); for (const f of [1, 2, 2.5, 5, 10]) if (f * p >= x) return f * p; return 10 * p; };
    const yMax = nice(max * 1.08 || 1), X = i => m.l + (n <= 1 ? 0 : i / (n - 1) * pw), Y = v => m.t + ph - Math.min(v, yMax) / yMax * ph;
    const svg = svgEl('svg', {viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': title});
    for (let k = 0; k <= 4; k++) { const v = yMax * k / 4, y = Y(v);
      svg.append(svgEl('line', {x1: m.l, x2: m.l + pw, y1: y, y2: y, stroke: k ? css('--grid') : css('--axis'), 'stroke-width': 1}));
      const tx = svgEl('text', {x: m.l - 6, y: y + 4, 'text-anchor': 'end'}); tx.textContent = (yMax >= 10 ? Math.round(v) : v.toFixed(1)); svg.append(tx); }
    const ticks = tickCount(pw); for (let k = 0; k <= ticks; k++) { const i = Math.round(k / ticks * (n - 1)); const tx = svgEl('text', {x: X(i), y: H - 6, 'text-anchor': k === 0 ? 'start' : k === ticks ? 'end' : 'middle'}); tx.textContent = fmtAxis(t(i)); svg.append(tx); }
    const ends = [];
    for (const s of has) { let d = '', pen = false; s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1); pen = true; });
      svg.append(svgEl('path', {d, fill: 'none', stroke: css(s.color), 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
      for (let i = s.values.length - 1; i >= 0; i--) if (s.values[i] != null) { ends.push({s, i, y: Y(s.values[i])}); break; } }
    if (has.length > 1) { ends.sort((a, b) => a.y - b.y); let lastY = -1e9;
      for (const e of ends) { if (e.y - lastY < 13) continue; lastY = e.y;
        const tx = svgEl('text', {x: m.l + pw + 6, y: e.y + 4, class: 'lbl'}); tx.textContent = e.s.label; svg.append(tx); } }
    for (const e of ends) svg.append(svgEl('circle', {cx: X(e.i), cy: e.y, r: 4, fill: css(e.s.color), stroke: css('--surface'), 'stroke-width': 2}));
    const cross = svgEl('line', {y1: m.t, y2: m.t + ph, stroke: css('--axis'), 'stroke-width': 1, visibility: 'hidden'}); svg.append(cross);
    const hit = svgEl('rect', {x: m.l, y: m.t, width: pw, height: ph, fill: 'transparent', tabindex: 0}); svg.append(hit);
    let cur = n - 1;
    const show = i => { cur = Math.max(0, Math.min(n - 1, i)); const x = X(cur); cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
      tip.replaceChildren(el('div', {class: 't'}, fmtTime(t(cur), true)));
      for (const s of has) { const r = el('div', {class: 'r'}); const k = el('span', {class: 'key'}); k.style.background = css(s.color);
        const v = s.values[cur]; r.append(k, el('b', {}, v == null ? '–' : (opts.fmt ? opts.fmt(v) : fmtMs(v))), el('span', {}, s.label)); tip.append(r); }
      tip.style.display = 'block'; const tw = tip.offsetWidth; tip.style.left = Math.min(Math.max(0, x + 12), W - tw) + 'px'; tip.style.top = '6px'; };
    const hide = () => { tip.style.display = 'none'; cross.setAttribute('visibility', 'hidden'); };
    hit.addEventListener('pointermove', ev => { const r = svg.getBoundingClientRect(); const px = (ev.clientX - r.left) / r.width * W; show(Math.round((px - m.l) / pw * (n - 1))); });
    hit.addEventListener('pointerleave', hide); hit.addEventListener('blur', hide); hit.addEventListener('focus', () => show(cur));
    hit.addEventListener('keydown', ev => { if (ev.key === 'ArrowLeft') { show(cur - 1); ev.preventDefault(); } else if (ev.key === 'ArrowRight') { show(cur + 1); ev.preventDefault(); } else if (ev.key === 'Escape') hide(); });
    box.prepend(svg);
  }
  draw(); let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(draw, 150); });
  // table view
  const det = el('details'); det.append(el('summary', {}, 'Table view')); const wrap = el('div', {class: 'tbl'}); const tb = el('table');
  const hr = el('tr'); hr.append(el('th', {}, 'Time')); for (const s of has) hr.append(el('th', {}, s.label + ' (' + unit + ')')); tb.append(hr);
  for (let i = n - 1; i >= 0; i--) { if (!has.some(s => s.values[i] != null)) continue; const tr = el('tr'); tr.append(el('td', {}, fmtTime(t(i), true)));
    for (const s of has) { const v = s.values[i]; tr.append(el('td', {}, v == null ? '–' : (opts.fmt ? opts.fmt(v) : v.toFixed(1)))); } tb.append(tr); }
  wrap.append(tb); det.append(wrap); card.append(det);
  return card;
}

// packet-loss heat strip: one row per target, cells binned on a one-hue ramp
function lossStrip(parent) {
  const st = D.strip, rows = ['router', 'isp', 'cloudflare', 'google'].filter(k => st.rows[k]);
  const card = el('div', {class: 'card'}); parent.append(card); card.append(el('h2', {}, 'Packet loss'),
    el('p', {class: 'cap'}, 'Share of pings lost per ' + (st.step >= 86400 ? 'day' : st.step >= 3600 ? (st.step / 3600) + ' hours' : (st.step / 60) + ' minutes').replace('1 hours', 'hour') + '. Empty cells: no loss.'));
  if (!rows.length) { card.append(el('div', {class: 'empty'}, 'No data in this range yet.')); return card; }
  const bins = [[0.5, '--l1', '< 0.5%'], [2, '--l2', '0.5–2%'], [10, '--l3', '2–10%'], [50, '--l4', '10–50%'], [101, '--l5', '50–100%']];
  const lg = el('div', {class: 'legend'}); for (const b of bins) { const i = el('span'); const s = el('span', {class: 'sw'}); s.style.background = css(b[1]); i.append(s, document.createTextNode(b[2])); lg.append(i); } card.append(lg);
  const box = el('div', {class: 'chart'}); card.append(box); const tip = el('div', {class: 'tip'}); box.append(tip);
  function draw() {
    box.querySelectorAll('svg').forEach(e => e.remove());
    const W = Math.max(300, box.clientWidth), rowH = 22, m = {l: 92, r: 16, t: 4, b: 22}, H = m.t + rows.length * (rowH + 4) + m.b;
    const pw = W - m.l - m.r, cw = pw / st.n;
    const svg = svgEl('svg', {viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Packet loss per target'});
    rows.forEach((k, r) => { const y = m.t + r * (rowH + 4);
      const tx = svgEl('text', {x: m.l - 8, y: y + rowH / 2 + 4, 'text-anchor': 'end', class: 'lbl'}); tx.textContent = L[k] || k; svg.append(tx);
      svg.append(svgEl('rect', {x: m.l, y, width: pw, height: rowH, rx: 4, fill: css('--grid'), opacity: 0.45}));
      st.rows[k].forEach((c, i) => { if (!c) return; const g = cw > 6 ? 2 : 0;
        const cell = svgEl('rect', {x: m.l + i * cw + g / 2, y, width: Math.max(1, cw - g), height: rowH, rx: cw > 8 ? 3 : 0, tabindex: 0,
          fill: c[0] > 0 ? css(bins.find(b => c[0] < b[0])[1]) : 'transparent'});
        const showTip = () => { tip.replaceChildren(el('div', {class: 't'}, fmtTime(st.t0 + i * st.step, true)));
          for (const [label, val] of [['lost', c[0].toFixed(2) + '%'], ['worst minute', c[1].toFixed(0) + '%'], ['minutes with loss', c[2] + ' of ' + c[3]]]) { const rr = el('div', {class: 'r'}); rr.append(el('b', {}, val), el('span', {}, label)); tip.append(rr); }
          tip.style.display = 'block'; tip.style.left = Math.min(m.l + i * cw, W - tip.offsetWidth) + 'px'; tip.style.top = (y + rowH + 6) + 'px'; };
        cell.addEventListener('pointerenter', showTip); cell.addEventListener('focus', showTip);
        cell.addEventListener('pointerleave', () => tip.style.display = 'none'); cell.addEventListener('blur', () => tip.style.display = 'none');
        svg.append(cell); }); });
    const sk = tickCount(pw); for (let k = 0; k <= sk; k++) { const i = Math.round(k / sk * (st.n - 1)); const tx = svgEl('text', {x: m.l + (k === 0 ? 0 : k === sk ? pw : (i + 0.5) * cw), y: H - 6, 'text-anchor': k === 0 ? 'start' : k === sk ? 'end' : 'middle'}); tx.textContent = fmtAxis(st.t0 + i * st.step); svg.append(tx); }
    box.prepend(svg);
  }
  draw(); let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(draw, 150); });
  const det = el('details'); det.append(el('summary', {}, 'Table view')); const wrap = el('div', {class: 'tbl'}); const tb = el('table');
  const hr = el('tr'); hr.append(el('th', {}, 'Period')); for (const k of rows) hr.append(el('th', {}, (L[k] || k) + ' lost')); tb.append(hr);
  for (let i = st.n - 1; i >= 0; i--) { if (!rows.some(k => st.rows[k][i])) continue; const tr = el('tr'); tr.append(el('td', {}, fmtTime(st.t0 + i * st.step, true)));
    for (const k of rows) { const c = st.rows[k][i]; tr.append(el('td', {}, c ? c[0].toFixed(2) + '%' : '–')); } tb.append(tr); }
  wrap.append(tb); det.append(wrap); card.append(det); return card;
}

const charts = document.getElementById('charts');
const P = D.ping_ms || {}, N = D.dns_ms || {}, HS = D.https_ms || {};
lineChart(charts, 'Internet latency', 'Ping round trip from the Pi.', 'ms',
  [{label: L.isp, color: '--isp', values: P.isp}, {label: L.cloudflare, color: '--cloudflare', values: P.cloudflare}, {label: L.google, color: '--google', values: P.google}], {cap: 400});
lossStrip(charts);
lineChart(charts, 'DNS lookup time', 'Through Pi-hole (cached name) and straight to each resolver (new name every time).', 'ms',
  [{label: L.pihole, color: '--pihole', values: N.pihole}, {label: L.cloudflare, color: '--cloudflare', values: N.cloudflare}, {label: L.google, color: '--google', values: N.google}], {cap: 1000});
lineChart(charts, 'Router latency', 'Ping to the router over the Pi\'s Ethernet cable: a rise here points at the LAN or the router itself.', 'ms',
  [{label: L.router, color: '--router', values: P.router}], {cap: 100});
lineChart(charts, 'HTTPS to Cloudflare', 'TCP connect, TLS handshake and first byte, every 5 minutes.', 'ms',
  [{label: 'HTTPS', color: '--cloudflare', values: HS.cloudflare}], {cap: 2000});

// speed tests: chart (if 2+ points) + table
(function () {
  const box = document.getElementById('speed'); const tests = (D.speed || []).filter(s => s.down_mbps != null || s.up_mbps != null);
  if (!tests.length) { box.append(el('div', {class: 'empty'}, 'No speed tests in this range yet.')); return; }
  if (tests.length >= 2) {
    const lg = el('div', {class: 'legend'}); for (const [lab, c] of [['Download', '--down'], ['Upload', '--up']]) { const i = el('span'); const k = el('span', {class: 'key'}); k.style.background = css(c); i.append(k, document.createTextNode(lab)); lg.append(i); } box.append(lg);
    const wrap = el('div', {class: 'chart'}); box.append(wrap); const tip = el('div', {class: 'tip'}); wrap.append(tip);
    const draw = () => { wrap.querySelectorAll('svg').forEach(e => e.remove());
      const W = Math.max(300, wrap.clientWidth), H = 180, m = {l: 44, r: 16, t: 8, b: 22}, pw = W - m.l - m.r, ph = H - m.t - m.b;
      const t0 = D.now - ({'6h': 21600, '24h': 86400, '7d': 604800, '30d': 2592000}[D.range]), X = ts => m.l + (ts - t0) / (D.now - t0) * pw;
      const yMax = 100, Y = v => m.t + ph - Math.min(v, yMax) / yMax * ph;
      const svg = svgEl('svg', {viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Speed tests'});
      for (let k = 0; k <= 4; k++) { const y = Y(k * 25); svg.append(svgEl('line', {x1: m.l, x2: m.l + pw, y1: y, y2: y, stroke: k ? css('--grid') : css('--axis'), 'stroke-width': 1}));
        const tx = svgEl('text', {x: m.l - 6, y: y + 4, 'text-anchor': 'end'}); tx.textContent = k * 25; svg.append(tx); }
      const capY = Y(94); svg.append(svgEl('line', {x1: m.l, x2: m.l + pw, y1: capY, y2: capY, stroke: css('--axis'), 'stroke-width': 1}));
      const ct = svgEl('text', {x: m.l + 4, y: capY - 4}); ct.textContent = 'Pi Ethernet limit ≈ 94 Mbps'; svg.append(ct);
      for (const [key, c] of [['down_mbps', '--down'], ['up_mbps', '--up']]) { const pts = tests.filter(s => s[key] != null);
        svg.append(svgEl('path', {d: pts.map((s, i) => (i ? 'L' : 'M') + X(s.ts).toFixed(1) + ' ' + Y(s[key]).toFixed(1)).join(''), fill: 'none', stroke: css(c), 'stroke-width': 2, 'stroke-linejoin': 'round'}));
        for (const s of pts) { const dot = svgEl('circle', {cx: X(s.ts), cy: Y(s[key]), r: 4, fill: css(c), stroke: css('--surface'), 'stroke-width': 2}); svg.append(dot); } }
      for (const s of tests) { const hit = svgEl('rect', {x: X(s.ts) - 12, y: m.t, width: 24, height: ph, fill: 'transparent', tabindex: 0});
        const sh = () => { tip.replaceChildren(el('div', {class: 't'}, fmtTime(s.ts, true)));
          for (const [lab, v, c] of [['download', s.down_mbps, '--down'], ['upload', s.up_mbps, '--up']]) { const r = el('div', {class: 'r'}); const k = el('span', {class: 'key'}); k.style.background = css(c); r.append(k, el('b', {}, v == null ? '–' : v + ' Mbps'), el('span', {}, lab)); tip.append(r); }
          tip.style.display = 'block'; tip.style.left = Math.min(Math.max(0, X(s.ts) + 14), W - tip.offsetWidth) + 'px'; tip.style.top = '6px'; };
        hit.addEventListener('pointerenter', sh); hit.addEventListener('focus', sh);
        hit.addEventListener('pointerleave', () => tip.style.display = 'none'); hit.addEventListener('blur', () => tip.style.display = 'none'); svg.append(hit); }
      const sk = tickCount(pw); for (let k = 0; k <= sk; k++) { const ts = t0 + k / sk * (D.now - t0); const tx = svgEl('text', {x: X(ts), y: H - 6, 'text-anchor': k === 0 ? 'start' : k === sk ? 'end' : 'middle'}); tx.textContent = fmtAxis(ts); svg.append(tx); }
      wrap.prepend(svg); };
    draw(); let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(draw, 150); });
  }
  const tb = el('table'); const hr = el('tr'); ['When', 'Download', 'Upload', 'Idle ping', 'Ping during download', 'Notes'].forEach((h, i) => hr.append(el('th', i === 5 ? {class: 'txt'} : {}, h))); tb.append(hr);
  for (const s of tests.slice().reverse()) { const tr = el('tr');
    const notes = [s.trigger === 'manual' ? 'on demand' : 'scheduled', s.uv ? 'undervoltage during test' : null, s.down_error ? 'download: ' + s.down_error : null, s.up_error ? 'upload: ' + s.up_error : null].filter(Boolean).join(', ');
    [fmtTime(s.ts, true), s.down_mbps != null ? s.down_mbps + ' Mbps' : '–', s.up_mbps != null ? s.up_mbps + ' Mbps' : '–', fmtMs(s.idle_ms), fmtMs(s.loaded_ms), notes].forEach((v, i) => tr.append(el('td', i === 5 ? {class: 'txt'} : {}, v)));
    tb.append(tr); }
  const w = el('div', {class: 'tbl'}); w.append(tb); box.append(w);
})();

// incidents
(function () { const box = document.getElementById('inc'); const inc = D.incidents || [];
  if (!inc.length) { box.append(el('div', {class: 'empty'}, 'None in this range.')); return; }
  const tb = el('table'); const hr = el('tr'); ['Start', 'Duration', 'What'].forEach((h, i) => hr.append(el('th', i === 2 ? {class: 'txt'} : {}, h))); tb.append(hr);
  for (const e of inc) { const tr = el('tr'); [fmtTime(e.start, true), e.minutes + ' min', e.kind === 'lan' ? 'Router not answering (LAN)' : 'Internet unreachable'].forEach((v, i) => tr.append(el('td', i === 2 ? {class: 'txt'} : {}, v))); tb.append(tr); }
  box.append(tb); })();

setTimeout(() => location.reload(), 120000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        rng = parse_qs(url.query).get("range", ["24h"])[0]
        rng = rng if rng in RANGES else "24h"
        try:
            if url.path in ("/", "/index.html"):
                payload = json.dumps(data(rng), separators=(",", ":")).replace("</", "<\\/")
                self._send(200, PAGE.replace("__DATA__", payload).encode(), "text/html; charset=utf-8")
            elif url.path == "/api/summary":
                self._send(200, json.dumps(summary()).encode(), "application/json")
            elif url.path == "/api/data":
                self._send(200, json.dumps(data(rng)).encode(), "application/json")
            else:
                self._send(404, b"not found\n", "text/plain")
        except Exception as exc:  # keep serving
            self._send(500, f"error: {type(exc).__name__}\n".encode(), "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/speedtest":
            return self._send(404, b"not found\n", "text/plain")
        wait = SPEED_MIN_GAP - (time.time() - STATE["speed_last_start"])
        if STATE["speed_running"]:
            body = {"started": False, "reason": "A test is already running"}
        elif wait > 0:
            body = {"started": False, "reason": f"Try again in {int(wait // 60) + 1} min"}
        else:
            STATE["speed_running"] = True
            STATE["speed_last_start"] = time.time()
            threading.Thread(target=run_speedtest, args=("manual",), daemon=True).start()
            body = {"started": True}
        self._send(200, json.dumps(body).encode(), "application/json")

    def log_message(self, *args):     # no per-request logs
        pass


def demo_data(store, days=7):
    """Synthetic samples for testing the page without a network (python3 netq.py --demo)."""
    now = int(time.time())
    rnd = random.Random(7)
    for ts in range(now - days * 86400, now, 60):
        hour = time.localtime(ts).tm_hour
        evening = 1.6 if 19 <= hour <= 23 else 1.0
        outage = (now - 2 * 86400 + 3600) <= ts < (now - 2 * 86400 + 3600 + 6 * 60)
        for name, base in (("router", 0.8), ("isp", 3.0), ("cloudflare", 31.0), ("google", 34.0)):
            loss = 1.0 if (outage and name != "router") else (0.2 if rnd.random() < 0.01 else 0.0)
            rtt = None if loss >= 1 else round(base * evening * (1 + rnd.random() * 0.15), 2)
            store.add(ts, "ping", name, rtt is not None, rtt, loss)
        for name, base in (("pihole", 1.5), ("cloudflare", 40.0), ("google", 70.0)):
            ok = not outage or name == "pihole"
            store.add(ts, "dns", name, ok, round(base * (1 + rnd.random()), 2) if ok else None, None, {"rcode": 0})
        if ts % 300 < 60:
            store.add(ts, "https", "cloudflare", not outage, None if outage else round(95 + rnd.random() * 30, 1))
        if ts % (6 * 3600) < 60:
            store.add(ts, "speed", "cloudflare", True, 90.0, None,
                      {"trigger": "schedule", "down_mbps": round(84 + rnd.random() * 9, 1),
                       "up_mbps": round(60 + rnd.random() * 25, 1), "idle_ms": 31.0,
                       "loaded_ms": round(40 + rnd.random() * 30, 1), "uv": rnd.random() < 0.1})
    store.flush()


def main():
    global STORE
    STORE = Store(DB_PATH)
    demo = "--demo" in sys.argv
    if demo:
        demo_data(STORE)

    def stop(*_):
        STOP.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not demo:
        threading.Thread(target=probe_loop, daemon=True).start()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.timeout = 1
    print(f"netq listening on {BIND}:{PORT}, db {DB_PATH}{' (demo)' if demo else ''}", flush=True)
    while not STOP.is_set():
        server.handle_request()
    STORE.flush()
    print("netq stopped, samples flushed", flush=True)


if __name__ == "__main__":
    main()
