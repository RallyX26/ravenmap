"""Remove the surroundings from snapshots already stored.

Every image written before isolate.py existed shows the reference camera's street: the
houses opposite, their porches and railings, parked cars, the angle and the
distance. `/snap/<name>` is served without authentication, so those are the
live exposure - and they are all of ONE camera, his, because he is the only
node. Turning the feature on for future sightings does nothing about the ones
already published.

Re-stripping in place, because there is no useful third option: the background
is not evidence, keeping a pristine copy alongside would recreate exactly the
file this is removing, and deleting the images would throw away the vehicle
that justified the sighting.

    python tools\\strip_existing_snaps.py                # report
    python tools\\strip_existing_snaps.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import isolate                    # noqa: E402
from core import SNAPS            # noqa: E402


def already_stripped(img: np.ndarray, tol: int = 26) -> bool:
    """Has this one been done? The backdrop is a single flat colour.

    Checked rather than tracked in a sidecar: a marker file can go missing or
    lie, the pixels cannot.
    """
    bg = np.array(isolate.BACKDROP, dtype=np.int16)
    flat = (np.abs(img.astype(np.int16) - bg).sum(axis=2) < tol)
    return bool(flat.mean() > 0.12)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    files = sorted(SNAPS.glob("*.jpg"))
    print(f"{len(files)} stored snapshots")
    todo = []
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        if already_stripped(img):
            continue
        todo.append(f)
    print(f"  {len(todo)} still showing their surroundings")

    if not args.apply:
        print("\n[--apply to rewrite them in place]")
        return

    done = partial = failed = 0
    for f in todo:
        img = cv2.imread(str(f))
        h, w = img.shape[:2]
        # Same centre-box rule as the live path: the snapshot is about the
        # vehicle it was framed on, not everything else in shot.
        centre = (w * 0.18, h * 0.18, w * 0.82, h * 0.82)
        out, method = isolate.strip(img, centre)
        if method == "none":
            failed += 1
            print(f"  ! no vehicle found in {f.name} - left alone, review it")
            continue
        cv2.imwrite(str(f), out, [cv2.IMWRITE_JPEG_QUALITY, 88])
        done += 1
        partial += (method == "box")
    print(f"\nstripped {done} ({partial} by bounding box only), "
          f"{failed} left alone")
    if failed:
        print("  ⚠️  The ones left alone still show the street. They had no")
        print("      detectable vehicle, which usually means the crop is junk -")
        print("      consider deleting those sightings' images outright.")


if __name__ == "__main__":
    main()
