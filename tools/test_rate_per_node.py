"""Does one busy camera still lock everybody else out?

🚨 THE BUG THIS COVERS WAS INVISIBLE AND EXPENSIVE.
Caddy strips X-Forwarded-For on purpose, so every caller looks like 127.0.0.1
and a bucket keyed on the address is one bucket for the whole project. The
sightings cap read as "600 per caller per hour" and behaved as "600 for
everyone on earth". Measured on the live box: the busiest hour carried 1,267
sightings and 40 requests were refused in a day - volunteers losing passes,
with no node outbox to retry them.

    python tools\\test_rate_per_node.py

Real HTTP against a real hub on a SCRATCH data directory. Three claims:
  1. a camera that floods is refused,
  2. a DIFFERENT camera is unaffected while that is happening,
  3. naming somebody else's camera cannot spend their budget.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="sparrow_rate_"))

import core        # noqa: E402
import db          # noqa: E402
import mirror      # noqa: E402
import snapshot    # noqa: E402
import review_api  # noqa: E402

REAL_DB = Path(db.DB_PATH)
for _p in ("snaps", "evidence", "held", "review", "inbox", "tiles"):
    (SCRATCH / _p).mkdir(parents=True, exist_ok=True)
for _mod, _name, _val in [
        (core, "DATA", SCRATCH), (core, "SNAPS", SCRATCH / "snaps"),
        (core, "EVIDENCE", SCRATCH / "evidence"), (core, "HELD", SCRATCH / "held"),
        (core, "DB_PATH", SCRATCH / "sparrow.db"),
        (db, "DB_PATH", SCRATCH / "sparrow.db"),
        (snapshot, "SNAPS", SCRATCH / "snaps"),
        (review_api, "SNAPS", SCRATCH / "snaps"),
        (review_api, "EVIDENCE", SCRATCH / "evidence"),
        (review_api, "HELD", SCRATCH / "held"),
        (mirror, "DATA", SCRATCH), (mirror, "REVIEW", SCRATCH / "review"),
        (mirror, "INBOX", SCRATCH / "inbox")]:
    setattr(_mod, _name, _val)

import road      # noqa: E402
import hub       # noqa: E402

# ⚠️ NO OVERPASS. _ingest asks which road a sighting is on, and this machine
# cannot reach Overpass - three mirrors at a 25s timeout each, so twenty posts
# took longer than the test's own patience and it "failed" on a network the
# test is not about. Snapping is stubbed rather than waited on.
road.ways_for_cell = lambda *a, **k: []

if Path(db.DB_PATH) == REAL_DB:
    print("REFUSING TO RUN: the database still points at live data.")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    sys.exit(2)

# A small per-camera cap so the test is quick. The SHAPE is what is under test,
# not the production number.
hub.RATE["/api/sightings"] = (12, 3600)

PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
UA = "SparrowMap-test/1.0"
FAIL = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAIL.append(name)


def call(path, body=None, token=""):
    hdrs = {"User-Agent": UA}
    data = None
    if body is not None:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if token:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, method="POST", data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        e.read()
        return e.code


def enrol(name, lat, lon):
    hdrs = {"User-Agent": UA, "Content-Type": "application/json"}
    req = urllib.request.Request(
        BASE + "/api/enroll", method="POST", headers=hdrs,
        data=json.dumps({"name": name, "lat": lat, "lon": lon,
                         "kind": "phone"}).encode())
    with urllib.request.urlopen(req, timeout=25) as r:
        nd = json.loads(r.read())
    c = db.connect()
    c.execute("UPDATE nodes SET status='active' WHERE id=?", (nd["id"],))
    c.commit()
    return nd["id"], nd["token"]


def sighting(nid):
    return {"node_id": nid, "ts": time.time(), "source": "phone_node",
            "body": "car", "det_conf": 0.9, "plate_text": "", "plate_conf": 0,
            "evidence": {}, "lat": 42.5, "lon": -83.7}


def main() -> int:
    print(f"scratch: {SCRATCH}\n")
    srv = hub.ThreadingHTTPServer(("127.0.0.1", PORT), hub.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(60):
        try:
            urllib.request.urlopen(urllib.request.Request(
                BASE + "/api/health", headers={"User-Agent": UA}), timeout=2).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        print("server never came up")
        return 2

    busy, busy_tok = enrol("busy street", 42.5, -83.7)
    quiet, quiet_tok = enrol("quiet street", 41.0, -80.0)
    print(f"busy={busy}  quiet={quiet}\n")

    # --- 1. flood from ONE camera until it is refused --------------------
    codes = [call("/api/sightings", sighting(busy), busy_tok) for _ in range(20)]
    accepted = sum(1 for c in codes if c == 200)
    refused = sum(1 for c in codes if c == 429)
    check("a flooding camera is eventually refused", refused > 0,
          f"{accepted} accepted, {refused} refused of 20")

    # --- 2. 🎯 THE WHOLE POINT: everybody else is unaffected -------------
    other = [call("/api/sightings", sighting(quiet), quiet_tok) for _ in range(5)]
    check("🎯 a DIFFERENT camera still posts fine while that one is capped",
          all(c == 200 for c in other), str(other))

    # --- 3. naming someone else's camera must not spend their budget ----
    # 🚨 The reason the auth had to move BEFORE the charge. Keying on an id
    # taken from an unauthenticated body would turn this route into a targeted
    # denial of service against any camera whose id is printed on the map.
    fresh, fresh_tok = enrol("victim", 40.0, -75.0)
    bad = [call("/api/sightings", sighting(fresh), "wrong-token-entirely")
           for _ in range(20)]
    check("a wrong token is rejected, not rate-limited",
          all(c == 401 for c in bad), f"{set(bad)}")
    still = [call("/api/sightings", sighting(fresh), fresh_tok) for _ in range(5)]
    check("🎯 and the victim's own budget is untouched",
          all(c == 200 for c in still), str(still))

    # --- 4. the global ceiling still exists ------------------------------
    check("a network-wide ceiling is configured too",
          "_all_sightings" in hub.RATE, str(hub.RATE.get("_all_sightings")))

    # --- 5. the table evicts by age instead of wiping itself -------------
    # 🚨 `_HITS.clear()` on overflow reset every counter, so the limiter
    # stopped limiting exactly when it was busiest.
    src = open(ROOT / "hub.py", encoding="utf-8").read()
    check("the hit table never calls clear() on overflow",
          "_HITS.clear()" not in src)

    srv.shutdown()
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
