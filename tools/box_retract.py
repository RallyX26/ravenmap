"""Retract published sightings on the public mirror, by id.

For auditing head-published contributions after the fact. A retraction does not
delete the row (the vehicle really did pass) - it demotes it to the private tier
and reclassifies it, which is what the map should have said. The plate goes with
it; a private row never holds one.

    # on the box, or over ssh:
    /opt/sparrowmap/.venv/bin/python tools/box_retract.py 31382 31390
    # list what the head has published (for review):
    /opt/sparrowmap/.venv/bin/python tools/box_retract.py --list
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


def list_published() -> None:
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, node_id, vclass, vclass_conf, ts, vclass_why FROM sightings "
        "WHERE tier='public' AND reviewed='confirmed' "
        "AND vclass_why LIKE '%home head%' ORDER BY ts DESC LIMIT 200").fetchall()
    if not rows:
        print("no head-published contributions found")
        return
    for r in rows:
        d = dict(r)
        print(f"#{d['id']}  {d['vclass']} "
              f"conf={d['vclass_conf']}  {d['vclass_why']}")
    print(f"\n{len(rows)} head-published contribution(s)")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list":
        list_published()
        return
    for a in args:
        try:
            sid = int(a)
        except ValueError:
            print(f"skip {a!r}: not an id")
            continue
        db.review_sighting(sid, "retracted")
        print(f"retracted #{sid}")


if __name__ == "__main__":
    main()
