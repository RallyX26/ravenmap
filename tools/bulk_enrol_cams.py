"""Register thousands of public traffic cameras, on the box, without abuse.

🚨 WHY THIS IS NOT `public_cams.py enrol`.

That command posts to /api/enroll one camera at a time with a 0.5s pause. It is
the right shape for eight cameras and the wrong shape for five thousand: 45
minutes of wall clock, and it would consume the enrol rate limit that exists to
stop a runaway client minting cameras - the exact limit that once locked the
operator out of registering a real one.

So this runs ON THE BOX and calls nodes.enroll directly. Same function, same
validation, same records; it simply does not travel over the network to reach
itself, and therefore does not pretend to be five thousand strangers signing up.

⚠️ SAFETY, because this writes thousands of rows:
  * dry run unless --apply, printing exactly what it would create;
  * --limit caps every run, so a mistake is small and bounded;
  * idempotent: a camera already registered (matched on its source ref, which
    is recorded in the node name) is skipped, so re-running never duplicates -
    duplicate cameras are already the single biggest data-quality problem on
    this map;
  * snap_road=False. A public camera gets no span (see nodes.enroll), so there
    is no road lookup at all - which is what makes this finish at all rather
    than issuing one Overpass query per camera.

    python tools/bulk_enrol_cams.py --source fi --limit 50
    python tools/bulk_enrol_cams.py --source fi --limit 3000 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db                       # noqa: E402
import nodes as node_mod        # noqa: E402
import public_cams as pc        # noqa: E402

PREFIX = "Public traffic camera - "


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=sorted(pc.SOURCES))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    print(f"fetching the {a.source} index...")
    cams = pc.SOURCES[a.source]()
    print(f"  {len(cams)} camera(s) offered")

    # Already-registered cameras, by the name we give them. The name is the
    # only durable link between a source's camera and our node id.
    have = set()
    for n in db.nodes():
        nm = n.get("name") or ""
        if nm.startswith(PREFIX):
            have.add(nm)

    # 🚨 THE NAME IS THE DURABLE LINK AND IT IS BUILT IN EXACTLY ONE PLACE.
    #
    # Neither half of it is unique alone. Fintraffic reuses preset names across
    # stations ("Tienpinta" at almost every site), so the human part cannot be
    # the key; Iowa publishes several directional views per device_id, so the
    # source ref cannot be either. Together they are unique, and the poller has
    # to rebuild the identical string to find these credentials again - so
    # pc.node_name_for is the one definition and nobody writes a second.
    todo = []
    for c in cams:
        name = pc.node_name_for(c)
        if name in have:
            continue
        todo.append((name, c))

    print(f"  {len(have)} already registered, {len(todo)} new")
    batch = todo[:a.limit]
    print(f"  this run would create {len(batch)}")

    if not a.apply:
        for name, c in batch[:10]:
            print(f"    {name[:64]}  @{c['lat']:.4f},{c['lon']:.4f}")
        if len(batch) > 10:
            print(f"    ... and {len(batch) - 10} more")
        print("\nDRY RUN - nothing written. Re-run with --apply.")
        return 0

    made = failed = 0
    for name, c in batch:
        try:
            node_mod.enroll(name=name, lat=c["lat"], lon=c["lon"],
                            kind="public_cam", reach_m=60, snap_road=False)
            made += 1
        except Exception as exc:
            failed += 1
            if failed <= 5:
                print(f"    FAILED {name[:50]}: {str(exc)[:80]}")
        if made and made % 250 == 0:
            print(f"    ...{made} registered")

    print(f"\nregistered {made}, failed {failed}")
    print(f"nodes now: {len(db.nodes())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
