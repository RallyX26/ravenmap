"""Push already-labelled scraped crops through the camera-domain degrader.

🚨 THIS FILE EXISTS BECAUSE I SKIPPED THE STEP AND IT COST 10 POINTS OF AP.

`ingest_scraped.py` wrote press photographs straight into the label bank at
their native sharpness - 994px, studio-lit, tack sharp - beside crops from a
window webcam. the operator labelled them, the head retrained, and the measured
result was:

    with raw scraped crops     AP 0.695   recall 47%
    without them               AP 0.791   recall 58%

The extra data made it **worse**, which is exactly the failure `prepare.py`'s
docstring predicts and refuses to allow: source style becomes the separable
cue. 67 of 101 positives were pristine photographs while nearly every negative
was a webcam frame, so "sharp" and "government" pointed the same way in
training and pointed nowhere on the street.

`prepare.py` already had the fix. It just never ran on this path, because the
bank was not one of the folders it was written to walk.

Each crop becomes N degraded variants and the original is removed, so nothing
in the bank is ever a photograph again. The human's label is carried over
untouched - it was a judgement about the vehicle, and degrading pixels does not
change what the vehicle is.

    python train\\degrade_scraped.py --variants 3
    python train\\degrade_scraped.py --restore     # undo, back to originals
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA        # noqa: E402
from train.prepare import degrade   # noqa: E402

BANK = DATA / "training" / "scraped"
KEEP = DATA / "scrape_originals"      # so this is reversible


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", type=int, default=3,
                    help="degraded copies per labelled crop")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--restore", action="store_true",
                    help="put the untouched originals back")
    args = ap.parse_args()

    if args.restore:
        if not KEEP.is_dir():
            raise SystemExit("no originals kept")
        for f in BANK.iterdir():
            f.unlink()
        for f in KEEP.iterdir():
            shutil.copy2(f, BANK / f.name)
        print(f"restored {len(list(KEEP.iterdir())) // 2} crops")
        return

    KEEP.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    np.random.seed(args.seed)

    originals = sorted(BANK.glob("*.jpg"))
    made = skipped = 0
    for f in originals:
        side = f.with_suffix(".json")
        if not side.exists():
            continue
        d = json.loads(side.read_text(encoding="utf-8"))
        if d.get("degraded"):
            continue
        if not d.get("label"):
            skipped += 1          # unlabelled: leave it looking like itself
            continue               # so the human sees a clean picture to judge

        img = cv2.imread(str(f))
        if img is None:
            continue

        # Keep an untouched copy exactly once, so --restore is real.
        if not (KEEP / f.name).exists():
            shutil.copy2(f, KEEP / f.name)
            shutil.copy2(side, KEEP / side.name)

        for v in range(args.variants):
            stem = f"{f.stem}_d{v}"
            cv2.imwrite(str(BANK / f"{stem}.jpg"), degrade(img.copy(), rng),
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            (BANK / f"{stem}.json").write_text(
                json.dumps({**d, "degraded": True, "from": f.stem},
                           indent=1), encoding="utf-8")
            made += 1

        f.unlink()
        side.unlink()

    print(f"{made} degraded variants from {len(originals) - skipped} crops")
    print(f"{skipped} left alone (unlabelled - a human still has to see them)")
    print(f"originals kept in {KEEP}")


if __name__ == "__main__":
    main()
