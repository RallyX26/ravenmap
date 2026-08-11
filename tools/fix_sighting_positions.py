"""Move already-stored sightings off their node's TRUE position.

Sightings were being written at the camera's exact coordinates while the node
itself was published jittered, so /api/sightings leaked the very position the
jitter existed to hide. Rows written before that fix still carry the true
coordinates and have to be moved, not just left for the retention window to
clear - they are being served right now.

Idempotent: a row already at the published-derived position does not move.

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
    a = ap.parse_args()

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
