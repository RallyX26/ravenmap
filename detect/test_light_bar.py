"""Does the light-bar detector still detect a light bar?

Tightening a heuristic against negatives alone can silence it completely, and
a signal that never fires looks identical to a signal that never false-fires on
the only data we have. the operator's confirmed negatives gave us one side of that;
these synthetic positives give us the other, so a future tightening cannot
quietly turn the detector into dead code.

Synthetic, deliberately. A real labelled positive from his street would be
better and does not exist yet - a marked unit has to actually drive past. What
this proves is narrower and still worth having: the geometry the detector
claims to look for is geometry it can still see.

    python detect\\test_light_bar.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detect import visual   # noqa: E402

W, H = 400, 240
VBOX = (0, 0, W, H)


def _car(colour=(40, 40, 45)) -> np.ndarray:
    """A plain vehicle box. BGR."""
    img = np.full((H, W, 3), colour, dtype=np.uint8)
    return img


def with_bar(img: np.ndarray, gap: int = 60, lamp=(34, 14)) -> np.ndarray:
    """Two small bright lamps side by side in the roof band."""
    band_h = int(H * visual.ROOF_BAND)
    y = band_h // 2
    cx = W // 2
    lw, lh = lamp
    # BGR: pure blue and pure red, bright and saturated, as a lit lamp is.
    cv2.rectangle(img, (cx - gap - lw, y - lh // 2), (cx - gap, y + lh // 2),
                  (255, 40, 40), -1)
    cv2.rectangle(img, (cx + gap, y - lh // 2), (cx + gap + lw, y + lh // 2),
                  (40, 40, 255), -1)
    return img


def blue_car_with_tail_light() -> np.ndarray:
    """The failure that put a blue Chevrolet Equinox on the map as police."""
    img = np.full((H, W, 3), (200, 90, 20), dtype=np.uint8)   # bright blue paint
    band_h = int(H * visual.ROOF_BAND)
    cv2.rectangle(img, (W - 90, 4), (W - 20, band_h - 4), (40, 40, 235), -1)
    return img


def sky_over_red_car() -> np.ndarray:
    """The earlier failure: red paint under a bright blue sky."""
    img = np.full((H, W, 3), (60, 60, 210), dtype=np.uint8)   # red car
    img[0:int(H * visual.ROOF_BAND) // 2, :] = (230, 150, 60)  # sky
    return img


def main() -> None:
    cases = [
        ("lit light bar, dark car",        with_bar(_car()),                True),
        ("lit light bar, white car",       with_bar(_car((235, 235, 235))), True),
        ("lit light bar, narrow gap",      with_bar(_car(), gap=28),        True),
        ("blue car + red tail light",      blue_car_with_tail_light(),      False),
        ("red car under blue sky",         sky_over_red_car(),              False),
        ("plain dark car",                 _car(),                          False),
        ("plain blue car",                 np.full((H, W, 3), (200, 90, 20), np.uint8), False),
    ]

    failures = 0
    for name, img, expect in cases:
        got = bool(visual.signals(img, VBOX).get("light_bar"))
        ok = got == expect
        failures += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name:<30} "
              f"expected {'fire' if expect else 'quiet'}, "
              f"got {'fire' if got else 'quiet'}")

    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
