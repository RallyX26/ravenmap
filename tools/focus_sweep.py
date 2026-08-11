"""Find the sharpest FIXED focus for a node, then lock it there.

A camera behind glass must not use autofocus. It hunts: dust, smears and
reflections on the pane are centimetres from the lens while the subject is tens
of metres away, so the AF motor oscillates between two valid-looking targets
and the picture is soft or moving most of the time. Worse, every re-hunt is a
second of blur, which lands on exactly the vehicles you wanted.

The right answer for a static installation is to sweep focus ONCE, measure, and
lock. That is what this does. It drives camctl's HTTP API rather than opening
the camera itself, so it never fights the control app for the device.

⚠️ PRECONDITIONS, because I have twice drawn confident conclusions from
sharpness numbers that were measuring something else:

  * DAYLIGHT. Any sharpness metric is a noise metric at high gain, and the
    comparison will favour whichever setting is noisiest.
  * A STATIC subject. Moving cars change the frame between samples and swamp
    the focus difference. The tool measures a band you nominate - point it at
    buildings, not at the road.

    python tools\focus_sweep.py
    python tools\focus_sweep.py --band 0.15 0.55   (top/bottom of frame to use)
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

import cv2
import numpy as np

BASE = "http://127.0.0.1:8160"
MIN_MEAN = 45.0


def api(path, body=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def snap(settle=1.1):
    time.sleep(settle)
    with urllib.request.urlopen(BASE + "/snapshot.jpg", timeout=15) as r:
        return cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)


def sharpness(img, band):
    """High-frequency energy in a horizontal band of the frame.

    Tenengrad (Sobel magnitude) rather than Laplacian variance: it responds to
    edges rather than to isolated hot pixels, so it is less easily fooled by
    sensor noise.
    """
    h = img.shape[0]
    y0, y1 = int(h * band[0]), int(h * band[1])
    g = cv2.cvtColor(img[y0:y1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--band", type=float, nargs=2, default=[0.10, 0.55],
                    help="fractional top/bottom of frame to measure (avoid the road)")
    ap.add_argument("--step", type=int, default=15)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    st = api("/api/state")
    print(f"  {st['width']}x{st['height']}  {st['fps']} fps  backend={st['backend']}")

    base = snap(0.4)
    mean = float(cv2.cvtColor(base, cv2.COLOR_BGR2GRAY).mean())
    print(f"  scene brightness {mean:.0f}/255")
    if mean < MIN_MEAN and not args.force:
        raise SystemExit(
            f"\n  TOO DARK (need > {MIN_MEAN:.0f}). Sharpness numbers at high gain\n"
            f"  measure noise, not focus. Run in daylight or pass --force.")

    print("  turning autofocus OFF (it hunts on the glass)")
    api("/api/set", {"name": "autofocus", "value": 0})

    print(f"\n  sweeping focus, measuring band {args.band[0]:.2f}-{args.band[1]:.2f}")
    print(f"  {'focus':>6}  {'sharpness':>11}")
    print("  " + "-" * 21)

    results = []
    for f in range(0, 256, args.step):
        api("/api/set", {"name": "focus", "value": f})
        img = snap()
        s = sharpness(img, args.band)
        results.append((f, s))
        print(f"  {f:>6}  {s:>11.1f}")

    best, best_s = max(results, key=lambda t: t[1])
    worst_s = min(s for _, s in results)
    print(f"\n  best focus = {best}  (sharpness {best_s:.1f}, "
          f"{best_s / max(worst_s, 1e-6):.1f}x the worst setting)")

    # refine around the winner
    lo, hi = max(0, best - args.step), min(255, best + args.step)
    fine = []
    for f in range(lo, hi + 1, max(1, args.step // 5)):
        api("/api/set", {"name": "focus", "value": f})
        fine.append((f, sharpness(snap(0.8), args.band)))
    best, best_s = max(fine, key=lambda t: t[1])
    print(f"  refined     = {best}  (sharpness {best_s:.1f})")

    api("/api/set", {"name": "focus", "value": best})
    print(f"\n  locked at focus={best}, autofocus OFF")
    print("  Save this as a preset in the app so it survives a restart.")


if __name__ == "__main__":
    main()
