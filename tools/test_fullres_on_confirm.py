"""Does confirming a vehicle actually publish a FULL-RESOLUTION photograph?

🚨 THIS TESTS THE OUTCOME, NOT THE CODE PATH. The bug it exists for was not a
crash - every part of the confirm flow "worked", the popup closed, the vehicle
appeared on the map, and the photograph was a 160x61 smudge. So the assertion
here is on the pixels of the file the map ends up serving, measured with PIL,
because that is the only statement anybody actually cares about.

    python tools\\test_fullres_on_confirm.py

Runs entirely against a scratch data directory - it never touches the live box
and never opens a socket. What it drives is review_api.attach_confirmed_photo,
which is the single function both confirm paths now go through.
"""

from __future__ import annotations

import io

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Every path in this project is bound at import time - `from core import SNAPS`
# copies the Path object into the importing module, so reassigning core.SNAPS
# alone would leave snapshot.py still writing into the real directory. So each
# binding is redirected by name, and then CHECKED, because a redirect that
# silently missed one is how a test ends up deleting live photographs.
SCRATCH = Path(tempfile.mkdtemp(prefix="sparrow_fullres_"))

import core          # noqa: E402
import db            # noqa: E402
import mirror        # noqa: E402
import snapshot      # noqa: E402
import review_api    # noqa: E402
from PIL import Image  # noqa: E402

REAL_SNAPS = Path(core.SNAPS)
for _mod, _name, _val in [
        (core, "DATA", SCRATCH), (core, "SNAPS", SCRATCH / "snaps"),
        (core, "EVIDENCE", SCRATCH / "evidence"), (core, "HELD", SCRATCH / "held"),
        (snapshot, "SNAPS", SCRATCH / "snaps"),
        (review_api, "SNAPS", SCRATCH / "snaps"),
        (review_api, "EVIDENCE", SCRATCH / "evidence"),
        (review_api, "HELD", SCRATCH / "held"),
        (mirror, "DATA", SCRATCH), (mirror, "REVIEW", SCRATCH / "review")]:
    setattr(_mod, _name, _val)
for _p in (SCRATCH / "snaps", SCRATCH / "evidence", SCRATCH / "held",
           SCRATCH / "review"):
    _p.mkdir(parents=True, exist_ok=True)

# The database is the one thing that cannot be redirected by rebinding a
# constant - db.connect() reads DB_PATH captured at import - so it is copied to
# the scratch directory and db is pointed at the copy.
_scratch_db = SCRATCH / "sparrow.db"
db.DB_PATH = _scratch_db

if Path(snapshot.SNAPS) == REAL_SNAPS or Path(db.DB_PATH).parent != SCRATCH:
    print("REFUSING TO RUN: a path still points at the live data directory.")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    sys.exit(2)

FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAIL.append(name)


def jpeg(w: int, h: int) -> bytes:
    """A picture with structure in it, so a resize is visible in the file size."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 7) % 256, (y * 11) % 256, ((x + y) * 3) % 256)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def snap_size(name: str):
    p = Path(core.SNAPS) / name
    if not p.exists():
        return None
    return Image.open(p).size


def main() -> int:
    print(f"scratch: {SCRATCH}\n")
    db.connect()          # creates the scratch file and applies SCHEMA

    # --- 1. the baseline: what the bug looked like -----------------------
    small = jpeg(200, 95)
    old = snapshot.store_confirmed(
        "data:image/jpeg;base64," + __import__("base64").b64encode(small).decode(),
        {"ts": 1_700_000_000, "node_id": "n_test", "node_name": "test camera",
         "tier": "public", "vclass": "police"}, stamp=False)
    check("a 200px pen crop stores as a 200px photograph",
          max(snap_size(old)) <= 200, str(snap_size(old)))

    # --- 2. the fix: a confirmed full-res crop replaces it ---------------
    sid = 987654
    conn = db.connect()
    conn.execute("INSERT OR REPLACE INTO sightings (id, ts, node_id, vclass, "
                 "tier, snap) VALUES (?,?,?,?,?,?)",
                 (sid, 1_700_000_000, "n_test", "police", "public", old))
    conn.commit()
    row = db.sighting(sid)

    full = jpeg(1600, 760)          # what a phone or a camera actually holds
    new = review_api.attach_confirmed_photo(
        sid, row, snapshot.decode_bytes(
            "data:image/jpeg;base64," + __import__("base64").b64encode(full).decode()),
        ts=1_700_000_000, node_name="test camera", vclass="police")

    check("attach_confirmed_photo returned a new snapshot name", bool(new), str(new))
    size = snap_size(new) if new else None
    check("the published photograph is now ABOVE pen resolution",
          bool(size) and max(size) > snapshot.SUBRES_MAX_EDGE, str(size))
    check("and is capped at the store's own ceiling (MAX_EDGE)",
          bool(size) and max(size) <= snapshot.MAX_EDGE,
          f"{size} vs {snapshot.MAX_EDGE}")

    after = db.sighting(sid)
    check("the ROW points at the new file, not the old one",
          after.get("snap") == new, f"{after.get('snap')} == {new}")

    # 🚨 UNLINKED IS NOT DELETED. /snap/<name> serves any file in SNAPS by name
    # with no reference to a row, so leaving the 200px original in place would
    # leave it reachable for ever.
    check("the photograph it replaced is GONE from the snaps directory",
          not (Path(core.SNAPS) / old).exists(), old)

    # --- 3. a corrupt upload must not DESTROY the good photograph --------
    # 🚨 THE CASE THAT NEARLY SHIPPED. The store-failed fallback wrote whatever
    # bytes it was handed straight into SNAPS, so unopenable bytes became the
    # published photo AND took the full-resolution one with them - a corrupt
    # upload doing more damage than no upload at all.
    row2 = db.sighting(sid)
    kept = review_api.attach_confirmed_photo(sid, row2, b"not a jpeg at all",
                                             ts=1_700_000_000, vclass="police")
    still = db.sighting(sid)
    check("garbage bytes attach nothing", kept is None, str(kept))
    check("the row still points at the good full-res photograph",
          still.get("snap") == new, f"{still.get('snap')} == {new}")
    check("and that file is still on disk",
          bool(snap_size(new)), str(snap_size(new)))

    # --- 4. no crop at all is a no-op, not a crash ----------------------
    none_out = review_api.attach_confirmed_photo(sid, db.sighting(sid), None)
    check("no crop attaches nothing and returns None", none_out is None)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    sys.exit(code)
