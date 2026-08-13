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

🚨 THE CLAIM IS NOT ONLY THE LINE. IT IS ALSO THE LABEL.
This cleared span_lat/span_lon and left `road_name` and `span_source` behind,
so after a successful run the four driving sessions kept publishing a street
name they do not watch - and /api/nodes serves road_name, which app.js prints
beside the camera name ("Driving 2026-08-12 · North Wilhite Street"). The map
drew nothing, everything LOOKED clean, and the false claim went on being made
in text. It also made the tool blind to its own leftovers: `has_span` was
false once the coordinates were gone, so a re-run reported "clear: 0" with the
labels still there, which reads as "nothing to do" rather than "half done".

So the test is now "does this node make a road claim AT ALL", and clearing
removes every part of it.
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
        # A road claim is the drawn span OR the published name of the road.
        # Either one on its own still tells a visitor this camera watches that
        # street, so either one is enough to qualify.
        has_span = bool(nodes_mod.span_of(nd))
        has_claim = has_span or bool(nd.get("road_name") or nd.get("span_source"))

        if mobile and silent and n_sight == 0 and has_claim:
            hits.append((nd["id"], name, nd.get("road_name"), not has_span))
        elif has_claim:
            kept += 1

    print(f"nodes claiming a road: {len(hits) + kept}")
    print(f"  keep  : {kept}")
    print(f"  clear : {len(hits)}   (driving session, no heartbeat, no sightings)")
    for nid, name, rname, label_only in hits:
        tail = "  [label only - span already cleared]" if label_only else ""
        print(f"     {nid[:14]}  {name}  ->  {rname or '(no road name)'}{tail}")

    if not a.apply:
        print("\ndry run - nothing written. Re-run with --apply.")
        return

    for nid, *_ in hits:
        conn.execute("UPDATE nodes SET span_lat1=NULL, span_lon1=NULL, "
                     "span_lat2=NULL, span_lon2=NULL, road_name=NULL, "
                     "span_source=NULL WHERE id=?", (nid,))
    conn.commit()
    print(f"\ncleared {len(hits)} phantom road claims. Node rows kept - only "
          f"the claim is gone, span and road name both.")


if __name__ == "__main__":
    main()
