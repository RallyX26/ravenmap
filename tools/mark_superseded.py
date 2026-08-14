"""Retire node rows that a camera has already moved on from.

    python tools/mark_superseded.py            # report only
    python tools/mark_superseded.py --apply

🚨 WHAT THIS IS NOT. It is not a duplicate merge and it must never become one.
A camera that moves more than MOVED_THRESHOLD_M is DELIBERATELY a new node
(nodes.enroll): moving a device must not drag its history and its watched road
onto a street it never saw. The old row is a true record of a true period and
it stays, with its sightings attached to it.

What was missing is any mark saying the old row is no longer a CAMERA. So it
kept beating until the device adopted its new id, and counted as online the
whole time - one physical camera at Ross street appeared three times, all
reporting in. enroll() now writes `superseded_by` at the moment it splits; this
backfills the rows that split before it did.

📌 THE REGISTERED TOTAL DOES NOT MOVE. Nothing is deleted and nothing is
merged: `nodes_active` counts registrations and every one of these really was
registered. Only `nodes_online` and the map's camera list change, and both
become more accurate.

⚠️ MATCHED ON NAME + PROXIMITY + HANDOVER, which is evidence and not proof.
Cameras now register an ed25519 key at detector startup (node_key.py), so
`nodes.pubkey` finally identifies a device - but only for nodes that have
STARTED since that shipped. Every row enrolled before it still has no key, and
those are exactly the rows this tool exists to sort out, so the matching stays
circumstantial. The rules below are deliberately narrow, and anything they are
not sure about is left alone and printed.

📌 When enough nodes carry keys, a pubkey match becomes the right test and
should REPLACE these heuristics rather than be added alongside them.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db          # noqa: E402
import nodes as node_mod   # noqa: E402

#: How far apart two rows may be and still be one camera that moved. Generous
#: on purpose - the split already happened, so the question here is only
#: "was this the same camera", and a camera is not carried across town.
MAX_MOVE_M = 500.0
#: The old row must have gone quiet relative to the new one. A camera that is
#: still producing is not retired, whatever its name says.
QUIET_FACTOR = 3.0


def _dist(a: dict, b: dict) -> float:
    if a.get("lat") is None or b.get("lat") is None:
        return float("inf")
    return math.dist((a["lat"], a["lon"]), (b["lat"], b["lon"])) * 111000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    now = time.time()
    rows = db.nodes(active_only=True, include_superseded=True)

    by_name: dict[str, list[dict]] = {}
    for n in rows:
        nm = (n.get("name") or "").strip().lower()
        if not nm:
            continue
        by_name.setdefault(nm, []).append(n)

    retire: list[tuple[dict, dict, float]] = []
    skipped: list[str] = []

    for nm, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        # A driving session is named per day and a test is a test. These are
        # not one camera that moved and must not be retired into each other.
        if nm.startswith("driving ") or nm in ("test", "tests"):
            skipped.append(f"{nm} ({len(group)}) - session/test name, left alone")
            continue
        group.sort(key=lambda n: n.get("last_beat") or 0)
        newest = group[-1]
        for old in group[:-1]:
            if old.get("superseded_by"):
                continue
            d = _dist(old, newest)
            if d > MAX_MOVE_M:
                skipped.append(f"{nm}: {old['id']} is {d:.0f}m from "
                               f"{newest['id']} - too far, left alone")
                continue
            # 🚨 A NODE THAT NEVER BEAT WAS NEVER A CAMERA, SO IT CANNOT HAVE
            # BEEN SUPERSEDED BY ONE.
            # `last_beat or 0` reads "never started" as "last seen in 1970",
            # which made every abandoned enrolment look infinitely stale and
            # therefore retired. Five rows for one address were swept up that
            # way - all of them enrolments that never sent a heartbeat, none of
            # them a camera that moved. They are a different problem with a
            # different fix, they are already absent from nodes_online because
            # they never beat, and calling them superseded would put a wrong
            # reason in the record to tidy a list.
            if not old.get("last_beat"):
                skipped.append(f"{nm}: {old['id']} never beat - never started, "
                               f"not superseded")
                continue
            old_age = now - old["last_beat"]
            new_age = now - (newest.get("last_beat") or 0)
            if not (old_age > new_age * QUIET_FACTOR and old_age > 600):
                skipped.append(f"{nm}: {old['id']} still as active as "
                               f"{newest['id']} - left alone")
                continue
            retire.append((old, newest, d))

    print(f"cameras examined: {len(rows)}")
    print(f"rows to retire:   {len(retire)}\n")
    for old, new, d in retire:
        print(f"  {old['id']} -> {new['id']}  \"{(old.get('name') or '')[:34]}\"")
        # Minutes, not hours. At 1 decimal place every handover inside an hour
        # printed as "0.0h ago", which is how a row that had never beaten at
        # all looked identical to one that beat seconds ago.
        print(f"      {d:.0f}m apart · old last beat "
              f"{(now - old['last_beat'])/60:.0f} min ago, new "
              f"{(now - (new.get('last_beat') or now))/60:.0f} min ago · "
              f"{old.get('sightings') or 0} sightings stay with it")
    if skipped:
        print("\n  left alone:")
        for s in skipped:
            print(f"    {s}")

    before_total = len(db.nodes(active_only=True, include_superseded=True))
    before_online = db.stats().get("nodes_online")
    print(f"\nregistered now: {before_total}   online now: {before_online}")

    if not a.apply:
        print("\n[--apply to write]")
        return

    for old, new, _d in retire:
        db.set_superseded(old["id"], new["id"])
        db.audit("node_superseded", f"{old['id']} -> {new['id']}",
                 actor="operator")

    after_total = len(db.nodes(active_only=True, include_superseded=True))
    after_online = db.stats().get("nodes_online")
    print(f"\nretired {len(retire)} row(s)")
    # The registered total is printed BOTH sides because "this does not change
    # the headline number" is the claim being made, and a claim about a number
    # should be shown rather than asserted.
    print(f"registered: {before_total} -> {after_total}  "
          f"{'unchanged' if before_total == after_total else 'CHANGED - WRONG'}")
    print(f"online:     {before_online} -> {after_online}")


if __name__ == "__main__":
    main()
