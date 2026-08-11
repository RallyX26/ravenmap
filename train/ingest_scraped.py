"""Turn scraped agency photographs into crops a human can label.

Between `scrape_agencies.py` and any training there is exactly one honest step:
somebody looks at each vehicle and says what it is. This script prepares that
work and does not shortcut it.

# What it does

For every scraped photo, run the same detector the camera runs, crop each
vehicle it finds, and file the crop in the label bank UNLABELLED with its
provenance attached. Then `:8160/label` shows them like any other crop.

# Three rules encoded here, each of which was expensive to learn

1. **NO AUTOMATIC LABELS.** The search query is a weak label for the photo, not
   for a vehicle inside it. Letting CLIP pick which crop is the patrol car and
   training on that answer produces a head that has learned to imitate CLIP -
   and CLIP is the component being replaced *because its precision here is
   zero*. A wrong label is worse than no label; it is indistinguishable from
   signal at training time and only shows up as a false claim on the map.

2. **THE BACKGROUND IS THE POINT.** Every ordinary car parked beside a cruiser
   is a negative from the same photographer, lens, compression, weather and
   year as the positive. `prepare.py` refuses to run without negatives for good
   reason: public positives against his-camera negatives teaches "stock photo
   vs webcam frame", validates beautifully and fails on the street. These
   background cars are the best negatives available anywhere, and they arrive
   free with the positives.

3. **SCRAPED CROPS TRAIN. THEY NEVER VALIDATE.** They are filed under the
   `scraped` day so `fit_local.py` can keep them out of every test fold. The
   standing rule is that public data trains and only local labelled crops
   measure - a number produced by testing on scraped photos is a number about
   photography.

# What it deliberately drops

Vintage. Commons' "Michigan State Police automobiles" is 77 files and a good
share are museum pieces - 1975 Plymouth Fury, 1991 Camaro, 1995 Caprice. A
model that learns those shapes learns cars that no longer drive past anybody's
window. Where a year is recoverable from the filename, pre-2010 is skipped; the
rest is caught by eye at labelling time, where the agency and source are shown.

    python train\\ingest_scraped.py                 # everything not yet done
    python train\\ingest_scraped.py --agency "ann arbor"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402

SCRAPE = DATA / "scrape"
BANK = DATA / "training" / "scraped"

# The camera's own crops run 130-900px on the long edge and the classifier
# needs roughly 120 to work at all. A vehicle smaller than this in a press
# photo is background scenery, not a labelable subject.
MIN_EDGE = 90

# Cars older than this are museum and parade pieces. A 1975 Fury shares almost
# nothing with a 2024 Explorer beyond having four wheels.
OLDEST_YEAR = 2010


def year_in(name: str) -> int | None:
    """A model year out of a filename, when the uploader put one there."""
    m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", name)
    return int(m.group(1)) if m else None


def crops_from(img, boxes, pad: float = 0.06):
    """Vehicle crops with a little context, in descending size order.

    Padded slightly because a tight box cuts the light bar off the roof, which
    is the single most useful pixel in the whole image.
    """
    h, w = img.shape[:2]
    out = []
    for (x0, y0, x1, y1), cls, conf in boxes:
        bw, bh = x1 - x0, y1 - y0
        px, py = int(bw * pad), int(bh * pad)
        a, b = max(0, int(x0 - px)), max(0, int(y0 - py))
        c, d = min(w, int(x1 + px)), min(h, int(y1 + py))
        if max(c - a, d - b) < MIN_EDGE:
            continue
        out.append((img[b:d, a:c], cls, float(conf), max(c - a, d - b)))
    out.sort(key=lambda t: -t[3])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agency", help="substring match on the folder name")
    ap.add_argument("--max-per-photo", type=int, default=6,
                    help="crops kept per photo, largest first")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SCRAPE.is_dir():
        raise SystemExit(f"nothing scraped yet: {SCRAPE}")

    from detect import cuda_setup
    cuda_setup.enable_cuda()
    from detect.pipeline import VehicleTracker
    det = VehicleTracker()

    BANK.mkdir(parents=True, exist_ok=True)
    dirs = [d for d in sorted(SCRAPE.iterdir()) if d.is_dir()
            and (not args.agency or args.agency.lower() in d.name.lower())]

    photos = vehicles = kept = vintage = novehicle = 0
    for d in dirs:
        for meta_f in sorted(d.glob("*.json")):
            img_f = meta_f.with_suffix(".jpg")
            if not img_f.exists():
                continue
            meta = json.loads(meta_f.read_text(encoding="utf-8"))
            photos += 1

            y = year_in(meta.get("page", "") or meta.get("source", ""))
            if y and y < OLDEST_YEAR:
                vintage += 1
                continue

            img = cv2.imread(str(img_f))
            if img is None:
                continue
            boxes = [(b, cls, conf) for _tid, cls, b, conf in det.track(img)]
            if not boxes:
                novehicle += 1
                continue
            vehicles += len(boxes)

            for i, (crop, cls, conf, edge) in enumerate(
                    crops_from(img, boxes)[:args.max_per_photo]):
                kept += 1
                if args.dry_run:
                    continue
                stem = f"{meta['sha']}_{i}"
                cv2.imwrite(str(BANK / f"{stem}.jpg"), crop,
                            [cv2.IMWRITE_JPEG_QUALITY, 92])
                (BANK / f"{stem}.json").write_text(json.dumps({
                    "ts": time.time(),
                    "node_id": None,
                    "cls_name": cls,
                    "det_conf": round(conf, 3),
                    "edge_px": edge,
                    # Provenance travels with the crop so the labelling page can
                    # show WHICH agency was searched for. Knowing the query was
                    # "Michigan State Police patrol car" is most of what a human
                    # needs to judge a small dark SUV.
                    "source": "scraped",
                    "agency": meta.get("agency"),
                    "category": meta.get("category"),
                    "query": meta.get("query"),
                    "origin_url": meta.get("source"),
                    "photo_sha": meta.get("sha"),
                    # No clip block on purpose: nothing has guessed, and the
                    # label UI must not be able to anchor the human.
                    "label": None,
                    "sampling": "scraped",
                }, indent=1), encoding="utf-8")

    print(f"photos            {photos}")
    print(f"  skipped, vintage  {vintage}")
    print(f"  no vehicle found  {novehicle}")
    print(f"vehicles detected {vehicles}")
    print(f"crops written     {kept}   -> {BANK}")
    print("\nAll unlabelled. Label them at :8160/label")


if __name__ == "__main__":
    main()
