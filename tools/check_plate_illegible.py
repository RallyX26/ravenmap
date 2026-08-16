"""Test the one number the whole privacy guarantee rests on.

    python tools/check_plate_illegible.py

🚨 WHY THIS EXISTS. A crop whose plate could not be LOCATED is allowed through
only if its longest edge is <= SUBRES_MAX_EDGE (200px), and snapshot.py
justifies that number with an assumption:

    "A plate is roughly a tenth of a vehicle's width. Below this longest edge
     the plate occupies about 20px."

That is true for a car photographed across a road, which is what the reference
camera sees. It is NOT obviously true for a vehicle close enough to fill the
frame - a rear-end crop at 200px could put a plate at 40-50px against a
legibility floor this project puts at ~60. Every civilian plate on the map
depends on which of those is the real worst case, and nobody has measured it.

So: measure the ASPECT of every stored private-tier crop. A crop is the vehicle
plus a fixed pad, so its shape says how the vehicle was framed - a wide, short
crop is a side view where the plate is edge-on and tiny; a squarer crop is a
rear or front view, which is the dangerous one because the plate faces us.

⚠️ THIS MEASURES THE BOUND, NOT THE PLATES. It does not read plates and cannot;
it reports how much of a plate the geometry ALLOWS at the sizes actually being
stored. A bound that holds for the worst real crop is the claim worth making.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image                       # noqa: E402

import core                                 # noqa: E402
import db                                   # noqa: E402
import snapshot                             # noqa: E402

# What a plate occupies across the back of a car, as a fraction of the vehicle's
# width. A US plate is 12 inches on a body between 68 and 80 inches, so this is
# geometry rather than a guess: 0.15 is a small car, 0.18 is generous.
PLATE_FRACTION = 0.18

# The width below which this project's own reader returns nothing. Quoted in
# classify.py: "22 px at the reference camera against the 60 needed".
LEGIBLE_PX = 60


def main() -> int:
    rows = db.connect().execute(
        "SELECT id, tier, vclass, snap FROM sightings "
        "WHERE snap IS NOT NULL AND snap <> '' ORDER BY id DESC LIMIT 4000"
    ).fetchall()
    snaps = Path(getattr(core, "SNAPS", core.DATA / "snaps"))

    worst = []
    hist = Counter()
    checked = 0
    for r in rows:
        if r["tier"] == "public":
            continue                     # a confirmed police plate is kept ON PURPOSE
        p = snaps / r["snap"]
        if not p.exists():
            continue
        try:
            w, h = Image.open(p).size
        except Exception:
            continue
        checked += 1
        hist[w] += 1
        # The vehicle spans nearly the whole crop by construction, so the
        # vehicle's width is about the crop's width.
        plate_px = w * PLATE_FRACTION
        worst.append((plate_px, w, h, r["id"], r["vclass"]))

    if not checked:
        print("no private-tier crops on this machine to measure")
        return 0

    worst.sort(reverse=True)
    print(f"SUBRES_MAX_EDGE = {snapshot.SUBRES_MAX_EDGE}px, "
          f"legibility floor = {LEGIBLE_PX}px, "
          f"plate = {PLATE_FRACTION:.0%} of vehicle width\n")
    print(f"{checked} private-tier crops measured")
    print("  widths: " + ", ".join(f"{w}px x{n}" for w, n in
                                   sorted(hist.items())))
    print("\nwidest implied plate, worst first:")
    for plate_px, w, h, sid, vc in worst[:8]:
        flag = "⚠ OVER" if plate_px >= LEGIBLE_PX else "ok"
        print(f"  {flag:<7} sighting {sid:<7} crop {w}x{h:<5} "
              f"vclass={str(vc):<9} implied plate {plate_px:.0f}px")

    over = [x for x in worst if x[0] >= LEGIBLE_PX]
    head = worst[0][0]
    print(f"\n{len(over)} of {checked} crops could geometrically carry a "
          f"{LEGIBLE_PX}px plate")
    if over:
        print(f"🚨 THE GUARANTEE DOES NOT HOLD at {snapshot.SUBRES_MAX_EDGE}px. "
              f"The cap that would hold is "
              f"{int(LEGIBLE_PX / PLATE_FRACTION) - 1}px.")
        return 1
    print(f"✅ Worst real crop implies a {head:.0f}px plate, "
          f"{LEGIBLE_PX - head:.0f}px under the floor. The bound holds for "
          f"every crop on this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
