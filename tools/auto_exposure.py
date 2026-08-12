"""Keep the camera exposed for the light it actually has, all day and all night.

A fixed exposure is a daylight exposure. Measured on the reference camera at
23:00 with exposure -5: **83% of the frame was near-black** and the detector
posted 1-4 vehicles an hour against 200-300 in the afternoon. The map did not
go quiet because traffic stopped; it went quiet because the camera stopped being
able to see. At -2 the same street was plainly visible (mean brightness 22 ->
70, near-black 87% -> 33%) with barely any blown highlights.

So this measures the frame and moves one step at a time toward a target, rather
than trusting either a constant or the webcam's own auto mode - which hunts
badly at night, because a pair of headlights is the brightest thing in view and
auto-exposure will expose for it and hide the car behind it.

Deliberately slow and deliberately clamped:
  * one step per pass, so it never oscillates between extremes;
  * a dead band, so it stops adjusting when the picture is good enough;
  * a floor and a ceiling, because a long exposure smears a moving vehicle and a
    smeared vehicle is worse than a dark one.

    python tools/auto_exposure.py --once      # measure, adjust, print, exit
    python tools/auto_exposure.py --interval 120
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

CAMCTL = "http://localhost:8160"

# The usable range on the reference camera (a C920-class UVC device). More
# negative is a SHORTER exposure and a darker frame - the opposite of what the
# sign suggests, and worth stating because it cost an experiment to find out.
EXP_MIN, EXP_MAX = -7, -2
# Median grey we aim for. Low enough to keep some highlight headroom for
# headlights, high enough that a dark car has a shape.
TARGET_LO, TARGET_HI = 45, 110
# Never chase highlights: if this much of the frame is blown out, step down even
# if the median still looks dark.
BLOWN_LIMIT_PCT = 4.0


def api(path: str, body: dict | None = None, timeout: float = 8.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        CAMCTL + path, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw[:1] in (b"{", b"[") else raw


def frame_stats() -> tuple[float, float] | None:
    """(median grey, percent blown) of the current frame, or None."""
    import cv2
    import numpy as np
    try:
        req = urllib.request.Request(CAMCTL + "/snapshot.jpg")
        with urllib.request.urlopen(req, timeout=10) as r:
            buf = np.frombuffer(r.read(), np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            return None
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return float(np.median(g)), float((g > 250).mean() * 100.0)
    except Exception:
        return None



# 🚨 THE CAMERA'S REPORTED EXPOSURE IS NOT ALWAYS THE ONE IT IS USING.
# Measured: after setting -2 the frame brightened exactly as expected (median
# 11 -> 56) while /api/state still read -5. Stepping from that readback would
# walk the wrong way from a value the camera is not actually on, so the last
# value WE set is remembered here and the readback is only a fallback for a
# cold start.
STATE = Path(__file__).resolve().parent.parent / "data" / "auto_exposure.json"


def _remembered() -> float | None:
    try:
        return float(json.loads(STATE.read_text(encoding="utf-8"))["exposure"])
    except Exception:
        return None


def _remember(v: float) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"exposure": v, "at": time.time()}),
                         encoding="utf-8")
    except Exception:
        pass


def current_exposure() -> float | None:
    v = _remembered()
    if v is not None:
        return v
    try:
        return float((api("/api/state") or {}).get("controls", {}).get("exposure"))
    except Exception:
        return None


def step() -> str:
    st = frame_stats()
    if st is None:
        return "no frame from camctl"
    median, blown = st
    exp = current_exposure()
    if exp is None:
        return "could not read the current exposure"

    want = exp
    if blown > BLOWN_LIMIT_PCT or median > TARGET_HI:
        want = exp - 1          # shorter exposure, darker
    elif median < TARGET_LO:
        want = exp + 1          # longer exposure, brighter
    want = max(EXP_MIN, min(EXP_MAX, want))

    where = f"median {median:.0f}, blown {blown:.1f}%, exposure {exp:.0f}"
    if want == exp:
        return f"ok ({where})"
    try:
        api("/api/set", {"name": "exposure", "value": want})
        _remember(want)
    except Exception as exc:
        return f"{where} -> wanted {want:.0f} but the camera refused ({exc})"
    return f"{where} -> {want:.0f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=120.0)
    args = ap.parse_args()

    if args.once:
        print(time.strftime("[%H:%M:%S]"), step())
        return
    print(f"holding exposure between {EXP_MIN} and {EXP_MAX}, "
          f"target median {TARGET_LO}-{TARGET_HI}; every {args.interval:.0f}s")
    while True:
        msg = step()
        if not msg.startswith("ok "):        # only speak when something changed
            print(time.strftime("[%H:%M:%S]"), msg, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
