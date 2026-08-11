"""Where does the overlay latency actually come from?

The box was measured at ~430ms behind and the operator reports it feels like a
second. Before raising a prediction cap to paper over that, find out which
stage is holding the frames - a prediction that compensates for the wrong
number is a guess that happens to look better.

The suspicion this tests: `cv2.VideoCapture` on an HTTP MJPEG source BUFFERS.
The detector processes at ~4.5 fps while camctl serves ~17 fps, so if frames
queue rather than drop, every `read()` returns something progressively older
and the detector is analysing the past. run_live's own docstring claims
"frames are dropped rather than queued when the pipeline falls behind" - that
is a claim about intent, not a measurement, and this is the measurement.

    python tools\\probe_latency.py

The tell: in a tight loop with no processing, frames should arrive at roughly
the stream rate. If they arrive much FASTER than realtime, a backlog is being
drained - which means the detector, which reads slowly, is always looking at
stale frames.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC = "http://localhost:8160/stream.mjpg"


def measure(label: str, work_s: float, n: int = 40, bufsize: int | None = None):
    """Read n frames, optionally simulating per-frame processing time."""
    cap = cv2.VideoCapture(SRC)
    if bufsize is not None:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, bufsize)
    ok, _ = cap.read()
    if not ok:
        raise SystemExit("cannot read stream - is camctl running?")

    gaps = []
    t_prev = time.time()
    for _ in range(n):
        ok, _f = cap.read()
        now = time.time()
        if ok:
            gaps.append(now - t_prev)
        t_prev = now
        if work_s:
            time.sleep(work_s)      # stand in for detection
    cap.release()

    inter = [g - work_s for g in gaps]      # time spent waiting on read()
    med = statistics.median(inter)
    return {"label": label, "median_read_wait_ms": med * 1000,
            "effective_fps": 1.0 / statistics.median(gaps)}


def main() -> None:
    print("stream:", SRC)
    print("\nA tight loop should wait roughly one frame interval per read.")
    print("A loop that also 'processes' should wait NEAR ZERO if frames are")
    print("dropped for it - and should ALSO wait near zero if they are being")
    print("QUEUED, which is the failure. The two are told apart by what")
    print("happens next: with a queue, the wait stays at zero forever because")
    print("there is always a stale frame ready.\n")

    a = measure("tight loop, no processing", 0.0)
    print(f"  {a['label']:<34} read wait {a['median_read_wait_ms']:6.1f} ms   "
          f"{a['effective_fps']:5.1f} fps")

    b = measure("simulating 220ms of detection", 0.220)
    print(f"  {b['label']:<34} read wait {b['median_read_wait_ms']:6.1f} ms   "
          f"{b['effective_fps']:5.1f} fps")

    print()
    if b["median_read_wait_ms"] < 15:
        print("  🚨 BUFFERED. With 220ms of work per frame the next frame is")
        print("     ALWAYS already waiting, which means it was captured while")
        print("     the previous one was still being processed. The detector is")
        print("     analysing the past, and the lag grows with the queue depth,")
        print("     not just with processing time.")
        print("     Fix: a capture thread that keeps only the newest frame.")
    else:
        print("  Frames appear to be dropped rather than queued; the latency is")
        print("  processing time and transport, not a backlog.")

    c = measure("with CAP_PROP_BUFFERSIZE=1", 0.220, bufsize=1)
    print(f"\n  {c['label']:<34} read wait {c['median_read_wait_ms']:6.1f} ms   "
          f"{c['effective_fps']:5.1f} fps")
    print("  (CAP_PROP_BUFFERSIZE is honoured by some backends and silently")
    print("   ignored by others - if this looks identical, it was ignored.)")


if __name__ == "__main__":
    main()
