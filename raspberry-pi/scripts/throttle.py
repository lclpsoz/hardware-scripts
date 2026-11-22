#!/usr/bin/env python3
"""Copy stdin to stdout at no more than KB_PER_S kilobytes a second (0 = no limit).

    some-command-on-the-pi | python3 -I throttle.py 2048 > file

Used by backup-pi.sh on the laptop. When this process sleeps, the pipe and the ssh channel fill up and
the Pi's tar, compressor and sshd block, so the Pi's CPU and network run at a low duty cycle instead
of flat out. Only the average rate is limited; the first buffer's worth (a few hundred KB to 2 MB,
the ssh window) still arrives at once.
"""
import sys
import time

rate = float(sys.argv[1]) * 1024 if len(sys.argv) > 1 else 0
src, dst = sys.stdin.buffer, sys.stdout.buffer
start = time.monotonic()
sent = 0
while True:
    chunk = src.read1(16384)
    if not chunk:
        break
    dst.write(chunk)
    dst.flush()
    if rate > 0:
        sent += len(chunk)
        ahead = sent / rate - (time.monotonic() - start)
        if ahead > 0:
            time.sleep(ahead)
