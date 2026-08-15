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
  * idempotent: cameras already registered under a given node name are
    skipped - COUNTED, not merely detected, because one name can legitimately
    cover several cameras (see the `have` comment). Re-running never
    duplicates and never leaves a shortfall; duplicate cameras are already the
    single biggest data-quality problem on this map;
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
    offered = pc.SOURCES[a.source]()
    # ⚠️ THE SOURCE ITSELF SERVES THE SAME PICTURE TWICE. Iowa publishes some
    # RWIS snapshots under both `/Public/` and `/public/`, and both were
    # registered, putting two nodes on one mast. Deduped before anything is
    # created, because a duplicate node is the hardest thing here to undo.
    cams = pc.dedupe_index(offered)
    dropped = len(offered) - len(cams)
    print(f"  {len(cams)} camera(s) offered"
          + (f" ({dropped} dropped as the same image under another URL)"
             if dropped else ""))

    # 🚨 HOW MANY ARE REGISTERED UNDER THAT NAME, NOT WHETHER ANY IS.
    #
    # Even the full name is not unique. Iowa runs three cameras at one rest area
    # - ENTRY, CENTER and EXIT - sharing coordinates, device_id and description,
    # so 101 names cover 499 real cameras. A set says "registered" after the
    # first of them, and the other two would never be created; the run would
    # report success having quietly enrolled a third of that rest area.
    #
    # Counting instead makes this idempotent AND complete: register the
    # shortfall, whatever it is, and nothing on a re-run.
    have: dict = {}
    for n in db.nodes():
        nm = n.get("name") or ""
        if nm.startswith(PREFIX):
            have[nm] = have.get(nm, 0) + 1

    # 🚨 THE NAME IS THE DURABLE LINK AND IT IS BUILT IN EXACTLY ONE PLACE.
    #
    # Neither half of it is unique alone. Fintraffic reuses preset names across
    # stations ("Tienpinta" at almost every site), so the human part cannot be
    # the key; Iowa reuses device_id across views, so the source ref cannot be
    # either. pc.node_name_for is the one definition - the poller has to rebuild
    # this identical string to find the credentials again, and a second copy of
    # the formula would drift silently in both directions.
    registered = sum(have.values())
    todo = []
    for c in cams:
        name = pc.node_name_for(c)
        if have.get(name, 0) > 0:
            have[name] -= 1
            continue
        todo.append((name, c))

    print(f"  {registered} already registered, {len(todo)} new")
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
