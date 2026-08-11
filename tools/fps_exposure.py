"""Is the framerate limited by EXPOSURE rather than bandwidth?

MJPG made no difference to a 5 fps 1080p stream, and 20.7 MB/s is comfortably
inside USB 2.0, so the pipe was never the constraint. The next suspect is the
one that actually spends frames: auto-exposure.

A camera in a dim room lengthens its exposure to gather light. At 1/5 s per
frame you get 5 fps, and no amount of compression changes that - the sensor is
still integrating for 200 ms. Auto-exposure will happily trade the entire frame
rate for brightness, silently.

This sweeps manual exposure from long to short and measures the resulting
framerate, which separates the two explanations completely:

  * fps rises as exposure shortens  -> exposure was the limit
  * fps stays pinned                -> something else (bandwidth, port, driver)

⚠️ FOR A PLATE-READING NODE, SHORT EXPOSURE IS WHAT YOU WANT ANYWAY. A moving
car at 1/5 s is a smear; plates need roughly 1/500 s or shorter to freeze. The
plate's retroreflective coating is what makes that possible at night, given an
IR illuminator. So the right setting is short exposure plus light, never a long
exposure - and this test measures the setting the node needs.

    python tools\fps_exposure.py
"""

from __future__ import annotations

import argparse
import time

import cv2

# DSHOW convention: 0.25 = manual, 0.75 = auto.
MANUAL, AUTO = 0.25, 0.75

# CAP_PROP_EXPOSURE on DSHOW is log2 seconds: -5 is 1/32 s, -10 is 1/1024 s.
LADDER = [-4, -5, -6, -7, -8, -9, -10]


def measure(cap, frames=40):
    for _ in range(12):
        cap.read()
    n, bad, t0 = 0, 0, time.time()
    while n < frames and bad < 12:
        ok, f = cap.read()
        if ok and f is not None:
            n += 1
        else:
            bad += 1
    dt = time.time() - t0
    ok, f = cap.read()
    bright = float(f.mean()) if ok and f is not None else -1
    return (n / dt if dt > 0 else 0.0), bright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise SystemExit("cannot open camera - is another app using it?")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.read()
    print(f"  {int(cap.get(3))}x{int(cap.get(4))}\n")

    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO)
    fps, bright = measure(cap)
    print(f"  {'auto exposure':<18} {fps:5.1f} fps   brightness {bright:5.1f}")

    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, MANUAL)
    print()
    for e in LADDER:
        cap.set(cv2.CAP_PROP_EXPOSURE, e)
        fps, bright = measure(cap)
        shutter = f"1/{2 ** -e}s"
        print(f"  exposure {e:>3} ({shutter:>8}) {fps:5.1f} fps   "
              f"brightness {bright:5.1f}")

    # leave it somewhere sane
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO)
    cap.read()
    cap.release()
    print("\n  restored auto exposure")


if __name__ == "__main__":
    main()
