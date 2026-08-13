"""Point a camera at the road its owner says it watches.

    python3 tools/set_aim.py --list
    python3 tools/set_aim.py --node n_0f9b78ab --heading 180
    python3 tools/set_aim.py --node n_0f9b78ab --heading 180 --apply

WHY A HUMAN SHOULD JUST SAY
The span is chosen automatically, and every automatic answer has been wrong in
a different way. Nearest-road put it down a driveway. Nearest-real-road put it
on a street perpendicular to the traffic. Inferring the road from the direction
vehicles travel is sound in principle and fails on real history: a camera that
was moved between windows during testing has passes from BOTH aims averaged
into one axis, so the inference confidently describes a camera position that
no longer exists.

The owner is looking out of the window. They know. `road.resolve` has always
had the right machinery - span_from_ways walks each road and keeps the stretch
that falls inside the camera's cone, which is a far better question than "what
is closest" - and it has simply never had a heading to work with, because a
browser in a stationary window cannot report a compass bearing. 25 of 28 nodes
sit at heading 0.

So: let them set it, and use the aim path that was built for it.

    heading is degrees CLOCKWISE FROM NORTH, the direction the camera FACES.
        0 = north      90 = east      180 = south      270 = west

Prints the road it resolves to before writing, because "which road did that
give me" is the only check that matters and it should not need a map to answer.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                        # noqa: E402
import nodes as nodes_mod                        # noqa: E402
import road                                      # noqa: E402

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg: float) -> str:
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show every node and its aim")
    ap.add_argument("--node", help="node id, or a unique part of its name")
    ap.add_argument("--heading", type=float, help="degrees clockwise from north")
    ap.add_argument("--fov", type=float, default=None, help="override field of view")
    ap.add_argument("--reach", type=float, default=None,
                    help="how far down the street the camera can actually see, metres")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    all_nodes = db.nodes()
    if a.list or not a.node:
        print(f"{'node':<14} {'name':<26} {'heading':>8}  {'reach':>6}  watched road")
        for n in all_nodes:
            sp = nodes_mod.span_of(n)
            h = float(n.get("heading") or 0)
            print(f"{n['id'][:14]:<14} {(n['name'] or '')[:26]:<26} "
                  f"{h:6.0f}{compass(h):>3}  {float(n.get('reach_m') or 0):5.0f}m  "
                  f"{'set' if sp else 'NO SPAN'}")
        if not a.node:
            print("\nPick one with --node and give it --heading (0=N 90=E 180=S 270=W).")
        return

    match = [n for n in all_nodes
             if n["id"] == a.node or a.node.lower() in (n["name"] or "").lower()]
    if len(match) != 1:
        print(f"--node matched {len(match)} nodes; be more specific")
        return
    nd = match[0]

    if a.heading is None:
        print(f"{nd['name']}: heading is currently "
              f"{float(nd.get('heading') or 0):.0f} ({compass(float(nd.get('heading') or 0))}). "
              f"Give --heading to change it.")
        return

    heading = a.heading % 360.0
    fov = a.fov if a.fov is not None else float(nd.get("fov") or 60.0)
    # 🚨 REACH IS WHY THE AIM PATH NEVER FIRES.
    # span_from_ways only keeps road points inside the cone AND within reach.
    # This camera had reach 28 m while its nearest street is ~30 m away, so the
    # cone stopped short of the road, matched nothing, and resolve() fell back
    # to nearest-road every time - which is how a driveway and then a
    # perpendicular street came to be published. Setting a truthful reach is
    # what lets the aim actually be used.
    reach = a.reach if a.reach is not None else float(nd.get("reach_m") or 40.0)

    # The aim path, not the proximity path: span_from_ways keeps the stretch of
    # road that actually falls inside the cone, and only falls back to nearest
    # if the cone catches nothing.
    got = road.resolve(nd["lat"], nd["lon"], heading, fov, reach, online=True)
    if not got:
        # Better to say so than to write a heading that resolves to nothing:
        # the whole point of this tool is printing the road before writing.
        sys.exit(f"{nd['name']} ({nd['id']}): no road could be resolved at that "
                 f"aim - the lookup may be rate-limited. Nothing written; try "
                 f"again shortly.")

    print(f"{nd['name']}  ({nd['id']})")
    print(f"   heading   {float(nd.get('heading') or 0):.0f} ({compass(float(nd.get('heading') or 0))})"
          f"  ->  {heading:.0f} ({compass(heading)})")
    print(f"   fov {fov:.0f} deg, reach {reach:.0f} m")
    print(f"   resolves to: {got.get('road_name') or '(unnamed road)'}"
          f"   [{got.get('source')}]  watched {got.get('watched_m')} m")
    if got.get("source") == "aim":
        print("   ⚠️  no road matched the cone - this is the camera's own aim axis,"
              " not a road. Check the heading.")

    if not a.apply:
        print("\ndry run - nothing written. Re-run with --apply.")
        return

    sp = got["span"]
    conn = db.connect()
    conn.execute("UPDATE nodes SET heading=?, reach_m=?, span_lat1=?, span_lon1=?, "
                 "span_lat2=?, span_lon2=? WHERE id=?",
                 (heading, reach, sp[0][0], sp[0][1], sp[1][0], sp[1][1], nd["id"]))
    conn.commit()

    fresh = db.node(nd["id"])
    n = 0
    for row in conn.execute("SELECT id FROM sightings WHERE node_id=?",
                            (nd["id"],)).fetchall():
        lat2, lon2 = nodes_mod.sighting_position(
            fresh, None, None, seed=f"{nd['id']}:{row['id']}")
        conn.execute("UPDATE sightings SET lat=?, lon=? WHERE id=?",
                     (lat2, lon2, row["id"]))
        n += 1
    conn.commit()
    print(f"\nheading set and {n} sightings re-placed onto the span.")


if __name__ == "__main__":
    main()
