"""Can a volunteer who has ONLY their camera key get everything back?

🚨 THIS IS THE UNTECH-SAVVY PATH, AND IT IS THE ONE THAT MUST NOT BREAK.
Somebody whose phone lost its storage holds, at best, the camera key they saved.
Their reviewer token is gone and CANNOT be given back - it is stored as a hash.
So /signin signs the camera in and then asks for a reviewer token on their
behalf, reissuing when the old one cannot be shown. That is two chained
requests, a fallback, and a revocation, and none of it is visible if it silently
does nothing: the person lands on a working camera and an empty review queue,
which looks exactly like having no work to review.

    python tools\\test_signin_recovery.py

Real HTTP against a real hub on a SCRATCH data directory. It is not run against
the live box on purpose: `regenerate` REVOKES the current reviewer token, so
testing it there would sign the operator out of his own review page.
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

SCRATCH = Path(tempfile.mkdtemp(prefix="sparrow_signin_"))

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

import hub  # noqa: E402

if Path(db.DB_PATH) == REAL_DB:
    print("REFUSING TO RUN: the database still points at live data.")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    sys.exit(2)

PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"
UA = "SparrowMap-test/1.0"
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAIL.append(name)


def call(path: str, body=None, token: str = "", method: str = "POST"):
    hdrs = {"User-Agent": UA}
    data = None
    if body is not None:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if token:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, method=method, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:
            return e.code, {}


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

    # ⚠️ kind='phone', not 'fixed'. Enrolling a FIXED camera makes the server
    # snap it to a road, which calls Overpass over the network - so the test
    # hung for 25s on somebody else's public API before failing. A phone gets
    # no span (nodes.enroll skips it deliberately), and nothing here is about
    # spans.
    st, nd = call("/api/enroll", {"name": "recovery test", "lat": 42.5,
                                  "lon": -83.7, "kind": "phone"})
    if not nd.get("token"):
        check("enrolled a camera", False, str(st))
        return 1
    nid, key = nd["id"], nd["token"]
    conn = db.connect()
    conn.execute("UPDATE nodes SET status='active' WHERE id=?", (nid,))
    conn.commit()
    check("enrolled a camera", True, nid)

    # --- 1. the camera key alone identifies the camera -------------------
    st, who = call("/api/node/whoami", {"node_id": nid}, token=key)
    check("the camera key signs the camera in", st == 200 and who.get("id") == nid,
          f"{st} {who.get('name')}")

    # --- 2. 🚨 THE TOKEN ALREADY EXISTS, AND THAT IS THE WHOLE POINT ------
    # This test first asserted that asking would ISSUE one. It does not:
    # /api/enroll mints the reviewer token when the camera is created, so by
    # the time anybody signs back in there is always one on record - stored as
    # a hash, so it can never be handed back.
    #
    # That makes `regenerate` not a rare fallback but THE recovery path, taken
    # by essentially every returning volunteer. Worth having found by testing
    # rather than by a volunteer hitting an empty review queue.
    first = nd.get("review_token")
    check("enrolment already issued a reviewer token", bool(first),
          "issued at enrol" if first else "none")

    st, t2 = call("/api/rv/my-token", {"node_id": nid, "scope": "own"}, token=key)
    check("so asking for it returns NO token (it is stored as a hash)",
          st == 200 and not t2.get("review_token"), str(t2.get("note")))

    st, t3 = call("/api/rv/my-token",
                  {"node_id": nid, "scope": "own", "regenerate": True}, token=key)
    fresh = t3.get("review_token")
    check("regenerate issues a fresh one", st == 200 and bool(fresh),
          "issued" if fresh else str(t3))
    check("and it is genuinely a different token", bool(fresh) and fresh != first)

    # --- 4. the fresh token has to actually work -------------------------
    st, me = call("/api/rv/me", token=fresh, method="GET")
    check("🎯 the fresh reviewer token opens the review queue",
          st == 200 and me.get("ok") is True, f"{st} scope={me.get('scope')}")

    # --- 5. and the old one must be DEAD ---------------------------------
    # 🚨 A token that was lost to somebody else must not stay live. If this
    # passes while the old one still works, the "refresh" is cosmetic.
    st, old = call("/api/rv/me", token=first, method="GET")
    check("the replaced token is revoked, not left live", st == 401, str(st))

    # --- 6. nobody else's key may fetch this camera's token --------------
    st2, nd2 = call("/api/enroll", {"name": "someone else", "lat": 41.0,
                                    "lon": -80.0, "kind": "phone"})
    if nd2.get("token"):
        conn.execute("UPDATE nodes SET status='active' WHERE id=?", (nd2["id"],))
        conn.commit()
        st, bad = call("/api/rv/my-token", {"node_id": nid, "scope": "own"},
                       token=nd2["token"])
        check("another camera's key cannot fetch this camera's token",
              st == 401 and not bad.get("review_token"), str(st))

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
