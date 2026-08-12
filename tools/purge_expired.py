"""Enforce the retention promise on a machine that cannot call /api/purge.

    python3 tools/purge_expired.py            # dry run: what WOULD be deleted
    python3 tools/purge_expired.py --apply    # delete it

🚨 WHY THIS HAD TO EXIST.
`privacy.purge_expired` has always been the code that keeps the About page's
promise - civilian sightings deleted after `civilian_retention_days`. Its only
caller was `/api/purge`, and `mirror.route_allowed` 404s `/api/purge` on a
mirror by design. The PUBLIC BOX is a mirror. So on the one machine holding
the public's data, the endpoint that enforces the promise does not exist, and
nothing was scheduled to call it anyway.

Nothing was overdue when this was written - the network was days old and the
oldest private row was 1.6 days - which is exactly why it was worth writing
then rather than after the promise had already been broken. A retention
promise is only false on the day the first row outlives it, and it fails
silently on that day.

Run it from cron on the box. It is safe to run often: it deletes only rows
already past their window, and a run with nothing to do reports zeroes.

    17 3 * * *  cd /opt/sparrowmap && .venv/bin/python tools/purge_expired.py --apply >> logs/purge.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                       # noqa: E402
import privacy                                  # noqa: E402
from core import CONFIG, SNAPS                  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete")
    a = ap.parse_args()

    conn = db.connect()
    now = time.time()
    priv_days = float(CONFIG.get("civilian_retention_days", 14) or 0)
    pub_days = float(CONFIG.get("public_retention_days", 0) or 0)

    def count(where: str, cutoff: float) -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM sightings WHERE {where} AND ts < ?",
            (cutoff,)).fetchone()[0]

    priv_due = count("tier != 'public'", now - priv_days * 86400) if priv_days > 0 else 0
    pub_due = count("tier = 'public'", now - pub_days * 86400) if pub_days > 0 else 0

    oldest = conn.execute(
        "SELECT MIN(ts) FROM sightings WHERE tier != 'public'").fetchone()[0]
    age = (now - oldest) / 86400.0 if oldest else 0.0

    report = {
        "civilian_retention_days": priv_days,
        "public_retention_days": pub_days or "kept indefinitely",
        "private_due_for_deletion": priv_due,
        "public_due_for_deletion": pub_due,
        "oldest_private_row_days": round(age, 2),
        # The number that matters for a promise nobody has tested yet: how long
        # until the first row outlives its window, if this never runs.
        "days_until_promise_breaks": (round(priv_days - age, 2)
                                      if priv_days > 0 and priv_due == 0 else 0),
        "applied": bool(a.apply),
    }

    if a.apply:
        report["result"] = privacy.purge_expired(conn)

    print(json.dumps(report, indent=1, default=str))
    if not a.apply:
        print("\ndry run - nothing deleted. Re-run with --apply.")


if __name__ == "__main__":
    main()
