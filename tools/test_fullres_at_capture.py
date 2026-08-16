"""The camera is asked for the good picture, and only for a PUBLISHED vehicle.

🚨 THE PREVIOUS TEST PASSED WHILE THE FEATURE WAS DEAD. tools/test_fullres_on_
confirm.py drives attach_confirmed_photo directly, which proves the function
works and says nothing about whether anything ever calls it. Measured on the
live map 2026-08-16: every published photo was still 200px, because the only
caller needed a field that only the drive popup ever sends.

So this test asks the question that actually mattered: given a row in the
database, does db.wants_fullres ASK for it? And does it refuse to ask about a
private one?

    python tools/test_fullres_at_capture.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sparrow-fullres-"))
    import os
    os.environ["SPARROW_DB"] = str(tmp / "t.db")

    import db
    db.init()
    conn = db.connect()

    node_id = "n:testcam"
    conn.execute(
        "INSERT INTO nodes (id, name, token, status, lat, lon) "
        "VALUES (?,?,?,?,?,?)",
        (node_id, "test", "tok", "active", 42.0, -83.0))

    def add(tier: str, snap: str, full: str | None = None, dt: float = 0.0) -> int:
        cur = conn.execute(
            "INSERT INTO sightings (node_id, ts, tier, vclass, snap, snap_full) "
            "VALUES (?,?,?,?,?,?)",
            (node_id, db.now() - dt, tier, "police", snap, full))
        conn.commit()
        return int(cur.lastrowid)

    pub = add("public", "small.jpg")
    priv = add("private", "small.jpg")
    done = add("public", "small.jpg", full="big.jpg")
    old = add("public", "small.jpg", dt=db.FULLRES_WINDOW_S + 60)

    want = db.wants_fullres(node_id)
    check("asks for a PUBLISHED sighting", pub in want, f"want={want}")
    check("never asks about a PRIVATE one", priv not in want,
          "this is the whole plate guarantee")
    check("does not ask twice", done not in want, "snap_full already set")
    check("forgets old ones", old not in want,
          f"older than {db.FULLRES_WINDOW_S:.0f}s")

    db.mark_fullres(pub, "big2.jpg")
    check("mark_fullres records it",
          (db.sighting(pub) or {}).get("snap_full") == "big2.jpg")
    check("and it stops being asked for", pub not in db.wants_fullres(node_id))

    other = db.wants_fullres("n:someoneelse")
    check("another camera is never offered these", not other, f"{other}")

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
