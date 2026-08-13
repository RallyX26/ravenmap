"""Take the watched-road spans off nodes that do not watch a road.

    python3 tools/clear_phantom_spans.py           # dry run
    python3 tools/clear_phantom_spans.py --apply

🚨 A MOVING CAMERA HAS NO WATCHED ROAD.
Driving mode enrols a fresh node per session (drive.html names them
"Driving <date>"), and resnap_nodes.py - which was written for FIXED cameras -
gave every node in the table a span. So the map drew a stretch of road that a
dashcam supposedly watches, at whatever point that session happened to start.
It is a claim about a street that nobody is watching.

It also inflates the camera count, which matters more than it sounds: the
header said 28 cameras while four of them had never sent a heartbeat or a
single sighting. A project whose whole argument is that it does not overstate
should not overstate its own size.

WHAT IT TOUCHES, narrowly and on purpose:
  * a node whose name marks it as a driving session, AND
  * which has never produced a sighting, AND
  * which has never sent a heartbeat.

All three, so a real fixed camera that merely had a quiet night is never
caught by it. The node row is KEPT - deleting it would break any token issued
against it - only the span is cleared, which is the thing being falsely drawn.

Database only. No restart, nothing goes offline.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                        # noqa: E402
import nodes as nodes_mod                        # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    conn = db.connect()
    conn.row_factory = sqlite3.Row
    hits, kept = [], 0

    for nd in db.nodes():
        name = (nd["name"] or "")
        n_sight = conn.execute("SELECT COUNT(*) FROM sightings WHERE node_id=?",
                               (nd["id"],)).fetchone()[0]
        mobile = name.lower().startswith("driving")
        silent = not nd.get("last_beat")
        has_span = bool(nodes_mod.span_of(nd))

        if mobile and silent and n_sight == 0 and has_span:
            hits.append((nd["id"], name))
        elif has_span:
            kept += 1

    print(f"nodes with a span: {len(hits) + kept}")
    print(f"  keep  : {kept}")
    print(f"  clear : {len(hits)}   (driving session, no heartbeat, no sightings)")
    for nid, name in hits:
        print(f"     {nid[:14]}  {name}")

    if not a.apply:
        print("\ndry run - nothing written. Re-run with --apply.")
        return

    for nid, _ in hits:
        conn.execute("UPDATE nodes SET span_lat1=NULL, span_lon1=NULL, "
                     "span_lat2=NULL, span_lon2=NULL WHERE id=?", (nid,))
    conn.commit()
    print(f"\ncleared {len(hits)} phantom spans. Node rows kept - only the "
          f"road claim is gone.")


if __name__ == "__main__":
    main()
