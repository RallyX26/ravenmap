"""Work out which road each camera ACTUALLY watches, from how its traffic moves.

    python3 tools/aim_from_traffic.py            # dry run
    python3 tools/aim_from_traffic.py --apply    # rewrite spans and re-place dots

🚨 WHY PROXIMITY IS THE WRONG QUESTION.
A span used to be placed on the nearest driveable road. On a corner plot the
nearest road can be the cross street: measured on the operator's own camera,
vehicles travel on a 93 degree axis (49 passes, concentration 0.97) while the
nearest road was North Bridge Street at 178 degrees - perpendicular - beating
Hamrick Street at 88.8 degrees by 0.7 metres. The map claimed a camera watched
a street its traffic never uses.

A camera pointed at a street sees vehicles travelling ALONG it, and the tracker
already records each pass's direction. So the road is recoverable from the
sightings, needing no compass and nothing for a volunteer to set - a browser in
a window cannot report a bearing, which is why nearly every node has heading 0
and falls through to proximity.

WHAT IT WILL NOT DO
- It will not act on weak evidence. A node needs `--min-passes` headings and a
  circular concentration of at least `--min-concentration`, or it is left alone
  and says why. A camera watching a junction legitimately has no single axis,
  and guessing there would be the same mistake in a new coat.
- It will not move a span that already agrees with the traffic.
- It touches the DATABASE ONLY. No service restart, so nothing goes offline.

Re-placing history is a rewrite of stored coordinates, so it is opt-in and
prints every before/after first.
"""

from __future__ import annotations

import argparse
import cmath
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                        # noqa: E402
import nodes as nodes_mod                        # noqa: E402
import road                                      # noqa: E402


def travel_axis(headings: list[float]) -> tuple[float, float]:
    """Dominant axis of travel (0-180) and how concentrated it is (0-1).

    Doubling the angle before averaging folds a heading and its reverse onto
    the same direction, which is what a road is: traffic runs both ways along
    one axis. Averaging raw headings would cancel eastbound against westbound
    and report a confident answer pointing at neither.
    """
    if not headings:
        return 0.0, 0.0
    v = sum(cmath.exp(2j * math.radians(h)) for h in headings) / len(headings)
    return (math.degrees(cmath.phase(v)) / 2.0) % 180.0, abs(v)


def span_axis(span: list) -> float:
    (a_lat, a_lon), (b_lat, b_lon) = span[0], span[1]
    dx = (b_lon - a_lon) * 111_320.0 * math.cos(math.radians(a_lat))
    dy = (b_lat - a_lat) * 111_320.0
    return math.degrees(math.atan2(dx, dy)) % 180.0


def off_axis(a: float, b: float) -> float:
    return abs(((a - b + 90.0) % 180.0) - 90.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-passes", type=int, default=12)
    ap.add_argument("--min-concentration", type=float, default=0.55)
    ap.add_argument("--max-off-axis", type=float, default=25.0)
    a = ap.parse_args()

    conn = db.connect()
    changed = skipped = 0
    for nd in db.nodes():
        hs = [float(r["heading"]) for r in conn.execute(
            "SELECT heading FROM sightings WHERE node_id=? AND heading IS NOT NULL",
            (nd["id"],)).fetchall()]
        name = (nd["name"] or nd["id"])[:26]

        if len(hs) < a.min_passes:
            print(f"  {name:<28} SKIP  only {len(hs)} passes carry a direction")
            skipped += 1
            continue
        axis, conc = travel_axis(hs)
        if conc < a.min_concentration:
            print(f"  {name:<28} SKIP  traffic has no single axis "
                  f"(concentration {conc:.2f}) - a junction, most likely")
            skipped += 1
            continue

        cur = nodes_mod.span_of(nd)
        cur_off = off_axis(span_axis(cur), axis) if cur else None
        try:
            ways = road.fetch_ways(nd["lat"], nd["lon"])
        except Exception as exc:
            print(f"  {name:<28} SKIP  road lookup failed ({type(exc).__name__})")
            skipped += 1
            continue

        got = road.span_from_travel(nd["lat"], nd["lon"], ways, axis,
                                    max_off_axis=a.max_off_axis)
        if not got:
            print(f"  {name:<28} SKIP  no road within {a.max_off_axis:.0f} deg "
                  f"of the traffic axis {axis:.0f}")
            skipped += 1
            continue

        if cur_off is not None and cur_off <= got["off_axis_deg"] + 1.0:
            print(f"  {name:<28} ok    already on-axis ({cur_off:.1f} deg off), "
                  f"n={len(hs)}")
            continue

        print(f"  {name:<28} MOVE  traffic {axis:.0f} deg (n={len(hs)}, conc {conc:.2f})")
        print(f"      {'was':<6} {cur_off:.1f} deg off-axis" if cur_off is not None
              else "      was    no span")
        print(f"      {'now':<6} {got['road_name'] or '(unnamed)'} "
              f"- {got['off_axis_deg']} deg off-axis, {got['camera_dist_m']} m from the camera")
        changed += 1

        if a.apply:
            sp = got["span"]
            conn.execute("UPDATE nodes SET span_lat1=?, span_lon1=?, "
                         "span_lat2=?, span_lon2=? WHERE id=?",
                         (sp[0][0], sp[0][1], sp[1][0], sp[1][1], nd["id"]))
            conn.commit()
            fresh = db.node(nd["id"])
            n = 0
            for row in conn.execute(
                    "SELECT id FROM sightings WHERE node_id=?", (nd["id"],)).fetchall():
                lat2, lon2 = nodes_mod.sighting_position(
                    fresh, None, None, seed=f"{nd['id']}:{row['id']}")
                conn.execute("UPDATE sightings SET lat=?, lon=? WHERE id=?",
                             (lat2, lon2, row["id"]))
                n += 1
            conn.commit()
            print(f"      re-placed {n} sightings onto it")

    print(f"\n{'moved' if a.apply else 'would move'}: {changed}   left alone: {skipped}")
    if not a.apply:
        print("dry run - nothing written. Re-run with --apply.")


if __name__ == "__main__":
    main()
