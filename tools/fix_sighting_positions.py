"""Move already-stored sightings off their node's TRUE position.

Sightings were being written at the camera's exact coordinates while the node
itself was published jittered, so /api/sightings leaked the very position the
jitter existed to hide. Rows written before that fix still carry the true
coordinates and have to be moved, not just left for the retention window to
clear - they are being served right now.

🚨 SUPERSEDED. DO NOT RUN THIS. IT WILL DAMAGE THE MAP.

Two faults, and the line that used to sit here claimed the opposite of one:

  * NOT IDEMPOTENT, AND NOT CORRECT EVEN ONCE. It computes ONE position per
    node, OUTSIDE the row loop, and writes that single point to every sighting
    that node ever recorded - collapsing its whole history onto one dot. That
    is the "one dot in a park with dozens of passes behind it" the rest of this
    codebase exists to prevent. nodes.sighting_position is seeded PER SIGHTING
    for exactly that reason; calling it once and reusing the answer throws the
    seeding away. A dry run right now proposes moving 3,730 sightings onto a
    single coordinate.

  * The leak it was written for is fixed at the source: _ingest has called
    nodes.sighting_position per row since, so nothing is stored at a camera's
    true position any more. It reports "0 sitting on the TRUE position".

If historical rows ever need re-placing, use tools/resnap_nodes.py - it seeds
each sighting individually. This file is kept so the incident stays legible.

    python tools\fix_sighting_positions.py --dry-run
    python tools\fix_sighting_positions.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                          # noqa: E402
import nodes as node_mod           # noqa: E402
from core import haversine_m       # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--i-understand-this-collapses-history", action="store_true",
                    dest="ack", help=argparse.SUPPRESS)
    a = ap.parse_args()

    # Refuses rather than warns, and refuses --dry-run too: a dry run is
    # precisely what somebody would try first to see whether this is still
    # needed, and its output reads like a plan worth carrying out.
    if not a.ack:
        raise SystemExit(
            "REFUSING. This writes ONE position to every sighting a node "
            "ever recorded, collapsing its entire history onto a single "
            "dot. The leak it was written for is fixed at the source. "
            "Use tools/resnap_nodes.py, which seeds each sighting "
            "individually.")

    conn = db.connect()
    moved = leaked = 0

    for nd in db.nodes(active_only=False):
        target = node_mod.sighting_position(nd)
        rows = conn.execute(
            "SELECT id, lat, lon FROM sightings WHERE node_id = ?",
            (nd["id"],)).fetchall()
        if not rows:
            continue

        exposed = sum(1 for r in rows
                      if haversine_m(r["lat"], r["lon"], nd["lat"], nd["lon"]) < 5)
        leaked += exposed
        print(f"  {nd['id']}  {nd['name']}")
        print(f"    {len(rows)} sightings, {exposed} sitting on the TRUE position")
        print(f"    moving to {target[0]:.6f}, {target[1]:.6f} "
              f"({haversine_m(*target, nd['lat'], nd['lon']):.0f} m from true)")

        if not a.dry_run:
            conn.execute(
                "UPDATE sightings SET lat = ?, lon = ? WHERE node_id = ?",
                (target[0], target[1], nd["id"]))
            moved += len(rows)

    if a.dry_run:
        print(f"\n  dry run: {leaked} sightings currently expose a true camera position")
        return

    conn.commit()
    print(f"\n  moved {moved} sightings ({leaked} had been exposing a true position)")


if __name__ == "__main__":
    main()
