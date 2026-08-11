"""Demote public sightings that no human ever checked.

2026-08-10. The public map held 78 police sightings. **70 of them carried the
single reason "identified as a police vehicle by sight"** - the trained head
firing alone - because `visual_police` sat in classify.py's SOLID corroboration
set and so satisfied the "not on one signal" rule by itself. A contractor's
truck wearing a parody "ROOFING PD" badge decal was published as police, which
is exactly what a head trained on door markings will do.

classify.py now requires two distinct VISUAL markers for a police call, but
that only governs what happens NEXT. The rows already published were written
under the old rule and are still on the map.

⚠️ WHAT THIS SCRIPT DELIBERATELY DOES NOT DO.

  * It does not touch the sightings the operator CONFIRMED by hand. A person
    looked at those and said yes; that is the ground truth every signal here is
    trying to approximate, and it does not expire because the gate changed.

  * It does not set vclass='civilian'. tools/retract.py does, and is right to:
    there the operator had looked and said "not a police vehicle", so civilian
    is a fact. Here nobody looked. Recording "civilian" would replace an
    unsupported claim with a different unsupported claim, and would poison any
    ground truth later derived from this table. The class is left alone; the
    PUBLICATION is what gets withdrawn.

  * It does not clear `reviewed`. These rows stay NULL so they remain in the
    review queue - the point is that they still need a human, not that they
    have been dealt with.

  * It does not delete anything. The pass really happened. Removing the row
    would replace one false claim with another.

What it does remove is `plate_text`/`plate_state`, for two reasons. The private
tier's standing invariant is that no private row holds readable plate text.
And the text is wrong anyway: the plate detector fires on door livery because
it wins on conf x sqrt(area) (see detect/pipeline.py), so the word POLICE came
back as PO415 / P0411 / P0116 / P04GE. Those were published as searchable
government plates.

    python tools\\demote_unverified.py            # report only
    python tools\\demote_unverified.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                       # noqa: E402

REASON = ("withdrawn 2026-08-10: published under the pre-two-marker rule on a "
          "single visual signal and never checked by a human; still pending "
          "review")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = db.connect()

    kept = conn.execute(
        "SELECT COUNT(*) FROM sightings "
        "WHERE tier='public' AND reviewed IS NOT NULL").fetchone()[0]
    doomed = conn.execute(
        "SELECT id, vclass, plate_text, vclass_why FROM sightings "
        "WHERE tier='public' AND reviewed IS NULL").fetchall()

    print(f"public sightings a human CONFIRMED  : {kept}   (kept, untouched)")
    print(f"public sightings NEVER checked      : {len(doomed)}   (withdrawing)")
    print()
    with_plate = [r for r in doomed if r["plate_text"]]
    print(f"of those, carrying a published plate: {len(with_plate)}")
    for r in with_plate[:12]:
        print(f"   #{r['id']}  {r['vclass']:<8} plate={r['plate_text']!r}")
    if len(with_plate) > 12:
        print(f"   ... and {len(with_plate) - 12} more")

    if not args.apply:
        print("\n[--apply to write]")
        return

    conn.execute(
        "UPDATE sightings SET tier='private', plate_text=NULL, plate_state=NULL,"
        " vclass_why=? WHERE tier='public' AND reviewed IS NULL",
        (REASON,))
    conn.commit()

    db.audit("withdraw_unverified_public", f"{len(doomed)} sightings",
             actor="operator")
    print(f"\nwithdrew {len(doomed)} unverified public sightings; "
          f"{kept} confirmed ones left on the map")


if __name__ == "__main__":
    main()
