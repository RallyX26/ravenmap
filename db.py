"""SparrowMap - storage.

SQLite, because a citizen network should be runnable by one person on a laptop
in a spare room, and because the whole database being a single file makes
"delete everything" an operation anybody can verify.

Note what the schema does NOT have: a column for readable civilian plate text.
That is not an oversight the ingest path fills in later. The column does not
exist for private-tier rows because a field that cannot be populated cannot be
leaked.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Optional

from core import DB_PATH, now

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;

-- A volunteer camera.
CREATE TABLE IF NOT EXISTS nodes (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    pubkey        TEXT,               -- ed25519, base64. Identity of the node.
    token         TEXT,               -- bearer secret for browser nodes that
                                      -- cannot hold an ed25519 key. Weaker than
                                      -- a signature; only ever issued to
                                      -- kind='phone'.
    lat           REAL, lon REAL,     -- true position, operator-only
    pub_lat       REAL, pub_lon REAL, -- jittered position shown publicly
    heading       REAL DEFAULT 0,     -- compass bearing the lens faces
    fov           REAL DEFAULT 60,    -- horizontal field of view, degrees
    reach_m       REAL DEFAULT 45,    -- usable plate-read distance, metres
    kind          TEXT DEFAULT 'fixed',   -- fixed | mobile | phone
    status        TEXT DEFAULT 'active',  -- active | paused | revoked
    contact       TEXT,               -- operator-only, never served publicly
    created       REAL,
    last_seen     REAL,               -- last SIGHTING. Traffic, not liveness.
    last_beat     REAL,               -- last heartbeat. This is what 'online'
                                      -- means: the node said so. Deriving it
                                      -- from last_seen made a camera watching
                                      -- an empty street look switched off.
    -- The stretch of real road this camera covers, published accurately
    -- because it is public information, unlike the camera's own position.
    -- See road.py for why these two are separated.
    span_lat1     REAL, span_lon1 REAL,
    span_lat2     REAL, span_lon2 REAL,
    road_name     TEXT,
    span_source   TEXT,               -- osm | aim | none
    sightings     INTEGER DEFAULT 0
);

-- One vehicle, seen once, by one camera.
CREATE TABLE IF NOT EXISTS sightings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id       TEXT NOT NULL,
    ts            REAL NOT NULL,
    lat           REAL, lon REAL,
    tier          TEXT NOT NULL,      -- public | private
    plate_hash    TEXT,               -- always present when a plate was read
    plate_text    TEXT,               -- populated ONLY when tier = 'public'
    plate_state   TEXT,
    plate_conf    REAL,
    vclass        TEXT,               -- police | gov | fleet | civilian | unknown
    vclass_conf   REAL,
    vclass_why    TEXT,               -- human-readable evidence for the class
    color         TEXT, body TEXT, make TEXT, model TEXT,
    heading       REAL,               -- direction of travel
    speed_mph     REAL,
    snap          TEXT,               -- filename under data/snaps
    source        TEXT,               -- synthetic | camera | phone
    sig_ok        INTEGER DEFAULT 0,  -- did the node's signature verify
    confirmed_by  INTEGER DEFAULT 0   -- humans who confirmed the vehicle class
);
CREATE INDEX IF NOT EXISTS ix_sight_ts     ON sightings(ts DESC);
CREATE INDEX IF NOT EXISTS ix_sight_hash   ON sightings(plate_hash, ts DESC);
CREATE INDEX IF NOT EXISTS ix_sight_class  ON sightings(vclass, ts DESC);
CREATE INDEX IF NOT EXISTS ix_sight_plate  ON sightings(plate_text, ts DESC);
CREATE INDEX IF NOT EXISTS ix_sight_node   ON sightings(node_id, ts DESC);

-- Every read of the public tier. We are building a surveillance network; the
-- least we can do is be the first thing it watches.
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL, action TEXT, target TEXT, actor TEXT, ip TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts DESC);

-- Out-of-band proof that a person owns a plate. Unused until a real
-- verification channel exists. See privacy.lookup_private_plate.
CREATE TABLE IF NOT EXISTS claims (
    plate_hash TEXT PRIMARY KEY,
    token      TEXT, created REAL, verified_by TEXT, note TEXT
);

-- Free-form community annotation on a public-tier vehicle: "unmarked unit",
-- "this is the county sheriff's K9 truck". Kept separate from detections so a
-- claim can never be mistaken for a measurement.
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_hash TEXT, ts REAL, body TEXT, author TEXT, votes INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_notes_hash ON notes(plate_hash);

-- A member of the public flagging a published sighting as wrong: not a
-- government vehicle, wrong description, and so on. A flag is a SUGGESTION, not
-- an edit. It surfaces the sighting in the operator's review queue, where a
-- human makes the actual call. That keeps the map correctable by anyone without
-- letting anyone quietly take a real patrol car off it.
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sighting_id INTEGER, ts REAL, reason TEXT, note TEXT, ip TEXT,
    resolved    REAL
);
CREATE INDEX IF NOT EXISTS ix_reports_sid ON reports(sighting_id);

-- Reviewer access, separate from the single operator secret. Each trusted
-- reviewer gets their own token (stored only as a hash), which can be scoped to
-- the shared pool or to specific cameras they own, and revoked without touching
-- anyone else's. Every verdict they cast is written to `audit` with their
-- label, so trust is the default and abuse is recoverable rather than assumed.
CREATE TABLE IF NOT EXISTS review_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT UNIQUE,
    label        TEXT,
    scope        TEXT,          -- 'pool' = all pending, 'own' = only `nodes`
    nodes        TEXT,          -- comma-separated node_ids for scope='own'
    created_at   REAL,
    created_by   TEXT,
    revoked_at   REAL,
    last_used_at REAL
);
CREATE INDEX IF NOT EXISTS ix_rvtok_hash ON review_tokens(token_hash);
"""


