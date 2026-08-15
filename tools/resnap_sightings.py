"""Put already-published sightings back on the road they were on.

🚨 WHY THIS EXISTS SEPARATELY FROM THE FIX.

Drive-mode sightings are supposed to be snapped to the nearest road at ingest -
`nodes.sighting_position` calls `road.snap_point` for span-less (phone) nodes.
That code was correct and correctly wired. Its road cache was memory-only, so
every hub restart emptied it, and the first sighting in an area then needed a
live Overpass query that frequently timed out. A failed lookup means no snap,
and the sighting is published wherever the 60 m privacy jitter put it.

Persisting the cache stopped it happening again. It did nothing for the rows
already written - reported as police dots scattered east of Bridge Street in
Linden that all belonged on South Main at East Broad.

⚠️ WHAT THIS DELIBERATELY WILL NOT DO.

A sighting is evidence. This moves a point only when the move is a CORRECTION
and not a guess:

  * span-less nodes only. A fixed camera's position is its own; only mobile
    contributions were ever snapped, so only they may be re-snapped.
  * bounded by --max-m (default 45). Beyond that the nearest road is not
    obviously the road it was on, and dragging a point onto whatever happens to
    be closest would invent a fact rather than restore one.
  * dry run unless --apply is passed, and it prints every move it would make
    with the distance, so the whole change can be read before it happens.

Run on the box, where the live database is:
    python tools/resnap_sightings.py --box-latlon 42.78,-83.82,42.86,-83.74
    python tools/resnap_sightings.py --box-latlon ... --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                      # noqa: E402
import road                    # noqa: E402
from core import haversine_m   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box-latlon", required=True,
                    help="lat1,lon1,lat2,lon2 - only touch sightings inside")
    ap.add_argument("--max-m", type=float, default=45.0)
    # 🚨 SCOPE, BECAUSE THE FIRST DRY RUN WANTED TO MOVE 18,126 ROWS.
    # The ask was "the police dots in Linden that are off the road". Without a
    # tier filter this selected every PRIVATE traffic dot in the county too -
    # inert markers that fade in 45 seconds and are not evidence of anything -
    # and offered to rewrite all of them. A correction that touches 1500x more
    # rows than the thing being corrected is not a correction.
    ap.add_argument("--tier", default="public",
                    help="only this tier (default public; 'any' to ignore)")
    # And a floor. A 2 m move is the jitter doing its job, not a dot in the
    # wrong place; rewriting those is churn on stored evidence for no visible
    # gain.
    ap.add_argument("--min-m", type=float, default=10.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    lat1, lon1, lat2, lon2 = [float(x) for x in a.box_latlon.split(",")]
    lat1, lat2 = min(lat1, lat2), max(lat1, lat2)
    lon1, lon2 = min(lon1, lon2), max(lon1, lon2)

    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    sql = ("SELECT id, node_id, lat, lon, tier, vclass FROM sightings "
           "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
    args = [lat1, lat2, lon1, lon2]
    if a.tier != "any":
        sql += " AND tier = ?"
        args.append(a.tier)
    rows = con.execute(sql + " ORDER BY id", args).fetchall()

    print(f"{len(rows)} sighting(s) in the box (tier={a.tier})")

    # Which nodes are mobile? A node with a span is a fixed camera aimed at a
    # stretch of road and its sightings were never snapped.
    spanless: dict[str, bool] = {}

    def is_mobile(node_id: str) -> bool:
        if node_id not in spanless:
            nd = db.node(node_id) or {}
            spanless[node_id] = not nd.get("span")
        return spanless[node_id]

    moved = skipped_fixed = skipped_far = already = failed = 0
    plan = []
    for r in rows:
        if not is_mobile(r["node_id"]):
            skipped_fixed += 1
            continue
        try:
            snapped = road.snap_point(r["lat"], r["lon"], str(r["id"]))
        except Exception as exc:
            print(f"  id={r['id']}: snap failed ({str(exc)[:50]})")
            failed += 1
            continue
        if not snapped:
            failed += 1
            continue
        d = haversine_m(r["lat"], r["lon"], snapped[0], snapped[1])
        if d < a.min_m:
            already += 1
            continue
        if d > a.max_m:
            print(f"  id={r['id']}: nearest road is {d:.0f} m away - LEFT ALONE "
                  f"(over --max-m {a.max_m:.0f})")
            skipped_far += 1
            continue
        plan.append((r["id"], r["lat"], r["lon"], snapped[0], snapped[1], d))

    for sid, olat, olon, nlat, nlon, d in plan:
        print(f"  id={sid}: {olat:.6f},{olon:.6f} -> {nlat:.6f},{nlon:.6f}  "
              f"({d:.0f} m onto the road)")

    print(f"\nfixed cameras skipped : {skipped_fixed}")
    print(f"already on a road     : {already}")
    print(f"too far to be sure    : {skipped_far}")
    print(f"no road data          : {failed}")
    print(f"to move               : {len(plan)}")

    if not a.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to move them.")
        return 0

    for sid, _, _, nlat, nlon, _ in plan:
        con.execute("UPDATE sightings SET lat=?, lon=? WHERE id=?",
                    (nlat, nlon, sid))
    con.commit()
    moved = len(plan)
    print(f"\nmoved {moved} sighting(s) onto the road")
    return 0


if __name__ == "__main__":
    sys.exit(main())
