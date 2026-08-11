"""Measure real capture framerate, and prove whether MJPG fixes a slow one.

WHY THIS IS SO CAREFUL ABOUT OPEN/CLOSE
The C920 wedges if you open and release it repeatedly in quick succession -
subsequent opens then fail for minutes with "backend is generally available but
can't be used to capture by index", and only a physical replug clears it. An
earlier version of this test cycled four capture modes with 0.4 s between them
and bricked the camera every single run, which looked like a hardware fault and
was actually the test.

So: long settle between modes, a bounded retry with backoff on open, and an
explicit release plus pause on the way out.

THE THING BEING MEASURED
OpenCV defaults USB cameras to YUY2, which is uncompressed. 1920x1080 at 2
bytes per pixel is 4.15 MB per frame, and USB 2.0 carries roughly 35 MB/s in
practice, so 1080p tops out near 8 fps before overhead. MJPG has the camera
compress in hardware at roughly 10:1, so the same resolution fits 30 fps.

    python tools\fps_check.py
    python tools\fps_check.py --index 0 --frames 90
"""

from __future__ import annotations

import argparse
import time

import cv2

SETTLE_BETWEEN_MODES = 3.0
OPEN_RETRIES = 4


def open_camera(index: int, fourcc: str | None, w: int, h: int):
    """Open with retry/backoff. Returns a capture or None."""
    for attempt in range(OPEN_RETRIES):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            if fourcc:
                # BEFORE the resolution. DSHOW picks a format to suit the
                # resolution already requested and ignores a later FOURCC.
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS, 30)
            ok, _ = cap.read()
            if ok:
                return cap
        cap.release()
        wait = 2.0 * (attempt + 1)
        print(f"    open failed, retrying in {wait:.0f}s "
              f"({attempt + 1}/{OPEN_RETRIES})")
        time.sleep(wait)
    return None


def measure(cap, frames: int) -> tuple[float, int, int]:
    for _ in range(20):                       # let exposure and the pipe settle
        cap.read()
    n, bad, t0 = 0, 0, time.time()
    while n < frames and bad < 15:
        ok, f = cap.read()
        if ok and f is not None:
            n += 1
        else:
            bad += 1
    dt = time.time() - t0
    return (n / dt if dt > 0 else 0.0), int(cap.get(3)), int(cap.get(4))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()

    results = {}
    for label, fourcc in (("YUY2 (OpenCV default)", None), ("MJPG", "MJPG")):
        print(f"  {label} ...")
        cap = open_camera(a.index, fourcc, a.width, a.height)
        if cap is None:
            print(f"  {label:<24} could not open - is another app using it?")
            continue
        fps, gw, gh = measure(cap, a.frames)
        cap.release()
        results[label] = fps
        raw = gw * gh * 2 / 1e6 * fps
        print(f"  {label:<24} {gw}x{gh}  {fps:5.1f} fps"
              f"   (uncompressed that is {raw:5.1f} MB/s)")
        time.sleep(SETTLE_BETWEEN_MODES)

    if len(results) == 2:
        y, m = results["YUY2 (OpenCV default)"], results["MJPG"]
        if y > 0:
            print(f"\n  MJPG is {m / y:.1f}x faster at the same resolution.")
        if m < 15:
            print("  Still under 15 fps: check the USB port is not a shared hub,"
                  " and that the camera is on USB 3 if the host has it.")


if __name__ == "__main__":
    main()
