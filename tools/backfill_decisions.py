"""Fill in WHO decided each public sighting, and re-queue the ones nobody did.

    python3 tools/backfill_decisions.py                    # dry run
    python3 tools/backfill_decisions.py --apply            # write decided_by
    python3 tools/backfill_decisions.py --apply --requeue  # + park the autos for review

WHY
`decided_by` was added after the fact, so every existing public row reads
'unknown'. /api/policy reports unknown separately rather than folding it into
either side - counting a row nobody can account for as human-decided is exactly
the flattering guess that endpoint exists to stop. This turns unknown into a
real answer for the rows where the reason string still says who acted.

The reason string is the ONLY surviving evidence: `reviewed='confirmed'` is
stamped by both the human path and the auto-publish path, which is why it could
never be used to count this in the first place.

⚠️ Anything whose reason does not clearly show a human is recorded as 'auto',
not left as unknown. When the evidence is ambiguous the honest default is the
less flattering one - a project claiming a person decides must not resolve its
own doubt in its own favour.

--requeue then parks those auto-published rows in the review pen so a human
actually looks at them. They stay on the map while they wait; a reviewer either
confirms them (decided_by becomes 'human') or rejects them, which retracts the
row. Nothing is hidden in the meantime - the claim is about who decided, and
pretending they were never published would be a different lie.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                        # noqa: E402
import review_api                                # noqa: E402

# Phrases that only a human action produces. Each is written by a code path
# that requires a person to have pressed something.
HUMAN_MARKS = (
    "confirmed by reviewer",              # review_api._publish, the /rv buttons
    "by the camera operator",             # labelbank._sync_sighting, camctl
    "confirmed by review",                # box_publish default for box_review.py
    "confirmed as a government vehicle by the camera operator",
)


def decided(why: str) -> str:
    w = (why or "").lower()
    return "human" if any(m in w for m in HUMAN_MARKS) else "auto"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--requeue", action="store_true",
                    help="with --apply: park auto-published rows for review")
    a = ap.parse_args()

    conn = db.connect()
    rows = conn.execute(
        "SELECT id, ts, vclass, vclass_why, snap, node_id, decided_by "
        "FROM sightings WHERE tier='public' ORDER BY ts").fetchall()

    plan = {"human": [], "auto": [], "already": []}
    for r in rows:
        if r["decided_by"]:
            plan["already"].append(r["id"])
            continue
        plan[decided(r["vclass_why"])].append(r)

    print(f"public sightings: {len(rows)}")
    print(f"  already recorded : {len(plan['already'])}")
    print(f"  -> human         : {len(plan['human'])}")
    print(f"  -> auto          : {len(plan['auto'])}")
    if plan["auto"]:
        print("\n  the ones no person decided:")
        for r in plan["auto"][-8:]:
            print(f"    #{r['id']} {time.strftime('%m-%d %H:%M', time.localtime(r['ts']))} "
                  f"{r['vclass']}: {(r['vclass_why'] or '')[:64]}")

    if not a.apply:
        print("\ndry run - nothing written. Re-run with --apply.")
        return

    for kind in ("human", "auto"):
        for r in plan[kind]:
            conn.execute("UPDATE sightings SET decided_by=? WHERE id=?", (kind, r["id"]))
    conn.commit()
    print(f"\nrecorded: {len(plan['human'])} human, {len(plan['auto'])} auto")

    if a.requeue:
        parked = skipped = 0
        for r in plan["auto"]:
            row = db.sighting(int(r["id"]))
            if review_api.park_reported(int(r["id"]), dict(row),
                                        "auto_published_before_human_review"):
                parked += 1
            else:
                skipped += 1
        print(f"re-queued for review: {parked}   could not queue: {skipped}")
        print("(could-not-queue is usually 'no stored photo' - nothing to judge by eye)")

    print()
    for k, v in db.public_decision_counts().items():
        print(f"  {k:<46} {v}")


if __name__ == "__main__":
    main()
