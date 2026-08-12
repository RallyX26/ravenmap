"""Put already-filed public flags in front of a reviewer.

    python3 tools/park_open_reports.py            # dry run
    python3 tools/park_open_reports.py --apply

WHY THIS EXISTS
/api/report used to end at the `reports` table. Its only reader was the
operator queue, which is local-only and absent from a public mirror, so on the
live site every flag landed where nothing running there could display it. The
endpoint now parks each new flag in the review pen; this catches the ones filed
before it did.

Idempotent: park_reported() refuses a sighting already in the pen, so running
this twice queues nothing twice. It also refuses anything not on the public
tier and anything with no stored photo - a sighting with no picture cannot be
re-judged by eye, and it says so rather than queueing a blank card.

Run it ON the box, where the pen and the snapshots are.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                      # noqa: E402
import review_api                              # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually park them")
    a = ap.parse_args()

    counts = db.open_report_counts()
    if not counts:
        print("no open reports")
        return

    print(f"{len(counts)} sighting(s) with open flags\n")
    parked = skipped = 0
    for sid in sorted(counts):
        row = db.sighting(int(sid))
        flags = db.reports_for(int(sid))
        reason = flags[0].get("reason") if flags else ""
        tier = (row or {}).get("tier")
        snap = (row or {}).get("snap")
        already = (review_api.mirror.REVIEW / f"{int(sid)}.json").exists()

        why = None
        if not row:
            why = "no such sighting"
        elif tier != "public":
            why = f"tier is {tier} - already off the public map"
        elif not snap:
            why = "no stored photo to re-judge"
        elif already:
            why = "already in the pen"

        if why:
            print(f"  {sid:<8} SKIP   {why}")
            skipped += 1
            continue

        if a.apply:
            ok = review_api.park_reported(int(sid), dict(row), reason)
            print(f"  {sid:<8} {'PARKED' if ok else 'FAILED'}  {counts[sid]} flag(s), {reason}")
            parked += bool(ok)
        else:
            print(f"  {sid:<8} would park   {counts[sid]} flag(s), {reason}")
            parked += 1

    print(f"\n{'parked' if a.apply else 'would park'}: {parked}   skipped: {skipped}")
    if not a.apply:
        print("dry run - nothing written. Re-run with --apply.")


if __name__ == "__main__":
    main()
