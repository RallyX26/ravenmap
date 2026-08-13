"""Remove the cameras nobody meant to create.

    python tools/clear_duplicate_nodes.py --box root@HOST --key path/to/key
    python tools/clear_duplicate_nodes.py --box ... --key ... --apply

WHY THESE EXIST
Registering took a second or two and the button said nothing while it worked,
so people tapped again - and every tap minted a whole camera. "Green St &
Wright St" was registered twice, two seconds apart, by someone who made one
camera. "3162" five times in 26 seconds.

The button is fixed. This clears what it already made.

🚨 THE VISIBLE DAMAGE IS A GHOST ROAD. Each duplicate got its own road snap
from the same unaimed heading, and they do not agree: one Green St & Wright St
row was drawn along the north/south street the camera actually watches, its
twin across the east/west one. A volunteer sees two watched roads and one
camera, and the map is asserting something about a street nobody is looking at.

WHAT IT DELETES, narrowly and on purpose:
  * a node created within 2 minutes of another with the SAME name, AND
  * which has never sent a heartbeat, AND
  * which has never produced a sighting, AND
  * where a sibling in that same burst DID one of those things.

The last condition is the important one. Without it, a person whose camera
genuinely never started would have their whole registration deleted; with it,
the row that is doing the work always survives and only its stillborn twins go.
If every row in a burst is equally dead, nothing is touched - that is a
different problem (see tools/setup_funnel.py) and deleting is not its fix.

Deleting the ROW, not just the span: unlike a driving session, nothing holds
these tokens. The phone kept whichever response arrived last, so the earlier
duplicates are unreachable by the person who made them, and leaving them
inflates the camera count with cameras that can never do anything.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

WINDOW_S = 120.0


def ssh(args, cmd: str) -> str:
    return subprocess.run(
        ["ssh", "-i", args.key, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", args.box, cmd],
        capture_output=True, text=True, timeout=90).stdout


def bursts(rows: list) -> list:
    """Group rows into same-name registrations made within WINDOW_S."""
    by: dict = {}
    for r in rows:
        by.setdefault((r.get("name") or "").strip(), []).append(r)
    out = []
    for _, rs in by.items():
        rs.sort(key=lambda x: x.get("created") or 0)
        run = [rs[0]]
        for r in rs[1:]:
            if (r.get("created") or 0) - (run[-1].get("created") or 0) < WINDOW_S:
                run.append(r)
            else:
                out.append(run)
                run = [r]
        out.append(run)
    return [g for g in out if len(g) > 1]


def alive(r: dict) -> bool:
    return bool(r.get("beats") or r.get("sightings") or r.get("last_beat"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--remote", default="/opt/sparrowmap")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    out = ssh(a, f"cd {a.remote} && ./.venv/bin/python -c \""
                 "import db,json;"
                 "c=db.connect();"
                 "print(json.dumps([dict(r) for r in c.execute("
                 "\\\"SELECT id,name,created,beats,sightings,last_beat,road_name "
                 "FROM nodes\\\")]))\"").strip()
    try:
        rows = json.loads(out.splitlines()[-1]) if out else []
    except Exception:
        print(f"could not read nodes: {out[:300]}", file=sys.stderr)
        sys.exit(2)

    doomed, kept_groups = [], 0
    for g in bursts(rows):
        live = [r for r in g if alive(r)]
        dead = [r for r in g if not alive(r)]
        if not live or not dead:
            # Nothing did anything, or everything did. Neither is a duplicate
            # this tool can safely resolve.
            continue
        kept_groups += 1
        keeper = max(live, key=lambda r: (r.get("sightings") or 0,
                                          r.get("beats") or 0))
        print(f"\n{g[0]['name']!r}  ({len(g)} registrations in "
              f"{max(x['created'] for x in g) - min(x['created'] for x in g):.0f}s)")
        print(f"   KEEP   {keeper['id']}  beats={keeper.get('beats') or 0} "
              f"sightings={keeper.get('sightings') or 0} "
              f"road={keeper.get('road_name') or '-'}")
        for r in dead:
            print(f"   DELETE {r['id']}  beats=0 sightings=0 "
                  f"road={r.get('road_name') or '-'}")
            doomed.append(r["id"])

    if not doomed:
        print("no resolvable duplicates found")
        return

    print(f"\n{len(doomed)} camera(s) to delete across {kept_groups} burst(s)")
    if not a.apply:
        print("dry run - nothing written. Re-run with --apply.")
        return

    # ⚠️ THE ID LIST GOES THROUGH THE ENVIRONMENT, NOT INTO THE COMMAND STRING.
    # The first version interpolated json.dumps(ids) into a double-quoted shell
    # argument containing a double-quoted python -c payload. The quotes in the
    # JSON closed the shell string, the remote python never ran, and the tool
    # printed nothing and reported success - the ghost was still on the map
    # afterwards, which is the only reason it was caught.
    ids = ",".join(doomed)          # ids are n_ + hex; nothing to escape
    res = ssh(a, f"cd {a.remote} && SPARROW_DEL={ids} ./.venv/bin/python -c \""
                 "import os,db;"
                 "ids=[i for i in os.environ['SPARROW_DEL'].split(',') if i];"
                 "c=db.connect();"
                 "[c.execute('DELETE FROM nodes WHERE id=?', (i,)) for i in ids];"
                 "c.commit();"
                 "print('deleted', len(ids))\"").strip()
    if not res:
        print("REMOTE DELETE PRODUCED NO OUTPUT - nothing was deleted",
              file=sys.stderr)
        sys.exit(2)
    print(res)


if __name__ == "__main__":
    main()
