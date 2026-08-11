"""SparrowMap hub - HTTP API, live feed and the public map.

Standard library only, on purpose. A hub that a neighbourhood can run should
not need a package index to survive, and every dependency is another thing a
volunteer has to trust. Same reasoning as the sonar build: threading HTTP
server plus Server-Sent Events, no websocket stack, no ASGI server.

Run it:      python hub.py
Then open:   http://localhost:8150/
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import sys
import queue
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import classify
import db
import mirror
import operator_auth
import qr
import nodes as node_mod
import privacy
import snapshot
from core import CONFIG, DATA, PUBLIC, SNAPS, is_operator_addr, now

# --------------------------------------------------------------------------
# Basemap tiles
#
# 🚨 THE MAP HAD NO BASEMAP AT ALL AND NOTHING SAID SO.
# The tile layer pointed straight at basemaps.cartocdn.com while the CSP said
# `img-src 'self' data: blob:`. Every tile was refused, so the map rendered as
# a black field with sightings floating on it - which reads as "GPS isn't
# loading", not as a security header doing its job. The header was right. The
# tile URL was the thing that disagreed with the design, and the comment above
# the CSP had already stated that design: everything this site loads, it ships.
#
# So the tiles are PROXIED rather than the CSP widened: a viewer looking at this
# map should not have
# their IP and their pan-and-zoom trail delivered to a third-party CDN. On a
# public map that is one request per tile per viewer - a far better record of
# who is interested in which street than anything SparrowMap itself stores. A
# project that jitters its own volunteers' positions should not hand that over.
#
# The cache means upstream sees each tile once, not once per viewer.
TILES = DATA / "tiles"
TILE_UPSTREAM = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
TILE_SUBDOMAINS = "abcd"
TILE_MAX_ZOOM = 20
# Tiles are ~5-15 KB. 20k of them is a few hundred MB and covers a town at
# every zoom a viewer will use.
TILE_CACHE_MAX = 20000
_tile_count = None


def _tile_prune() -> None:
    """Keep the tile cache bounded, without stat-ing the tree on every hit.

    The count is held in memory and only recounted when it is unknown (first
    write after start) or when the cap is reached. Walking the cache on every
    tile would turn a 2 ms disk read into a directory crawl at exactly the
    moment a viewer is dragging the map.
    """
    global _tile_count
    if _tile_count is None:
        _tile_count = sum(1 for _ in TILES.rglob("*.png"))
    else:
        _tile_count += 1
    if _tile_count <= TILE_CACHE_MAX:
        return
    # Oldest first. Tiles are interchangeable and cheap to refetch, so there is
    # no cleverness to buy here - unlike the crop bank, which prunes by whole
    # DAYS because dropping the oldest crops first would bias the training set
    # toward one time of day.
    files = sorted(TILES.rglob("*.png"), key=lambda f: f.stat().st_mtime)
    for f in files[:len(files) - TILE_CACHE_MAX + 1000]:
        try:
            f.unlink()
        except OSError:
            pass
    _tile_count = None

VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Live feed
# ---------------------------------------------------------------------------

class Feed:
    """Fan a sighting out to every connected browser."""

    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, rec: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(rec)
            except queue.Full:
                pass        # a stalled browser must never block the network


FEED = Feed()

# ---------------------------------------------------------------------------
# Rate limiting
#
# The two routes a stranger can write to - enrolment and sighting submission -
# had no limit at all. On a private tailnet that is fine; on sparrowmap.com it
# is an invitation to fill the database overnight. Deliberately crude: a fixed
# window per address, in memory, no dependency. It will not stop a distributed
# flood, and it is not trying to - it stops the trivial script, which is the
# actual threat to a small project on day one.
# ---------------------------------------------------------------------------

_HITS: dict = {}
_HIT_LOCK = threading.Lock()
# Per-IP request budgets: (count, window_seconds).
# /api/tile is here because each hit triggers one UPSTREAM fetch that holds a
# worker thread up to 15s - an anonymous amplification lever. A human panning
# the map pulls tens of tiles a minute; 600/5min is generous for that and still
# caps a scraper walking the whole tile pyramid.
RATE = {"/api/enroll": (5, 3600), "/api/sightings": (600, 3600),
        "/api/tile": (600, 300), "/api/report": (20, 3600)}


def rate_ok(path: str, ip: str) -> bool:
    limit = RATE.get(path)
    if not limit:
        return True
    n, window = limit
    bucket = int(now() // window)
    key = (path, ip, bucket)
    with _HIT_LOCK:
        # Drop old buckets rather than growing for ever.
        if len(_HITS) > 5000:
            _HITS.clear()
        _HITS[key] = _HITS.get(key, 0) + 1
        return _HITS[key] <= n

# Anonymous viewers get a rolling daily alias instead of the real plate hash so
# a track is followable on screen but not archivable across days. We keep the
# reverse map in memory only, and it dies with the process.
_ALIAS: dict[str, str] = {}
_ALIAS_DAY = [0]


def _alias_map(rows: list[dict]) -> None:
    day = int(now() // 86400)
    if day != _ALIAS_DAY[0]:
        _ALIAS.clear()
        _ALIAS_DAY[0] = day
    for r in rows:
        red = privacy.redact(r, "anon")
        # `or ""` matters: plate_hash is NULL for a pass with no readable
        # plate, and dict.get's default does not fire on a present-but-None key.
        if (red.get("plate_hash") or "").startswith("a:") and r.get("plate_hash"):
            _ALIAS[red["plate_hash"]] = r["plate_hash"]


def _resolve_hash(h: str) -> str:
    """Turn a client-supplied alias back into the real hash, if we minted it."""
    return _ALIAS.get(h, h)


def _public_rows(rows: list[dict]) -> list[dict]:
    _alias_map(rows)
    return [privacy.redact(r, "anon") for r in rows]


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    # Do not advertise the software or the Python build. The default banner
    # read "SparrowMap/0.1.0 Python/3.12.10", which hands an attacker the exact
    # version to look up CVEs against before they try anything.
    server_version = "SparrowMap"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):        # quieter than the default
        pass

    # Public read paths served IDENTICALLY to every anonymous viewer, so a short
    # shared cache collapses thousands of pollers into ~one origin fetch/window.
    _CACHEABLE_API = frozenset({"/api/sightings", "/api/stats", "/api/policy",
                                "/api/nodes", "/api/leaderboard", "/api/health"})

    def _cache_control(self) -> str:
        """Per-path caching policy.

        🚨 THIS IS WHAT LETS THE MAP SURVIVE A CROWD. The origin (a threaded
        Python server) caps near 55 req/s on the map data - measured. But that
        data is PUBLIC and identical for everyone, so it belongs on the edge:
        with a short shared cache, thousands of viewers collapse to about one
        origin fetch per window and the ceiling stops mattering.

        Default stays no-store. It is opened up ONLY for things that are public
        and the same for all viewers. The privacy reason no-store existed - not
        keeping a record of who looked at which plate - lives on the SEARCH and
        OPERATOR and per-user paths, which stay no-store below.
        """
        p = urlparse(self.path).path
        if p.startswith(("/vendor/", "/api/tile/")):
            # PINNED content only: the vendored detector runtime (a 10 MB model
            # + wasm + Leaflet) and basemap tiles. These do not change without a
            # deliberate library swap, so a long cache saves re-downloading 10 MB
            # on every visit. NOT `immutable` - if a library is ever replaced a
            # 7-day revalidation is cheap insurance against serving a stale one.
            return "public, max-age=604800"
        if p.startswith("/static/"):
            # 🚨 THE APP'S OWN CODE (app.js, transparency.js, refresh.js). It
            # MUST be able to change - marking it immutable froze every JS fix
            # for a week on returning visitors. Short cache: still absorbs a
            # launch spike (thousands of requests in a minute -> one origin hit)
            # but a code change propagates within the minute. (A content-hashed
            # filename would let this be immutable too - a later build step.)
            return "public, max-age=60"
        if p in self._CACHEABLE_API:
            # The public map data. The frontend buckets its `since` timestamps
            # so the URL is stable within the window and the cache actually hits.
            return "public, max-age=15"
        if p == "/" or p.endswith(".html") or p in (
                "/about", "/transparency", "/app", "/node", "/key"):
            return "public, max-age=60"        # page shells: reuse, revalidate
        # /api/plate search, /api/track, /api/sighting/<id>, operator routes,
        # /api/live, /api/audit - anything per-user or a lookup - is never cached.
        return "no-store"

    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        # A fresh nonce per response. Four pages carry an inline <script>, and
        # the alternatives were both worse: allowing 'unsafe-inline' would make
        # the policy decorative, and moving the code out to four new files
        # would scatter page logic away from the page for a deployment detail.
        self._nonce = secrets.token_urlsafe(12)
        # ⚠️ SUBSTITUTE BEFORE Content-Length IS COMPUTED.
        # The placeholder and the nonce are different lengths, so filling it in
        # after the header was set truncated every page by seven bytes - a
        # silently broken site with a valid-looking response.
        if b"@@NONCE@@" in body:
            body = body.replace(b"@@NONCE@@", self._nonce.encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A caller that set its own Cache-Control (the tile proxy) wins; this
        # also stops the two conflicting Cache-Control headers the tile response
        # used to carry. Otherwise apply the per-path policy.
        if not (extra and any(k.lower() == "cache-control" for k in extra)):
            self.send_header("Cache-Control", self._cache_control())

        # ---- headers that protect the VISITOR ---------------------------
        # 🚨 REFERRER POLICY IS AN ANONYMITY CONTROL HERE, NOT A FORMALITY.
        # Without it, every outbound click - the OpenStreetMap attribution
        # link at the bottom of the map, for one - tells the destination that
        # the visitor came from sparrowmap.com, and carries the full URL
        # including any plate they searched for.
        self.send_header("Referrer-Policy", "no-referrer")
        # Stops a browser from second-guessing a declared content type, which
        # is how a stored file gets executed as script.
        self.send_header("X-Content-Type-Options", "nosniff")
        # Nobody frames this map. Clickjacking a "retract" button would be a
        # quiet way to vandalise the record.
        self.send_header("X-Frame-Options", "DENY")
        # Everything this site loads, it ships. Leaflet is vendored, there is
        # no CDN and no analytics, so the policy can be strict enough to
        # actually contain an injection rather than decorate the response.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' "
            # 🚨 'wasm-unsafe-eval' IS REQUIRED OR THE DETECTOR CANNOT LOAD.
            # Compiling a WebAssembly module counts as script generation, so a
            # script-src without it refuses the ONNX runtime outright - which
            # broke the camera on every device, with the map and every other
            # page still working perfectly. I had verified the model loading
            # BEFORE this header existed and never re-tested after, so the
            # regression shipped invisibly.
            #
            # It is far narrower than 'unsafe-eval': it permits WebAssembly
            # compilation and nothing else - no eval, no new Function, no
            # inline string execution. The policy stays meaningful.
            f"'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval' "
            f"'nonce-{self._nonce}'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'")

        # The public map is meant to be embeddable and mirrorable by anyone.
        # Operator JSON is not, and a wildcard on it is needless surface even
        # with a SameSite=Strict cookie in front.
        if not self.path.startswith(("/api/review", "/api/operator",
                                     "/api/purge")):
            self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _tile(self, path: str) -> None:
        """Serve one basemap tile, from disk if we already have it.

        ⚠️ THE UPSTREAM URL IS BUILT FROM INTEGERS, NEVER FROM THE REQUEST.
        A proxy that forwards a caller-supplied URL is an open proxy: it will
        happily fetch `http://169.254.169.254/` or anything else the box can
        reach, using the box's own network position. So z/x/y are parsed as
        ints, range-checked against the zoom, and formatted into a fixed
        template. There is no code path here that can be pointed somewhere
        else. A proxy that accepts a request-supplied URL and guards it with a
        prefix check may be acceptable on an operator-only LAN page; this one is
        reachable by every viewer of a public map, so it takes no URL at all.
        """
        parts = path[len("/api/tile/"):].split("/")
        if len(parts) != 3 or not parts[2].endswith(".png"):
            return self._send(404, b"", "text/plain")
        try:
            z = int(parts[0]); x = int(parts[1]); y = int(parts[2][:-4])
        except ValueError:
            return self._send(404, b"", "text/plain")
        # Reject anything outside the tile grid before it becomes a request.
        if not (0 <= z <= TILE_MAX_ZOOM) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
            return self._send(404, b"", "text/plain")

        cached = TILES / str(z) / str(x) / f"{y}.png"
        if cached.exists():
            return self._send(200, cached.read_bytes(), "image/png",
                              {"Cache-Control": "public, max-age=604800"})

        # Rate-limit only the UPSTREAM path - a cache hit above is cheap, but a
        # miss makes this box fetch from the CDN and hold a thread up to 15s.
        # That is the amplification lever, so the budget guards exactly it.
        if not rate_ok("/api/tile", self.client_ip):
            return self._send(429, b"", "text/plain")

        import urllib.request
        url = TILE_UPSTREAM.format(s=TILE_SUBDOMAINS[(x + y) % len(TILE_SUBDOMAINS)],
                                   z=z, x=x, y=y)
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "SparrowMap/0.1 (+https://sparrowmap.com)"}),
                timeout=15).read()
        except Exception:
            # A missing tile must not be an error page: Leaflet would draw the
            # HTML as a broken image across the map. Fail as a 404 and let it
            # leave that square blank.
            return self._send(404, b"", "text/plain")

        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(raw)
            _tile_prune()
        except Exception:
            # Caching is an optimisation. If the disk says no, still serve.
            pass
        return self._send(200, raw, "image/png",
                          {"Cache-Control": "public, max-age=604800"})

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    def _err(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            return self._err(404, "not found")
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        # The browser refuses to instantiate a wasm module served as anything
        # else, and mimetypes on Windows does not know these two.
        ctype = {".wasm": "application/wasm",
                 ".onnx": "application/octet-stream"}.get(path.suffix, ctype)
        body = path.read_bytes()
        if path.suffix == ".html":
            # The nonce is generated inside _send, so mark the tags with a
            # placeholder and let _send fill it. Only bare <script> tags are
            # touched; ones with a src attribute are already covered by 'self'.
            body = body.replace(b"<script>", b"<script nonce=\"@@NONCE@@\">")
        self._send(200, body, ctype)

    # A sighting carries a base64 vehicle crop, which is the only large body this
    # server has any reason to accept. 8 MB covers a generous JPEG with base64's
    # 33% overhead; everything else is a few hundred bytes. Bigger than this is
    # not a real submission, it is memory pressure on a 2-vCPU/3-GB box.
    MAX_BODY = 8 * 1024 * 1024

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        # 🚨 CAP BEFORE READING. Trusting Content-Length and calling read(n) with
        # no ceiling lets one request ask the box to buffer gigabytes; a handful
        # of those, or a slow-loris trickle, exhausts a thread-per-connection
        # server. Never buffer more than MAX_BODY, and on an oversize claim close
        # the connection (unread bytes would otherwise corrupt the next
        # keep-alive request). The handler then sees {} and answers 400. No
        # response is sent from here, so the caller can never double-respond.
        if n > self.MAX_BODY:
            self.close_connection = True
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _is_local(self) -> bool:
        """May this caller retract a published claim?

        Loopback, the LAN, and the Tailscale range - which on this deployment
        is exactly the operator and his phone, and nobody else. Strict localhost was
        the first cut and made the review page unusable from the phone he
        actually reviews things on; a control nobody can reach when they need
        it gets worked around rather than used.

        Checked against the SOCKET, never a header. X-Forwarded-For is
        client-controlled, and trusting it is how an "operator only" route
        becomes an anyone-who-asks route.

        🚨 THIS IS ONLY SAFE WHILE THE HUB IS PRIVATE. The moment it is exposed
        to the internet - or put behind a proxy, which makes every request
        appear to come from the proxy's address - this must become real
        authentication. `operator_requires_auth` in config.json is the reminder;
        the check refuses everything if it is ever set true without an auth
        mechanism existing, because failing closed is the correct direction.
        """
        # Auth exists now (operator_auth.py). When it is switched on the
        # SOCKET ADDRESS IS IGNORED, because behind a reverse proxy it is the
        # proxy's address and says nothing about who is calling - which is the
        # trap this check used to warn about and would have walked into the
        # first time sparrowmap.com pointed at a box.
        return operator_auth.check(self.headers, self.client_address[0])

    @property
    def client_ip(self) -> str:
        # Trust the socket, not a header. X-Forwarded-For is client-controlled
        # unless a proxy you own overwrote it, and treating it as truth is how
        # audit logs get poisoned.
        return self.client_address[0]

    # -- routing --------------------------------------------------------
    def do_GET(self) -> None:
        try:
            u = urlparse(self.path)
            p, q = u.path, parse_qs(u.query)

            # A mirror has no operator surface at all. Reviewing happens at
            # home, on the hub that holds the evidence to judge with.
            if not mirror.route_allowed(p):
                return self._err(404, "not found")

            if p == "/":                 return self._file(PUBLIC / "index.html")
            if p == "/about":            return self._file(PUBLIC / "about.html")
            if p == "/transparency":     return self._file(PUBLIC / "transparency.html")
            # One program, three modes. /node and /key are kept as aliases
            # because keys, QR codes and bookmarks already point at them - a
            # link a volunteer printed must not stop working because the pages
            # were reorganised.
            #
            # /contribute is kept for the same reason, but it no longer has a
            # page of its own: log-by-hand was removed, so the alias lands on
            # the app rather than 404ing a printed link.
            if p in ("/app", "/node", "/key", "/contribute"):
                return self._file(PUBLIC / "app.html")
            if p.startswith("/vendor/"): return self._file(PUBLIC / "vendor" / Path(p[8:]).name)
            if p.startswith("/static/"): return self._file(PUBLIC / Path(p[8:]).name)
            if p.startswith("/snap/"):   return self._file(SNAPS / Path(unquote(p[6:])).name)

            if p == "/api/health":
                return self._json({"ok": True, "version": VERSION, "ts": now()})

            if p == "/api/policy":
                # The privacy posture of this deployment, machine-readable.
                # Anyone mirroring or auditing the network can diff it.
                return self._json({
                    "site": CONFIG["site_name"],
                    "public_tiers": CONFIG["public_tiers"],
                    "civilian_retention_days": CONFIG["civilian_retention_days"],
                    "public_retention_days": CONFIG["public_retention_days"],
                    "pepper_rotation_days": CONFIG["pepper_rotation_days"],
                    "node_position_jitter_m": CONFIG["node_position_jitter_m"],
                    "min_plate_confidence": CONFIG["min_plate_confidence"],
                    "public_threshold": classify.PUBLIC_THRESHOLD,
                    "private_plate_lookup": False,
                    "stores_video": False,
                    "stores_full_frames": not CONFIG.get("crop_only", True),
                    # Whether this deployment is currently willing to assert
                    # "police" about a vehicle. Published so an outside auditor
                    # can see that the tier is gated rather than having to
                    # infer it from an empty map. See classify.py.
                    "publishes_public_tier": CONFIG.get("publish_public_tier", False),
                    "classifier_validated": CONFIG.get("publish_public_tier", False),
                    # Where the map opens. This is CONFIG, not a camera: it
                    # belongs to the deployment, and it is served rather than
                    # hardcoded in app.js so that no real coordinate has to
                    # live in the published source. It is deliberately
                    # town-level and says nothing a viewer cannot see anyway -
                    # the watched spans are far more precise than this.
                    "map_center": CONFIG["map_center"],
                    "map_zoom": CONFIG["map_zoom"],
                })

            if p == "/api/whoami":
                # Lets the review page tell "you are not signed in" apart from
                # "the server is broken", without revealing anything.
                return self._json({"operator": self._is_local(),
                                   "auth_required": operator_auth.required()})
            if p == "/api/plate":
                # Search government plates. Only confirmed public-tier rows are
                # scanned at all - see db.search_plate for why filtering in the
                # redactor alone would still leak a yes/no answer about private
                # vehicles.
                # Searching public-tier data is NOT logged, on purpose. The
                # target of this system is government vehicles on public roads,
                # not the people curious enough to look them up. A search history
                # - even a truncated one - would be a chilling record of who
                # asked a question about a public record, which is exactly the
                # surveillance posture SparrowMap exists to refuse. Reading public
                # data is nobody's business but the reader's.
                query = (q.get("q") or [""])[0]
                rows = db.search_plate(query)
                return self._json({"query": query,
                                   "results": _public_rows(rows)})
            if p == "/api/stats":
                return self._json(db.stats())

            if p == "/sw.js":
                # Served at the ROOT so its scope covers /app and /node - a
                # service worker only controls pages under its own path. Its
                # Cache-Control is the default no-store, which is right: the
                # browser must re-check it to pick up an updated worker.
                return self._file(PUBLIC / "sw.js")

            if p == "/login":
                # The one operator page that must be reachable WITHOUT being the
                # operator - it is where you become one. Serving it never leaks
                # anything: it only offers a box to paste the token that already
                # lives in data/operator.token on this machine. When auth is off
                # it says so and points at /review.
                return self._file(PUBLIC / "login.html")

            if p == "/review":
                # OPERATOR ONLY. This page shows what the map has ASSERTED and
                # lets it be taken back, which is not a power a visitor gets.
                # A 403 here now points at /login instead of dead-ending, so
                # turning auth on does not read as "the review page is broken".
                if not self._is_local():
                    return self._err(403, "not signed in - open /login")
                return self._file(PUBLIC / "review.html")

            if p == "/api/review/queue":
                if not self._is_local():
                    return self._err(403, "local only")
                rows = db.connect().execute(
                    "SELECT * FROM sightings WHERE tier='public' "
                    "ORDER BY (reviewed IS NOT NULL), ts DESC LIMIT 200"
                ).fetchall()
                report_counts = db.open_report_counts()
                out = []
                for r in rows:
                    d = dict(r)
                    item = {k: d.get(k) for k in
                            ("id", "ts", "vclass", "vclass_conf", "vclass_why",
                             "plate_text", "snap", "node_id", "reviewed",
                             "reviewed_at", "source", "color", "body")}
                    # Attach any public flags so the operator sees WHAT a
                    # stranger disputed, not just that something is disputed.
                    item["reports"] = (db.reports_for(d["id"])
                                       if report_counts.get(d["id"]) else [])
                    out.append(item)
                # Flagged-but-unreviewed rows are the ones a human most needs to
                # look at, so lift them to the top without disturbing the rest.
                out.sort(key=lambda x: (x["reviewed"] is not None,
                                        0 if x["reports"] else 1))
                # 🚨 MISSES ARE ALSO ERRORS, and only one direction was
                # correctable. The first real patrol unit this network saw sat
                # in the private tier because a gate read a field nothing
                # populated - a bug, not a decision - and nothing surfaced it.
                # These are private sightings the model DID think were police,
                # offered for promotion. Bounded to the recent window: this is
                # a review queue, not a second map.
                missed = []
                try:
                    from detect import bank
                    day = now() - 86400 * 3
                    rows2 = db.connect().execute(
                        "SELECT * FROM sightings WHERE tier='private' AND ts > ? "
                        "AND reviewed IS NULL ORDER BY ts DESC LIMIT 400",
                        (day,)).fetchall()
                    for r in rows2:
                        # One indexed column, one file read - no scanning.
                        if not r["bank_ref"]:
                            continue
                        j = bank.sidecar(r["bank_ref"])
                        if not j:
                            continue
                        meta = json.loads(j.read_text(encoding="utf-8"))
                        clip = meta.get("clip") or {}
                        # A floor, or the queue fills with coin flips. CLIP's
                        # argmax alone calls 13% of ordinary traffic police, so
                        # an unfiltered list is noise - and a review queue
                        # nobody trusts is a review queue nobody opens.
                        if (clip.get("vclass") != "police"
                                or (clip.get("conf") or 0) < 0.50
                                or (clip.get("margin") or 0) < 0.20):
                            continue
                        d = dict(r)
                        missed.append({
                            **{k: d.get(k) for k in
                               ("id", "ts", "vclass", "snap", "node_id")},
                            "clip_conf": clip.get("conf"),
                            "clip_margin": clip.get("margin"),
                            "label": meta.get("label"),
                        })
                    missed.sort(key=lambda m: -(m.get("clip_conf") or 0))
                    missed = missed[:40]
                except Exception:
                    traceback.print_exc()
                # ⚠️ THE HEADER MUST COUNT EVERYTHING THAT NEEDS A DECISION.
                # `pending` counts only unreviewed PUBLISHED sightings, so the
                # page read "0 to review" while three possibly-missed cards sat
                # below it with live buttons. A counter that ignores half the
                # work is a counter that teaches you to ignore it.
                return self._json({"queue": out, "missed": missed,
                                   "missed_pending": len(missed),
                                   **db.review_stats()})

            if p.startswith("/api/tile/"):
                return self._tile(p)

            if p == "/api/nodes":
                out = []
                for n in db.nodes():
                    span = node_mod.span_of(n)
                    # 🚨 NO CAMERA POSITION IS PUBLISHED AT ALL. Not the true
                    # one, not the jittered one.
                    #
                    # This used to send `pub_lat/pub_lon` - the 60 m jittered
                    # point - and the map drew a dot on it. Jitter of that size
                    # does not hide a camera on a residential street: it puts
                    # the dot within a few houses of the right one, which is
                    # close enough for anyone standing on that street. The
                    # protection was always the ROAD SPAN being the published
                    # unit, and the point beside it was a second, weaker claim
                    # about the same camera that could only ever narrow the
                    # first.
                    #
                    # Removing it from the DRAWING would have been theatre -
                    # `curl /api/nodes` returns whatever this dict holds. The
                    # field is gone from the response, so there is nothing to
                    # hide client-side. Same rule this file keeps relearning: a
                    # control applied to one representation is bypassed by the
                    # other. See isolate.py.
                    #
                    # heading/fov/reach are gone with it. They were only ever
                    # sent so a span-less node had something to draw a cone
                    # from; with no origin point a cone cannot be drawn, and an
                    # aim vector is precisely what re-derives a camera from its
                    # span.
                    rec = {
                        "id": n["id"], "name": n["name"],
                        "kind": n["kind"], "sightings": n["sightings"],
                        # The stretch of road this camera covers. Public, exact,
                        # and the ONLY geometry a viewer gets - people are
                        # entitled to know where they are recorded.
                        "span": span, "road_name": n["road_name"],
                        "span_source": n["span_source"],
                        "last_seen": n["last_seen"], "last_beat": n["last_beat"],
                        # 'online' now means the node SAID SO. It used to mean
                        # 'a car drove past in the last 15 minutes', which
                        # marked every camera on a quiet street as switched off
                        # and read as a fault the user then went looking for.
                        "online": bool(n["last_beat"] and
                                       n["last_beat"] > now() - db.ONLINE_WINDOW_S),
                    }
                    out.append(rec)
                return self._json(out)

            if p == "/api/sightings":
                since = float(q.get("since", [now() - 3600])[0])
                limit = int(q.get("limit", [400])[0])
                vclass = q.get("vclass", ["all"])[0]
                bbox = None
                if "bbox" in q:
                    try:
                        bbox = tuple(float(x) for x in q["bbox"][0].split(","))
                    except ValueError:
                        bbox = None
                rows = db.recent_sightings(since, limit, vclass, bbox)
                return self._json(_public_rows(rows))

            if p.startswith("/api/sighting/"):
                r = db.sighting(int(p.rsplit("/", 1)[1]))
                if not r:
                    return self._err(404, "no such sighting")
                # 🚨 READING IS NOT AUDITED, DELIBERATELY.
                # This used to write a row saying that somebody looked at this
                # sighting. The whole project refuses to keep a record of which
                # vehicle went where; keeping a record of which reader looked
                # at which vehicle is the same dossier pointed at the public.
                # And it is written on the way IN, so hiding the endpoint would
                # not help - the disk is what gets copied or compelled.
                # The audit log's purpose is to show what the OPERATOR did to
                # the record, and operator actions are still audited below.
                return self._json(_public_rows([r])[0])

            if p.startswith("/api/track/"):
                h = _resolve_hash(unquote(p.rsplit("/", 1)[1]))
                rows = db.track_for(h)
                if not rows:
                    return self._json([])
                # 🚨 NOT AUDITED - and this one was the worst of the two.
                # Its target was the PLATE TEXT itself, so following a
                # government vehicle's trail wrote "this reader looked up this
                # plate" to disk. Someone checking on a patrol car that
                # followed them home should leave no trace here.
                out = _public_rows(rows)
                for r in out:
                    r["patrol_score"] = classify.patrol_score(rows)
                return self._json(out)

            if p == "/api/leaderboard":
                hours = int(q.get("hours", [24])[0])
                return self._json(db.leaderboard(hours))

            if p == "/api/audit":
                rows = db.connect().execute(
                    "SELECT ts, action, target, ip FROM audit ORDER BY ts DESC LIMIT 200"
                ).fetchall()
                # The operator's decisions on the map - confirms, retractions,
                # public flags. Searches are never in here (they are not logged
                # at all). The IP is truncated so even this decision log cannot
                # become a second tracking database.
                return self._json([
                    {"ts": r["ts"], "action": r["action"], "target": r["target"],
                     "who": ".".join(str(r["ip"]).split(".")[:2]) + ".x.x"}
                    for r in rows])

            if p == "/api/live":
                # RETIRED. The map polls /api/sightings now (see public/app.js).
                # A persistent event-stream is one pinned thread per connection
                # on a threaded server - a scale ceiling and a trivial DoS lever
                # (open N connections, pin N threads) - so it no longer holds the
                # socket open. FEED stays for any internal consumer.
                return self._err(410, "live stream retired; the map polls now")

            return self._err(404, "no such route")
        except Exception:
            traceback.print_exc()
            self._err(500, "internal error")

    # State-changing routes authenticated by the operator COOKIE. A browser
    # attaches that cookie to any cross-site request automatically, so these need
    # CSRF defence beyond the cookie itself.
    _CSRF_SENSITIVE = {"/api/review", "/api/review/bulk", "/api/review/edit",
                       "/api/purge", "/api/key/rotate", "/api/operator/login",
                       "/api/operator/logout", "/api/report"}

    def do_POST(self) -> None:
        try:
            p = urlparse(self.path).path

            # 🚨 CSRF: require a real application/json Content-Type on cookie-
            # authed routes. `_body` parses JSON regardless of type, so without
            # this a cross-site <form> POSTing text/plain that HAPPENS to be
            # valid JSON would be accepted, and the browser would attach the
            # operator cookie - letting any page the operator visits retract a
            # sighting or purge data. application/json is NOT a form-reachable
            # "simple" content type: a cross-origin fetch sending it triggers a
            # CORS preflight, which this server never approves for these paths.
            # SameSite=Strict already blocks the cookie cross-site; this is the
            # second lock, and the one that still holds when auth is off and
            # operator power comes from a LAN/loopback source IP instead.
            if p in self._CSRF_SENSITIVE:
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    return self._err(415, "state-changing requests must be "
                                          "application/json")

            if p == "/api/enroll":
                if not rate_ok(p, self.client_ip):
                    return self._err(429, "too many cameras registered from "
                                          "this address; try later")
                b = self._body()
                for k in ("name", "lat", "lon"):
                    if k not in b:
                        return self._err(400, f"missing {k}")
                # ⚠️ node_id MUST be passed through. Without it every re-save
                # of a placement minted a NEW node, so nudging the heading
                # littered the map with duplicate cameras - the exact thing the
                # placement page claimed to avoid.
                try:
                    rec = node_mod.enroll(
                        name=str(b["name"])[:80], lat=float(b["lat"]),
                        lon=float(b["lon"]),
                        pubkey=b.get("pubkey"), heading=float(b.get("heading", 0)),
                        fov=float(b.get("fov", 60)),
                        reach_m=float(b.get("reach_m", 45)),
                        kind=b.get("kind", "fixed"), contact=b.get("contact", ""),
                        node_id=str(b.get("node_id") or "")[:32],
                        # Proving ownership of a node you claim to already be.
                        # Accepted from the body or the header, because the
                        # phone pages hold it in localStorage and the detector
                        # sends it as a bearer.
                        token=(b.get("token")
                               or (self.headers.get("Authorization") or ""
                                   ).replace("Bearer ", "").strip() or None))
                except node_mod.NodeAuthError as exc:
                    return self._err(403, str(exc))
                # The token is returned exactly once, at enrollment.
                return self._json({"id": rec["id"], "status": rec["status"],
                                   "token": rec.get("token")})

            if p == "/api/sightings":
                if not rate_ok(p, self.client_ip):
                    return self._err(429, "posting too fast")
                return self._ingest(self._body())

            if p == "/api/heartbeat":
                # "I am awake and watching." Deliberately separate from a
                # sighting: a camera pointed at an empty street at 4am is
                # working perfectly and has nothing to report, and inferring
                # liveness from traffic reported exactly that camera as down.
                b = self._body()
                nd = db.node(str(b.get("node_id") or ""))
                if not nd:
                    return self._err(404, "unknown node")
                if not self._token_ok(nd):
                    return self._err(401, "bad node token")
                db.heartbeat(nd["id"])
                return self._json({"ok": True, "ts": now()})

            if p == "/api/review/edit":
                # Operator fixes a cosmetic description (e.g. the detector called
                # an SUV a 'motorcycle'). Descriptive fields only - class, plate
                # and position have their own review paths and are not touched.
                if not self._is_local():
                    return self._err(403, "local only")
                b = self._body()
                try:
                    sid = int(b.get("id"))
                except (TypeError, ValueError):
                    return self._err(400, "bad id")
                if not db.sighting(sid):
                    return self._err(404, "no such sighting")
                db.set_sighting_desc(sid, body=b.get("body"), color=b.get("color"))
                db.audit("edit_desc", str(sid), actor="operator",
                         ip=privacy.audit_ip(self.client_ip))
                return self._json({"ok": True})

            if p == "/api/report":
                # A public flag on a published sighting. Anyone may send one: it
                # is a SUGGESTION that surfaces the sighting in the operator's
                # review queue, never an edit to the map. Rate-limited (RATE) and
                # JSON-only (_CSRF_SENSITIVE) so it cannot be driven from a
                # cross-site form or hammered. Only public-tier rows can be
                # flagged - a private pass has no claim to dispute.
                b = self._body()
                reason = b.get("reason")
                if reason not in db.REPORT_REASONS:
                    return self._err(400, "unknown reason")
                try:
                    sid = int(b.get("id"))
                except (TypeError, ValueError):
                    return self._err(400, "bad id")
                row = db.sighting(sid)
                if not row or row.get("tier") != "public":
                    return self._err(404, "no such public sighting")
                db.add_report(sid, reason, b.get("note") or "",
                              privacy.audit_ip(self.client_ip))
                db.audit("report", str(sid), actor="public",
                         ip=privacy.audit_ip(self.client_ip))
                return self._json({"ok": True})

            if p == "/api/review":
                # 🚨 A PUBLIC SIGHTING IS AN ASSERTION, SO IT HAS TO BE
                # RETRACTABLE - and the retraction is the most valuable label
                # this project can get: a human judgement on the camera's own
                # view of something the system actually published. It is
                # written straight back into the training set.
                if not self._is_local():
                    return self._err(403, "local only")
                b = self._body()
                sid, verdict = b.get("id"), b.get("verdict")
                if verdict not in ("confirmed", "retracted", "promoted"):
                    return self._err(400, "verdict must be confirmed, retracted "
                                          "or promoted")
                row = db.sighting(int(sid)) if sid else None
                if not row:
                    return self._err(404, "no such sighting")

                if verdict == "promoted":
                    # A miss being corrected. The plate stays gone - see
                    # db.promote_sighting.
                    conf = None
                    try:
                        from detect import bank
                        j = bank.find_by_sighting(int(sid))
                        if j:
                            conf = ((json.loads(j.read_text(encoding="utf-8"))
                                     .get("clip") or {}).get("conf"))
                    except Exception:
                        pass
                    db.promote_sighting(int(sid), "police", conf,
                                        "confirmed by the camera operator; the "
                                        "classifier identified it but a gating "
                                        "bug held it private")
                else:
                    db.review_sighting(int(sid), verdict)
                # Feed it back as ground truth. Best effort: a node that ran
                # with --no-bank has no crop to label, and that must not make
                # the retraction fail - taking the claim down matters more.
                labelled = False
                try:
                    sys.path.insert(0, str(Path(__file__).parent))
                    from detect import bank
                    labelled = bank.label_by_sighting(
                        int(sid),
                        "civilian" if verdict == "retracted" else "police")
                except Exception:
                    pass
                db.audit(f"review_{verdict}", str(sid), actor="operator",
                         ip=privacy.audit_ip(self.client_ip))
                return self._json({"ok": True, "labelled": labelled,
                                   **db.review_stats()})

            if p == "/api/key/qr":
                # 🚨 THE TOKEN ARRIVES IN A POST BODY, NEVER A QUERY STRING.
                # A key in a URL is written to every proxy log between here and
                # the browser, kept in history, and leaked in the Referer of
                # any outbound link. The same reason operator_auth refuses to
                # read one from the query.
                b = self._body()
                nid, tok = str(b.get("node_id") or ""), str(b.get("token") or "")
                nd = db.node(nid) if nid else None
                if not nd or not nd.get("token") or not tok:
                    return self._err(404, "unknown camera")
                import secrets as _s
                if not _s.compare_digest(str(nd["token"]), tok):
                    return self._err(403, "wrong key")
                origin = b.get("origin") or ""
                if not origin.startswith(("http://", "https://")):
                    return self._err(400, "bad origin")
                # The key lives in the FRAGMENT. Browsers never transmit it, so
                # scanning this code sends nothing to any server - the
                # capability travels in the picture and stops at the device.
                url = f"{origin.rstrip('/')}/node#k={nid}.{tok}"
                try:
                    return self._send(200, qr.png(url), "image/png")
                except ValueError as exc:
                    return self._err(400, str(exc))

            if p == "/api/key/rotate":
                # Losing a key must be recoverable. Rotating mints a new token
                # and every copy of the old QR stops working at once.
                b = self._body()
                nid, tok = str(b.get("node_id") or ""), str(b.get("token") or "")
                nd = db.node(nid) if nid else None
                if not nd or not nd.get("token"):
                    return self._err(404, "unknown camera")
                import secrets as _s
                if not (_s.compare_digest(str(nd["token"]), tok) or self._is_local()):
                    return self._err(403, "wrong key")
                new = _s.token_urlsafe(24)
                c = db.connect()
                c.execute("UPDATE nodes SET token=? WHERE id=?", (new, nid))
                c.commit()
                return self._json({"ok": True, "node_id": nid, "token": new})

            if p == "/api/operator/login":
                if not operator_auth.required():
                    return self._json({"ok": True, "note": "auth is off"})
                val = operator_auth.login(self._body())
                if not val:
                    return self._err(401, "wrong token")
                self._send(200, json.dumps({"ok": True}).encode(),
                           "application/json",
                           {"Set-Cookie": operator_auth.cookie_header(val)})
                return
            if p == "/api/operator/logout":
                self._send(200, json.dumps({"ok": True}).encode(),
                           "application/json",
                           {"Set-Cookie": operator_auth.cookie_header("", clear=True)})
                return
            if p == "/api/review/bulk":
                # Clearing the possibly-missed queue in one action, after a
                # human has scrolled it and promoted the government vehicles.
                #
                # 🚨 IT CLEARS THE IDs THE PAGE SENDS, NEVER "everything the
                # query matches". Re-running the query here would also clear
                # anything that arrived between him looking and pressing the
                # button - sightings nobody has seen, marked reviewed by
                # someone who never saw them. The page knows what was on
                # screen; the server must not guess.
                #
                # Each one is still written back as a `civilian` label, so a
                # sweep of 17 non-police is 17 negatives for the classifier
                # rather than a delete.
                if not self._is_local():
                    return self._err(403, "local only")
                b = self._body()
                ids = b.get("ids") or []
                verdict = b.get("verdict")
                if verdict not in ("retracted", "confirmed"):
                    return self._err(400, "verdict must be retracted or confirmed")
                if not isinstance(ids, list) or not ids:
                    return self._err(400, "no ids")
                if len(ids) > 200:
                    return self._err(400, "too many at once; 200 is the limit")

                done, labelled, skipped = 0, 0, 0
                for raw in ids:
                    try:
                        sid = int(raw)
                    except (TypeError, ValueError):
                        skipped += 1
                        continue
                    row = db.sighting(sid)
                    if not row or row["reviewed"] is not None:
                        # Already decided - by him a moment ago, or by another
                        # tab. Not an error, and not something to overwrite.
                        skipped += 1
                        continue
                    db.review_sighting(sid, verdict)
                    done += 1
                    try:
                        from detect import bank
                        if bank.label_by_sighting(
                                sid, "civilian" if verdict == "retracted"
                                else "police"):
                            labelled += 1
                    except Exception:
                        pass
                db.audit(f"review_bulk_{verdict}", ",".join(str(i) for i in ids[:50]),
                         actor="operator", ip=privacy.audit_ip(self.client_ip))
                return self._json({"ok": True, "cleared": done,
                                   "labelled": labelled, "skipped": skipped,
                                   **db.review_stats()})

            if p == "/api/purge":
                # Deleting rows is an operator action. It was reachable by
                # anyone: it only removes ALREADY-EXPIRED data, so the damage
                # was bounded, but an unauthenticated state change on a public
                # box is a thing an attacker builds on, not a thing to leave.
                if not self._is_local():
                    return self._err(403, "operator only")
                rep = privacy.purge_expired(db.connect())
                return self._json(rep)

            return self._err(404, "no such route")
        except Exception:
            traceback.print_exc()
            self._err(500, "internal error")

    # -- auth -----------------------------------------------------------
    def _token_ok(self, nd: dict) -> bool:
        """Constant-time bearer check. A node with no token accepts anyone."""
        if not nd.get("token"):
            return True
        import hmac as _hmac
        supplied = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        return _hmac.compare_digest(supplied, nd["token"])

    # -- ingest ---------------------------------------------------------
    def _ingest(self, ev: dict) -> None:
        """Accept a detection from a node.

        The node has already done recognition locally. What arrives here is a
        few hundred bytes of structured claim plus a cropped still. No video
        ever crosses this boundary, which is the architectural reason SparrowMap
        is not a wiretap: there is no stream to intercept, subpoena or leak.
        """
        nid = ev.get("node_id")
        nd = db.node(nid) if nid else None
        if not nd:
            return self._err(404, "unknown node")
        if nd["status"] != "active":
            return self._err(403, f"node is {nd['status']}")

        # --- authenticate the node ---------------------------------------
        sig_ok = node_mod.verify_event(ev, ev.get("sig", ""), nd.get("pubkey") or "")
        if nd.get("pubkey") and not sig_ok:
            return self._err(401, "signature did not verify")
        if not self._token_ok(nd):
            return self._err(401, "bad node token")

        conf = float(ev.get("plate_conf") or 0)
        plate = ev.get("plate_text") or ""

        # ⚠️ THE PLATE CONFIDENCE GATE ONLY APPLIES TO PLATES.
        #
        # It used to reject every submission scoring under the threshold,
        # including ones carrying no plate at all - which silently discarded
        # exactly the sightings the visual identifier exists to produce. A
        # camera that cannot read plates would have been gated out by a plate
        # rule. Drop a WEAK plate; never drop the whole sighting for not
        # having one.
        if plate and conf < float(CONFIG.get("min_plate_confidence", 0.55)):
            plate, conf = "", 0.0

        evidence = dict(ev.get("evidence") or {})
        source = ev.get("source", "camera")

        # A submitter cannot hand themselves signals that are supposed to be
        # EARNED, not asserted:
        #   human_confirmed - the reviewer's judgement; only the operator tool
        #     sets it, or anybody could tag a neighbour's car and publish it.
        #   visual_police   - the trained head's verdict. It carries weight 0.0,
        #     so asserting it as a boolean adds no confidence but fabricates a
        #     second "visual" marker for the two-marker police rule, storing a
        #     stranger's plate on one real cue. It may only arise inside the
        #     gated head block from visual_police_conf/margin, which the node
        #     computes and this server re-derives.
        evidence.pop("human_confirmed", None)
        evidence.pop("visual_police", None)

        c = classify.classify(evidence)

        # A public SIGHTING and a published PLATE are different decisions.
        # `sightable` puts "a marked patrol unit was here" on the public map -
        # no identifier, so nothing to protect. `tierable` is what allows the
        # plate TEXT through, and it keeps the strict bar. Gating both on
        # `tierable` meant a plate-blind camera could never contribute
        # anything, which is most cameras. See classify.py rule 4.
        tier = "public" if (c["tierable"] or c["sightable"]) else "private"

        # A phone submission is one person's eyes, not a calibrated detector.
        # It is allowed to reach the public tier, because a human looking at a
        # marked patrol car is more reliable than any classifier - but it is
        # marked as unverified and it is attributed to the node that sent it.
        if source == "phone":
            c["why"] = f"human-submitted by {nid}, unverified; " + c["why"]

        dropped_image = None
        banked_stem = None
        if ev.get("snap_b64") and not mirror.may_store_image(tier):
            # Nothing to redact, nothing to leak, nothing to subpoena. A
            # mirror keeps photographs of published government vehicles only.
            ev.pop("snap_b64", None)
            dropped_image = "public mirror keeps no private-tier imagery"
        if ev.get("snap_b64") and not ev.get("snap"):
            pbox = ev.get("plate_box")
            pboxes = ev.get("plate_boxes") or ([pbox] if pbox else [])
            vbox = ev.get("vehicle_box")
            meta = {"ts": float(ev.get("ts") or now()), "node_id": nid,
                    "node_name": nd["name"], "tier": tier,
                    "plate_text": plate, "vclass": c["vclass"],
                    "watermark": "UNVERIFIED" if source == "phone" else ""}
            if source == "phone_node" and not pbox:
                # A phone node cannot locate a plate to redact, so it destroys
                # it instead: the crop arrives already below plate legibility.
                # store_subresolution MEASURES that rather than believing it.
                try:
                    ev["snap"] = snapshot.store_subresolution(ev["snap_b64"], meta)
                    # And keep a copy for labelling. This is the entire reason
                    # phone nodes are worth building: every window someone puts
                    # a camera in is real vehicles in real conditions, which is
                    # what the classifier has been starving for.
                    #
                    # 🚨 BUT A MIRROR MUST NEVER BANK. mirror.may_bank() existed
                    # for exactly this and was never called, so a public mirror
                    # was writing the ORIGINAL full-resolution crop to disk -
                    # the un-degraded image, the thing THREAT_MODEL promises a
                    # breach cannot yield. Labelling happens where the camera is;
                    # the mirror carries claims, not photographs of the street.
                    if mirror.may_bank():
                        from detect import bank as _bank
                        banked_stem = _bank.bank_remote(
                            snapshot.decode_bytes(ev["snap_b64"]), nid,
                            {"ts": float(ev.get("ts") or now()),
                             "cls_name": ev.get("body") or "car",
                             "det_conf": ev.get("det_conf")})
                except ValueError as exc:
                    dropped_image = str(exc)
                except Exception as exc:
                    return self._err(400, f"snapshot rejected: {exc}")
            elif tier != "public" and not pbox:
                # We cannot redact a plate we cannot locate, and a photograph of
                # a car IS a photograph of its plate. So a private-tier image
                # with no plate box is discarded rather than stored. The
                # sighting itself still counts; only the picture is dropped.
                dropped_image = "no plate box to redact on a private-tier image"
            elif source == "phone":
                # A human aimed the camera; their framing IS the crop.
                try:
                    ev["snap"] = snapshot.store_prepared(
                        ev["snap_b64"], meta,
                        plate_box=tuple(pbox) if pbox else None)
                except Exception as exc:
                    return self._err(400, f"snapshot rejected: {exc}")
            elif not vbox:
                # 🚨 A CAMERA NODE MUST SEND THE BOX IT DETECTED.
                # Without one there is nothing to crop to, and the previous
                # behaviour - fall through to the phone path - stored the whole
                # street: the neighbours' houses and other vehicles' plates,
                # none of them redacted. Drop the picture instead. The sighting
                # still counts; an un-croppable image is not worth a bystander.
                dropped_image = "camera submission carried no vehicle_box to crop to"
            else:
                try:
                    ev["snap"] = snapshot.store_submitted(
                        ev["snap_b64"], meta, tuple(vbox),
                        plate_box=tuple(pbox) if pbox else None,
                        plate_boxes=[tuple(b) for b in pboxes])
                except Exception as exc:
                    return self._err(400, f"snapshot rejected: {exc}")

        ts = float(ev.get("ts") or now())

        # ⚠️ NEVER nd["lat"] / nd["lon"] HERE. Those are the camera's TRUE
        # coordinates, and /api/sightings serves whatever is stored to anyone.
        # Storing them defeated the node-position jitter entirely - one
        # sighting gave up the exact camera location. See
        # nodes.sighting_position.
        #
        # The seed makes the position stable for this sighting and different
        # from the next one, so passes spread along the watched stretch instead
        # of stacking 31 dots on one pixel.
        s_lat, s_lon = node_mod.sighting_position(
            nd, ev.get("lat"), ev.get("lon"),
            seed=f"{nid}:{ts:.3f}:{ev.get('snap_sha256') or plate or ''}")

        # An empty string is not "no plate", it is a value - and it was being
        # counted as a distinct vehicle. Store the absence as an absence.
        phash = privacy.plate_hash(plate, ev.get("plate_state", "")) or None

        rec = {
            "node_id": nid, "ts": ts,
            "lat": s_lat, "lon": s_lon,
            "tier": tier,
            "plate_hash": phash,
            # Plate text rides on `tierable` alone, NOT on the tier. A public
            # SIGHTING is public because it carries no identifier; attaching an
            # unverified plate to it would smuggle the identifier back in
            # through the very row that was supposed to be identifier-free.
            # Stored for a PUBLIC-tier row, served only after a human confirms
            # it - see privacy.redact. The photograph on a public row already
            # shows the plate, so keeping the text alongside adds no exposure
            # that the image did not; what it adds is SEARCH, and search waits
            # for a person. A retraction purges both (db.review_sighting).
            # 🚨 PLATE TEXT RIDES ON `tierable` ALONE. The old `or tier=="public"`
            # attached a plate to any public row - including a `sightable`-only
            # dot, which is public precisely BECAUSE it carries no identifier.
            # That smuggled the identifier back into the row that was supposed to
            # be identifier-free, the exact thing the comment below warns of.
            "plate_text": plate if c["tierable"] else None,
            "plate_state": ev.get("plate_state") if c["tierable"] else None,
            "plate_conf": conf,
            "vclass": c["vclass"], "vclass_conf": c["conf"], "vclass_why": c["why"],
            "color": ev.get("color"), "body": ev.get("body"),
            "make": ev.get("make"), "model": ev.get("model"),
            "heading": ev.get("heading"), "speed_mph": ev.get("speed_mph"),
            "snap": ev.get("snap"), "source": ev.get("source", "camera"),
            "bank_ref": (ev.get("bank_ref") or None),
            "sig_ok": 1 if sig_ok else 0,
        }
        # 🚨 IS THIS A NEW VEHICLE, OR THE SAME ONE STILL CROSSING THE FRAME?
        # A tracker that loses a vehicle behind a window pillar and re-acquires
        # it produces several completed tracks for one pass, and each posted its
        # own sighting - three dots on the map for one patrol car. Fold them.
        # See db.merge_window_row for why the test is deliberately blunt.
        prior = (db.merge_window_row(nid, c["vclass"], ts)
                 if tier == "public" else None)
        if prior:
            db.bump_detections(prior["id"], ts, c["conf"])
            db.heartbeat(nid, ts)
            return self._json({"id": prior["id"], "tier": tier,
                               "vclass": c["vclass"], "merged_into": prior["id"],
                               "why": "same pass as a sighting seconds earlier"})

        # Reduce BEFORE the write, never on the way out: a read-time redaction
        # leaves the data on the disk, and the disk is what gets copied.
        rec = mirror.strip_sighting(rec)
        rec["id"] = db.insert_sighting(rec)
        # 🚨 LINK THE BANKED CROP TO THE SIGHTING IT CAME FROM.
        # Without this a remote crop is an orphan: labelling it "police" in the
        # UI would record the judgement and leave the map untouched, because
        # _sync_sighting has no id to promote. A volunteer's phone catching a
        # patrol car has to be able to reach the map, or their contribution is
        # training data and nothing else.
        if banked_stem:
            try:
                from detect import bank as _bank
                _bank.link_sighting(banked_stem, rec["id"])
            except Exception:
                pass
        # A node that is posting is self-evidently awake, so a submission is
        # also a heartbeat. Detectors that never learn to beat still show
        # online while they are actually working.
        db.heartbeat(nid, ts)
        FEED.publish(rec)
        out = {"id": rec["id"], "tier": tier, "vclass": c["vclass"], "why": c["why"]}
        if dropped_image:
            out["image_dropped"] = dropped_image
        return self._json(out)

    # -- server-sent events ---------------------------------------------
    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = FEED.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            last_beat = now()
            while True:
                try:
                    rec = q.get(timeout=5.0)
                    payload = json.dumps(_public_rows([rec])[0], default=str)
                    self.wfile.write(f"event: sighting\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    if now() - last_beat > 15:
                        self.wfile.write(b": beat\n\n")
                        self.wfile.flush()
                        last_beat = now()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            FEED.unsubscribe(q)


# ---------------------------------------------------------------------------
# Background chores
# ---------------------------------------------------------------------------

def _janitor() -> None:
    """Enforce retention on a timer.

    A retention promise that only runs when someone remembers to click a button
    is not a retention promise.
    """
    while True:
        try:
            rep = privacy.purge_expired(db.connect())
            if rep["private_deleted"] or rep["public_deleted"]:
                print(f"[janitor] purged {rep}")
        except Exception:
            traceback.print_exc()
        time.sleep(600)


def _simulator() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "sources"))
    import synthetic
    print("[sim] simulated town running (source=synthetic)")
    synthetic.run(ticks=0, dt=4.0, sleep=1.0, on_sighting=FEED.publish)


def _tls_listener(port: int) -> None:
    """Second listener over TLS.

    A browser will not hand a page the camera or a precise GPS fix unless the
    origin is secure, so the phone contributor page is unusable over plain http
    on the LAN. Self-signed is enough: the browser warns once, and accepting it
    makes the origin secure. (Same wall, same fix, as the acoustic sonar page.)
    """
    import ssl
    crt = Path(__file__).parent / "certs" / "sparrow.crt"
    key = Path(__file__).parent / "certs" / "sparrow.key"
    if not (crt.exists() and key.exists()):
        print("[tls] no certs in ./certs, https listener not started")
        return
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    import os as _os
    from dualstack import serve
    srv = serve(Handler, port, _os.environ.get("SPARROW_BIND") or "::")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"  https  ->  https://localhost:{port}/app   (phone camera)")
    srv.serve_forever()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="SparrowMap hub")
    ap.add_argument("--port", type=int, default=CONFIG.get("http_port", 8150))
    ap.add_argument("--https-port", type=int, default=CONFIG.get("https_port", 8151))
    # ⚠️ THE SIMULATOR IS OFF BY DEFAULT AND MUST STAY THAT WAY.
    #
    # It was invaluable for building the whole system before any hardware
    # existed. It is a liability the moment anyone else can see the map,
    # because a synthetic sighting is indistinguishable from a real one once
    # it is a dot on a map with a photograph attached - and this project's
    # entire value is that what it shows is true.
    #
    # Opt in explicitly with --sim, and everything it writes is tagged
    # source='synthetic' so it can always be found and removed.
    ap.add_argument("--sim", action="store_true",
                    help="run the SIMULATED town (development only - writes fake sightings)")
    args = ap.parse_args()

    # 🚨 FAIL-CLOSED BACKSTOP - RUNS BEFORE ANYTHING BINDS A SOCKET.
    # When operator auth is not effectively required (operator_auth.required is
    # also forced on by behind_tls), operator power is granted purely by source
    # IP, which is only safe on loopback: `::`/`0.0.0.0` are wildcard binds that
    # expose the port to the whole network, where "the request came from an
    # operator address" stops meaning anything. The repo ships no config.json and
    # a first run auto-generates one from fail-open defaults, so this is the last
    # thing between a fresh `python hub.py` and an internet-reachable box that
    # trusts anyone. It runs here, before the HTTP and TLS listeners start, so no
    # socket is ever briefly bound on a public interface before the check.
    import os as _os
    import ipaddress as _ip
    host = _os.environ.get("SPARROW_BIND") or "::"

    def _loopback_only(h: str) -> bool:
        h = (h or "").strip()
        if h in ("localhost",):
            return True
        try:
            return _ip.ip_address(h).is_loopback
        except ValueError:
            return False        # "::", "0.0.0.0", or a hostname => not loopback

    if not _loopback_only(host) and not operator_auth.required():
        raise SystemExit(
            f"REFUSING TO START: binding {host}:{args.port} with operator "
            "authentication OFF lets anyone who can reach this port act as the "
            "operator (retract, promote, confirm - which publishes a plate). "
            "Fix ONE of:\n"
            "  * set operator_requires_auth: true in config.json (then log in "
            "at /login with the token in data/operator.token), or\n"
            "  * set behind_tls: true if a proxy terminates TLS in front, or\n"
            "  * bind loopback only: SPARROW_BIND=127.0.0.1 python hub.py")

    db.init()
    threading.Thread(target=_janitor, daemon=True).start()
    if args.sim:
        print("!! SIMULATOR ON - this map will contain FAKE sightings (source=synthetic)")
        threading.Thread(target=_simulator, daemon=True).start()
    threading.Thread(target=_tls_listener, args=(args.https_port,),
                     daemon=True).start()

    # Dual-stack (see dualstack). `host` and the fail-closed bind backstop were
    # computed right after arg-parse, before any listener bound a socket.
    from dualstack import serve
    srv = serve(Handler, args.port, host)
    print(f"SparrowMap hub {VERSION}  ->  http://localhost:{args.port}/  "
          f"(bound to {host})")
    print(f"  policy: civilian plates hashed, {CONFIG['civilian_retention_days']}d "
          f"retention, pepper rotates every {CONFIG['pepper_rotation_days']}d")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")


if __name__ == "__main__":
    main()
