#!/usr/bin/env python3
"""netprobe: the laptop half of the speed test that the Pi asks for. Docs: raspberry-pi/13-laptop-netprobe.md

Run once a minute by pi-netprobe.timer (through run.sh). Each run:
  1. sends a heartbeat to netq on the Pi (POST /api/laptop/poll): is the laptop up, how long since boot,
     how long it was suspended, which network it is on, how much it is moving, whether qBittorrent runs;
  2. if the Pi's answer says "run a test", runs a full speed test and posts the result with its context.

Away from home net.lan does not resolve, the heartbeat fails quietly, and the Pi sees a gap.

Output protocol (read by run.sh): lines "LOG<TAB>text" and "NOTIFY<TAB>title<TAB>message<TAB>prio<TAB>tags".
Standard library only. Configuration comes from environment variables set by run.sh.
"""
import http.client
import json
import os
import re
import signal
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

NETQ_URL = os.environ.get("NETQ_URL", "http://net.lan").rstrip("/")
TOKEN = os.environ.get("NETQ_TOKEN", "")
QBIT_URL = os.environ.get("QBIT_URL", "").rstrip("/")
STATE_DIR = os.environ.get("PI_OPS_STATE", os.path.expanduser("~/.local/state/pi-ops"))
STATE_FILE = os.path.join(STATE_DIR, "netprobe.hb.json")
SPEED_HOST = "speed.cloudflare.com"
UA = "netprobe/1.0 (home network quality, laptop side)"
MAX_BETWEEN_S = 150          # a heartbeat older than this means the laptop was gone (off, asleep or away)
DOWN_REQUEST = 50_000_000    # bytes asked per download request; a stream starts a new request when one ends
UP_REQUEST = 10_000_000


def out(kind, *parts):
    print(kind + "\t" + "\t".join(str(p).replace("\t", " ").replace("\n", " ") for p in parts), flush=True)


def log(msg):
    out("LOG", msg)


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


# --------------------------------------------------------------------------- what the laptop looks like

def default_route():
    """(gateway, interface) of the default route, or (None, None)."""
    try:
        txt = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    m = re.search(r"default via (\S+) dev (\S+)", txt)
    return (m.group(1), m.group(2)) if m else (None, None)


def iface_bytes(iface):
    try:
        return (int(read(f"/sys/class/net/{iface}/statistics/rx_bytes")), int(read(f"/sys/class/net/{iface}/statistics/tx_bytes")))
    except (TypeError, ValueError):
        return None


def wireless_counters(iface):
    """retry and missed-beacon counters from /proc/net/wireless (cumulative)."""
    for line in (read("/proc/net/wireless", "") or "").splitlines():
        if line.strip().startswith(iface + ":"):
            f = line.split(":", 1)[1].split()
            try:
                return {"retry": int(f[7]), "beacon": int(f[9])}
            except (IndexError, ValueError):
                return {}
    return {}


def ethernet_counters(iface):
    c = {}
    for name in ("rx_errors", "rx_dropped", "rx_crc_errors", "tx_errors", "tx_dropped", "collisions"):
        v = read(f"/sys/class/net/{iface}/statistics/{name}")
        if v is not None and v.isdigit():
            c[name] = int(v)
    return c


