"""Will this camera work with SparrowMap? Ask before installing anything.

    python tools\\test_stream.py rtsp://USER:PASS@CAMERA:554/stream1
    python tools\\test_stream.py --list          # common URL shapes by brand

🚨 WHY THIS IS THE FIRST THING A BUSINESS NEEDS.
An IP camera is not like the phone or the USB webcam this project started with:
nothing about it is discoverable from the outside. The URL depends on the brand,
the model, the channel and the sub-stream, and getting any part of it wrong
fails in exactly the same way as a wrong password or a blocked port. Asking
somebody to install a node, aim it on a map and enrol it - and only THEN find
out the stream never opened - is how a volunteer decides the project is broken
and stops.

⚠️ AND A WRONG URL TAKES 30 SECONDS TO SAY SO. Measured on this machine:
cv2.VideoCapture against a dead host sat for 30.1s before returning. That is
long enough to read as a hang rather than an answer, so the timeout is set
explicitly here and reported, rather than left to the default nobody chose.

This tool NEVER sends anything anywhere. It opens the stream, reads a few
frames, prints what it found, and exits. The camera's credentials stay on the
machine that ran it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# 🚨 THE TIMEOUT HAS TO BE SET ON THE CAPTURE, NOT IN THE ENVIRONMENT.
# Measured on this build (OpenCV 5.0.0, prebuilt FFmpeg): a dead host takes
# 30.1s to fail, and NONE of the documented env options change that -
# `stimeout`, `timeout`, with or without rtsp_transport, all still 30.1s. The
# option name was renamed between FFmpeg versions and this build ignores it
# either way. `CAP_PROP_OPEN_TIMEOUT_MSEC` passed to VideoCapture.open() does
# bind: 5.0s, measured. Preferring the documented-but-ignored one would have
# left the tool feeling hung on exactly the input it exists to diagnose.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
OPEN_TIMEOUT_MS = 6000
READ_TIMEOUT_MS = 6000

import cv2                                                    # noqa: E402

# The shapes that cover most of what a small business already has on the wall.
# Deliberately not a promise: it is a starting point for somebody who does not
# know their camera's URL, and the tool tells them whether the guess worked.
COMMON = [
    ("Hikvision",       "rtsp://USER:PASS@HOST:554/Streaming/Channels/102"),
    ("Dahua / Amcrest", "rtsp://USER:PASS@HOST:554/cam/realmonitor?channel=1&subtype=1"),
    ("Reolink",         "rtsp://USER:PASS@HOST:554/h264Preview_01_sub"),
    ("Axis",            "rtsp://USER:PASS@HOST/axis-media/media.amp?resolution=640x480"),
    ("UniFi Protect",   "rtsp://HOST:7447/STREAM_ID"),
    ("Ubiquiti G3/G4",  "rtsp://HOST:554/STREAM_ID"),
    ("ONVIF (generic)", "rtsp://USER:PASS@HOST:554/onvif1"),
    ("MJPEG over HTTP", "http://HOST/video.mjpg"),
]


def redact(url: str) -> str:
    """A URL safe to print or paste into a support message.

    Camera URLs carry credentials in the host part, and the whole point of this
    tool is that people will copy its output to ask for help.
    """
    if "@" not in url:
        return url
    head, _, tail = url.partition("://")
    creds, _, rest = tail.partition("@")
    if ":" in creds:
        user = creds.split(":", 1)[0]
        return f"{head}://{user}:***@{rest}"
    return f"{head}://***@{rest}"


def probe(url: str, seconds: float = 6.0) -> int:
    print(f"opening {redact(url)}")
    print(f"  (giving it {OPEN_TIMEOUT_MS // 1000}s to answer)")
    t0 = time.time()
    cap = cv2.VideoCapture()
    cap.open(url, cv2.CAP_FFMPEG,
             [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
              cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS])
    open_s = time.time() - t0
    if not cap.isOpened():
        cap.release()
        print(f"\n  COULD NOT OPEN (gave up after {open_s:.1f}s)\n")
        print("  The usual causes, in the order they actually happen:")
        print("    * wrong path for the brand      - try --list")
        print("    * username or password wrong    - they are in the URL itself")
        print("    * main stream instead of sub    - ask for the SUB stream;")
        print("      a 4K main stream is wasted here and much harder to decode")
        print("    * the camera is on a separate VLAN from this machine")
        print("    * RTSP disabled in the camera's own settings (common default")
        print("      on Reolink and UniFi)")
        return 1

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    declared = cap.get(cv2.CAP_PROP_FPS) or 0.0
    print(f"  opened in {open_s:.1f}s: {w}x{h}, camera reports {declared:.0f} fps")

    # Measure the fps we can actually READ, which is the number that matters and
    # is regularly not the number the camera declares.
    frames, t1, first = 0, time.time(), None
    while time.time() - t1 < seconds:
        ok, frame = cap.read()
        if not ok:
            break
        if first is None:
            first = frame
        frames += 1
    dt = time.time() - t1
    cap.release()

    if not frames:
        print("\n  OPENED BUT DELIVERED NO FRAMES.")
        print("  That is usually a main stream this machine cannot decode fast")
        print("  enough, or a camera that needs rtsp_transport=udp.")
        return 1

    fps = frames / dt if dt else 0
    print(f"  read {frames} frames in {dt:.1f}s = {fps:.1f} fps")

    print("\n  VERDICT")
    if w and h and min(w, h) >= 240 and fps >= 4:
        print("  ✅ This camera can feed SparrowMap.")
    elif fps < 4:
        print("  ⚠️  It works, but slowly. Below about 4 fps a vehicle can cross")
        print("     the view between frames and never be seen. Try the SUB")
        print("     stream, or a lower resolution.")
    else:
        print("  ⚠️  It works, but the picture is small. A vehicle needs to be")
        print("     a few hundred pixels across to be classified at all.")

    print("\n  What SparrowMap would send from it: a plate-illegible crop of a")
    print("  vehicle, and nothing else. No video and no full frames leave this")
    print("  machine - that is enforced in code, not policy (snapshot.py).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="rtsp:// or http:// stream URL")
    ap.add_argument("--seconds", type=float, default=6.0,
                    help="how long to measure the frame rate for")
    ap.add_argument("--list", action="store_true",
                    help="print common URL shapes by camera brand")
    a = ap.parse_args()

    if a.list or not a.url:
        print("Common stream URLs. Replace USER, PASS and HOST:\n")
        for brand, shape in COMMON:
            print(f"  {brand:<18} {shape}")
        print("\nUse the SUB stream where there is a choice. SparrowMap needs a")
        print("vehicle to be a few hundred pixels across, not 4K - and the sub")
        print("stream costs the camera, the network and this machine far less.")
        return 0 if a.list else 1

    return probe(a.url, a.seconds)


if __name__ == "__main__":
    sys.exit(main())
