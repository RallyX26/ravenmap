"""Move named sightings to a position a HUMAN says they belong at.

🚨 THIS IS NOT resnap_sightings.py AND MUST NOT BECOME IT.

`resnap_sightings.py` RECOMPUTES: it takes a stored point that failed to snap
and puts it on the nearest road. That is a correction derived from the data.

This one takes a position from a PERSON - the contributor who was there - and
re-places sightings whose true position is gone. It exists because the original
GPS is never stored: `sightings` keeps only the published point, so once a
sighting is written to the wrong street there is nothing to recompute FROM.

⚠️ WHEN THIS IS LEGITIMATE, AND WHEN IT IS FABRICATION.
Legitimate: the camera's owner says "I was here, these eight are mine, they are
on the wrong street". That is the same authority the review pen already trusts
to publish or retract a vehicle, applied to position instead of class.
Fabrication: guessing from a map because a dot looks wrong. If nobody who was
there is telling you where it was, do not run this.

The position is an ARGUMENT, never a constant in this file - the repo is
public and a contributor's position does not belong in it.

    python tools/replace_sightings.py --ids 1,2,3 --at LAT,LON
    python tools/replace_sightings.py --ids 1,2,3 --at LAT,LON --apply
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _m(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="comma-separated sighting ids")
    ap.add_argument("--at", help="LAT,LON the person says they were at")
    # 🚨 --on-span IS NOT A HUMAN'S GUESS, IT IS A RECOMPUTATION.
    # A fixed camera's sightings belong on its published watched span, and the
    # span is already stored. When one has escaped it - see the GPS-overrides-
    # span bug in nodes.sighting_position - putting it back needs no
    # coordinates from anybody, so this mode takes none.
    ap.add_argument("--on-span", action="store_true",
                    help="re-place onto the sighting's own node's watched span")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-m", type=float, default=400.0,
                    help="refuse to move a sighting further than this")
    a = ap.parse_args()

    import db
    import nodes as node_mod
    import road

    if bool(a.at) == bool(a.on_span):
        print("pass exactly one of --at LAT,LON or --on-span")
        return 2
    lat = lon = None
    if a.at:
        try:
            lat, lon = (float(x) for x in a.at.split(","))
        except ValueError:
            print("--at must be LAT,LON")
            return 2
    ids = [int(x) for x in a.ids.split(",") if x.strip()]

    # 🚨 SNAP FROM THE STATED POINT, THE SAME WAY INGEST NOW DOES.
    # Not "put them all on one pixel": each keeps its own seed, so they spread
    # along the road exactly as a live sighting would. Stacking eight dots on a
    # single point would claim a precision nobody has - he said "roughly".
    conn = db.connect()
    moves, refused = [], []
    for sid in ids:
        row = conn.execute("SELECT id, node_id, ts, lat, lon, source, vclass "
                           "FROM sightings WHERE id=?", (sid,)).fetchone()
        if not row:
            refused.append((sid, "no such sighting"))
            continue
        r = dict(row)
        if a.on_span:
            n = conn.execute("SELECT * FROM nodes WHERE id=?",
                             (r["node_id"],)).fetchone()
            nd = dict(n) if n else None
            span = node_mod.span_of(nd) if nd else None
            if not span:
                refused.append((sid, "its node has no watched span"))
                continue
            if (nd.get("kind") or "") in ("phone", "mobile", "drive"):
                # A moving node's span is only where it enrolled; pinning a
                # dashcam sighting to it would be the wrong-street bug again.
                refused.append((sid, f"node kind={nd.get('kind')} moves"))
                continue
            snapped = road.point_on_span(span, f"respan:{sid}")
        else:
            if (r.get("source") or "") not in ("phone_node", "drive", "mobile"):
                # A fixed camera's position is its own and is not a
                # contributor's recollection to correct.
                refused.append((sid, f"source={r.get('source')} is not mobile"))
                continue
            snapped = road.snap_point(lat, lon, f"replace:{sid}")
        if not snapped:
            refused.append((sid, "no road near the stated position"))
            continue
        d = _m((r["lat"], r["lon"]), snapped)
        if d > a.max_m:
            refused.append((sid, f"would move {d:.0f} m, over --max-m {a.max_m:.0f}"))
            continue
        moves.append((sid, (r["lat"], r["lon"]), snapped, d))

    for sid, old, new, d in moves:
        print(f"  {sid}: {old[0]:.6f},{old[1]:.6f} -> {new[0]:.6f},{new[1]:.6f}"
              f"   ({d:.0f} m)")
    for sid, why in refused:
        print(f"  {sid}: REFUSED - {why}")
    print(f"\n{len(moves)} to move, {len(refused)} refused")

    if not a.apply:
        print("dry run - nothing written. Pass --apply to commit.")
        return 0
    for sid, _old, new, _d in moves:
        conn.execute("UPDATE sightings SET lat=?, lon=? WHERE id=?",
                     (new[0], new[1], sid))
        # The move is a claim about a published record, so it is auditable.
        try:
            db.audit("sighting_replaced", str(sid), actor="operator")
        except Exception:
            pass
    conn.commit()
    print(f"moved {len(moves)} sighting(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
