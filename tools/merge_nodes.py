"""Fold a duplicate node into the one it should have been.

A phone that registered twice appears on the map as two cameras. The cause is
fixed in the setup mode of public/app.html (the existing node_id is sent back,
so re-registering updates in place), but the duplicates already created have to
be merged by hand.

Merging rather than deleting, because the duplicate may carry sightings and a
sighting is a record of something that really happened - throwing it away to
tidy the camera list would be losing data to fix a display problem.

    python tools\\merge_nodes.py --keep n_abc12345 --drop n_def67890
    python tools\\merge_nodes.py ... --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db   # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", required=True)
    ap.add_argument("--drop", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.keep == args.drop:
        raise SystemExit("--keep and --drop are the same node")

    conn = db.connect()
    keep, drop = db.node(args.keep), db.node(args.drop)
    for label, n, nid in (("keep", keep, args.keep), ("drop", drop, args.drop)):
        if not n:
            raise SystemExit(f"no such node: {nid}")
        cnt = conn.execute("SELECT COUNT(*) FROM sightings WHERE node_id = ?",
                           (nid,)).fetchone()[0]
        print(f"  {label:<5} {nid}  \"{n['name']}\"  kind={n['kind']}  "
              f"{cnt} sightings")

    moving = conn.execute("SELECT COUNT(*) FROM sightings WHERE node_id = ?",
                          (args.drop,)).fetchone()[0]
    print(f"\nwould move {moving} sightings to {args.keep} and delete {args.drop}")

    if not args.apply:
        print("[--apply to write]")
        return

    conn.execute("UPDATE sightings SET node_id = ? WHERE node_id = ?",
                 (args.keep, args.drop))
    # The counter is denormalised on the node row, so recompute rather than add
    # - the two numbers can already disagree if anything was ever purged.
    total = conn.execute("SELECT COUNT(*) FROM sightings WHERE node_id = ?",
                         (args.keep,)).fetchone()[0]
    conn.execute("UPDATE nodes SET sightings = ? WHERE id = ?", (total, args.keep))
    conn.execute("DELETE FROM nodes WHERE id = ?", (args.drop,))
    conn.commit()
    db.audit("merge_nodes", f"{args.drop} -> {args.keep}", actor="operator")
    print(f"merged. {args.keep} now has {total} sightings; {args.drop} deleted.")
    print("\n⚠️  The dropped node's TOKEN is gone with it. Any device still")
    print("    holding it will get 401 on its next submission and must")
    print("    re-register - which now updates in place instead of duplicating.")


if __name__ == "__main__":
    main()
