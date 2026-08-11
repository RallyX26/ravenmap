"""Retract false public claims, and delete snapshots that were never crops.

Two clean-ups, both arising from 2026-08-08:

1. Five vehicles reached the public tier as police off the reference camera's street.
   He looked at them and confirmed none of them was a police vehicle. They are
   demoted to private tier and reclassified civilian rather than deleted - the
   pass really did happen, so removing the row would replace one false claim
   with a different one. What gets removed is the claim ABOUT the vehicle.

2. Every snapshot in the database is a full 900x506 frame of the street rather
   than a vehicle crop, because camera submissions were routed through the
   phone path. See snapshot.store_submitted. They contain neighbours' houses,
   whoever was on their porch, and other vehicles' plates which were never
   redacted - only the tracked vehicle's plate box was ever passed in. They
   cannot be fixed in place (re-cropping requires knowing which vehicle each
   one was about, and guessing wrong crops to a bystander instead), so they are
   deleted. Sightings written after the fix store proper crops.

   Copies of the five confirmed-negative frames were kept under
   data/eval/labelled BEFORE this runs, so the calibration evidence survives
   the purge. That set is operator-only and never served.

    python tools\\retract.py             # report only
    python tools\\retract.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                       # noqa: E402
from core import SNAPS          # noqa: E402

# A stored image this large in BOTH dimensions was never cropped to a vehicle.
#
# ⚠️ Width alone is not enough and cost 12 legitimate crops. A vehicle close to
# the camera fills the frame horizontally, so a real crop can be 900 px wide -
# but it is short (the Silverado crop was 900x369 against a 900x506 frame).
# Requiring both dimensions keeps wide crops and still catches whole frames.
FULL_FRAME_W = 800
FULL_FRAME_H = 480

REASON = ("retracted 2026-08-08: published on a single unverified visual cue; "
          "the camera operator confirmed this was not a police vehicle")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = db.connect()

    # ---- 1. the false public sightings ---------------------------------
    pub = conn.execute(
        "SELECT id, ts, vclass, vclass_why, snap FROM sightings "
        "WHERE tier = 'public'").fetchall()
    print(f"public-tier sightings to retract: {len(pub)}")
    for r in pub:
        print(f"   #{r['id']}  {r['vclass']}  \"{r['vclass_why']}\"")

    # ---- 2. the full-frame snapshots -----------------------------------
    rows = conn.execute(
        "SELECT id, snap FROM sightings WHERE snap IS NOT NULL").fetchall()
    doomed = []
    for r in rows:
        p = SNAPS / r["snap"]
        if not p.exists():
            continue
        try:
            w, h = Image.open(p).size
            if w >= FULL_FRAME_W and h >= FULL_FRAME_H:
                doomed.append((r["id"], p))
        except Exception:
            doomed.append((r["id"], p))
    print(f"\nfull-frame snapshots to delete: {len(doomed)} of {len(rows)}")

    # Orphans: files on disk with no row pointing at them.
    referenced = {r["snap"] for r in rows}
    orphans = [p for p in SNAPS.glob("*.jpg") if p.name not in referenced]
    print(f"orphaned snapshot files: {len(orphans)}")

    if not args.apply:
        print("\n[--apply to write]")
        return

    conn.execute(
        "UPDATE sightings SET tier='private', vclass='civilian', vclass_conf=NULL,"
        " vclass_why=?, plate_text=NULL, plate_state=NULL WHERE tier='public'",
        (REASON,))
    for sid, p in doomed:
        p.unlink(missing_ok=True)
        conn.execute("UPDATE sightings SET snap=NULL WHERE id=?", (sid,))
    for p in orphans:
        p.unlink(missing_ok=True)
    conn.commit()

    db.audit("retract_false_public", f"{len(pub)} sightings", actor="operator")
    print(f"\nretracted {len(pub)} public sightings, "
          f"deleted {len(doomed)} full frames and {len(orphans)} orphans")


if __name__ == "__main__":
    main()