def link_info(iface):
    """Kind of link and its health. No SSID, MAC or address: only what explains a speed result."""
    if not iface:
        return {"type": "none"}
    if os.path.isdir(f"/sys/class/net/{iface}/wireless") or os.path.isdir(f"/sys/class/net/{iface}/phy80211"):
        info = {"type": "wifi"}
        try:
            txt = subprocess.run(["iwconfig", iface], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            txt = ""
        m = re.search(r"Frequency[:=]([\d.]+) GHz", txt)
        if m:
            ghz = float(m.group(1))
            info["band"] = "2.4" if ghz < 3 else "5" if ghz < 5.9 else "6"
        m = re.search(r"Signal level=(-?\d+) dBm", txt)
        if m:
            info["signal_dbm"] = int(m.group(1))
        m = re.search(r"Bit Rate[:=]\s*([\d.]+) Mb/s", txt)
        if m:
            info["rate_mbps"] = int(float(m.group(1)))
        return info
    info = {"type": "ethernet"}
    speed = read(f"/sys/class/net/{iface}/speed")
    if speed and speed.lstrip("-").isdigit() and int(speed) > 0:
        info["speed_mbps"] = int(speed)
    duplex = read(f"/sys/class/net/{iface}/duplex")
    if duplex:
        info["duplex"] = duplex
    return info


def qbit_running():
    for pid in os.listdir("/proc"):
        if pid.isdigit() and (read(f"/proc/{pid}/comm", "") or "").startswith("qbittorrent"):
            return True
    return False


def qbit_rates(running):
    """qBittorrent's own transfer rates in Mbps through its Web API, if that is enabled. None if not."""
    if not running:
        return None
    for base in ([QBIT_URL] if QBIT_URL else []) + ["http://127.0.0.1:8080"]:
        try:
            with urllib.request.urlopen(base + "/api/v2/transfer/info", timeout=1) as r:
                j = json.loads(r.read())
            return {"dl_mbps": round(j.get("dl_info_speed", 0) * 8 / 1e6, 1), "ul_mbps": round(j.get("up_info_speed", 0) * 8 / 1e6, 1)}
        except (OSError, ValueError):
            continue
    return None


def on_battery():
    try:
        for name in os.listdir("/sys/class/power_supply"):
            if (read(f"/sys/class/power_supply/{name}/type") == "Mains"):
                return read(f"/sys/class/power_supply/{name}/online") == "0"
    except OSError:
        pass
    return False


# --------------------------------------------------------------------------- talking to the Pi

def post(path, body, timeout=8):
    req = urllib.request.Request(NETQ_URL + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Netq-Token": TOKEN, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def heartbeat(state):
    now = time.time()
    gateway, iface = default_route()
    boot = time.clock_gettime(time.CLOCK_BOOTTIME)
    suspended = max(0.0, boot - time.clock_gettime(time.CLOCK_MONOTONIC))
    prev = state.get("hb") or {}
    gap = now - prev.get("ts", 0)
    continuous = gap <= MAX_BETWEEN_S and prev.get("boot", 1e18) <= boot and prev.get("iface") == iface
    counters = iface_bytes(iface) if iface else None
    rx = tx = None
    if continuous and counters and prev.get("bytes"):
        dt = max(1.0, now - prev["ts"])
        rx = round(max(0, counters[0] - prev["bytes"][0]) * 8 / dt / 1e6, 2)
        tx = round(max(0, counters[1] - prev["bytes"][1]) * 8 / dt / 1e6, 2)
    running = qbit_running()
    reason = None
    if not gateway:
        reason = "no network"
    elif on_battery():
        reason = "on battery"
    elif not continuous:
        reason = "just reconnected"
    body = {"v": 1, "uptime_s": round(boot), "suspended_s": round(suspended), "iface": iface, "gateway": gateway,
            "link": link_info(iface), "rx_mbps": rx, "tx_mbps": tx, "qbit_running": running,
            "ready": reason is None, "not_ready": reason}
    return body, {"ts": now, "boot": boot, "iface": iface, "bytes": counters}


# --------------------------------------------------------------------------- the speed test

def ping_start(host, count):
    return subprocess.Popen(["ping", "-n", "-c", str(count), "-i", "0.2", "-W", "1", host],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def ping_parse(text):
    times = [float(x) for x in re.findall(r"time=([\d.]+) ms", text)]
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", text)
    sent, got = (int(m.group(1)), int(m.group(2))) if m else (len(times), len(times))
    res = {"sent": sent, "loss_pct": round(100 * (sent - got) / sent, 1) if sent else None}
    if times:
        res["ms"] = round(statistics.median(times), 1)
        res["max_ms"] = round(max(times), 1)
        res["jitter_ms"] = round(statistics.mean(abs(a - b) for a, b in zip(times, times[1:])), 1) if len(times) > 1 else 0.0
    return res


def ping_many(targets, secs):
    count = max(3, int(secs / 0.2))
    procs = {name: ping_start(host, count) for name, host in targets.items() if host}
    res = {}
    for name, p in procs.items():
        try:
            res[name] = ping_parse(p.communicate(timeout=secs + 6)[0])
        except subprocess.TimeoutExpired:
            p.kill()
            res[name] = {}
    return res


def transfer_phase(kind, secs, streams, budget_bytes, iface, targets, errors):
    """Run `streams` parallel transfers for up to `secs`, sampling the byte counters every 0.1 s.
    Returns the rate over the steady part (after warm-up), the traffic of everything else on this
    interface during that part, and the latency to `targets` measured while the line was loaded."""
    stop = threading.Event()
    counts = [0] * streams
    ctx = ssl.create_default_context()
    buf = os.urandom(1 << 20)

    def down(i):
        while not stop.is_set():
            try:
                conn = http.client.HTTPSConnection(SPEED_HOST, 443, timeout=10, context=ctx)
                conn.request("GET", f"/__down?bytes={DOWN_REQUEST}", headers={"User-Agent": UA})
                resp = conn.getresponse()
                if resp.status != 200:
                    errors.append(f"download HTTP {resp.status}")
                    return
                while not stop.is_set():
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    counts[i] += len(chunk)
                conn.close()
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                errors.append("download " + type(exc).__name__)
                return

    def up(i):
        while not stop.is_set():
            try:
                conn = http.client.HTTPSConnection(SPEED_HOST, 443, timeout=10, context=ctx)
                conn.putrequest("POST", "/__up")
                conn.putheader("Content-Length", str(UP_REQUEST))
                conn.putheader("Content-Type", "application/octet-stream")
                conn.putheader("User-Agent", UA)
                conn.endheaders()
                sent = 0
                while sent < UP_REQUEST and not stop.is_set():
                    n = min(len(buf), UP_REQUEST - sent)
                    conn.sock.sendall(memoryview(buf)[:n])
                    counts[i] += n
                    sent += n
                if stop.is_set():
                    conn.close()
                    return
                resp = conn.getresponse()
                resp.read()
                if resp.status != 200:
                    errors.append(f"upload HTTP {resp.status}")
                    return
                conn.close()
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                errors.append("upload " + type(exc).__name__)
                return

    count = int(secs / 0.2)
    pings = {name: ping_start(host, count) for name, host in targets.items() if host}
    threads = [threading.Thread(target=down if kind == "down" else up, args=(i,), daemon=True) for i in range(streams)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    samples = []                                      # (seconds, payload bytes, iface rx, iface tx)
    while time.monotonic() - t0 < secs:
        total = sum(counts)
        c = iface_bytes(iface) or (0, 0)
        samples.append((time.monotonic() - t0, total, c[0], c[1]))
        if total >= budget_bytes or not any(t.is_alive() for t in threads):
            break
        time.sleep(0.1)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    loaded = {}
    for name, p in pings.items():
        try:
            loaded[name] = ping_parse(p.communicate(timeout=secs + 6)[0])
        except subprocess.TimeoutExpired:
            p.kill()
            loaded[name] = {}
    warm = min(2.0, secs * 0.3)
    steady = [s for s in samples if s[0] >= warm]
    if len(steady) < 5:
        steady = samples
    res = {"loaded": loaded, "mb": round(sum(counts) / 1e6, 1), "streams": streams}
    if len(steady) >= 2 and steady[-1][0] - steady[0][0] >= 1.0:
        dt = steady[-1][0] - steady[0][0]
        payload = (steady[-1][1] - steady[0][1]) * 8 / dt / 1e6
        rx = (steady[-1][2] - steady[0][2]) * 8 / dt / 1e6
        tx = (steady[-1][3] - steady[0][3]) * 8 / dt / 1e6
        res["mbps"] = round(payload, 1)
        # Everything else on the interface: its total minus the test's own bytes (payload plus about 6%
        # for TLS, TCP and IP headers and retransmits; about 3% of the rate comes back the other way as ACKs).
        if kind == "down":
            res["bg_down_mbps"], res["bg_up_mbps"] = round(max(0, rx - payload * 1.06), 1), round(max(0, tx - payload * 0.03), 1)
        else:
            res["bg_up_mbps"], res["bg_down_mbps"] = round(max(0, tx - payload * 1.06), 1), round(max(0, rx - payload * 0.03), 1)
    return res


def speed_test(run, link_before):
    started = time.time()
    gateway, iface = default_route()
    targets = {k: v for k, v in (run.get("ping") or {}).items() if k in ("router", "isp", "cloudflare")}
    targets["router"] = gateway or targets.get("router")
    errors, notes = [], []
    qbit = {"running": qbit_running()}
    api0 = qbit_rates(qbit["running"])

    # 1) idle: latency to the router, the ISP gateway and Cloudflare, and the traffic already there
    b0 = iface_bytes(iface) or (0, 0)
    wl0, eth0 = wireless_counters(iface), ethernet_counters(iface)
    t0 = time.monotonic()
    idle = ping_many(targets, 3)
    dt = max(0.5, time.monotonic() - t0)
    b1 = iface_bytes(iface) or (0, 0)
    pre_down, pre_up = round((b1[0] - b0[0]) * 8 / dt / 1e6, 1), round((b1[1] - b0[1]) * 8 / dt / 1e6, 1)

    budget = int(run.get("max_mb", 300)) * 1_000_000
    streams = int(run.get("streams", 4))
    down = transfer_phase("down", float(run.get("down_secs", 8)), streams, budget, iface, targets, errors)
    api1 = qbit_rates(qbit["running"])
    up = transfer_phase("up", float(run.get("up_secs", 6)), streams, budget, iface, targets, errors)
    api2 = qbit_rates(qbit["running"])

    link = link_info(iface)
    wl1, eth1 = wireless_counters(iface), ethernet_counters(iface)
    if wl0 and wl1:
        link["wifi_retries"] = max(0, wl1.get("retry", 0) - wl0.get("retry", 0))
        link["missed_beacons"] = max(0, wl1.get("beacon", 0) - wl0.get("beacon", 0))
    if eth0 and eth1:
        bad = sum(max(0, eth1[k] - eth0.get(k, 0)) for k in eth1)
        link["errors"] = bad

    # qBittorrent: exact rates when the Web API is on, otherwise only "running"
    apis = [a for a in (api0, api1, api2) if a]
    if apis:
        qbit["api"] = True
        qbit["dl_mbps"] = max(a["dl_mbps"] for a in apis)
        qbit["ul_mbps"] = max(a["ul_mbps"] for a in apis)
    else:
        qbit["api"] = False

    # Was something else using the line? Compare the other traffic with what the test measured.
    bg = {"pre_down_mbps": pre_down, "pre_up_mbps": pre_up,
          "down_phase_up_mbps": down.get("bg_up_mbps"), "down_phase_down_mbps": down.get("bg_down_mbps"),
          "up_phase_down_mbps": up.get("bg_down_mbps"), "up_phase_up_mbps": up.get("bg_up_mbps")}
    contended = False
    for phase, rate, other_dir in (("down", down.get("mbps"), down.get("bg_down_mbps")), ("up", up.get("mbps"), up.get("bg_up_mbps"))):
        # the same-direction traffic is what steals capacity from the test; the margin covers the
        # overhead estimate above, which is rough
        if rate and other_dir is not None and other_dir > max(8.0, 0.12 * rate):
            contended = True
            notes.append(f"other traffic during {phase}load: {other_dir:g} Mbps")
    # background traffic before the test counts only if it is big next to what the line then delivered
    if pre_down > max(5.0, 0.10 * (down.get("mbps") or 0)) or pre_up > max(5.0, 0.10 * (up.get("mbps") or 0)):
        contended = True
        notes.append(f"other traffic before the test: ↓{pre_down:g} ↑{pre_up:g} Mbps")
    if qbit.get("api") and (qbit["dl_mbps"] > 5 or qbit["ul_mbps"] > 5):
        contended = True
        notes.append(f"qBittorrent ↓{qbit['dl_mbps']:g} ↑{qbit['ul_mbps']:g} Mbps")
    elif qbit["running"] and contended:
        notes.append("qBittorrent is running (enable its Web UI for exact rates)")
    if link.get("type") == "wifi" and link.get("signal_dbm") is not None and link["signal_dbm"] < -70:
        notes.append(f"weak Wi-Fi signal ({link['signal_dbm']} dBm)")

    def ms(d, key="ms"):
        return (d or {}).get(key)

    loaded_loss = [x for x in (ms(down["loaded"].get("cloudflare"), "loss_pct"),
                               ms(up["loaded"].get("cloudflare"), "loss_pct")) if x is not None]

    result = {
        "trigger": run.get("trigger"), "started": int(started), "secs": round(time.time() - started, 1),
        "down_mbps": down.get("mbps"), "up_mbps": up.get("mbps"),
        "down_mb": down.get("mb"), "up_mb": up.get("mb"), "streams": streams,
        "idle_ms": ms(idle.get("cloudflare")), "loaded_ms": ms(down["loaded"].get("cloudflare")),
        "loaded_up_ms": ms(up["loaded"].get("cloudflare")),
        "router_idle_ms": ms(idle.get("router")), "router_loaded_ms": ms(down["loaded"].get("router")),
        "router_loaded_up_ms": ms(up["loaded"].get("router")),
        "isp_idle_ms": ms(idle.get("isp")), "isp_loaded_ms": ms(down["loaded"].get("isp")),
        "jitter_ms": ms(idle.get("cloudflare"), "jitter_ms"), "jitter_loaded_ms": ms(down["loaded"].get("cloudflare"), "jitter_ms"),
        "loss_idle_pct": ms(idle.get("cloudflare"), "loss_pct"),
        "loss_loaded_pct": max(loaded_loss) if loaded_loss else None,
        "idle": idle, "link": link, "iface": iface, "bg": bg, "qbit": qbit, "contended": contended, "notes": notes,
        "colo": trace_colo(),
    }
    if errors:
        result["errors"] = sorted(set(errors))[:4]
        if "down_mbps" not in result or result["down_mbps"] is None:
            result["down_error"] = result["errors"][0]
    if result["up_mbps"] is None:
        result["up_error"] = next((e for e in errors if e.startswith("upload")), "no data")
    return result


def trace_colo():
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://{SPEED_HOST}/cdn-cgi/trace", headers={"User-Agent": UA}), timeout=4) as r:
            m = re.search(r"^colo=(\w+)", r.read().decode(), re.M)
            return m.group(1) if m else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- main

def describe(result):
    link = result.get("link") or {}
    if link.get("type") == "wifi":
        where = f"Wi-Fi {link.get('band', '?')} GHz {link.get('signal_dbm', '?')} dBm"
    elif link.get("type") == "ethernet":
        where = f"Ethernet {link.get('speed_mbps', '?')} Mbps"
    else:
        where = "no link info"
    return where


def light_probe(spec):
    """3 s of pings to the router, the ISP gateway and Cloudflare: no downloads, so no data cost."""
    gateway, iface = default_route()
    targets = {k: v for k, v in (spec.get("ping") or {}).items() if k in ("router", "isp", "cloudflare")}
    targets["router"] = gateway or targets.get("router")
    return {"ping": ping_many(targets, float(spec.get("secs", 3))), "link": link_info(iface)}


def one_round(state):
    """One heartbeat and whatever the Pi asks for. Returns True if a full test ran."""
    body, hb_state = heartbeat(state)
    try:
        answer = post("/api/laptop/poll", body)
    except urllib.error.HTTPError as exc:
        if state.get("status") != f"http{exc.code}":
            log(f"netq refused the heartbeat: HTTP {exc.code} (wrong NETQ_TOKEN, or the token is not set on the Pi?)")
        state["status"] = f"http{exc.code}"
        save_state(state)
        return False
    except (OSError, ValueError) as exc:
        # Normal away from home: net.lan does not resolve. Log only the change, not every minute.
        if state.get("status") != "unreachable":
            log(f"netq not reachable ({type(exc).__name__}); probably away from home. Heartbeats resume on their own.")
        state["status"] = "unreachable"
        save_state(state)
        return False
    if state.get("status") not in (None, "ok"):
        log("heartbeat to netq works again")
    state["status"] = "ok"
    state["hb"] = hb_state
    save_state(state)

    light = answer.get("light")
    if light:
        try:
            post("/api/laptop/light", light_probe(light), timeout=10)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    run = answer.get("run")
    if not run:
        return False
    log(f"speed test requested ({run.get('trigger')}, id {run.get('id')}); {describe(body)}")
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("test took too long")))
    signal.alarm(150)
    try:
        result = speed_test(run, body.get("link"))
    except Exception as exc:            # report the failure to the Pi instead of leaving the request hanging
        result = {"trigger": run.get("trigger"), "started": int(time.time()), "down_error": type(exc).__name__}
    finally:
        signal.alarm(0)
    try:
        reply = post("/api/laptop/result", {"id": run.get("id"), "result": result}, timeout=10)
    except (OSError, ValueError) as exc:
        log(f"could not send the result to netq ({type(exc).__name__}): {result.get('down_mbps')} / {result.get('up_mbps')} Mbps")
        return True
    log(f"result {result.get('down_mbps')} / {result.get('up_mbps')} Mbps, idle {result.get('idle_ms')} ms, "
        f"loaded {result.get('loaded_ms')} ms, {describe(result)}{', contended: ' + '; '.join(result['notes']) if result.get('contended') else ''}")

    base, down = reply.get("baseline_down_mbps"), result.get("down_mbps")
    if base and down is not None and not result.get("contended") and down < 0.5 * base:
        if time.time() - state.get("last_low_notice", 0) > 3 * 3600:
            state["last_low_notice"] = time.time()
            save_state(state)
            out("NOTIFY", "Laptop speed test is low",
                f"{down:g} Mbps down against {base:g} usual ({describe(result)}), nothing else was using the line. "
                f"Up {result.get('up_mbps')} Mbps, loaded latency {result.get('loaded_ms')} ms. Details: http://net.lan", 3, "chart_with_downwards_trend")
    return True


def main():
    if not TOKEN:
        log("NETQ_TOKEN is not set")
        return 1
    state = load_state()
    started = time.time()
    # A series with no gap ("N tests in a row") is handed out one test per heartbeat; keep going, with a
    # heartbeat between tests so the Pi does not see the laptop as gone. Never more than 15 minutes.
    for _ in range(40):
        if not one_round(state) or time.time() - started > 900:
            break
        state = load_state()
    return 0


if __name__ == "__main__":
    sys.exit(main())
