"""Community labelling: the crowd's votes, kept away from everything that matters.

Anyone can label crops here. That is only safe because of what this module is
NOT allowed to do, so those limits are the whole design and they live in one
file rather than being spread through the hub.

🚨 A VOTE CANNOT PUBLISH ANYTHING.

Labelling a crop `police` in the operator's own tool promotes its sighting to
the public tier - deliberately, because a person looked at it. A stranger's
click must never do that. So a vote is written to a SEPARATE DATABASE FILE that
has no sightings table in it at all. It is not a policy that votes do not reach
the map; there is no code path and no schema by which they could.

🚨 A VOTE IS NOT A LABEL.

Nothing here writes to the training bank either. Votes are collected, pulled
home over ssh, and only then turned into labels - on his machine, under
consensus, tagged `sampling='community'` so they train and can never measure.
The crowd produces evidence; the decision stays local.

🚨 THE BUNDLE CARRIES NO IDENTITY.

Crops arrive as opaque random ids with no day, stem, node, timestamp or
coordinate (see tools/export_task.py), and every one is from a PUBLIC traffic
camera rather than a volunteer's window. So the worst a scraper gets is a set of
plate-illegible vehicle crops with nothing to tie them to a place or a time.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from core import DATA

TASK = DATA / "label_task"
VOTES = DATA / "label_votes.db"          # deliberately NOT sparrow.db

VALID = {"police", "gov", "fleet", "civilian", "unsure", "skip"}
_ID = re.compile(r"^[0-9a-f]{16}$")      # exactly what export_task mints

SCHEMA = """
CREATE TABLE IF NOT EXISTS votes(
  id     INTEGER PRIMARY KEY,
  item   TEXT NOT NULL,
  label  TEXT NOT NULL,
  voter  TEXT NOT NULL,
  ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS votes_item  ON votes(item);
CREATE INDEX IF NOT EXISTS votes_voter ON votes(voter);
CREATE UNIQUE INDEX IF NOT EXISTS votes_once ON votes(item, voter);
"""


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(VOTES, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def items() -> list:
    f = TASK / "task.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("items") or []
    except Exception:
        return []


def next_for(voter: str, want: int = 12) -> dict:
    """A batch this voter has not already answered.

    Batched rather than one at a time so the page can preload and stay quick,
    and so a round trip is not paid per decision.
    """
    all_ids = [i["id"] for i in items()]
    if not all_ids:
        return {"items": [], "done": True, "reason": "no task loaded"}
    db = _db()
    seen = {r["item"] for r in db.execute(
        "SELECT item FROM votes WHERE voter = ?", (voter,))}
    # How many people have already answered each one, so the queue can favour
    # the crops that still need agreement rather than piling onto the same few.
    counts = {r["item"]: r["c"] for r in db.execute(
        "SELECT item, COUNT(*) c FROM votes GROUP BY item")}
    db.close()
    todo = [i for i in all_ids if i not in seen]
    todo.sort(key=lambda i: counts.get(i, 0))
    return {"items": todo[:want], "done": not todo,
            "remaining": len(todo), "total": len(all_ids)}


def record(item: str, label: str, voter: str) -> dict:
    if not _ID.match(item or ""):
        return {"error": "bad item"}
    if label not in VALID:
        return {"error": "bad label"}
    if not re.match(r"^[0-9a-z]{8,40}$", voter or ""):
        return {"error": "bad voter"}
    if not (TASK / (item + ".jpg")).exists():
        return {"error": "unknown item"}
    if label == "skip":
        return {"ok": True, "skipped": True}
    db = _db()
    try:
        db.execute("INSERT OR IGNORE INTO votes(item,label,voter,ts) "
                   "VALUES(?,?,?,?)", (item, label, voter, time.time()))
        db.commit()
        n = db.execute("SELECT COUNT(*) c FROM votes WHERE voter = ?",
                       (voter,)).fetchone()["c"]
        tot = db.execute("SELECT COUNT(*) c FROM votes").fetchone()["c"]
    finally:
        db.close()
    return {"ok": True, "yours": n, "everyone": tot}


def image(item: str):
    """Bytes for one crop, or None. The id shape is the whole guard."""
    if not _ID.match(item or ""):
        return None
    p = TASK / (item + ".jpg")
    if not p.exists():
        return None
    return p.read_bytes()


def stats() -> dict:
    db = _db()
    try:
        tot = db.execute("SELECT COUNT(*) c FROM votes").fetchone()["c"]
        voters = db.execute(
            "SELECT COUNT(DISTINCT voter) c FROM votes").fetchone()["c"]
        settled = db.execute(
            "SELECT COUNT(*) c FROM (SELECT item FROM votes GROUP BY item "
            "HAVING COUNT(*) >= 3)").fetchone()["c"]
    finally:
        db.close()
    return {"votes": tot, "voters": voters, "settled": settled,
            "crops": len(items())}
