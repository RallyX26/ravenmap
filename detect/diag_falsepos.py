"""Re-run the visual signals over stored snapshots and show WHY they fired.

Built for a specific failure: five vehicles reached the public tier off his own
street on 2026-08-08 and he confirmed that none of them was a police vehicle.
Guessing at the cause of a vision bug is how the last two colour-heuristic
regressions happened, so this prints the actual measured quantities - the red
and blue fractions, their positions, their brightness - for each frame, next to
the decision they produced.

    python detect\\diag_falsepos.py data\\snaps\\*.jpg
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detect import visual              # noqa: E402
from detect.pipeline import VehicleTracker   # noqa: E402


def explain(frame, vbox) -> dict:
    """Everything visual.signals looks at, exposed instead of reduced to a bool."""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = (max(0, int(vbox[0])), max(0, int(vbox[1])),
                      min(w, int(vbox[2])), min(h, int(vbox[3])))
    band = frame[y0:y0 + int((y1 - y0) * visual.ROOF_BAND), x0:x1]
    if band.size == 0:
        return {}
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    red = (cv2.inRange(hsv, np.array(visual.RED_LO_1), np.array(visual.RED_HI_1)) |
           cv2.inRange(hsv, np.array(visual.RED_LO_2), np.array(visual.RED_HI_2)))
    blue = cv2.inRange(hsv, np.array(visual.BLUE_LO), np.array(visual.BLUE_HI))
    n = band.shape[0] * band.shape[1]
    r_frac, b_frac = red.sum() / 255 / n, blue.sum() / 255 / n
    out = {"band_px": n, "r_frac": r_frac, "b_frac": b_frac,
           "gate_fracs": r_frac > visual.MIN_FRACTION and b_frac > visual.MIN_FRACTION}
    r_rows, r_cols = np.nonzero(red)
    b_rows, b_cols = np.nonzero(blue)
    if len(r_rows) and len(b_rows):
        v = hsv[:, :, 2]
        out.update({
            "same_height": abs(r_rows.mean() - b_rows.mean()) < band.shape[0] * 0.25,
            "dy_px": abs(r_rows.mean() - b_rows.mean()),
            "side_by_side": abs(r_cols.mean() - b_cols.mean()) > band.shape[1] * 0.08,
            "dx_px": abs(r_cols.mean() - b_cols.mean()),
            "red_v": v[red > 0].mean(), "blue_v": v[blue > 0].mean(),
            # How much of the band is blue overall. A blue CAR paints the whole
            # roof band blue; a light bar paints a small strip of it.
            "blue_share_of_band": b_frac,
        })
    return out


def main() -> None:
    pats = sys.argv[1:] or ["data/snaps/*.jpg"]
    files = sorted({f for p in pats for f in glob.glob(p)})
    if not files:
        raise SystemExit("no files matched")

    tracker = VehicleTracker()
    for f in files:
        frame = cv2.imread(f)
        if frame is None:
            continue
        dets = tracker.track(frame)
        print(f"\n=== {Path(f).name}  {frame.shape[1]}x{frame.shape[0]}  "
              f"{len(dets)} vehicles ===")
        for tid, cls_name, vbox, conf in dets:
            ev = visual.signals(frame, vbox)
            d = explain(frame, vbox)
            box = f"[{int(vbox[0])},{int(vbox[1])},{int(vbox[2])},{int(vbox[3])}]"
            print(f"  {cls_name:<8} conf={conf:.2f} {box:<26} "
                  f"light_bar={'YES' if ev.get('light_bar') else 'no'}")
            if d:
                print(f"      r_frac={d['r_frac']:.4f} b_frac={d['b_frac']:.4f}"
                      f"  (gate {visual.MIN_FRACTION})")
                if "same_height" in d:
                    print(f"      same_height={d['same_height']} (dy={d['dy_px']:.1f}px)"
                          f"  side_by_side={d['side_by_side']} (dx={d['dx_px']:.1f}px)")
                    print(f"      red_v={d['red_v']:.0f} blue_v={d['blue_v']:.0f}"
                          f"  (need >150)")


if __name__ == "__main__":
    main()
