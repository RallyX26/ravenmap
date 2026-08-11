"""Ingest hand-taken photos as LABELLED training data.

🚨 THIS IS THE PATH THAT WAS MISSING, AND IT MATTERS MORE THAN IT LOOKS.

SparrowMap has never seen a confirmed police vehicle. Every threshold in the
classifier is therefore set from negatives alone, `detect/calibrate.py` reports
recall as UNKNOWN, and the public tier is disabled because there is no honest
way to choose a bar. One afternoon of photographs outside a police station
fixes that, and nothing else does.

But the phone CONTRIBUTE page is the wrong tool for it. That page submits
SIGHTINGS - it asserts that a vehicle was at a place at a time, which is not
what a photo of a parked patrol car in a car park is. Worse, with the public
tier gated off those submissions land as private tier, and a private-tier image
with no plate box is DELIBERATELY DISCARDED by the hub. His photographs would
have been thrown away by a privacy rule doing exactly its job.

So: take normal photos with the normal camera app, drop the folder here, and
they become labelled crops in the training bank. No sighting is created, no
claim is made about anyone, and nothing depends on a self-signed certificate
working over mobile data in a car park.

    python tools\\ingest_photos.py --dir "photos\\police" --label police
    python tools\\ingest_photos.py --dir ... --label police --detect

`--detect` runs the vehicle detector and banks each vehicle CROP rather than
the whole photo, which is what the classifier actually consumes - a crop is
what it will see at inference time. Without it the whole frame is banked, which
is only right if the photo is already tightly framed on one vehicle.

## Two warnings about photographs taken this way

**Do not photograph people.** A car park full of patrol cars is fine; an
officer's face is a person, and this project does not collect those. `--detect`
crops to vehicles, which handles it structurally.

**Vary the angle, and get the BACK of the car.** The reference camera sees
receding traffic at 45-60 degrees. Twenty hero shots from the front are
worth less than five rear-quarter shots from across a road, because the model
has to work on what the camera actually sees. See train/README.md on the domain
gap - this is the same trap, and photographs are the one chance to not walk
into it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402

BANK = DATA / "training"
EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def _day_dir(tag: str) -> Path:
    d = BANK / f"{time.strftime('%Y-%m-%d')}_{tag}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="folder of photographs")
    ap.add_argument("--label", required=True,
                    choices=["police", "civilian", "unsure"])
    ap.add_argument("--detect", action="store_true",
                    help="crop to detected vehicles instead of banking whole frames")
    ap.add_argument("--label-all", action="store_true",
                    help="apply --label to EVERY detected vehicle, even in photos "
                         "containing several (see the warning in this file)")
    ap.add_argument("--tag", default="handheld",
                    help="folder suffix, so these are separable from camera data")
    ap.add_argument("--min-conf", type=float, default=0.5)
    args = ap.parse_args()

    src = Path(args.dir)
    if not src.is_dir():
        raise SystemExit(f"not a directory: {src}")
    files = [f for f in sorted(src.rglob("*")) if f.suffix.lower() in EXTS]
    if not files:
        raise SystemExit(f"no images under {src}")
    print(f"{len(files)} photos, labelling as '{args.label}'")

    heic = [f for f in files if f.suffix.lower() in (".heic", ".heif")]
    if heic:
        print(f"\n⚠️  {len(heic)} HEIC files. iPhones shoot HEIC by default and"
              f"\n    OpenCV cannot read it. Either set the iPhone to"
              f"\n    Settings > Camera > Formats > Most Compatible before shooting,"
              f"\n    or AirDrop/copy them out as JPEG. Skipping them for now.\n")

    det = None
    if args.detect:
        from detect.pipeline import VehicleTracker
        print("loading detector...")
        det = VehicleTracker()

    import cv2
    out = _day_dir(args.tag)
    n = 0
    for f in files:
        if f.suffix.lower() in (".heic", ".heif"):
            continue
        # ⚠️ ORIENTATION EXPLICITLY, NOT BY LUCK.
        # iPhones store a landscape sensor image plus an EXIF rotation flag.
        # Modern OpenCV happens to apply it; older builds and several other
        # readers do not, and a sideways police car is not what a street camera
        # ever sees - the detector would likely miss it outright and any crop
        # that survived would teach the model the wrong thing. PIL is asked to
        # normalise it, so this does not depend on which OpenCV is installed.
        try:
            from PIL import Image, ImageOps
            pil = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
            import numpy as np
            img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except Exception:
            img = cv2.imread(str(f))
        if img is None:
            print(f"  ! unreadable: {f.name}")
            continue

        boxes = []
        if det is not None:
            for _tid, cls, box, conf in det.track(img):
                if conf >= args.min_conf:
                    boxes.append((cls, box, conf))
            if not boxes:
                print(f"  - no vehicle found in {f.name}")
                continue
        else:
            h, w = img.shape[:2]
            boxes = [("photo", (0, 0, w, h), 1.0)]

        for i, (cls, box, conf) in enumerate(boxes):
            x0, y0, x1, y1 = (int(v) for v in box)
            h, w = img.shape[:2]
            px, py = int((x1 - x0) * 0.06), int((y1 - y0) * 0.06)
            crop = img[max(0, y0 - py):min(h, y1 + py),
                       max(0, x0 - px):min(w, x1 + px)]
            if crop.size == 0:
                continue
            if max(crop.shape[:2]) > 512:
                s = 512 / max(crop.shape[:2])
                crop = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)),
                                  interpolation=cv2.INTER_AREA)

            stem = hashlib.blake2b(f"{f.name}:{i}".encode(),
                                   digest_size=6).hexdigest()
            cv2.imwrite(str(out / f"{stem}.jpg"), crop,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            (out / f"{stem}.json").write_text(json.dumps({
                "ts": f.stat().st_mtime,
                "node_id": "handheld",
                "cls_name": cls,
                "frames_seen": 1,
                "plate_reads": 0,
                "evidence": {},
                "clip": {},
                "auto_verdict": {},
                # 🚨 A PHOTO OF POLICE CARS IS NOT A PHOTO OF ONLY POLICE CARS.
                #
                # The first run of this applied --label to every vehicle the
                # detector found, and the eight photographs from outside the
                # station contained a white Ram, two white 4x4 pickups, a blue
                # sedan and two Chevrolet Equinoxes parked among the patrol
                # units. All six were banked as 'police'. Training on that
                # would teach the model that a white pickup is a patrol car -
                # the exact class of error the classifier is being rebuilt to
                # remove, introduced by the tool meant to fix it.
                #
                # So a photo containing MORE THAN ONE vehicle banks its crops
                # UNLABELLED for review, unless --label-all is passed
                # deliberately. A single-vehicle photo is unambiguous and keeps
                # the label.
                #
                # (The mislabelled civilians turned out to be worth keeping,
                # correctly labelled: same camera, same light, same car park as
                # the positives, which is precisely the style-matched negative
                # set train/README.md says is worth more than volume.)
                "label": (args.label if (len(boxes) == 1 or args.label_all)
                          else None),
                "labelled_at": time.time(),
                "sampling": "handheld",
                "source_photo": f.name,
            }, indent=1), encoding="utf-8")
            n += 1

    print(f"\nbanked {n} crops -> {out}")
    if not args.label_all:
        print(f"    crops from photos containing SEVERAL vehicles were left")
        print(f"    UNLABELLED - review them at http://localhost:8160/label")
        print(f"    (a car park full of patrol cars also contains other people's cars)")
    print(f"\n⚠️  These are marked sampling='handheld'. They can TRAIN a model.")
    print(f"    They are not a random sample of traffic, so precision and recall")
    print(f"    computed over them would be meaningless - measure on the camera's")
    print(f"    own review-mode labels instead. See train/README.md.")


if __name__ == "__main__":
    main()
