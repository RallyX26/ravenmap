"""Give existing nodes a watched road span, and move their sightings onto it.

Nodes enrolled before road.py existed have no span, so their sightings sit on a
single invented point offset from a jittered camera position - which, with a
60 m jitter budget and a 28 m reach, is usually not on the road at all. This
recomputes the span from each node's TRUE position and re-places every stored
sighting along it.

    python tools\\resnap_nodes.py                # show what would change
    python tools\\resnap_nodes.py --apply        # do it

⚠️ `--offline` CANNOT PRODUCE A SPAN, AND THIS TEXT USED TO CLAIM IT COULD.
It once meant "aim-axis span, no Overpass". road.resolve() deliberately stopped
doing that - inventing a compass line gave 10 of 52 nodes a fabricated road,
one of them carrying 58 real sightings along a street that does not exist - so
it now returns None rather than guessing, and `--offline` returns None for
EVERY node. The flag is kept because callers pass it, but it can only ever
print "no road could be resolved". A stale usage line cost real debugging time
here: it looks like a working fallback for exactly the situation (Overpass
disagreeing with a stored road name) where you most want one.

Re-placing history is a rewrite of stored data, so it is opt-in and prints the
before/after distance for every node first. The sighting TIMES are untouched;
only the coordinates move, and they move from a wrong place to a better one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db          # noqa: E402
import nodes       # noqa: E402
import road        # noqa: E402
from core import haversine_m   # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--offline", action="store_true",
                    help="(no-op) road.resolve refuses to invent a road, so "
                         "this resolves nothing for every node - see the "
                         "module docstring")
    ap.add_argument("--node", default="", help="only this node id")
    args = ap.parse_args()

    conn = db.connect()
    rows = db.nodes(active_only=False)
    if args.node:
        rows = [n for n in rows if n["id"] == args.node]
    if not rows:
        raise SystemExit("no nodes")

    for n in rows:
        if n["kind"] == "mobile":
            print(f"{n['id']}  mobile node, sightings carry their own GPS - skipped")
            continue

        # 🚨 NEVER RE-SPAN A CARRIED CAMERA. Enrolment refuses to give
        # kind='mobile' a span, and this tool overrode that by walking
        # every node in the table - which is how driving sessions came to be
        # drawn as watched roads. A tool that bypasses the rule its own
        # module enforces is worse than no tool.
        if (n.get("kind") or "") == "mobile":
            print(f"{n['id']}  {n['name']}")
            print("   SKIP  carried camera - no watched road")
            continue
        span = road.resolve(n["lat"], n["lon"], n["heading"] or 0,
                            n["fov"] or 60, n["reach_m"] or 45,
                            online=not args.offline)
        # resolve() now returns None when no ROAD could be determined, rather
        # than inventing a compass line. Skip: a node keeps whatever it had,
        # and a later run with a working Overpass can snap it properly.
        if not span:
            print(f"{n['id']}  no road could be resolved (lookup unavailable) "
                  f"- left alone")
            continue
        (a_lat, a_lon), (b_lat, b_lon) = span["span"]
        length = road.span_length_m(span["span"])

        # How far is the new span from where sightings are sitting today?
        cur = conn.execute(
            "SELECT lat, lon FROM sightings WHERE node_id = ? LIMIT 1",
            (n["id"],)).fetchone()
        moved = ""
        if cur:
            mid_lat, mid_lon = (a_lat + b_lat) / 2, (a_lon + b_lon) / 2
            moved = (f"  (existing dots move ~"
                     f"{haversine_m(cur['lat'], cur['lon'], mid_lat, mid_lon):.0f} m)")

        print(f"{n['id']}  {n['name']}")
        print(f"   source={span['source']}  road={span['road_name'] or '(unnamed)'}"
              f"  watched={span['watched_m']:.0f} m  published span={length:.0f} m{moved}")

        count = conn.execute("SELECT COUNT(*) FROM sightings WHERE node_id = ?",
                             (n["id"],)).fetchone()[0]
        if not args.apply:
            print(f"   would re-place {count} sightings   [--apply to write]\n")
            continue

        conn.execute("""UPDATE nodes SET span_lat1=?, span_lon1=?, span_lat2=?,
                        span_lon2=?, road_name=?, span_source=? WHERE id=?""",
                     (a_lat, a_lon, b_lat, b_lon, span["road_name"],
                      span["source"], n["id"]))

        # Re-place each sighting with the same seed rule the ingest path uses,
        # so a row written today and a row re-placed today land the same way.
        srows = conn.execute(
            "SELECT id, ts FROM sightings WHERE node_id = ?", (n["id"],)).fetchall()
        for s in srows:
            lat, lon = road.point_on_span(span["span"], f"{n['id']}:{s['ts']:.3f}:")
            conn.execute("UPDATE sightings SET lat=?, lon=? WHERE id=?",
                         (lat, lon, s["id"]))
        conn.commit()
        print(f"   re-placed {len(srows)} sightings\n")

    # An empty-string plate hash counts as a distinct vehicle in
    # COUNT(DISTINCT). Rows written before the ingest fix still carry it.
    if args.apply:
        fixed = conn.execute(
            "UPDATE sightings SET plate_hash = NULL WHERE plate_hash = ''").rowcount
        conn.commit()
        if fixed:
            print(f"cleared {fixed} empty-string plate hashes to NULL")


if __name__ == "__main__":
    main()
