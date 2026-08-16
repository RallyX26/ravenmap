"""Retire public cameras that measurement says can never produce a sighting.

    python tools/retire_dead_cams.py --source mi
    python tools/retire_dead_cams.py --source mi --apply

🚨 WHY THIS IS NEEDED, AND IT IS A CORRECTION TO MY OWN WORK.

Cameras were enrolled on a width test alone. Several networks answer a DEAD
camera with a full-HD "STREAM NOT AVAILABLE" card, which passes a width test
perfectly - so those got registered as cameras. Measured on Michigan: 77 of the
95 enrolled were that card. They beat, they count as online, they sit on the
map, and they will never produce a sighting for as long as the project runs.

That is worse than missing them. A camera that reports "online" and produces
nothing is indistinguishable from a real camera watching a quiet road, which is
exactly the signal the health watch exists to notice.

⚠️ THIS PAUSES, IT DOES NOT DELETE. The node is a real record of a real
registration and its sightings stay attached to it. `status='paused'` is the
same state a node gets before approval: it stops counting as online, stops
being polled, and can be un-paused if a network comes back. Deleting would
destroy history to fix a count.

⚠️ AND IT ONLY EVER ACTS ON MEASURED EVIDENCE. A camera missing from the probe
is UNMEASURED, not dead, and is left alone - the same rule probe_filter
follows. Missing data is not negative data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db                       # noqa: E402
import public_cams as pc        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    probe = pc.load_probe()
    if not probe:
        print("no measurements on this machine - copy data/probe/ here first")
        return 1

    # What the source offers now, unfiltered, so a node can be matched to the
    # measurement of its own image.
    raw = pc.SOURCES[a.source](measured_only=False) \
        if a.source in ("oh", "nm", "mo", "ny", "mi", "in", "tx", "al", "ne",
                        "nc") \
        else (pc.arcgis_index(a.source, measured_only=False)
              if a.source in pc.ARCGIS else pc.SOURCES[a.source]())
    hi = pc.SRC_MAX_WIDTH.get(a.source, 10 ** 9)
    by_name = {pc.node_name_for(c): c for c in raw}

    rows = [n for n in db.nodes(active_only=False)
            if (n.get("kind") or "") == "public_cam"
            and pc.ref_of(n.get("name") or "").startswith(a.source + ":")]
    print(f"{len(rows)} registered {a.source} node(s); "
          f"{len(raw)} offered by the source now")

    doomed, unmeasured, keep = [], 0, 0
    for n in rows:
        c = by_name.get(n["name"])
        if not c:
            unmeasured += 1
            continue
        w = probe.get(c["url"])
        if w is None:
            unmeasured += 1              # missing data is not negative data
        elif w == 0 or w < pc.MIN_HD_WIDTH or w > hi:
            doomed.append((n, w))
        else:
            keep += 1

    print(f"  {keep} still qualify")
    print(f"  {unmeasured} unmeasured or no longer offered - LEFT ALONE")
    print(f"  {len(doomed)} measured as unusable (placeholder, too small, "
          f"or a mosaic)")
    for n, w in doomed[:6]:
        print(f"    {n['id']}  width={w:<5} {n['name'][:52]}")
    if len(doomed) > 6:
        print(f"    ... and {len(doomed) - 6} more")

    if not a.apply:
        print("\nDRY RUN - nothing changed. Re-run with --apply.")
        return 0

    conn = db.connect()
    for n, _w in doomed:
        conn.execute("UPDATE nodes SET status = 'paused' WHERE id = ?",
                     (n["id"],))
    conn.commit()
    print(f"\npaused {len(doomed)} node(s); their sightings and history stay")
    return 0


if __name__ == "__main__":
    sys.exit(main())
