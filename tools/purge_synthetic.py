"""Remove every simulated sighting and simulated node from the database.

The simulator built the entire system before any camera existed and was worth
every line. It becomes a liability the instant the map is shown to anyone,
because a synthetic sighting with a photograph attached is indistinguishable
from a real one - and the only thing this project actually sells is that what
it shows is true.

Everything the simulator writes carries source='synthetic', so it can always be
found and removed. Its cameras are matched by the fixed names in
sources/synthetic.py rather than by guesswork.

    python tools\purge_synthetic.py --dry-run
    python tools\purge_synthetic.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                # noqa: E402
from core import SNAPS   # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="wipe EVERYTHING, including hand-posted test sightings. "
                         "Test data posted from a fake node with stock photos is "
                         "not 'real' just because source != synthetic.")
    a = ap.parse_args()

    if a.all and not a.dry_run:
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
        nn = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        conn.execute("DELETE FROM sightings")
        conn.execute("DELETE FROM nodes")
        conn.execute("DELETE FROM audit")
        conn.commit()
        gone = 0
        for f in SNAPS.glob("*.jpg"):
            try:
                f.unlink()
                gone += 1
            except OSError:
                pass
        conn.execute("VACUUM")
        print(f"  WIPED: {n} sightings, {nn} nodes, {gone} snapshots")
        print(f"  {db.stats()}")
        return

    conn = db.connect()
    cur = conn.cursor()

    n_sight = cur.execute(
        "SELECT COUNT(*) FROM sightings WHERE source = 'synthetic'").fetchone()[0]
    snaps = [r[0] for r in cur.execute(
        "SELECT snap FROM sightings WHERE source = 'synthetic' AND snap IS NOT NULL"
    ).fetchall()]

    # Simulator cameras: the fixed site list in sources/synthetic.py.
    from sources.synthetic import CAMERA_SITES
    sim_names = tuple(name for name, *_ in CAMERA_SITES)
    marks = ",".join("?" for _ in sim_names)
    n_nodes = cur.execute(
        f"SELECT COUNT(*) FROM nodes WHERE name IN ({marks})", sim_names).fetchone()[0]

    real_sight = cur.execute(
        "SELECT COUNT(*) FROM sightings WHERE source != 'synthetic'").fetchone()[0]
    real_nodes = cur.execute(
        f"SELECT COUNT(*) FROM nodes WHERE name NOT IN ({marks})", sim_names).fetchone()[0]

    print(f"  synthetic sightings : {n_sight}")
    print(f"  synthetic snapshots : {len(snaps)}")
    print(f"  synthetic nodes     : {n_nodes}")
    print(f"  REAL sightings kept : {real_sight}")
    print(f"  REAL nodes kept     : {real_nodes}")

    if a.dry_run:
        print("\n  dry run, nothing deleted")
        return

    cur.execute("DELETE FROM sightings WHERE source = 'synthetic'")
    cur.execute(f"DELETE FROM nodes WHERE name IN ({marks})", sim_names)
    # Audit entries about fake vehicles (decisions, flags) are fiction too, and
    # would otherwise show up on the transparency page's decision log.
    cur.execute("DELETE FROM audit WHERE ts < (SELECT COALESCE(MIN(ts), 0) "
                "FROM sightings WHERE source != 'synthetic')")
    conn.commit()

    gone = 0
    for name in snaps:
        f = SNAPS / name
        try:
            if f.exists():
                f.unlink()
                gone += 1
        except OSError:
            pass

    cur.execute("VACUUM")
    print(f"\n  deleted {n_sight} sightings, {n_nodes} nodes, {gone} snapshots")
    print(f"  remaining: {db.stats()}")


if __name__ == "__main__":
    main()
