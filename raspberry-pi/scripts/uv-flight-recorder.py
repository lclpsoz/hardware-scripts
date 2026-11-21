#!/usr/bin/env python3
"""uv-flight-recorder: record what the Pi was doing in the 60 s BEFORE each
undervoltage event.

PROPOSAL ONLY - not installed. See ../01-undervoltage.md, "Better observability".

Why: the existing uv-snapshot service runs `top` after the kernel logs the
event, so it sees the moment after the drop. Short bursts that caused the drop
(a docker exec, a gzip, a Python plugin start) are often over by then.

How: once a second it reads a handful of small kernel files (no subprocesses):
  - /sys/class/hwmon/hwmon*/in0_lcrit_alarm   undervoltage flag (rpi_volt hwmon)
  - /proc/stat                                 total and per-core CPU use
  - /sys/fs/cgroup/system.slice/*/cpu.stat     CPU time per service/container
  - cpufreq and thermal_zone0                  clock and temperature
It keeps the last 90 samples in memory. When the flag goes 0 -> 1 it waits 10 s
more, then appends one JSON line per event to /var/log/uv-flight/events.jsonl
and prints a one-line summary to the journal.

Cost (estimate, verify after install): about 1% of one core at 600 MHz and
10-15 MB of RAM. Nothing is written to the SD card between events.
"""
import glob
import json
import os
import time
from collections import deque
from datetime import datetime

INTERVAL = 1.0
KEEP = 90           # samples kept in RAM
BEFORE = 60         # seconds of history saved per event
AFTER = 10          # seconds recorded after the event starts
OUT_DIR = "/var/log/uv-flight"
OUT = os.path.join(OUT_DIR, "events.jsonl")


def find_alarm():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(os.path.join(d, "name")).read().strip() == "rpi_volt":
                return os.path.join(d, "in0_lcrit_alarm")
        except OSError:
            pass
    raise SystemExit("rpi_volt hwmon not found")


def read_int(path, default=0):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return default


def cpu_times():
    """Return {cpu_name: (busy, total)} from /proc/stat."""
    out = {}
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                break
            parts = line.split()
            vals = list(map(int, parts[1:9]))
            idle = vals[3] + vals[4]  # idle + iowait
            out[parts[0]] = (sum(vals) - idle, sum(vals))
    return out


def cgroup_usage():
    """Return {cgroup_name: usage_usec} for services and containers."""
    out = {}
    for path in glob.glob("/sys/fs/cgroup/system.slice/*/cpu.stat"):
        name = path.split("/")[-2]
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("usage_usec"):
                        out[name] = int(line.split()[1])
                        break
        except OSError:
            pass
    return out


_names = {}


def pretty(cg):
    """docker-<id>.scope -> container name (needs read access to /var/lib/docker)."""
    if not cg.startswith("docker-"):
        return cg
    if cg not in _names:
        cid = cg[len("docker-"):].split(".")[0]
        try:
            with open(f"/var/lib/docker/containers/{cid}/config.v2.json") as f:
                _names[cg] = "container:" + json.load(f)["Name"].lstrip("/")
        except (OSError, ValueError, KeyError):
            _names[cg] = "container:" + cid[:12]
    return _names[cg]


def sample(prev):
    now_cpu, now_cg = cpu_times(), cgroup_usage()
    s = {"t": time.time(),
         "uv": read_int(ALARM),
         "mhz": read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") // 1000,
         "temp": read_int("/sys/class/thermal/thermal_zone0/temp") / 1000}
    if prev:
        p_cpu, p_cg = prev
        busy = {}
        for k, (b, t) in now_cpu.items():
            pb, pt = p_cpu.get(k, (b, t))
            busy[k] = round(100 * (b - pb) / (t - pt), 1) if t > pt else 0.0
        s["cpu"] = busy.get("cpu", 0.0)
        s["cores"] = [busy[k] for k in sorted(busy) if k != "cpu"]
        s["cg_ms"] = {k: (v - p_cg[k]) // 1000 for k, v in now_cg.items()
                      if k in p_cg and v - p_cg[k] >= 20000}  # >= 20 ms in this second
    return s, (now_cpu, now_cg)


def summarize(samples, event_t):
    before = [s for s in samples if event_t - BEFORE <= s["t"] < event_t and "cpu" in s]
    last10 = [s for s in before if s["t"] >= event_t - 10]
    totals = {}
    for s in last10:
        for k, v in s.get("cg_ms", {}).items():
            totals[k] = totals.get(k, 0) + v
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:4]
    peak = max((s["cpu"] for s in last10), default=0.0)
    peak_core = max((max(s["cores"]) for s in last10 if s.get("cores")), default=0.0)
    # Classify by the busiest single core: on this supply one busy core at
    # 600 MHz was enough to trigger events (see 01-undervoltage.md).
    kind = "idle" if peak_core < 30 else ("light" if peak_core < 70 else "load")
    return {
        "event": datetime.fromtimestamp(event_t).astimezone().isoformat(timespec="seconds"),
        "class": kind,
        "prev10s_cpu_avg": round(sum(s["cpu"] for s in last10) / max(len(last10), 1), 1),
        "prev10s_cpu_peak": peak,
        "prev10s_busiest_core_peak": peak_core,
        "prev10s_top_cpu_ms": [[pretty(k), v] for k, v in top],
        "mhz": before[-1]["mhz"] if before else None,
        "temp_c": before[-1]["temp"] if before else None,
        "samples": [{k: v for k, v in s.items() if k != "cg_ms"} |
                    {"top": [[pretty(k), v] for k, v in
                             sorted(s.get("cg_ms", {}).items(), key=lambda kv: -kv[1])[:3]]}
                    for s in samples if s["t"] >= event_t - BEFORE],
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ring, state, last_uv, pending = deque(maxlen=KEEP), None, 0, None
    while True:
        start = time.monotonic()
        s, state = sample(state)
        ring.append(s)
        if s["uv"] and not last_uv and pending is None:
            pending = s["t"]                      # rising edge: wait AFTER seconds
        last_uv = s["uv"]
        if pending is not None and s["t"] - pending >= AFTER:
            rec = summarize(list(ring), pending)
            with open(OUT, "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"UV event {rec['event']} class={rec['class']} "
                  f"cpu_avg={rec['prev10s_cpu_avg']}% peak_core={rec['prev10s_busiest_core_peak']}% "
                  f"top={rec['prev10s_top_cpu_ms']}", flush=True)
            pending = None
        time.sleep(max(0.0, INTERVAL - (time.monotonic() - start)))


ALARM = find_alarm()

if __name__ == "__main__":
    main()
