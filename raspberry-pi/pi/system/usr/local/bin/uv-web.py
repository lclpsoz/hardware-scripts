#!/usr/bin/env python3
"""uv-web: tiny read-only page with the Raspberry Pi undervoltage history.

Reads the kernel log of ALL boots (the journal is persistent; plain -k would
show only the current boot). The full history is read once; after that only the last
few minutes are read, at most once a minute, and nothing while nobody asks. Standard library only.
"""
import html
import json
import re
import subprocess
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8099
DAYS = 7
SNAP_FILE = "/var/log/uv-events.log"
CACHE_SECONDS = 60
LINE = re.compile(
    r"^(\S+)\s+\S+\s+kernel: hwmon hwmon\d+: (Undervoltage detected!|Voltage normalised)"
)
_cache = {"t": 0.0, "page": b"", "json": b""}


_seen = {}          # journal line -> None (ordered): all undervoltage lines of the last DAYS days
_last_read = [0.0]  # when the journal was last read


def get_lines():
    """First call: read DAYS days of the kernel log of all boots. Later calls: only the last few
    minutes (journalctl seeks by time, so it is cheap) and merge, instead of re-scanning
    the whole journal on every refresh."""
    import time
    first = not _seen
    since = f"{DAYS} days ago" if first else "5 minutes ago"
    out = subprocess.run(
        ["journalctl", "-k", "-b", "all", "--since", since, "--grep", "hwmon hwmon[0-9]+: (Under|Voltage)",
         "-o", "short-iso", "--no-pager"],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode in (0, 1):  # 1 = no match
        for ln in out.stdout.splitlines():
            _seen.setdefault(ln, None)
        _last_read[0] = time.time()
    cutoff = (datetime.now().astimezone() - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    for ln in [l for l in _seen if l[:19] < cutoff]:   # drop what is older than DAYS
        del _seen[ln]
    return list(_seen)


def parse(lines):
    """Pair 'detected' with the next 'normalised' -> list of (start, end|None)."""
    episodes, start = [], None
    for ln in lines:
        m = LINE.match(ln)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z")
        if m.group(2).startswith("Under"):
            if start is not None:  # no recovery logged (e.g. reboot)
                episodes.append((start, None))
            start = ts
        elif start is not None:
            episodes.append((start, ts))
            start = None
    if start is not None:
        episodes.append((start, None))
    return episodes


def throttled():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        raw = out.split("=")[1]
        n = int(raw, 16)
        names = [(0x1, "under-voltage NOW"), (0x4, "throttled NOW"),
                 (0x10000, "under-voltage since boot"), (0x40000, "throttled since boot")]
        flags = [t for bit, t in names if n & bit]
        return raw, ", ".join(flags) or "OK", bool(n & 0x1)
    except Exception:
        return "n/a", "vcgencmd not available", False


def uptime():
    try:
        s = int(float(open("/proc/uptime").read().split()[0]))
        return f"{s // 86400}d {s % 86400 // 3600}h {s % 3600 // 60}m"
    except Exception:
        return "n/a"


def snapshots():
    try:
        with open(SNAP_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 6000))
            return f.read().decode("utf-8", "replace")
    except Exception:
        return "(no snapshots yet)"


def fmt(ts):
    return ts.astimezone().strftime("%d %b %H:%M:%S")


def build(lines=None, now=None):
    now = now or datetime.now().astimezone()
    eps = parse(get_lines() if lines is None else lines)

    def count(hours):
        return sum(1 for s, _ in eps if now - s <= timedelta(hours=hours))

    bars = []
    for i in range(23, -1, -1):
        h0 = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        n = sum(1 for s, _ in eps if h0 <= s < h0 + timedelta(hours=1))
        bars.append(f'<div class="b" title="{h0:%d %b %H}h: {n}">'
                    f'<i style="height:{min(60, 4 * n) + 1}px"></i><span>{h0:%H}</span></div>')

    day_rows = ""
    for i in range(DAYS):
        d = (now - timedelta(days=i)).date()
        n = sum(1 for s, _ in eps if s.astimezone().date() == d)
        day_rows += f"<tr><td>{d:%a %d %b}</td><td>{n}</td></tr>"

    rows = ""
    for s, e in reversed(eps[-40:]):
        dur = f"{int((e - s).total_seconds())} s" if e else "no recovery logged"
        rows += f"<tr><td>{fmt(s)}</td><td>{fmt(e) if e else '-'}</td><td>{dur}</td></tr>"

    raw, text, under_now = throttled()
    color = "#e5484d" if under_now else "#30a46c"
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Pi voltage</title><link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E%3Crect%20width='32'%20height='32'%20rx='7'%20fill='%23ef6c00'/%3E%3Cg%20transform='translate(2.56%202.56)%20scale(1.12)'%20fill='none'%20stroke='white'%20stroke-width='2'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Cpath%20d='M13%202L4%2014h7l-1%208%209-12h-7z'/%3E%3C/g%3E%3C/svg%3E">
<style>body{{font:15px system-ui;background:#111;color:#ddd;margin:12px auto;padding:0 12px;max-width:640px}}
h1{{font-size:18px}}h2{{font-size:15px;margin:18px 0 6px;color:#9ab}}
.c{{background:#1c1c1c;border-radius:8px;padding:10px;margin:8px 0}}
.s{{border-left:5px solid {color}}}table{{width:100%;border-collapse:collapse}}
td{{padding:3px 4px;border-bottom:1px solid #2a2a2a}}
.g{{display:flex;gap:2px;align-items:flex-end;height:80px}}
.b{{flex:1;text-align:center;font-size:9px}}.b i{{display:block;background:#e5484d;width:100%}}
pre{{white-space:pre-wrap;font-size:11px;max-height:300px;overflow:auto}}</style></head><body>
<h1>Raspberry Pi voltage</h1>
<div class="c s"><b>{html.escape(text)}</b><br>flags {html.escape(raw)} &middot; up {uptime()}
<br>checked {now:%d %b %H:%M:%S}</div>
<div class="c">Drops: last hour <b>{count(1)}</b> &middot; 24 h <b>{count(24)}</b>
&middot; {DAYS} days <b>{len(eps)}</b></div>
<h2>Drops per hour (last 24 h)</h2><div class="c g">{''.join(bars)}</div>
<h2>Per day</h2><div class="c"><table>{day_rows}</table></div>
<h2>Latest drops (newest first)</h2><div class="c"><table>
<tr><td><b>Detected</b></td><td><b>Normalised</b></td><td><b>Lasted</b></td></tr>{rows}</table></div>
<h2>Snapshots taken at drop time</h2><div class="c"><pre>{html.escape(snapshots())}</pre></div>
</body></html>"""
    data = [{"start": s.isoformat(), "end": e.isoformat() if e else None,
             "seconds": int((e - s).total_seconds()) if e else None} for s, e in eps]
    return page.encode(), json.dumps(data).encode()


def refresh():
    import time
    if time.time() - _cache["t"] > CACHE_SECONDS:
        _cache["page"], _cache["json"] = build()
        _cache["t"] = time.time()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            refresh()
            if self.path in ("/", "/index.html"):
                body, ctype = _cache["page"], "text/html; charset=utf-8"
            elif self.path == "/json":
                body, ctype = _cache["json"], "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:  # keep the service alive
            self.send_error(500, str(exc)[:100])

    def log_message(self, *args):  # no per-request logging (saves journal writes)
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
