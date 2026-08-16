"""The signal-partner endpoint: token-gated, and it can never reach a private row.

🚨 THE TEST THAT MATTERS IS THE LAST ONE. A partner reports what they heard; if
correlating that against a PRIVATE sighting were ever possible, this would be a
way to follow an ordinary person by something their car emits - the exact thing
the two-tier design exists to prevent. The predicate lives in SQL so no caller
can forget it, and this proves it.

    python tools/test_signals.py
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
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sparrow-signals-"))
    # 🚨 PATCH db.DB_PATH, NOT AN ENV VAR. core.DB_PATH is a plain constant and
    # nothing reads SPARROW_DB, so setting that environment variable did
    # NOTHING and this test quietly wrote into the real local database. It
    # passed the first time and failed the second on a UNIQUE constraint, which
    # is the only reason it was ever noticed. db.connect() resolves DB_PATH from
    # the module namespace at call time, so rebinding it here really does
    # isolate the run.
    import db
    db.DB_PATH = str(tmp / "t.db")
    import review_auth
    db.init()
    conn = db.connect()

    # --- tokens ------------------------------------------------------------
    tok = "partner-secret-abc"
    pid = db.add_signal_token(review_auth._hash(tok), "Hone (Shin)")
    check("token issues", pid > 0, f"partner id {pid}")
    check("a good token resolves", (db.signal_token(review_auth._hash(tok)) or {})
          .get("label") == "Hone (Shin)")
    check("an unknown token resolves to nothing",
          db.signal_token(review_auth._hash("not-a-token")) is None)

    # --- observations ------------------------------------------------------
    t = db.now()
    good = {"ts": t - 10, "lat": 42.8, "lon": -83.7,
            "band": "sub-ghz", "signature": "SIGX", "conf": 0.8}
    kept = db.add_signal_obs(pid, [
        good,
        {"ts": t - 20, "lat": 42.8, "lon": -83.7, "band": "sub-ghz",
         "signature": "SIGX"},
        {"ts": t - 10, "lat": 42.8, "lon": -83.7},                 # no signature
        {"ts": t - (30 * 86400), "lat": 42.8, "lon": -83.7, "signature": "OLD"},
        {"ts": t + 99999, "lat": 42.8, "lon": -83.7, "signature": "FUTURE"},
        {"ts": t, "lat": 999, "lon": -83.7, "signature": "BADLAT"},
    ])
    check("keeps the valid ones, drops the rest", kept == 2, f"stored {kept} of 6")

    # --- correlation, the whole point --------------------------------------
    def sighting(tier, vclass, dt=0.0, dlat=0.0):
        cur = conn.execute(
            "INSERT INTO sightings (node_id, ts, lat, lon, tier, vclass, snap) "
            "VALUES (?,?,?,?,?,?,?)",
            ("n:test", t - 10 + dt, 42.8 + dlat, -83.7, tier, vclass, "x.jpg"))
        conn.commit()
        return int(cur.lastrowid)

    pub = sighting("public", "police")
    priv = sighting("private", "civilian")
    far = sighting("public", "police", dlat=0.5)
    late = sighting("public", "police", dt=9999)

    m = db.signal_matches("SIGX")
    ids = {r["sighting_id"] for r in m}
    check("matches the published vehicle", pub in ids, f"{len(m)} match(es)")
    check("🚨 NEVER matches a private row", priv not in ids,
          "this is the whole guarantee")
    check("ignores one too far away", far not in ids)
    check("ignores one at the wrong time", late not in ids)
    check("an unknown signature matches nothing", not db.signal_matches("NOPE"))

    # --- revocation --------------------------------------------------------
    check("revoke works", db.revoke_signal_token(pid))
    check("a revoked token stops resolving",
          db.signal_token(review_auth._hash(tok)) is None,
          "cutting off one partner touches nobody else")
    check("revoking twice is not an error", not db.revoke_signal_token(pid))

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
