"""The three figures on sparrowmap.com, computed from the live database.

🚨 THE PUBLIC SITE HAS BEEN SERVING STALE NUMBERS SINCE IT LAUNCHED.
`landing/index.html` hardcodes 1,811 vehicles / 25 confirmed / 0 plates. Those
were true when the page was written and are not now (measured 2026-08-10:
3,478 sightings, 47 confirmed). On a page whose entire argument is *we do not
publish unverified claims*, publishing a figure that quietly drifts is the worst
available failure - and nothing on the page announces that it is a snapshot.

⚠️ FIGURE ONE IS DISTINCT PASSES, NOT SIGHTINGS, AND SWAPPING THEM WOULD BE
CHEATING IN THE FLATTERING DIRECTION. The page says "1,811 vehicles have driven
past" beside "that is 2,811 recorded sightings, because one car crossing the
view is often caught more than once". The raw sighting count is the larger and
more impressive number and it is not what the sentence claims. Same 10-second
grouping as labelbank._pass_count and train/fit_local.pass_groups, so all three
agree by construction rather than by coincidence.

Only aggregates leave this machine. No positions, no per-node breakdown, no
timestamps - a count of vehicles seen cannot be turned back into who they were.

Usage:
    python tools\\public_stats.py                 # print the JSON
    python tools\\public_stats.py --out stats.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "sparrow.db"
PASS_GAP = 10.0


def stats() -> dict:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    one = lambda q: conn.execute(q).fetchone()[0]

    sightings = one("select count(*) from sightings")

    # Distinct vehicle passes: same node, within ten seconds. The tracker loses
    # and re-acquires a vehicle, so one car crossing the view banks several
    # rows; counting rows would inflate the headline by ~55%.
    passes, last = 0, None
    for r in conn.execute("select node_id, ts from sightings order by node_id, ts"):
        node, ts = r["node_id"] or "", float(r["ts"] or 0)
        if last is None or node != last[0] or ts - last[1] > PASS_GAP:
            passes += 1
        last = (node, ts)

    confirmed = one("select count(*) from sightings "
                    "where tier='public' and reviewed='confirmed'")
    private_rows = one("select count(*) from sightings where tier='private'")
    # THE PROMISE, CHECKED RATHER THAN ASSERTED. If this is ever non-zero the
    # page must not render, because the sentence it supports would be false.
    private_plates = one("select count(*) from sightings where tier='private' "
                         "and plate_text is not null and plate_text<>''")
    nodes = one("select count(*) from nodes")
    conn.close()

    return {
        "vehicles_seen": passes,
        "sightings": sightings,
        "confirmed_public": confirmed,
        "private_seen": private_rows,
        "private_plates_stored": private_plates,
        "cameras": nodes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    s = stats()

    # 🚨 REFUSE TO PUBLISH A BROKEN PROMISE. The site's strongest claim is that
    # no private plate is ever stored. If that stops being true, the correct
    # behaviour is to fail loudly here, not to publish a number that makes the
    # sentence beside it a lie.
    if s["private_plates_stored"] != 0:
        print(f"REFUSING: {s['private_plates_stored']} private rows hold plate "
              f"text. The site claims 0. Fix the data or the claim.",
              file=sys.stderr)
        return 1

    out = json.dumps(s, indent=1)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
        print(f"{a.out} written")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