# Columns added after the first deployment. CREATE TABLE IF NOT EXISTS is a
# no-op on an existing table, so a new column in SCHEMA above never reaches a
# database that already exists - which is a silent failure of exactly the kind
# this project keeps catching itself in. Adding them here means the schema and
# a live database cannot drift apart.
MIGRATIONS = [
    ("nodes", "last_beat", "REAL"),
    # Operator review of a PUBLISHED claim. NULL = nobody has looked.
    # 'confirmed' / 'retracted' - see hub /review. A public sighting is an
    # assertion about a vehicle, so there has to be a way to take one back,
    # and the taking-back is the most valuable training label in the project:
    # a human judgement on the camera's own view of a real published claim.
    ("sightings", "reviewed", "TEXT"),
    ("sightings", "reviewed_at", "REAL"),
    # Which banked training crop came from this sighting, so a correction on
    # the map writes a label into the dataset instead of dying as a one-off fix.
    ("sightings", "bank_ref", "TEXT"),
    # How many separate detections were folded into this one pass. A vehicle
    # crossing the frame can break into several tracker tracks, and each used
    # to post its own sighting - one patrol car, three dots on the map. See
    # merge_window_row.
    ("sightings", "detections", "INTEGER"),
    ("nodes", "span_lat1", "REAL"),
    ("nodes", "span_lon1", "REAL"),
    ("nodes", "span_lat2", "REAL"),
    ("nodes", "span_lon2", "REAL"),
    ("nodes", "road_name", "TEXT"),
    ("nodes", "span_source", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, decl in MIGRATIONS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def connect() -> sqlite3.Connection:
    """One connection per thread. sqlite objects are not thread-portable."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        _migrate(conn)
        _local.conn = conn
    return conn


def init() -> None:
    connect()


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

FIELDS = ("node_id", "ts", "lat", "lon", "tier", "plate_hash", "plate_text",
          "plate_state", "plate_conf", "vclass", "vclass_conf", "vclass_why",
          "color", "body", "make", "model", "heading", "speed_mph", "snap",
          "source", "sig_ok",
          # Which banked training crop this sighting came from. Stored HERE
          # rather than discovered by scanning the bank: the review queue used
          # to search every sidecar once per sighting, which was over 300,000
          # file reads on a single page load and got worse every hour the node
          # ran. A join key belongs in the table that needs to join.
          "bank_ref")


def insert_sighting(rec: dict) -> int:
    """Write one detection.

    The tier gate lives here, at the single choke point every path must pass
    through, rather than being re-decided by each caller. If the tier is not
    'public', plate_text and plate_state are dropped on the floor before the
    INSERT is even built.
    """
    rec = dict(rec)
    if rec.get("tier") != "public":
        rec["plate_text"] = None
        rec["plate_state"] = None

    conn = connect()
    cols = ",".join(FIELDS)
    marks = ",".join("?" for _ in FIELDS)
    cur = conn.execute(
        f"INSERT INTO sightings ({cols}) VALUES ({marks})",
        tuple(rec.get(f) for f in FIELDS))
    conn.execute(
        "UPDATE nodes SET last_seen = ?, sightings = sightings + 1 WHERE id = ?",
        (rec.get("ts", now()), rec.get("node_id")))
    conn.commit()
    return int(cur.lastrowid)


def upsert_node(n: dict) -> None:
    conn = connect()
    conn.execute("""
        INSERT INTO nodes (id, name, pubkey, token, lat, lon, pub_lat, pub_lon,
                           heading, fov, reach_m, kind, status, contact,
                           created, last_seen,
                           span_lat1, span_lon1, span_lat2, span_lon2,
                           road_name, span_source)
        VALUES (:id,:name,:pubkey,:token,:lat,:lon,:pub_lat,:pub_lon,:heading,
                :fov,:reach_m,:kind,:status,:contact,:created,:last_seen,
                :span_lat1,:span_lon1,:span_lat2,:span_lon2,
                :road_name,:span_source)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, pubkey=excluded.pubkey, token=excluded.token,
            lat=excluded.lat, lon=excluded.lon,
            pub_lat=excluded.pub_lat, pub_lon=excluded.pub_lon,
            heading=excluded.heading, fov=excluded.fov, reach_m=excluded.reach_m,
            kind=excluded.kind, status=excluded.status, contact=excluded.contact,
            span_lat1=excluded.span_lat1, span_lon1=excluded.span_lon1,
            span_lat2=excluded.span_lat2, span_lon2=excluded.span_lon2,
            road_name=excluded.road_name, span_source=excluded.span_source
    """, {**{"pubkey": None, "token": None, "contact": None, "created": now(),
             "last_seen": None, "heading": 0, "fov": 60, "reach_m": 45,
             "kind": "fixed", "status": "active",
             "span_lat1": None, "span_lon1": None, "span_lat2": None,
             "span_lon2": None, "road_name": None, "span_source": None}, **n})
    conn.commit()


def heartbeat(node_id: str, ts: Optional[float] = None) -> None:
    """A node reporting that it is awake and watching.

    Separate from last_seen on purpose. 'A car drove past recently' and 'the
    camera is running' are different questions, and answering the second with
    the first marks every quiet street offline.
    """
    conn = connect()
    conn.execute("UPDATE nodes SET last_beat = ? WHERE id = ?",
                 (ts or now(), node_id))
    conn.commit()


def audit(action: str, target: str = "", actor: str = "anon", ip: str = "") -> None:
    conn = connect()
    conn.execute("INSERT INTO audit (ts, action, target, actor, ip) VALUES (?,?,?,?,?)",
                 (now(), action, target, actor, ip))
    conn.commit()


# --------------------------------------------------------------------------
# Reads.  Every one of these returns raw rows; redaction happens in the hub so
# it is applied uniformly and is visible in one place during review.
# --------------------------------------------------------------------------

def search_plate(text: str, limit: int = 200) -> list[dict]:
    """Every CONFIRMED public sighting of a plate.

    🚨 THE FILTER IS IN THE QUERY, NOT ONLY IN THE REDACTOR.
    privacy.redact already withholds the text from unconfirmed rows, but a
    search that scanned every row and then hid the results would still ANSWER
    the question - "no matches" and "matches you may not see" are
    distinguishable by anyone paying attention, and that is a lookup oracle for
    private plates. So private and unconfirmed rows are never scanned.

    Matching is prefix-or-exact on a normalised string, deliberately not
    fuzzy: a near-miss search that returns somebody else's vehicle is worse
    than no result.
    """
    q = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    if len(q) < 3:
        return []
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM sightings WHERE tier='public' AND reviewed='confirmed' "
        "AND plate_text IS NOT NULL AND plate_text != '' "
        "AND REPLACE(REPLACE(UPPER(plate_text),' ',''),'-','') LIKE ? "
        "ORDER BY ts DESC LIMIT ?", (q + "%", int(limit))).fetchall()
    return [dict(r) for r in rows]


def recent_sightings(since: float = 0, limit: int = 500,
                     vclass: Optional[str] = None,
                     bbox: Optional[tuple] = None) -> list[dict]:
    sql = "SELECT * FROM sightings WHERE ts > ?"
    args: list[Any] = [since]
    if vclass and vclass != "all":
        if vclass == "public":
            sql += " AND tier = 'public'"
        else:
            sql += " AND vclass = ?"
            args.append(vclass)
    if bbox:
        sql += " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        args += [bbox[0], bbox[2], bbox[1], bbox[3]]
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(min(int(limit), 2000))
    return [dict(r) for r in connect().execute(sql, args).fetchall()]


def track_for(plate_hash: str, limit: int = 500) -> list[dict]:
    """All sightings sharing a hash, oldest first. This is a vehicle's trail."""
    return [dict(r) for r in connect().execute(
        "SELECT * FROM sightings WHERE plate_hash = ? ORDER BY ts ASC LIMIT ?",
        (plate_hash, min(int(limit), 2000))).fetchall()]


def sighting(sid: int) -> Optional[dict]:
    r = connect().execute("SELECT * FROM sightings WHERE id = ?", (sid,)).fetchone()
    return dict(r) if r else None


def nodes(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM nodes"
    if active_only:
        sql += " WHERE status = 'active'"
    return [dict(r) for r in connect().execute(sql + " ORDER BY name").fetchall()]


def node(nid: str) -> Optional[dict]:
    r = connect().execute("SELECT * FROM nodes WHERE id = ?", (nid,)).fetchone()
    return dict(r) if r else None


# A node is online if it has beaten within this window. Two missed beats at the
# 30 s cadence, so one dropped request does not blink a camera offline.
ONLINE_WINDOW_S = 90.0


def stats() -> dict:
    c = connect()
    one = lambda q, a=(): c.execute(q, a).fetchone()[0]
    day = now() - 86400

    # ⚠️ COUNT(DISTINCT plate_hash) COUNTED THE EMPTY STRING AS A VEHICLE.
    #
    # privacy.plate_hash returns '' for a pass with no readable plate, and ''
    # is a value, not a NULL - so 31 plateless sightings counted as exactly one
    # distinct vehicle and the map reported "1 vehicle" while identifying none.
    # The insert path now stores NULL, and this guard keeps the figure honest
    # for rows written before that fix.
    #
    # The headline figure is also PUBLIC-TIER ONLY. A private vehicle is a
    # thing the system deliberately cannot identify or count across cameras -
    # reporting a distinct count of them implies a tracking ability that the
    # whole architecture exists to not have.
    # ⚠️ AND WHEN NOTHING CAN BE COUNTED, SAY SO RATHER THAN SAYING ZERO.
    #
    # This counts DISTINCT IDENTIFIED vehicles, which needs a plate. At
    # the reference camera the effective plate width is ~22px against the 60 needed,
    # so no plate is ever read and the figure is structurally zero - displayed
    # next to "4 public sightings", which reads as a contradiction or a bug.
    #
    # It is neither. It is the difference between "we counted none" and "we
    # cannot count", and reporting the second as the first is the mistake his
    # standing rule names: a gap in the instrument is not evidence of absence.
    # `vehicles_countable` lets the UI tell the truth instead.
    identified = """SELECT COUNT(DISTINCT plate_hash) FROM sightings
                    WHERE ts > ? AND tier='public'
                      AND plate_hash IS NOT NULL AND plate_hash != ''"""
    countable = """SELECT COUNT(*) FROM sightings
                   WHERE ts > ? AND tier='public'
                     AND plate_hash IS NOT NULL AND plate_hash != ''"""
    return {
        "nodes_active":   one("SELECT COUNT(*) FROM nodes WHERE status='active'"),
        "nodes_online":   one("SELECT COUNT(*) FROM nodes WHERE last_beat > ?",
                              (now() - ONLINE_WINDOW_S,)),
        "sightings_24h":  one("SELECT COUNT(*) FROM sightings WHERE ts > ?", (day,)),
        "public_24h":     one("SELECT COUNT(*) FROM sightings WHERE tier='public' AND ts > ?", (day,)),
        # Passes past a camera. Not vehicles - the same car twice is two.
        "traffic_24h":    one("SELECT COUNT(*) FROM sightings WHERE tier!='public' AND ts > ?", (day,)),
        "vehicles_24h":   one(identified, (day,)),
        # 0 here means the question could not be asked, not that the answer
        # was none. The UI shows a dash rather than a zero.
        "vehicles_countable": one(countable, (day,)),
        "total":          one("SELECT COUNT(*) FROM sightings"),
        # No "lookups" figure: searches against public-tier data are not logged
        # or counted, by design. Counting them would mean tracking the people who
        # read a public record, which is the surveillance this project refuses.
    }


def merge_window_row(node_id: str, vclass: str, ts: float,
                     window: float = 6.0) -> Optional[dict]:
    """A recent public sighting this one is probably a continuation of.

    🚨 ONE VEHICLE WAS PUBLISHING AS SEVERAL SIGHTINGS.
    Sightings #26974, #26975 and #26976 arrived within 1.3 seconds of each
    other from the same camera. They are one marked municipal unit crossing the
    frame: the tracker lost and re-acquired it as the pillar of the window and
    a parked car occluded it, and each fragment completed independently and
    posted. The map drew three dots for one car and the counter agreed with the
    map, so nothing looked wrong.

    A short window and a matching class is a deliberately blunt test. It cannot
    tell one fragmented car from two patrol cars in convoy, and it will
    occasionally merge the second one - which is the right way round to be
    wrong. Over-counting police presence on a map whose only asset is
    truthfulness is worse than under-counting it, and the merged row keeps a
    detection count so the evidence is not lost.
    """
    row = connect().execute(
        """SELECT * FROM sightings
           WHERE node_id = ? AND vclass = ? AND tier = 'public'
             AND ts > ? AND ts <= ?
           ORDER BY ts DESC LIMIT 1""",
        (node_id, vclass, ts - window, ts)).fetchone()
    return dict(row) if row else None


def bump_detections(sighting_id: int, ts: float, conf: Optional[float]) -> None:
    """Fold another detection into an existing pass.

    Keeps the BEST confidence seen rather than the latest: the strongest look
    the camera got at the vehicle is the honest summary of the pass, and a
    fragment caught as it left the frame should not downgrade one caught square
    on.
    """
    conn = connect()
    conn.execute(
        """UPDATE sightings
           SET detections = COALESCE(detections, 1) + 1,
               ts = MIN(ts, ?),
               vclass_conf = MAX(COALESCE(vclass_conf, 0), COALESCE(?, 0))
           WHERE id = ?""", (ts, conf, sighting_id))
    conn.commit()


def unreview_sighting(sighting_id: int) -> None:
    """Put a sighting back to 'no decision has been made'.

    For UNDO only. `review_sighting` and `promote_sighting` both stamp
    `reviewed`, which is correct for a judgement and wrong for a judgement that
    was withdrawn: leaving 'retracted' behind after an undo hides the sighting
    from the possibly-missed queue for ever, so an accidental keypress would
    still cost the vehicle its chance of being looked at again.

    An undo that leaves a residue is not an undo.
    """
    conn = connect()
    conn.execute("UPDATE sightings SET reviewed=NULL, reviewed_at=NULL "
                 "WHERE id=?", (int(sighting_id),))
    conn.commit()


def review_sighting(sighting_id: int, verdict: str) -> None:
    """Record an operator's judgement on a published sighting.

    A retraction does not delete the row - the vehicle really did pass, and
    replacing one false claim (it was police) with another (nothing was there)
    is not a correction. It is demoted to the private tier and reclassified,
    which is what the map should have said in the first place, and the plate
    text goes with it because a private-tier row must never hold one.
    """
    conn = connect()
    if verdict == "retracted":
        # ⚠️ THE REASON IS A BOUND PARAMETER, NOT AN INLINE LITERAL.
        # It was written as two adjacent quoted strings across a line break -
        # Python's implicit concatenation, which does not exist in SQL. SQLite
        # saw two string literals with no operator between them and raised a
        # syntax error, so EVERY retraction failed with "internal error" while
        # confirm and promote (different code paths) worked fine.
        #
        # Worth noting how it survived my own testing: I checked that the
        # endpoint rejected a bad verdict and an unknown id, and both of those
        # return BEFORE reaching this statement. I tested the guards and never
        # once exercised the operation.
        conn.execute("""UPDATE sightings
                        SET tier='private', vclass='civilian', vclass_conf=NULL,
                            plate_text=NULL, plate_state=NULL, vclass_why=?,
                            reviewed=?, reviewed_at=?
                        WHERE id=?""",
                     ("retracted by the camera operator: not a public-tier vehicle",
                      verdict, now(), sighting_id))
    else:
        conn.execute("UPDATE sightings SET reviewed=?, reviewed_at=? WHERE id=?",
                     (verdict, now(), sighting_id))
    conn.commit()
    # A verdict is the answer to whatever the public flagged, so it clears the
    # flags too. Otherwise a reviewed sighting keeps showing up as "reported".
    resolve_reports(sighting_id)


# The fixed set of things a member of the public can flag. Free text is only a
# note attached to one of these, never a category of its own, so the queue can
# group and count flags without parsing prose.
REPORT_REASONS = {
    "not_government":    "not a government vehicle",
    "wrong_description": "wrong description (body or colour)",
    "other":            "other (see note)",
}


def add_report(sighting_id: int, reason: str, note: str, ip: str) -> int:
    """Store a public flag against a published sighting. Caller validates that
    the reason is known and the sighting is public tier."""
    conn = connect()
    cur = conn.execute(
        "INSERT INTO reports (sighting_id, ts, reason, note, ip, resolved) "
        "VALUES (?,?,?,?,?,NULL)",
        (sighting_id, now(), reason, (note or "")[:500] or None, ip))
    conn.commit()
    return cur.lastrowid


def open_report_counts() -> dict:
    """{sighting_id: count} of unresolved flags, to mark the review queue."""
    conn = connect()
    return {r[0]: r[1] for r in conn.execute(
        "SELECT sighting_id, COUNT(*) FROM reports WHERE resolved IS NULL "
        "GROUP BY sighting_id").fetchall()}


def reports_for(sighting_id: int, open_only: bool = True) -> list:
    conn = connect()
    q = "SELECT reason, note, ts FROM reports WHERE sighting_id=?"
    if open_only:
        q += " AND resolved IS NULL"
    return [dict(r) for r in conn.execute(q + " ORDER BY ts DESC",
                                          (sighting_id,)).fetchall()]


def resolve_reports(sighting_id: int) -> None:
    """Clear a sighting's open flags. Called when the operator reviews it, so
    acting on a flag is what takes it out of the queue."""
    conn = connect()
    conn.execute("UPDATE reports SET resolved=? "
                 "WHERE sighting_id=? AND resolved IS NULL", (now(), sighting_id))
    conn.commit()


# --------------------------------------------------------------------------
# Reviewer tokens (multi-reviewer access, separate from the operator secret).
# --------------------------------------------------------------------------
def add_review_token(token_hash: str, label: str, scope: str,
                     nodes: str, created_by: str) -> int:
    conn = connect()
    cur = conn.execute(
        "INSERT INTO review_tokens(token_hash,label,scope,nodes,created_at,created_by) "
        "VALUES(?,?,?,?,?,?)",
        (token_hash, label, scope, nodes, now(), created_by))
    conn.commit()
    return int(cur.lastrowid)


def review_token_by_hash(token_hash: str) -> Optional[dict]:
    r = connect().execute(
        "SELECT * FROM review_tokens WHERE token_hash=? AND revoked_at IS NULL",
        (token_hash,)).fetchone()
    return dict(r) if r else None


def touch_review_token(tid: int) -> None:
    conn = connect()
    conn.execute("UPDATE review_tokens SET last_used_at=? WHERE id=?", (now(), tid))
    conn.commit()


def revoke_review_token(tid: int) -> bool:
    conn = connect()
    cur = conn.execute(
        "UPDATE review_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
        (now(), tid))
    conn.commit()
    return cur.rowcount > 0


def list_review_tokens(include_revoked: bool = True) -> list[dict]:
    q = "SELECT id,label,scope,nodes,created_at,created_by,revoked_at,last_used_at FROM review_tokens"
    if not include_revoked:
        q += " WHERE revoked_at IS NULL"
    return [dict(r) for r in connect().execute(q + " ORDER BY created_at DESC").fetchall()]


def set_sighting_desc(sighting_id: int, body: Optional[str] = None,
                      color: Optional[str] = None) -> None:
    """Operator correction of the cosmetic description (e.g. the detector said
    'motorcycle' for an SUV). Only the descriptive fields - never the class, the
    plate, or the position, which have their own review paths."""
    sets, args = [], []
    if body is not None:
        sets.append("body=?"); args.append(body.strip() or None)
    if color is not None:
        sets.append("color=?"); args.append(color.strip() or None)
    if not sets:
        return
    args.append(sighting_id)
    conn = connect()
    conn.execute(f"UPDATE sightings SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()


def promote_sighting(sighting_id: int, vclass: str = "police",
                     conf: Optional[float] = None, why: str = "") -> None:
    """Put a wrongly-private sighting onto the public map.

    The mirror of a retraction, and needed for the same reason: the classifier
    can be wrong in both directions, and only one of them was correctable.
    The first genuine patrol car this network ever saw sat in the private tier
    because a gate was reading a field nothing populated - a bug, not a policy
    decision - and there was no way to say so.

    ⚠️ THE PLATE DOES NOT COME BACK. A private-tier snapshot has the plate
    destroyed in its pixels at capture time and the readable text was never
    stored, so a promoted sighting is public with no plate. That is rule 2
    working exactly as intended: a public SIGHTING carries no identifier, and
    the strict bar exists only for publishing plate TEXT. Nothing here can
    resurrect an identifier, which is the correct thing for this function to
    be incapable of.
    """
    conn = connect()
    conn.execute("""UPDATE sightings
                    SET tier='public', vclass=?, vclass_conf=?, vclass_why=?,
                        reviewed='confirmed', reviewed_at=?
                    WHERE id=?""",
                 (vclass, conf,
                  why or "confirmed by the camera operator as a public-tier vehicle",
                  now(), sighting_id))
    conn.commit()


def review_stats() -> dict:
    c = connect()
    one = lambda q, a=(): c.execute(q, a).fetchone()[0]
    # ⚠️ 'merged' IS NOT A VERDICT. Rows folded into another pass are marked
    # reviewed so they drop out of the queue, and marking them 'confirmed' put
    # duplicates in the confirmed count - the header said 8 confirmed while the
    # page showed 6 cards. A field that means "has been dealt with" and a field
    # that means "a human judged this true" are different fields.
    return {
        "pending":   one("SELECT COUNT(*) FROM sightings WHERE tier='public' AND reviewed IS NULL"),
        "confirmed": one("SELECT COUNT(*) FROM sightings WHERE reviewed='confirmed'"),
        "retracted": one("SELECT COUNT(*) FROM sightings WHERE reviewed='retracted'"),
        "merged":    one("SELECT COUNT(*) FROM sightings WHERE reviewed='merged'"),
    }


def leaderboard(hours: int = 24, limit: int = 25) -> list[dict]:
    """Most-seen public-tier vehicles. The patrol-pattern view.

    🚨 THIS IS A READ PATH AND IT HAS TO OBEY THE SAME TWO GATES AS EVERY OTHER.
    It used to SELECT plate_text/plate_state AND the stable plate_hash for ALL
    public rows, with no `reviewed='confirmed'` filter and never routed through
    privacy.redact - the one path that skipped both. That served the plate text
    of UNCONFIRMED public rows (which redact deliberately withholds until a human
    confirms, because the classifier is ~95% precision) and handed out the
    durable cross-day identifier that the per-day hash alias exists to deny.

    Fixed two ways: only CONFIRMED public rows are counted, so a plate shown here
    is one a human already agreed is a government vehicle - the same bar
    /api/plate search uses; and the raw plate_hash is used only to GROUP,
    server-side, and never leaves this function. Nothing here can become a
    tracking key or expose an unreviewed plate.
    """
    since = now() - hours * 3600
    rows = connect().execute("""
        SELECT plate_text, plate_state, vclass,
               COUNT(*) AS hits,
               COUNT(DISTINCT node_id) AS cameras,
               MIN(ts) AS first_ts, MAX(ts) AS last_ts
        FROM sightings
        WHERE tier = 'public' AND reviewed = 'confirmed' AND ts > ?
        GROUP BY plate_hash
        ORDER BY hits DESC LIMIT ?""", (since, limit)).fetchall()
    return [dict(r) for r in rows]
