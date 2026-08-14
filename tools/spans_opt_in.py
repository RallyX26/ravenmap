"""Turn OFF span publication for every camera, so it becomes opt-in.

    python tools/spans_opt_in.py            # report only
    python tools/spans_opt_in.py --apply    # switch them all off

🚨 WHY THIS EXISTS. The stretch of road a camera watches was published for
every node unconditionally, and the "watched roads" checkbox on the map only
ever decided whether it was DRAWN - `curl /api/nodes` returned all of them
regardless. So no camera owner has ever been asked, and the answer was assumed
to be yes for all of them.

Adding the column does most of the work: it is NULL on every existing row and
NULL reads as off. But NULL is "never asked" and 0 is "answered no", and only
one of those is a decision. Anything explicitly set to 1 before this ran was
set by code that predates consent, so it is cleared too - and the point of the
sweep is to be able to SAY the map publishes no span nobody agreed to, which
is a claim about a measured state rather than about a default.

⚠️ This deliberately does not exempt the operator's own cameras. He can turn
his back on from /aim in a few seconds, and a sweep with an exception in it
cannot be described honestly in one sentence.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import nodes as node_mod   # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without it this only reports")
    a = ap.parse_args()

    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name, kind, publish_span, road_name FROM nodes").fetchall()]

    on = [r for r in rows if r.get("publish_span")]
    with_span = [r for r in rows if node_mod.span_of(db.node(r["id"])) is not None]

    print(f"cameras:                {len(rows)}")
    print(f"have a span computed:   {len(with_span)}")
    print(f"currently PUBLISHING:   {len(on)}")
    for r in on[:20]:
        print(f"   {r['id']}  {r.get('name') or '?'}  ({r.get('road_name') or 'no road'})")
    if len(on) > 20:
        print(f"   ... and {len(on) - 20} more")

    if not a.apply:
        print("\nreport only - pass --apply to switch them off")
        return

    cur = conn.execute("UPDATE nodes SET publish_span=0")
    conn.commit()
    print(f"\nset publish_span=0 on {cur.rowcount} camera(s)")

    left = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE publish_span IS NOT NULL "
        "AND publish_span<>0").fetchone()[0]
    # Verified, not assumed. The whole value of the sweep is being able to state
    # the resulting number rather than the intention behind the UPDATE.
    print(f"still publishing after the sweep: {left}  "
          f"{'OK' if left == 0 else 'PROBLEM'}")


if __name__ == "__main__":
    main()
