"""Does this webcam's zoom add real detail, or just pixels? Run it in DAYLIGHT.

A UVC zoom control that accepts a value proves nothing. There are two kinds:

  REAL   the sensor is larger than the output, so zooming crops it closer to
         1:1 and genuinely resolves more. Worth using.
  FAKE   the camera crops its own output and upscales. Adds pixels, no
         information, and is strictly WORSE than cropping in software later,
         because it throws away the surrounding view for nothing.

HOW THIS TEST AVOIDS THE TWO TRAPS I FELL INTO

1. It ALIGNS the frames instead of assuming the zoom crops the centre. It does
   not. Assuming it did meant comparing two different parts of the scene and
   getting a confident, meaningless answer.

2. It runs in daylight and refuses to run when the frame is too dark. At night
   with gain pinned at maximum, sensor noise raises every sharpness metric, and
   the upscaled comparison arm has its noise SMOOTHED by interpolation - so the
   test is biased toward saying the zoom is real no matter what it is.

    python tools\zoom_check.py            (defaults to camera 0, zoom 200)
"""

from __future__ import annotations

import argparse
import sys

import cv2
import numpy as np

MIN_MEAN = 45.0      # below this the scene is too dark to trust the result


def grab(index: int, zoom: int, w: int, h: int, settle: int = 15):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {index}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_ZOOM, float(zoom))
    for _ in range(settle):
        cap.read()
    ok, f = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("no frame")
    return f


def detail(img: np.ndarray) -> float:
    """High-frequency energy after denoising. Real structure survives; noise
    does not, which is the whole point."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.fastNlMeansDenoising(g, None, h=10)
    return float(cv2.Laplacian(g.astype(np.float32), cv2.CV_32F).var())


def align(wide: np.ndarray, tele: np.ndarray):
    """Find where the zoomed frame sits inside the wide one, by feature match.

    Returns (wide_region, scale) or (None, None). The scale it recovers is the
    REAL zoom factor, which is worth knowing on its own - the control's number
    is often not the magnification.
    """
    orb = cv2.ORB_create(4000)
    kw, dw = orb.detectAndCompute(cv2.cvtColor(wide, cv2.COLOR_BGR2GRAY), None)
    kt, dt = orb.detectAndCompute(cv2.cvtColor(tele, cv2.COLOR_BGR2GRAY), None)
    if dw is None or dt is None or len(kw) < 20 or len(kt) < 20:
        return None, None

    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(dt, dw)
    matches = sorted(matches, key=lambda m: m.distance)[:200]
    if len(matches) < 15:
        return None, None

    src = np.float32([kt[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kw[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    M, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                             ransacReprojThreshold=4.0)
    if M is None:
        return None, None
    scale = float(np.sqrt(M[0, 0] ** 2 + M[0, 1] ** 2))
    if not (0.05 < scale < 1.0):
        return None, None

    th, tw = tele.shape[:2]
    corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
    proj = cv2.transform(corners, M).reshape(-1, 2)
    x0, y0 = proj.min(axis=0)
    x1, y1 = proj.max(axis=0)
    H, W = wide.shape[:2]
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None, None
    return wide[y0:y1, x0:x1], 1.0 / scale


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--zoom", type=int, default=200)
    ap.add_argument("--width", type=int, default=2560)
    ap.add_argument("--height", type=int, default=1472)
    ap.add_argument("--force", action="store_true", help="run even if dark")
    args = ap.parse_args()

    wide = grab(args.index, 100, args.width, args.height)
    tele = grab(args.index, args.zoom, args.width, args.height)

    mean = float(cv2.cvtColor(tele, cv2.COLOR_BGR2GRAY).mean())
    print(f"scene brightness {mean:.0f}/255")
    if mean < MIN_MEAN and not args.force:
        print(f"\nTOO DARK to trust this test (need > {MIN_MEAN:.0f}). At high gain,")
        print("noise inflates every sharpness metric and the answer will be")
        print("wrong in a confident-looking way. Re-run in daylight, or pass")
        print("--force if you accept a meaningless number.")
        sys.exit(2)

    region, factor = align(wide, tele)
    if region is None:
        print("could not align the two frames - point the camera at something")
        print("with texture and detail, not a blank wall or a night sky.")
        sys.exit(3)

    print(f"measured magnification: {factor:.2f}x  (control said {args.zoom/100:.2f}x)")

    # Arm A: the same region from the WIDE frame, upscaled to the tele size.
    a = cv2.resize(region, (tele.shape[1], tele.shape[0]),
                   interpolation=cv2.INTER_CUBIC)
    da, db = detail(a), detail(tele)
    print(f"\nA  wide crop upscaled : {da:8.2f}")
    print(f"B  camera zoom        : {db:8.2f}")
    ratio = db / da if da else 0.0
    print(f"B/A = {ratio:.2f}")

    if ratio > 1.6:
        print("\nREAL zoom. The sensor is bigger than the output; use it.")
        print(f"Effective range improves by about {factor:.1f}x.")
    elif ratio < 1.25:
        print("\nFAKE zoom - crop and upscale. It adds pixels, not information.")
        print("Leave zoom off and crop in software instead, which at least")
        print("keeps the rest of the view. Range is unchanged; you need optics.")
    else:
        print("\nAmbiguous. Marginal at best; do not plan around it.")


if __name__ == "__main__":
    main()
