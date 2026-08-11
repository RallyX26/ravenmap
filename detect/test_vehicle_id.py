"""Can we identify police vehicles by sight, with no plate?

Scores every image in testvideo/vehicles, where the filename prefix is the
ground truth. The civilian set is deliberately the reference camera's OWN street footage
rather than stock photos - side-on, small, shot through a window - because that
is the distribution the classifier will actually face, and a classifier that
only separates studio photos is worthless here.

The number that matters is not accuracy, it is FALSE POSITIVES. Calling a
police car civilian costs a missed sighting. Calling a civilian car police
would put a private person into the public tier, which is the one failure this
project exists to prevent. So the report treats those two errors separately.

    python detect\test_vehicle_id.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classify  # noqa: E402
from detect.vehicle_id import PUBLIC_CLASSES, VehicleIdentifier  # noqa: E402

FOLDER = Path(__file__).resolve().parent.parent / "testvideo" / "vehicles"


def main() -> None:
    imgs = sorted(FOLDER.glob("*.jpg"))
    if not imgs:
        raise SystemExit(f"no images in {FOLDER}")

    print("loading CLIP (first run downloads ~600 MB)...")
    vid = VehicleIdentifier()
    print(f"  device: {vid.device}\n")

    hdr = f"{'truth':<10}{'file':<44}{'predicted':<12}{'conf':>6}{'margin':>8}  tier"
    print(hdr)
    print("-" * len(hdr))

    stats = {"police_found": 0, "police_total": 0,
             "civ_total": 0, "false_public": 0}

    for p in imgs:
        truth = p.name.split("_")[0].lower()
        img = cv2.imread(str(p))
        if img is None:
            continue
        r = vid.classify(img)
        ev = vid.evidence(img)
        # Run the REAL gate, not a proxy. An earlier version of this test just
        # checked whether an evidence key had been set, which is not the same
        # question at all - classify.py additionally requires a high combined
        # score AND corroboration before anything is published.
        ev_for_gate = {k: v for k, v in ev.items() if not k.startswith("_")}
        verdict = classify.classify(ev_for_gate)
        would_publish = verdict["tierable"]          # plate published?
        sighting = verdict["sightable"]              # public sighting, no plate?
        tier = ("PLATE" if would_publish else
                ("SIGHTING" if sighting else "private"))

        if truth == "police":
            stats["police_total"] += 1
            if sighting:
                stats["police_found"] += 1
        elif truth == "civilian":
            stats["civ_total"] += 1
            if would_publish or sighting:
                stats["false_public"] += 1

        flag = ""
        if truth == "police" and not sighting:
            flag = "  <- MISSED"
        if truth == "civilian" and (would_publish or sighting):
            flag = "  <- FALSE POSITIVE"
        print(f"{truth:<10}{p.name[:43]:<44}{r['vclass']:<12}"
              f"{r['conf']:>6.2f}{r['margin']:>8.2f}  {tier}{flag}")

    print()
    if stats["police_total"]:
        print(f"  police recall      : {stats['police_found']}/{stats['police_total']}")
    if stats["civ_total"]:
        print(f"  civilians wrongly made public: {stats['false_public']}/{stats['civ_total']}"
              f"   <- THIS IS THE ONE THAT MATTERS")


if __name__ == "__main__":
    main()
