"""A queryable index of the training bank, because the bank outgrew the walk.

    python tools/bank_index.py            # incremental update
    python tools/bank_index.py --full     # re-read every sidecar
    python tools/bank_index.py --stats    # what is in there
    python tools/bank_index.py --prune    # drop rows whose files are gone

WHY THIS EXISTS

`labelbank.items()` reads and JSON-parses every sidecar in the bank, and its
docstring says "cheap enough at this scale". That was true when it was written:
the bank held about 30,000 crops. Measured 2026-08-17 it holds **718,389**, and
one items() call costs **4.5 minutes**. Every mode in next_item() calls it, and
so does stats(), so /api/bank/next and /api/bank/stats each pay it - which means
the labelling UI is not slow, it is unusable, and the queue this project needs
most is the one nobody can open.

The fix is not to make the walk faster. It is to stop doing it per request.

WHAT THIS IS NOT

🚨 **THE SIDECAR IS THE TRUTH AND THIS IS A CACHE.** Every field here is copied
from a sidecar and can be rebuilt from one. Nothing is ever written to the bank
from this index. If the two disagree the sidecar wins, and the repair is to
rebuild rather than to reconcile.

That distinction is what makes an index safe to add to a project whose whole
argument is that its claims can be checked: this file cannot invent a label, it
can only be out of date about one - and being out of date is recoverable in a
way that inventing is not.

INCREMENTAL BY MTIME, NOT BY PRESENCE

The obvious incremental rule - "skip stems already indexed" - would be wrong,
because a sidecar is rewritten when it is LABELLED. set_label updates this index
directly, but it is not the only writer: box verdicts, sync_review_labels and
any hand edit all touch sidecars too. Comparing the file's mtime against the
one recorded here catches every writer without trusting any of them to
cooperate, and costs one stat per file.

⚠️ A ROW HERE DOES NOT PROVE THE CROP STILL EXISTS. Crops are deleted by
purge_bank_window and friends, and this index will happily keep pointing at
them. Callers must check the image before serving it - `labelbank.image_path`
already does - and `--prune` clears them out properly.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA  # noqa: E402

BANK = DATA / "training"
INDEX = DATA / "bank_index.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS crops(
  day          TEXT NOT NULL,
  stem         TEXT NOT NULL,
  mtime        REAL,
  ts           REAL,
  node_id      TEXT,
  source       TEXT,
  sampling     TEXT,
  cls_name     TEXT,
  label        TEXT,
  labelled_at  REAL,
  vocab        INTEGER,
  clip_vclass  TEXT,
  clip_conf    REAL,
  clip_margin  REAL,
  head_conf    REAL,
  head_gov     INTEGER,
  sighting_id  INTEGER,
  PRIMARY KEY (day, stem)
);
CREATE INDEX IF NOT EXISTS crops_label   ON crops(label);
CREATE INDEX IF NOT EXISTS crops_clip    ON crops(clip_vclass);
CREATE INDEX IF NOT EXISTS crops_head    ON crops(head_conf);
CREATE INDEX IF NOT EXISTS crops_source  ON crops(source);
CREATE INDEX IF NOT EXISTS crops_ts      ON crops(ts);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(INDEX)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def read() -> sqlite3.Connection:
    """A READER. Does not run the schema script, so it takes no write lock.

    🚨 connect() calls executescript, and executescript begins a transaction
    even when every statement is CREATE ... IF NOT EXISTS and nothing changes.
    So a reader using it blocks behind a running index build and fails with
    'database is locked' - which is exactly what happened the first time the
    labelling queue was opened while tools/bank_index.py was still going.
    A queue that dies whenever the index is being refreshed is not usable.
    """
    db = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


# Each queue is a WHERE plus an ORDER BY, so it belongs in the database rather
# than in 635,000 Python dicts. Loading them all cost 6s per click; these
# answer in milliseconds off the indexes.
#
# ⚠️ `label IS NULL OR label = ''` in every unlabelled queue: the walk wrote
# `""` for an unlabelled crop and the index copies whatever the sidecar says,
# so testing `IS NULL` alone silently skips the older half of the bank.
_UNLABELLED = "(label IS NULL OR label = '')"
_GOVCLASS = "clip_vclass IN ('police','gov_dot','emergency')"

QUERIES = {
    # CLIP says government, the head refused it. Most confident first.
    "gap": ("SELECT * FROM crops WHERE " + _UNLABELLED + " AND " + _GOVCLASS +
            " AND head_conf IS NOT NULL AND head_conf < :thr"
            " ORDER BY clip_conf DESC LIMIT :n"),
    # Highest government confidence first, whatever the head thought.
    "likely": ("SELECT * FROM crops WHERE " + _UNLABELLED + " AND " + _GOVCLASS +
               " ORDER BY clip_conf DESC LIMIT :n"),
    # Closest to CLIP's own boundary. Crops with no CLIP block are EXCLUDED:
    # they have no margin, so they would score as maximally uncertain and fill
    # a queue that is supposed to be about the boundary.
    "hunt": ("SELECT * FROM crops WHERE " + _UNLABELLED +
             " AND clip_margin IS NOT NULL"
             " ORDER BY clip_margin ASC LIMIT :n"),
    # Unbiased. The ONLY mode whose labels may be quoted as precision or recall.
    "review": ("SELECT * FROM crops WHERE " + _UNLABELLED +
               " ORDER BY RANDOM() LIMIT :n"),
    # Crops from other people's nodes, oldest first.
    "remote": ("SELECT * FROM crops WHERE " + _UNLABELLED +
               " AND source = 'remote_node' ORDER BY ts ASC LIMIT :n"),
}


def pick(mode: str, thr: float = 0.45, n: int = 1) -> list[sqlite3.Row]:
    """First n rows of a queue, straight from the index."""
    q = QUERIES.get(mode)
    if not q:
        return []
    db = read()
    try:
        return list(db.execute(q, {"thr": thr, "n": n}))
    finally:
        db.close()


def counts(thr: float = 0.45) -> dict:
    """How many are waiting in each queue. One row, not 635,000."""
    db = read()
    try:
        out = {}
        for mode, q in QUERIES.items():
            if mode == "review":
                continue
            # ⚠️ STRIP THE ORDER BY AS WELL AS THE LIMIT. Counting a sorted
            # subquery makes SQLite materialise and sort every matching row -
            # 100k+ of them - to produce one integer, which took long enough
            # that the first version of this call had to be killed. Order is
            # meaningless to a COUNT.
            body = q.split(" ORDER BY ")[0]
            cq = "SELECT COUNT(*) c FROM (" + body + ")"
            out[mode] = db.execute(cq, {"thr": thr, "n": 1}).fetchone()["c"]
        out["unlabelled"] = db.execute(
            "SELECT COUNT(*) c FROM crops WHERE " + _UNLABELLED).fetchone()["c"]
        out["labelled"] = db.execute(
            "SELECT COUNT(*) c FROM crops WHERE NOT " + _UNLABELLED).fetchone()["c"]
        return out
    finally:
        db.close()


def _row_from(day: str, stem: str, mtime: float, d: dict) -> tuple:
    clip = d.get("clip") or {}
    hg = clip.get("head_gov")
    return (
        day, stem, mtime, d.get("ts"), d.get("node_id"), d.get("source"),
        d.get("sampling"), d.get("cls_name"), d.get("label"),
        d.get("labelled_at"), int(d.get("label_vocab") or 1),
        clip.get("vclass"), clip.get("conf"), clip.get("margin"),
        clip.get("head_conf"), (None if hg is None else int(bool(hg))),
        d.get("sighting_id"),
    )


INSERT = ("INSERT OR REPLACE INTO crops(day,stem,mtime,ts,node_id,source,"
          "sampling,cls_name,label,labelled_at,vocab,clip_vclass,clip_conf,"
          "clip_margin,head_conf,head_gov,sighting_id) "
          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def update_one(db: sqlite3.Connection, day: str, stem: str) -> None:
    """Re-read one sidecar into the index. Used by labelbank.set_label."""
    p = BANK / day / f"{stem}.json"
    try:
        st = p.stat()
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    db.execute(INSERT, _row_from(day, stem, st.st_mtime, d))
    db.commit()


def build(full: bool = False, quiet: bool = False) -> dict:
    db = connect()
    known = {}
    if not full:
        for r in db.execute("SELECT day, stem, mtime FROM crops"):
            known[(r["day"], r["stem"])] = r["mtime"] or 0.0
    t0 = time.time()
    seen = added = updated = skipped = broken = 0
    batch = []
    for d in sorted(x for x in BANK.glob("*") if x.is_dir()):
        day = d.name
        for j in d.glob("*.json"):
            seen += 1
            stem = j.stem
            try:
                mt = j.stat().st_mtime
            except OSError:
                continue
            prev = known.get((day, stem))
            if prev is not None and mt <= prev + 0.001:
                skipped += 1
                continue
            try:
                doc = json.loads(j.read_text(encoding="utf-8"))
            except Exception:
                broken += 1
                continue
            batch.append(_row_from(day, stem, mt, doc))
            if prev is None:
                added += 1
            else:
                updated += 1
            if len(batch) >= 5000:
                db.executemany(INSERT, batch)
                db.commit()
                batch = []
                if not quiet:
                    print("  ... %d seen, %d new, %d changed (%.0fs)"
                          % (seen, added, updated, time.time() - t0))
    if batch:
        db.executemany(INSERT, batch)
    db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('built_at',?)",
               (str(time.time()),))
    db.commit()
    out = {"seen": seen, "added": added, "updated": updated,
           "skipped": skipped, "broken": broken,
           "seconds": round(time.time() - t0, 1),
           "rows": db.execute("SELECT COUNT(*) c FROM crops").fetchone()["c"]}
    db.close()
    return out


def prune(quiet: bool = False) -> int:
    """Drop rows whose sidecar is gone. Separate from build on purpose: a
    missing file during a build is far more likely to be a race with the
    puller writing than a deletion."""
    db = connect()
    gone = []
    for r in db.execute("SELECT day, stem FROM crops"):
        if not (BANK / r["day"] / f"{r['stem']}.json").exists():
            gone.append((r["day"], r["stem"]))
    db.executemany("DELETE FROM crops WHERE day=? AND stem=?", gone)
    db.commit()
    db.close()
    if not quiet:
        print("pruned %d row(s) whose sidecar is gone" % len(gone))
    return len(gone)


def stats() -> None:
    if not INDEX.exists():
        print("no index yet - run: python tools/bank_index.py")
        return
    db = connect()
    n = db.execute("SELECT COUNT(*) c FROM crops").fetchone()["c"]
    built = db.execute("SELECT v FROM meta WHERE k='built_at'").fetchone()
    print("rows: %d" % n)
    if built:
        age = (time.time() - float(built["v"])) / 60
        print("built: %s (%.0f min ago)"
              % (time.strftime("%Y-%m-%d %H:%M",
                               time.localtime(float(built["v"]))), age))
    print("size : %.1f MB" % (INDEX.stat().st_size / 1048576))
    print()
    print("--- labels ---")
    for r in db.execute("SELECT COALESCE(label,'<unlabelled>') l, COUNT(*) c "
                        "FROM crops GROUP BY l ORDER BY c DESC"):
        print("  %-16s %d" % (r["l"], r["c"]))
    print()
    print("--- source ---")
    for r in db.execute("SELECT COALESCE(source,'<none>') s, COUNT(*) c "
                        "FROM crops GROUP BY s ORDER BY c DESC LIMIT 8"):
        print("  %-16s %d" % (r["s"], r["c"]))
    print()
    print("--- head score present ---")
    r = db.execute("SELECT SUM(head_conf IS NOT NULL) h, COUNT(*) c "
                   "FROM crops").fetchone()
    print("  %d of %d" % (r["h"] or 0, r["c"]))
    print()
    print("--- THE GAP: CLIP called it government, the head refused ---")
    for lo in (0.5, 0.7, 0.85):
        r = db.execute(
            "SELECT COUNT(*) c FROM crops WHERE label IS NULL "
            "AND clip_vclass IN ('police','gov_dot','emergency') "
            "AND clip_conf >= ? AND head_conf IS NOT NULL AND head_conf < 0.45",
            (lo,)).fetchone()
        print("  clip_conf >= %.2f : %d unlabelled" % (lo, r["c"]))
    r = db.execute(
        "SELECT COUNT(*) c FROM crops WHERE label IS NULL "
        "AND head_conf >= 0.45 AND clip_vclass = 'civilian'").fetchone()
    print("  the other direction (head yes, CLIP civilian): %d" % r["c"])
    db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--full", action="store_true",
                    help="re-read every sidecar, not just changed ones")
    ap.add_argument("--stats", action="store_true", help="show what is indexed")
    ap.add_argument("--prune", action="store_true",
                    help="drop rows whose sidecar no longer exists")
    a = ap.parse_args()
    if a.stats:
        return stats()
    if a.prune:
        return prune() and None
    print("indexing %s" % BANK)
    out = build(full=a.full)
    print()
    print("seen %(seen)d, new %(added)d, changed %(updated)d, "
          "unchanged %(skipped)d, unreadable %(broken)d" % out)
    print("rows now %(rows)d, took %(seconds)ss" % out)


if __name__ == "__main__":
    main()
