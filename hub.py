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
import re
import secrets
import socket
import sys
import queue
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import admission
import classify
import community
import db
import mapdata
import microcache
import mirror
import node_credentials
import node_label
import node_lifecycle
import node_self
import operator_admin
import operator_auth
import operator_bugs
import pages
import qr
import nodes as node_mod
import privacy
import ratelimit
import response_policy
import review_api
import review_auth
import reviewer_mutation
import reviewer_read
import snapshot
import static
import tiles
import transport
from core import CONFIG, DATA, PUBLIC, SNAPS, is_operator_addr, now
from ratelimit import RATE, rate_ok

# --------------------------------------------------------------------------
# Basemap tiles
#
# Moved to tiles.py in Stage 1B (tile substage). TILES/TILE_UPSTREAM/
# TILE_SUBDOMAINS/TILE_MAX_ZOOM/TILE_CACHE_MAX/_tile_count/_tile_prune_lock/
# _tile_prune/_TILE_FETCH are now aliases to the same objects in tiles.py -
# not copies - so existing tooling that reaches into hub.TILES / hub._TILE_FETCH
# / hub._tile_prune still observes/mutates the real shared state.
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
TILES = tiles.TILES
TILE_UPSTREAM = tiles.TILE_UPSTREAM
TILE_SUBDOMAINS = tiles.TILE_SUBDOMAINS
TILE_MAX_ZOOM = tiles.TILE_MAX_ZOOM
TILE_CACHE_MAX = tiles.TILE_CACHE_MAX


def _tile_prune() -> None:
    """Moved to tiles._tile_prune in Stage 1B (tile substage)."""
    return tiles._tile_prune()


VERSION = "0.1.0"


# High-water marks for /api/health, and when this process started. Kept in
# memory on purpose: they describe THIS process, they cost nothing, and a file
# would need permissions the hub should not want. They reset on restart, which
# is why uptime_s is published beside them - peaks without a window are a
# number pretending to be a measurement.
_STARTED = time.time()
_PEAK = {"fd_pct": 0.0, "threads": 0}

# The window `since` timestamps are rounded to for caching. Must match
# CACHE_BUCKET_S in public/app.js: the frontend rounds so its polls share a URL,
# and the server rounds so a client that DOESN'T round cannot mint a new cache
# key per request. Protection that depends on the client cooperating is not
# protection.
#
# Moved to microcache.py in Stage 1B (step 3). Aliased here, unchanged.
CACHE_BUCKET_S = microcache.CACHE_BUCKET_S

# Admission/semaphore accounting (MAX_REQUESTS, MAX_HEAVY, MAX_INGEST,
# HEAVY_ROUTES, INGEST_ROUTES, *_WAIT_S and the semaphores themselves) moved
# to admission.py in Stage 1B (step 2). Aliased here, unchanged, because
# /api/health below reads MAX_REQUESTS/MAX_HEAVY/MAX_INGEST by these names.
MAX_REQUESTS = admission.MAX_REQUESTS
MAX_HEAVY = admission.MAX_HEAVY
MAX_INGEST = admission.MAX_INGEST

# MAX_BODY rather than taste: an entry is a node id and a token, ~80 bytes of
# JSON, so a thousand is ~80 KB and comfortably inside the body cap. The
# 4,400-camera fleet therefore arrives as five requests instead of 4,400, and
# a caller cannot make one request cost unbounded work.
BULK_BEAT_MAX = 1000

# How coarsely a viewport box is snapped before it is used as a cache key or a
# filter.
#
# THIS IS A CACHE-KEY CARDINALITY KNOB, NOT AN ACCURACY KNOB, AND 0.1 COST
# US THE BOX. At ~11 km, two people looking at the same city from slightly
# different scroll positions produced DIFFERENT keys, so the single-flight
# below collapsed almost nothing and the edge missed almost every time. During
# the 2026-08-16 spike that meant a crowd of readers each triggered their own
# 3.4 MB build. At ~55 km a whole city is one key, so a thousand readers of the
# same place become one build and 999 cache hits - which is the entire point of
# having a key at all.
#
# The cost is that the superset returned is larger, so a phone viewport carries
# some points just off its edges. That is invisible on a map and cheap; the
# alternative was measured and it was an out-of-memory kill.
#
# Moved to microcache.py in Stage 1B (step 3). Aliased here, unchanged, because
# _do_GET_inner below (application logic, not in scope for this stage) still
# reads BOX_SNAP/_snap_box by these names.
BOX_SNAP = microcache.BOX_SNAP


def _snap_box(raw: str) -> str:
    """`S,W,N,E` snapped OUTWARD to the BOX_SNAP grid, or "" if unparseable.

    Moved to microcache.snap_box in Stage 1B (step 3).
    """
    return microcache.snap_box(raw)


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
# Moved to ratelimit.py in Stage 1B (step 1). `rate_ok`/`RATE` are imported
# above for existing call sites within this file.
# ---------------------------------------------------------------------------

# The upstream-fetch concurrency bound moved to tiles.py in Stage 1B (tile
# substage). `_TILE_FETCH` is aliased here (not copied) so existing tooling
# reaching into hub._TILE_FETCH still observes the real semaphore.
_TILE_FETCH = tiles._TILE_FETCH


# Anonymous viewers get a rolling daily alias instead of the real plate hash so
# a track is followable on screen but not archivable across days. We keep the
# reverse map in memory only, and it dies with the process.
# Geocode results, keyed by the lowercased query. Nominatim's usage policy is
# roughly one request a second and it is a free service run by volunteers, so a
# viral hour must not be passed straight through to them. A day is plenty: a
# town does not move.
# FIPS state codes. Broadcastify's /listen/stid/<n> IS the FIPS code - checked
# against Louisiana (22) and Michigan (26) rather than assumed, because the
# county ids on the same site are opaque internal numbers and it would have
# been easy to believe both were.
US_STATE_FIPS = {
    "AL": 1,  "AK": 2,  "AZ": 4,  "AR": 5,  "CA": 6,  "CO": 8,  "CT": 9,
    "DE": 10, "DC": 11, "FL": 12, "GA": 13, "HI": 15, "ID": 16, "IL": 17,
    "IN": 18, "IA": 19, "KS": 20, "KY": 21, "LA": 22, "ME": 23, "MD": 24,
    "MA": 25, "MI": 26, "MN": 27, "MS": 28, "MO": 29, "MT": 30, "NE": 31,
    "NV": 32, "NH": 33, "NJ": 34, "NM": 35, "NY": 36, "NC": 37, "ND": 38,
    "OH": 39, "OK": 40, "OR": 41, "PA": 42, "RI": 44, "SC": 45, "SD": 46,
    "TN": 47, "TX": 48, "UT": 49, "VT": 50, "VA": 51, "WA": 53, "WV": 54,
    "WI": 55, "WY": 56, "PR": 72,
}

_GEO_CACHE: dict = {}

# Per-day plate-hash alias mapping used by /api/plate, /api/sightings,
# /api/sighting/<id>, /api/track/<hash> and the SSE feed.
#
# Moved to privacy.py in Stage 2B so mapdata.py (which must not import hub)
# can resolve/record aliases through the same shared state. Aliased here,
# not copied, so any existing caller that reaches hub._ALIAS/hub._alias_map/
# hub._resolve_hash still observes the SAME dict/list objects privacy.py now
# owns.
_ALIAS = privacy.ALIAS
_ALIAS_DAY = privacy.ALIAS_DAY


def _alias_map(rows: list[dict]) -> None:
    """Moved to privacy.alias_map in Stage 2B."""
    return privacy.alias_map(rows)


def _resolve_hash(h: str) -> str:
    """Moved to privacy.resolve_hash in Stage 2B."""
    return privacy.resolve_hash(h)



# The packaged desktop app. Hosted on GitHub releases rather than here - see the
# /download route for why - and CHECKED rather than assumed: the button on
# /IPCamera appears only when the asset really exists, so the page never offers
# a download that 404s. Cached, because this is a third-party round trip on a
# path a crowd may hit.
#
# Moved to pages.py in Stage 2A. Aliased here, not copied, so existing tooling
# that reaches into hub.DOWNLOAD_URL / hub._DL_CACHE still observes the real
# shared state.
DOWNLOAD_URL = pages.DOWNLOAD_URL
_DL_CACHE = pages._DL_CACHE
_DL_TTL_S = pages._DL_TTL_S


def _download_url():
    """Moved to pages.download_url in Stage 2A."""
    return pages.download_url()


def _public_rows(rows: list[dict]) -> list[dict]:
    """Moved to privacy.public_rows in Stage 2B."""
    return privacy.public_rows(rows)


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

    # 🚨 THE ADMISSION GATE, AND IT COUNTS REQUESTS - NOT CONNECTIONS.
    #
    # Moved to admission.py in Stage 1B (step 2); aliased here so existing
    # callers of Handler._INFLIGHT/_HEAVY/_INGEST/etc. (both inside this file
    # and in tools/test_overload.py, tools/test_slowloris.py,
    # tools/test_cache_key_leak.py) keep reaching the SAME semaphore/dict
    # objects admission.py now owns - not fresh copies of them.
    _INFLIGHT = admission.INFLIGHT
    _HEAVY = admission.HEAVY
    _HEAVY_ROUTES = admission.HEAVY_ROUTES
    _INGEST = admission.INGEST
    _INGEST_ROUTES = admission.INGEST_ROUTES
    _INFLIGHT_PATHS = admission.INFLIGHT_PATHS
    _INFLIGHT_LOCK = admission.INFLIGHT_LOCK
    _SLOW_HELD = admission.SLOW_HELD
    _SLOW_S = admission.SLOW_S

    def handle_one_request(self):
        # One handler instance serves every request on a keep-alive connection,
        # so per-REQUEST state must be reset per request. Clearing the cache key
        # here means no path - including ones that bypass _send entirely - can
        # carry a key from one request into the next.
        self.__dict__.pop("_micro_key", None)
        self.__dict__.pop("_body_done", None)
        # The read that BLOCKS on an idle keep-alive connection happens inside
        # the parent, before any handler runs. Wrapping the whole call would put
        # us straight back to holding a permit for an idle socket, so the gate
        # goes around the DISPATCH instead - see _dispatch_guarded below, which
        # do_GET/do_POST route through.
        return super().handle_one_request()

    def _drain_body(self) -> None:
        """Read and discard a request body we are about to refuse.

        Moved to transport.drain_body in Stage 1A; see that function's
        docstring for the desync reasoning this preserves unchanged.
        """
        return transport.drain_body(self)

    def _too_busy(self) -> None:
        # Moved to admission.too_busy_response in Stage 1B.
        return admission.too_busy_response(self)

    # Public read paths served IDENTICALLY to every anonymous viewer, so a short
    # shared cache collapses thousands of pollers into ~one origin fetch/window.
    #
    # Moved to microcache.py in Stage 1B (step 3). Aliased here, unchanged,
    # because _cache_control below reads _CACHEABLE_API by this name.
    _CACHEABLE_API = microcache.CACHEABLE_API

    def _cache_control(self) -> str:
        """Per-path caching policy.

        Moved to response_policy.cache_control in Stage 1B (step 4).
        """
        return response_policy.cache_control(self.path, getattr(self, "_status", 200))


    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        # A fresh nonce per response. Four pages carry an inline <script>, and
        # the alternatives were both worse: allowing 'unsafe-inline' would make
        # the policy decorative, and moving the code out to four new files
        # would scatter page logic away from the page for a deployment detail.
        # Recorded before headers are built so _cache_control can see it - a
        # failure and a success must not get the same caching policy.
        self._status = code
        self._nonce = secrets.token_urlsafe(12)
        # ⚠️ SUBSTITUTE BEFORE Content-Length IS COMPUTED.
        # The placeholder and the nonce are different lengths, so filling it in
        # after the header was set truncated every page by seven bytes - a
        # silently broken site with a valid-looking response.
        if b"@@NONCE@@" in body:
            body = body.replace(b"@@NONCE@@", self._nonce.encode())

        # 🚨 FILL THE MICRO-CACHE HERE, AFTER THE NONCE SUBSTITUTION.
        # Caching a body that still held @@NONCE@@ would serve a placeholder to
        # everyone who got a hit; caching one WITH a nonce baked in would reuse
        # a single nonce across viewers. Neither matters for the JSON API paths
        # this is limited to (they carry no nonce), and the ordering is written
        # down so it stays true if that ever changes.
        # 🚨 TAKEN AND CLEARED IN ONE STEP, BEFORE ANY EARLY RETURN.
        # This used to clear the key only inside the success branch, so a
        # non-200 left it set on a handler instance that lives for the WHOLE
        # keep-alive connection - and the next 200 on that connection was then
        # stored under the previous request's public key. _too_busy() made it
        # reachable: it writes its 503 straight to wfile and never enters
        # _send, so it could not clear anything, and it fires exactly during
        # overload. Worst case the body cached under a public key came from
        # /api/node/me, the one endpoint that returns a camera's TRUE position.
        key = self.__dict__.pop("_micro_key", None)
        if key and code == 200:
            # Moved to microcache.store in Stage 1B (step 3).
            microcache.store(key, body)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A caller that set its own Cache-Control (the tile proxy) wins; this
        # also stops the two conflicting Cache-Control headers the tile response
        # used to carry. Otherwise apply the per-path policy.
        if not (extra and any(k.lower() == "cache-control" for k in extra)):
            self.send_header("Cache-Control", self._cache_control())

        # ---- headers that protect the VISITOR ---------------------------
        # Moved to response_policy.security_headers in Stage 1B (step 4).
        for k, v in response_policy.security_headers(self._nonce):
            self.send_header(k, v)

        # The public map is meant to be embeddable and mirrorable by anyone.
        # Operator JSON is not, and a wildcard on it is needless surface even
        # with a SameSite=Strict cookie in front.
        #
        # Moved to response_policy.cors_allowed in Stage 1B (step 4).
        if response_policy.cors_allowed(self.path):
            self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        # 🚨 A HEAD RESPONSE CARRIES THE HEADERS AND NOT THE BODY.
        # Writing one anyway desynchronises a keep-alive connection: the client
        # reads our body as the start of its NEXT response. See do_HEAD.
        if getattr(self, "_head_only", False):
            return
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self) -> None:
        """HEAD, which this server answered with 501 for its whole life.

        🚨 IT LOOKS LIKE AN EDGE CASE AND IS NOT. BaseHTTPRequestHandler has no
        default do_HEAD, so every HEAD got "501 Unsupported method" - and HEAD is
        what link checkers, uptime monitors, CDNs and link-preview crawlers use
        BEFORE they fetch anything. A site that 501s them looks broken to
        exactly the tools that decide whether a shared link is worth showing,
        which matters most at the moment a link is spreading.

        Found by my own test doing `curl -I` and reading 501 as a broken route.

        Handled by running the ordinary GET path and dropping the body in
        _send, so a HEAD can never disagree with the GET it describes - the
        status, the headers and the Content-Length are all the real ones.

        Moved to transport.handle_head in Stage 1A.
        """
        return transport.handle_head(self)

    def _tile(self, path: str) -> None:
        """Moved to tiles.serve in Stage 1B (tile substage)."""
        return tiles.serve(self, path)

    def _json(self, obj, code: int = 200) -> None:
        return transport.send_json(self, obj, code)

    def _err(self, code: int, msg: str) -> None:
        # Moved to transport.send_error in Stage 1A; see that function's
        # docstring for the drain-before-refusing reasoning this preserves
        # unchanged.
        return transport.send_error(self, code, msg)

    def _file(self, path: Path) -> None:
        """Moved to static.serve in Stage 1B (step 5)."""
        return static.serve(self, path)

    # A sighting carries a base64 vehicle crop, which is the only large body this
    # server has any reason to accept. 8 MB covers a generous JPEG with base64's
    # 33% overhead; everything else is a few hundred bytes. Bigger than this is
    # not a real submission, it is memory pressure on a 2-vCPU/3-GB box.
    #
    # Kept as a Handler attribute for compatibility (existing call sites and
    # tools reference self.MAX_BODY / Handler.MAX_BODY); the value itself now
    # lives in transport.MAX_BODY, which _body reads from via transport.py.
    MAX_BODY = transport.MAX_BODY

    def _body(self) -> dict:
        # Moved to transport.read_body in Stage 1A; see that function's
        # docstring for the slow-loris deadline reasoning this preserves
        # unchanged.
        return transport.read_body(self)

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
    # Wrappers, not edits inside the handlers: both are hundreds of lines with
    # many returns and their own try/except at the bottom, and a permit that
    # leaks on one path takes the site down slowly. try/finally around a single
    # call covers every exit including the 500 handler.
    # 🚨 TILES AND STATIC FILES ARE NOT GATED, AND THAT IS THE WHOLE POINT.
    #
    # The cap exists to bound work that costs CPU and the sqlite writer. A tile
    # is the opposite: on a cache miss `_tile` fetches from the CDN and, as the
    # note above RATE already said, "holds a worker thread up to 15s". Gating it
    # meant a single visitor panning to a new area fired 20-40 tile misses that
    # each took a permit and sat on it for fifteen seconds waiting on somebody
    # else's network - so all 32 permits went to tiles and every real request
    # got "busy - too many requests in flight". Reported as the map being down
    # while /api/health, checked from elsewhere, answered 200 the whole time.
    #
    # 📌 The permit was measuring the wrong thing AGAIN - first connections
    # instead of requests, now waiting instead of working. What must be bounded
    # is work this box does, never time it spends waiting on someone else.
    #
    # Tiles are not unprotected: they carry their own budget (RATE
    # "/api/tile" = 600/5min) which limits exactly the upstream amplification,
    # and dualstack's thread ceiling plus the idle timeout still bound them.
    # Static files under /static and /vendor are disk reads served from cache
    # and a single page load pulls a dozen; gating those buys nothing either.
    _UNGATED = ("/api/tile/", "/static/", "/vendor/")

    def _gated(self, inner, label: str):
        """Run a handler holding one permit, and RECORD that it holds it.

        Moved to admission.run_gated in Stage 1B; kept as a method so
        do_GET/do_POST and external tooling keep calling self._gated(...)
        unchanged.
        """
        return admission.run_gated(self, inner, label)

    # 🚨 THE EDGE CACHE THIS SERVER WAS DESIGNED AROUND IS NOT SWITCHED ON.
    #
    # _cache_control says it plainly: the origin caps near 55 req/s on map data,
    # and survives a crowd only because "thousands of viewers collapse to about
    # one origin fetch per window". That assumes something in front is doing the
    # collapsing. sparrowmap.com is proxied by Cloudflare; map.sparrowmap.com
    # resolves straight to this box, so every viewer, every poll and every tile
    # arrives here. Measured during the outage: 128 requests in flight, 254
    # threads, /api/nodes and /api/sightings each holding a permit for 10-21
    # SECONDS - plain reads, starved of CPU by the crowd doing them all at once.
    #
    # So the origin does the collapsing itself. These responses are PUBLIC and
    # byte-identical for every viewer - that is already why they carry a shared
    # Cache-Control - so one hundred simultaneous identical requests can be one
    # computation and ninety-nine memory reads.
    #
    # ⚠️ ONLY _CACHEABLE_API PATHS, and the key includes the query string. The
    # search, operator and per-visitor routes stay no-store and are never
    # entered here: caching a plate search would build the record of who looked
    # up what that no-store exists to prevent.
    # Moved to microcache.py in Stage 1B (step 3). Aliased here, unchanged,
    # because callers below (and existing tools) still read Handler._MICRO*
    # by these names, and they must alias the SAME dict/lock objects
    # microcache.py owns - not fresh copies - or the cache/single-flight
    # state would silently split into two disjoint pools.
    _MICRO = microcache.MICRO
    _MICRO_LOCK = microcache.MICRO_LOCK
    _MICRO_FLIGHT = microcache.MICRO_FLIGHT
    _MICRO_PARAMS = microcache.MICRO_PARAMS

    def _micro_key_for(self, path: str) -> str:
        """Cache key from the path plus ONLY the parameters that change the answer.

        Moved to microcache.key_for in Stage 1B (step 3).
        """
        return microcache.key_for(path, urlparse(self.path).query)

    def _micro_ttl(self) -> float:
        """Moved to microcache.ttl_for in Stage 1B (step 3)."""
        return microcache.ttl_for(self._cache_control())

    @staticmethod
    def _route_label(path: str) -> str:
        """A stable label for a path, so counters key on ROUTES not URLs.

        Moved to transport.route_label in Stage 1A.
        """
        return transport.route_label(path)

    def do_GET(self) -> None:
        if self.path.startswith(Handler._UNGATED):
            return self._do_GET_inner()

        p = urlparse(self.path).path
        ttl = self._micro_ttl() if p in self._CACHEABLE_API else 0.0
        if not ttl:
            return self._gated(self._do_GET_inner, self._route_label(p))

        key = self._micro_key_for(p)
        hit = microcache.get_hit(key)
        if hit and time.time() - hit[0] < ttl:
            # Served without taking a permit at all: a memory read is not the
            # work the gate exists to bound.
            return self._send(200, hit[1], "application/json")

        # 🚨 SINGLE-FLIGHT, NOT MERELY A TTL. A plain cache does nothing about a
        # STAMPEDE, and a stampede is precisely what was measured: 128 requests
        # in flight with /api/nodes and /api/sightings each held 10-21 seconds,
        # every one of them computing the same public answer at the same moment.
        # They all miss an empty cache together, so a TTL alone would let all 128
        # through and only help the 129th.
        #
        # So the FIRST caller computes and the rest wait for it. One hundred
        # simultaneous identical requests become one query and ninety-nine
        # waiters - which is exactly what the absent edge cache would have done.
        mine, leader = microcache.begin_or_join(key)

        if not mine:
            # ⚠️ BOUNDED WAIT. If the leader dies or is unusually slow, a
            # follower must fall through and do the work itself rather than hang
            # - a cache that can wedge a request is worse than no cache.
            leader.wait(timeout=min(ttl + 5.0, 20.0))
            hit = microcache.get_hit(key)
            if hit and time.time() - hit[0] < ttl + 5.0:
                return self._send(200, hit[1], "application/json")
            return self._gated(self._do_GET_inner, self._route_label(p))

        try:
            # Captured in _send rather than by threading a key through
            # _do_GET_inner, which is hundreds of lines with dozens of exits.
            self._micro_key = key
            return self._gated(self._do_GET_inner, self._route_label(p))
        finally:
            microcache.finish(key, leader)


    def do_POST(self) -> None:
        return self._gated(self._do_POST_inner,
                           "POST " + self._route_label(self.path.split("?")[0]))

    def _do_GET_inner(self) -> None:
        try:
            u = urlparse(self.path)
            p, q = u.path, parse_qs(u.query)

            # A mirror has no operator surface at all. Reviewing happens at
            # home, on the hub that holds the evidence to judge with.
            if not mirror.route_allowed(p):
                return self._err(404, "not found")

            # Fixed page shells, redirects and the desktop-download probe
            # pair are pure route/HTTP concerns with no domain logic, moved
            # to pages.py in Stage 2A. See that module's docstring for the
            # extraction rationale; the per-route reasoning previously
            # inline here now lives as comments on each pages.py function.
            if p == "/":                 return pages.index(self)
            if p == "/about":            return pages.about(self)
            if p == "/transparency":     return pages.transparency(self)
            if p == "/status":          return pages.status_page(self)
            if p == "/checksums":       return pages.checksums(self)
            if p in ("/support", "/donate"):
                return pages.support_or_donate(self)
            if p in ("/business", "/ipcamera"):
                return pages.business_redirect(self)
            if p == "/IPCamera":        return pages.ipcamera(self)
            if p == "/relay.py":
                return pages.relay_py(self)
            if p == "/download":
                return pages.download(self)

            if p == "/api/download":
                return pages.api_download(self)
            if p == "/hardware":         return pages.hardware(self)
            if p == "/build16":          return pages.build16(self)
            # 🚨 COMMUNITY LABELLING. Public on purpose, and safe because of
            # what it cannot do rather than who it lets in: a vote lands in a
            # SEPARATE database file with no sightings table, it never becomes a
            # label here, and every crop in a task is from a PUBLIC traffic
            # camera carrying an opaque id with no day, node, time or place.
            # See help_api.py, which exists to hold those limits in one place.
            if p == "/help":             return community.help_page(self)
            if p == "/api/help/next":
                return community.help_next(self, q)
            if p == "/api/help/stats":
                return community.help_stats(self)
            if p.startswith("/api/help/img/"):
                return community.help_img(self, p)
            # One program, three modes. /node and /key are kept as aliases
            # because keys, QR codes and bookmarks already point at them - a
            # link a volunteer printed must not stop working because the pages
            # were reorganised.
            #
            # /contribute is kept for the same reason, but it no longer has a
            # page of its own: log-by-hand was removed, so the alias lands on
            # the app rather than 404ing a printed link.
            if p in ("/app", "/node", "/key", "/contribute"):
                return pages.app_alias(self)
            # The way back in for a camera whose browser lost its key. See
            # /api/node/whoami for what was actually happening to these people.
            if p == "/admin/bugs":
                return operator_bugs.admin_bugs_page(self)
            if p == "/api/bug/list":
                return operator_bugs.bug_list(self, self.path)
            if p.startswith("/api/bug/shot/"):
                return operator_bugs.bug_shot(self, p)

            if p in ("/signin", "/login/camera"):
                return pages.signin(self)
            if p.startswith("/vendor/"):
                # Traversal guard moved together with the file-I/O primitive
                # to static.vendor_file_path/static.serve in Stage 1B (step 5)
                # - see static.py's module docstring for why the guard is not
                # safe to leave behind at this call site alone.
                return self._file(static.vendor_file_path(PUBLIC, p[8:]))
            if p.startswith("/static/"):
                return self._file(static.static_file_path(PUBLIC, p[8:]))
            if p.startswith("/snap/"):
                return self._file(static.snap_file_path(SNAPS, unquote(p[6:])))


            # --- reviewer app (token-gated; separate from operator /review) ---
            if p == "/drive":
                return community.drive_page(self)
            if p == "/api/drive/reports":
                return community.drive_reports(self)
            if p == "/api/geocode":
                # 🚨 PROXIED, NEVER CALLED FROM THE BROWSER.
                # Tiles already go through /api/tile and road lookups happen in
                # road.py for the same reason: a visitor of this site never
                # talks to a third party. A client-side geocode would hand
                # someone else every visitor's IP together with the place they
                # searched for - on a site about being watched, that is the one
                # request you cannot afford to leak.
                # `q` is already the parsed query dict in this handler; the
                # search term needs its own name or it silently shadows it.
                term = (q.get("q") or [""])[0].strip()[:120]
                if len(term) < 3:
                    return self._json({"results": []})
                # 🚨 CACHE FIRST, BUDGET SECOND. This spent the rate-limit
                # token BEFORE looking in the cache, so repeated searches for
                # the same place burned quota they never needed - and because
                # Caddy strips XFF, client_ip is 127.0.0.1 for everyone and the
                # 300/hour bucket is NETWORK-WIDE. A handful of people searching
                # the same town could 429 the search box for the entire site
                # while the answer sat in memory. The budget exists to protect
                # NOMINATIM; a cache hit never touches Nominatim.
                hit = _GEO_CACHE.get(term.lower())
                if hit and now() - hit[0] < 86400:
                    return self._json({"results": hit[1]})
                if not rate_ok("/api/geocode", self.client_ip):
                    return self._err(429, "too many searches right now; "
                                          "try again in a moment")
                # hub.py imports urllib.parse only, so urllib.request has to
                # be imported here. The first version assumed it was module-
                # level and raised NameError - which the broad `except` below
                # then reported to the user as "search unavailable right now",
                # blaming a third party for a typo. Catch only what a network
                # call can actually do to us.
                import urllib.error
                import urllib.parse as _up
                import urllib.request as _ur
                try:
                    req = _ur.Request(
                        "https://nominatim.openstreetmap.org/search?"
                        + _up.urlencode({"format": "json", "limit": "6",
                                         "q": term}),
                        # Nominatim's policy requires an identifying agent. A
                        # generic one gets the whole project blocked, and the
                        # block would look like "search is broken".
                        headers={"User-Agent": "SparrowMap/1.0 "
                                               "(https://sparrowmap.com)"})
                    with _ur.urlopen(req, timeout=12) as r:
                        raw = json.loads(r.read())
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    print(f"[geocode] upstream failed: {exc}")
                    return self._json({"results": [], "error": "search "
                                       "unavailable right now"})
                out = [{"name": str(x.get("display_name") or "")[:140],
                        "lat": float(x["lat"]), "lon": float(x["lon"]),
                        "kind": str(x.get("type") or "")}
                       for x in raw if x.get("lat") and x.get("lon")]
                if len(_GEO_CACHE) > 500:
                    _GEO_CACHE.clear()
                _GEO_CACHE[term.lower()] = (now(), out)
                return self._json({"results": out})

            if p in ("/planes", "/api/aircraft"):
                # 🧪 STAGED. Off unless a deployment opts in, so this can live
                # in the repo - local, repo and box identical - without showing
                # on a public map before it has earned a place there.
                if not CONFIG.get("aircraft_preview"):
                    return self._err(404, "not found")
                if p == "/planes":
                    return self._file(PUBLIC / "planes.html")
                try:
                    import aircraft
                except Exception as exc:
                    return self._json({"error": f"aircraft module: {exc}"})
                try:
                    box = [float(x) for x in
                           (q.get("box") or ["42.3,-84.4,43.3,-83.2"])[0].split(",")]
                except ValueError:
                    return self._err(400, "box wants lamin,lomin,lamax,lomax")
                if not rate_ok("/api/geocode", self.client_ip):
                    return self._err(429, "too many lookups right now")
                return self._json(aircraft.live(box))

            if p == "/api/scanner":
                # Where to LISTEN to public-safety radio for a place.
                #
                # 🚨 A LINK, NEVER A STREAM. Broadcastify's terms allow a feed
                # OWNER to embed their OWN feed with a domain key, and forbid
                # becoming "a redistribution layer" - proxying their audio to
                # visitors is exactly the banned case. Receiving unencrypted
                # public-safety radio is legal; rebroadcasting someone else's
                # infrastructure is a contract question, and the answer is no.
                # So this resolves a place to their STATE page and stops.
                #
                # stid is the FIPS state code, verified against two states
                # (Louisiana 22, Michigan 26) rather than assumed - county ids
                # are opaque internal numbers, so this deliberately links one
                # level up and lets the reader pick their own county. A link
                # that is always right beats a deep link that is sometimes
                # wrong about which county is watching them.
                try:
                    lat = float((q.get("lat") or [""])[0])
                    lon = float((q.get("lon") or [""])[0])
                except (TypeError, ValueError):
                    return self._err(400, "lat and lon required")
                key = f"{lat:.2f},{lon:.2f}"      # ~1 km, plenty for a state
                hit = _GEO_CACHE.get("rev:" + key)
                if hit and now() - hit[0] < 86400:
                    return self._json(hit[1])
                if not rate_ok("/api/geocode", self.client_ip):
                    return self._err(429, "too many lookups right now")
                import urllib.error
                import urllib.parse as _up
                import urllib.request as _ur
                try:
                    req = _ur.Request(
                        "https://nominatim.openstreetmap.org/reverse?"
                        + _up.urlencode({"format": "json", "zoom": "8",
                                         "lat": lat, "lon": lon}),
                        headers={"User-Agent": "SparrowMap/1.0 "
                                               "(https://sparrowmap.com)"})
                    with _ur.urlopen(req, timeout=12) as r:
                        addr = (json.loads(r.read()) or {}).get("address") or {}
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    print(f"[scanner] upstream failed: {exc}")
                    return self._json({"ok": False})
                iso = str(addr.get("ISO3166-2-lvl4") or "")   # e.g. "US-MI"
                fips = US_STATE_FIPS.get(iso.split("-")[-1].upper())
                out = {"ok": bool(fips),
                       "state": addr.get("state"),
                       "county": addr.get("county"),
                       "url": (f"https://www.broadcastify.com/listen/stid/{fips}"
                               if fips else None)}
                _GEO_CACHE["rev:" + key] = (now(), out)
                return self._json(out)

            if p == "/api/places":
                # Moved to mapdata.places in Stage 2B.
                return mapdata.places(self)

            if p == "/api/heat":
                # Moved to mapdata.heat in Stage 2B.
                return mapdata.heat(self)

            if p == "/api/node/me":
                # Stage 2D1 moved the route-specific HTTP glue to node_self.py.
                # The inline domain/consent logic stays here only until the
                # later route-adapter/service split has a clean seam.
                return node_self.node_me(self, q)

            if p == "/aim":
                # Aiming a camera from the device that owns it. The capability
                # is in the fragment or in this device's localStorage, so the
                # page is served to anyone and shows nothing without one.
                return self._file(PUBLIC / "aim.html")

            # 🚨 ONE APP, THREE DOORS. `/rv` was the only way in and nothing
            # linked to it, so in practice there was no way in at all: the map
            # never mentioned it, and the single pointer that existed was a
            # banner on /app shown once, just after a token was minted. Close
            # that tab and the review queue is gone.
            #
            # The two scopes are now separate PAGES rather than a dropdown
            # inside one, because they are different jobs. "Is this my camera's
            # catch right?" and "help work through everyone's backlog" want
            # different framing, different empty states and different urgency,
            # and a control that silently changes which cameras you are ruling
            # on is a control someone will get wrong.
            #
            # Same file for all three: the page reads its own path and locks
            # its scope to it. Three copies of a 16 KB reviewer app would drift
            # apart by the second bug fix.
            if p in ("/rv", "/rv/mine", "/rv/pool"):
                return self._file(PUBLIC / "rv.html")
            if p == "/rv/admin":
                # Served open; the token endpoints it drives are operator-gated,
                # and the page shows an operator sign-in until you are.
                return self._file(PUBLIC / "rv-admin.html")
            if p == "/api/rv/me":
                return reviewer_read.rv_me(self)
            if p == "/api/rv/queue":
                return reviewer_read.rv_queue(self, q)
            if p == "/api/rv/contributed":
                return reviewer_read.rv_contributed(self)

            # --- retracted-photo shelf (pool-scope reviewer only) ------------
            # A retraction demotes the row and drops the plate, but never
            # touched the picture, and /snap/<name> serves any file by name -
            # so the photograph of a vehicle the map no longer claims stayed
            # fetchable by direct URL. This is where those are listed and
            # deleted. Gated on pool scope rather than an operator secret: a
            # mirror has no operator surface by design, and a pool reviewer can
            # already see and retract everything anyway.
            if p == "/rv/retracted":
                return self._file(PUBLIC / "retracted.html")
            if p == "/api/rv/retracted":
                r = review_auth.identify(self.headers)
                if not r:
                    return self._err(401, "not signed in")
                if not review_api.is_trusted(r):
                    return self._err(403, "pool reviewers only")
                return self._json(review_api.retracted(r))
            if p.startswith("/api/rv/retracted/photo/"):
                r = review_auth.identify(self.headers)
                if not r:
                    return self._err(401, "not signed in")
                try:
                    sid = int(p.rsplit("/", 1)[-1])
                except ValueError:
                    return self._err(400, "bad id")
                b = review_api.retracted_photo_bytes(r, sid)
                if not b:
                    return self._err(404, "no photo")
                return self._send(200, b, "image/jpeg",
                                  {"Cache-Control": "no-store"})

            # --- held photographs: fix what is already on the map -------------
            # Where a privacy flag lands. The picture is off the map already
            # (moved into core.HELD, which no route serves); this is where a
            # human crops the person out and puts it back, restores it whole, or
            # deletes it. Any reviewer may work their own cameras' items, on the
            # rule in review_api.fix_photo: the two open actions can only ever
            # show LESS, and the one that re-publishes the flagged pixels is
            # gated on a trusted token.
            if p == "/rv/photos":
                return self._file(PUBLIC / "photos.html")
            if p == "/api/rv/held":
                r = review_auth.identify(self.headers)
                if not r:
                    return self._err(401, "not signed in")
                return self._json(review_api.held_queue(r))
            if p.startswith("/api/rv/held/photo/"):
                r = review_auth.identify(self.headers)
                if not r:
                    return self._err(401, "not signed in")
                try:
                    sid = int(p.rsplit("/", 1)[-1])
                except ValueError:
                    return self._err(400, "bad id")
                b = review_api.held_photo_bytes(r, sid)
                if not b:
                    return self._err(404, "no photo")
                # no-store, like the retracted shelf: this is a picture that has
                # been taken off the public map, and a cache is a second copy of
                # it in a place nobody can revoke.
                return self._send(200, b, "image/jpeg",
                                  {"Cache-Control": "no-store"})

            if p.startswith("/api/rv/crop/"):
                r = review_auth.identify(self.headers)
                if not r:
                    return self._err(401, "not signed in")
                try:
                    sid = int(p.rsplit("/", 1)[-1])
                except ValueError:
                    return self._err(400, "bad id")
                # An 'own'-scoped token may only see its own cameras' crops -
                # checked here, not only in the queue listing.
                if not review_api.may_touch(r, sid):
                    return self._err(404, "no crop")
                b = review_api.crop_bytes(sid)
                if not b:
                    return self._err(404, "no crop")
                return self._send(200, b, "image/jpeg",
                                  {"Cache-Control": "no-store"})
            if p == "/api/rv/progress":
                return reviewer_read.rv_progress(self)

            if p == "/api/rv/tokens":
                return operator_admin.rv_tokens(self)

            if p == "/api/health":
                # 🚨 THIS ROUTE SAID "ok": true THROUGH A TWO-HOUR OUTAGE.
                #
                # It reported that the HTTP server was answering, which was
                # true and useless: the process had exhausted its file
                # descriptors, every other route was returning "unable to open
                # database file", and the one endpoint a watchdog would poll
                # was the one endpoint that touched nothing. A health check
                # that cannot fail for the reason the service actually fails is
                # worse than none, because it is believed.
                #
                # So it now does the two things it was missing:
                #
                #  1. TOUCHES THE DATABASE. `ok` means "this process can serve
                #     a request", not "this process accepted a socket".
                #  2. REPORTS HEADROOM, NOT JUST STATE. Descriptor exhaustion
                #     is a RAMP - it climbed for roughly 45 minutes before the
                #     cliff - so a watcher that only sees up/down learns about
                #     it strictly too late. `fd_used_pct` is the leading
                #     indicator; the crash is the lagging one.
                #
                # Counts of descriptors and threads say nothing about any
                # vehicle or visitor, so this stays unauthenticated like the
                # rest of the operational surface.
                health = {"ok": True, "version": VERSION, "ts": now()}
                try:
                    db.connect().execute("SELECT 1").fetchone()
                    health["db"] = "ok"
                except Exception as exc:
                    health["ok"] = False
                    health["db"] = f"{type(exc).__name__}: {exc}"
                try:
                    import os as _os
                    import resource
                    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                    used = len(list(Path(f"/proc/{_os.getpid()}/fd").iterdir()))
                    health["fd_used"] = used
                    health["fd_limit"] = soft
                    health["fd_used_pct"] = round(100.0 * used / soft, 1)
                    health["threads"] = threading.active_count()
                    # 🚨 A POINT SAMPLE CANNOT SEE A RAMP, AND THE RAMP IS THE
                    # WHOLE STORY. An hourly checker polling only "how are you
                    # right now" would have watched this exact outage develop
                    # and reported ok, ok, ok, dead - because the climb from
                    # healthy to fatal fits between two visits. The high-water
                    # marks are what a visitor who was not here needs, so they
                    # are kept in-process (free, no file, no permissions) and
                    # published alongside the live numbers.
                    #
                    # ⚠️ `uptime_s` IS PART OF THE SIGNAL, NOT DECORATION. These
                    # peaks reset when the process does, so a small uptime is
                    # the caller's only warning that the peaks it is reading
                    # describe a short window - and that something restarted
                    # this service, which is itself the thing worth asking
                    # about.
                    _PEAK["fd_pct"] = max(_PEAK["fd_pct"], health["fd_used_pct"])
                    _PEAK["threads"] = max(_PEAK["threads"], health["threads"])
                    health["fd_peak_pct"] = _PEAK["fd_pct"]
                    health["threads_peak"] = _PEAK["threads"]
                    health["uptime_s"] = round(time.time() - _STARTED, 1)
                    # Degraded, not dead. Something is retaining descriptors
                    # and there is still time to look at it.
                    if used > soft * 0.8:
                        health["ok"] = False
                        health["warn"] = (f"{used}/{soft} file descriptors in "
                                          f"use - approaching the limit, past "
                                          f"which every route fails")
                except Exception:
                    pass          # not Linux, or no /proc: report what we have

                # 🚨 WHAT IS ACTUALLY HOLDING THE REQUEST PERMITS.
                # Published because this limiter has now refused visitors on an
                # idle box three times, and each diagnosis needed a live
                # investigation that this one field would have answered.
                # ⚠️ SNAPSHOT UNDER THE LOCK, SORT OUTSIDE IT.
                # This sorted the whole dict INSIDE _INFLIGHT_LOCK - the lock
                # every gated request takes twice - so an O(N log N) pass ran
                # while holding the one thing serialising all traffic. It grew
                # during overload and was read by the watchdog, which restarts
                # the service from this very URL: the diagnostic stalled the
                # server hardest exactly when it was being consulted about a
                # stall.
                with Handler._INFLIGHT_LOCK:
                    holding = list(Handler._INFLIGHT_PATHS.values())
                    slow = list(Handler._SLOW_HELD.items())
                health["slowest_ever"] = dict(
                    sorted(slow, key=lambda kv: -kv[1])[:6])
                nowt = time.time()
                health["inflight"] = len(holding)
                health["inflight_cap"] = MAX_REQUESTS
                # ⚠️ REPORTED, for the same reason _gated records who holds a
                # permit: this cap is the one that stopped the OOM kills, and a
                # cap nobody can watch is a cap that gets tuned by argument.
                # heavy_free near 0 under load means readers are queueing on
                # map data and MAX_HEAVY is the thing to look at first.
                health["heavy_cap"] = MAX_HEAVY
                health["heavy_free"] = Handler._HEAVY._value
                health["ingest_cap"] = MAX_INGEST
                health["ingest_free"] = Handler._INGEST._value
                # Longest-held first: the one at the top is the one to blame.
                # ⚠️ THE ONE RESOURCE THE LAST OUTAGE FIX INTRODUCED, AND THE
                # ONLY ONE HEALTH DID NOT REPORT. Saturate these 12 permits and
                # fds, threads and inflight all look perfectly normal while the
                # map goes blank - a failure mode invisible to every existing
                # number. Same for Overpass: a road lookup queue that is full
                # stalls enrolment and drive reports with nothing else moving.
                try:
                    health["tile_fetch_free"] = tiles._TILE_FETCH._value
                    health["tile_fetch_cap"] = 12
                except Exception:
                    pass
                try:
                    import road as _road
                    health["road_lookup_free"] = _road._OVERPASS_SLOTS._value
                    health["road_cells_failing"] = len(_road._FAIL_UNTIL)
                except Exception:
                    pass
                health["inflight_now"] = [
                    {"path": lbl, "held_s": round(nowt - t0, 1)}
                    for lbl, t0 in sorted(holding, key=lambda x: x[1])[:8]]
                return self._json(health, 200 if health["ok"] else 503)

            if p == "/api/policy":
                # Moved to mapdata.policy in Stage 2B.
                return mapdata.policy(self)

            if p == "/api/whoami":
                # Moved to mapdata.whoami in Stage 2B.
                return mapdata.whoami(self)
            if p == "/api/plate":
                # Moved to mapdata.plate in Stage 2B.
                return mapdata.plate(self, q)
            if p == "/api/stats":
                # Moved to mapdata.stats in Stage 2B.
                return mapdata.stats(self)

            if p == "/sw.js":
                return pages.sw_js(self)

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
                return reviewer_read.review_queue(self)

            if p == "/api/pending":
                # Moved to mapdata.pending in Stage 2B.
                return mapdata.pending(self)

            if p.startswith("/api/tile/"):
                return self._tile(p)

            if p == "/api/nodes":
                # 🚨 4,800 PUBLIC CAMERAS WERE DOWNLOADED ON EVERY MAP LOAD IN
                # ORDER TO BE HIDDEN.
                #
                # This response went from ~90 KB to 1.48 MB when the traffic
                # camera fleet landed, and the layer that draws them now
                # defaults OFF because 4,800 markers made the map lag on a
                # phone. So the default page was paying a megabyte and a second
                # of parsing for rows it then filtered out client-side - a
                # bound applied to the DRAWING and not to the DOWNLOAD, which
                # is the mistake this project keeps making in a new place.
                #
                # The client asks for them only when the layer is on. Absent
                # parameter = include, so every existing caller of this API
                # (and anyone reading it from outside) sees exactly what it
                # always returned.
                _q = parse_qs(urlparse(self.path).query)
                want_public = (_q.get("public_cams", ["1"])[0] != "0")
                # 🗺️ AND BOUNDED BY VIEWPORT WHEN THE LAYER IS ON.
                #
                # A phone in Michigan has no use for 2,160 Finnish cameras, and
                # switching the layer on used to mean downloading every one of
                # them. Snapped OUTWARD to the same grid the cache keys on, so
                # the answer is a superset of what was asked for and panning
                # keeps hitting one key instead of minting one per drag.
                #
                # ⚠️ ONLY public_cam is filtered. Volunteer nodes are ~350 rows
                # and their SPANS are the map's actual content; dropping those
                # off-screen would make the map look empty while it loaded, and
                # they are not what made this response big.
                cam_box = None
                if "box" in _q:
                    try:
                        cam_box = tuple(float(x) for x in
                                        _snap_box(_q["box"][0]).split(","))
                    except (ValueError, AttributeError):
                        cam_box = None
                out = []
                for n in db.nodes():
                    if n["kind"] == "public_cam":
                        if not want_public:
                            continue
                        if cam_box and n["lat"] is not None:
                            s, w, ne, e = cam_box
                            if not (s <= n["lat"] <= ne and w <= n["lon"] <= e):
                                continue
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
                    # 🚨 THE SPAN IS THE OWNER'S TO PUBLISH, AND THEY WERE NEVER
                    # ASKED.
                    #
                    # The line below used to read "Public, exact, and the ONLY
                    # geometry a viewer gets - people are entitled to know where
                    # they are recorded." The entitlement is real. The mistake
                    # was deciding it on the volunteer's behalf: an 80 m span on
                    # a NAMED road is coarse in a town and close to an address
                    # in the country, and on a rural stretch overlooked by one
                    # house, the span plus the road name IS that house.
                    #
                    # ⚠️ AND THE "watched roads" CHECKBOX WAS NEVER A CONTROL ON
                    # THIS. It only decided whether the map DREW them; every
                    # span was in this response for anyone who ran curl. That is
                    # the same lesson three comments up - a control applied to
                    # one representation is bypassed by the other - arriving
                    # again, on the field those comments were protecting.
                    #
                    # So consent is checked HERE, at the point of publication.
                    # road_name and span_source go with it: naming the street a
                    # camera watches re-states most of the span in words.
                    shares = bool(n.get("publish_span"))
                    # 🚨 A GOVERNMENT TRAFFIC CAMERA IS NOT SOMEBODY'S HOUSE.
                    #
                    # Every rule above exists because a volunteer's camera
                    # position describes where they live, and the project
                    # refuses to publish that. A public_cam is the opposite
                    # case: it is a state-owned camera on a pole whose exact
                    # coordinates the transport department publishes itself, in
                    # the same feed we read the pictures from. Withholding it
                    # protects nobody and hides the coverage from the people
                    # deciding whether this project is worth anything.
                    #
                    # So these get their true position and nothing else changes:
                    # the consent gate above still governs every volunteer node,
                    # which is the field it was written to protect.
                    is_public_cam = n["kind"] == "public_cam"
                    rec = {
                        "id": n["id"], "name": n["name"],
                        "kind": n["kind"], "sightings": n["sightings"],
                        "lat": n["lat"] if is_public_cam else None,
                        "lon": n["lon"] if is_public_cam else None,
                        "span": span if shares else None,
                        "road_name": n["road_name"] if shares else None,
                        "span_source": n["span_source"] if shares else None,
                        "last_seen": n["last_seen"], "last_beat": n["last_beat"],
                        # 'online' now means the node SAID SO. It used to mean
                        # 'a car drove past in the last 15 minutes', which
                        # marked every camera on a quiet street as switched off
                        # and read as a fault the user then went looking for.
                        "online": bool(n["last_beat"] and n["last_beat"]
                                       > now() - db.beat_window(n["kind"])),
                    }
                    out.append(rec)
                return self._json(out)

            if p == "/api/sightings":
                # Moved to mapdata.sightings in Stage 2B.
                return mapdata.sightings(self, q)

            if p.startswith("/api/sighting/"):
                # Moved to mapdata.sighting in Stage 2B.
                return mapdata.sighting(self, p)

            if p.startswith("/api/track/"):
                # Moved to mapdata.track in Stage 2B.
                return mapdata.track(self, p)

            if p == "/api/leaderboard":
                # Moved to mapdata.leaderboard in Stage 2B.
                return mapdata.leaderboard(self, q)

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
    #
    # Moved to response_policy.CSRF_SENSITIVE in Stage 1B (step 4). Aliased
    # here, unchanged, because _do_POST_inner below (application/route logic,
    # not in scope for this stage) reads _CSRF_SENSITIVE by this name.
    _CSRF_SENSITIVE = response_policy.CSRF_SENSITIVE

    def _do_POST_inner(self) -> None:
        try:
            p = urlparse(self.path).path

            # CSRF check moved to response_policy.csrf_ok in Stage 1B (step 4).
            if not response_policy.csrf_ok(p, self.headers.get("Content-Type")):
                return self._err(415, "state-changing requests must be "
                                      "application/json")

            if p == "/api/enroll":
                b = self._body()
                # 🚨 THE LIMIT IS ON CREATING CAMERAS, NOT ON CORRECTING ONE.
                # 5/hour is right for a stranger minting nodes and wrong for
                # the owner of an existing camera, who nudges a heading, looks
                # at which road it resolved to, and nudges again - the /aim
                # page is built on exactly that loop, and at 5/hour it would
                # lock them out mid-adjustment with their camera pointing at
                # the wrong street. The body is read first so the two cases can
                # be told apart; _body() is already capped at MAX_BODY.
                #
                # ⚠️ THE EXEMPTION IS FOR A PROVEN OWNER, NOT FOR ANY REQUEST
                # CARRYING A node_id. Exempting on the mere PRESENCE of a
                # node_id let anyone skip the limiter by inventing one: the
                # token is checked inside enroll(), which is the right place
                # for the AUTHORISATION, but by then the limiter had already
                # been stepped around. Node ids are printed on the public map,
                # so "has a node_id" is not a claim to anything. Ownership is
                # therefore proved HERE, before the exemption is granted - and
                # a wrong token now counts against the create limit, which is
                # exactly what guessing at somebody else's token should cost.
                _nid = str(b.get("node_id") or "").strip()
                _owner = False
                if _nid:
                    _prior = db.node(_nid)
                    _tok = (b.get("token")
                            or (self.headers.get("Authorization") or ""
                                ).replace("Bearer ", "").strip())
                    if _prior and _prior.get("token") and _tok:
                        import hmac as _hmac
                        _owner = _hmac.compare_digest(str(_tok),
                                                      str(_prior["token"]))
                if not _owner and not rate_ok(p, self.client_ip):
                    # Do NOT say "from this address". The limit is network-wide
                    # (see RATE), so blaming the visitor's address is both
                    # false and unactionable - they change networks, retry, and
                    # get the same refusal.
                    return self._err(429, "the network is registering a lot of "
                                          "cameras right now - please try "
                                          "again in a few minutes")
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
                out = {"id": rec["id"], "status": rec["status"],
                       "token": rec.get("token")}
                # A move past the threshold minted a NEW camera (see
                # nodes.enroll). The client MUST hear about it: it has to adopt
                # the new id and token, or it carries on posting as a camera
                # that is no longer where it says it is. Dropping this field
                # here would have made the split invisible and therefore worse
                # than not splitting at all.
                if rec.get("moved_from"):
                    out["moved_from"] = rec["moved_from"]
                # A brand-new camera (no node_id came in - a re-save carries one)
                # also gets its own reviewer token, scoped to just this camera,
                # so the volunteer can review their own camera's catches without
                # anyone having to hand them access. It is 'own' scope: it cannot
                # see the shared pool or anyone else's cameras, every verdict is
                # audited under it, and the operator can revoke it. Minted once,
                # at creation, so nudging the camera later does not spawn more.
                if not str(b.get("node_id") or "").strip():
                    try:
                        rtok = review_auth.ensure_own_token(
                            rec["id"], str(b["name"])[:60], created_by="enroll")
                        if rtok:
                            out["review_token"] = rtok
                            out["review_url"] = "/rv"
                    except Exception:
                        pass
                return self._json(out)

            if p == "/api/sightings":
                # 🚨 AUTHENTICATE BEFORE SPENDING THE BUDGET, AND SPEND THE
                # RIGHT NODE'S. This charged a single network-wide bucket
                # before it knew who was calling, so one busy camera could
                # refuse every other camera on the project for the rest of the
                # hour - and _ingest, which does the real authentication, only
                # ran afterwards. Same shape as the geocode cache-before-budget
                # note above: the check has to know what it is protecting.
                b = self._body()
                nd = db.node(str(b.get("node_id") or ""))
                if not nd:
                    return self._err(404, "unknown node")
                if nd["status"] != "active":
                    return self._err(403, f"node is {nd['status']}")
                if not self._token_ok(nd):
                    return self._err(401, "bad node token")
                if not rate_ok(p, self.client_ip, who=nd["id"]):
                    return self._err(429, "this camera is posting too fast")
                # A second, much higher ceiling that protects the BOX rather
                # than any one camera. Without it a fleet of nodes each inside
                # its own limit can still add up to more than 2 vCPUs can take.
                if not rate_ok("_all_sightings", self.client_ip):
                    return self._err(503, "the map is at capacity right now - "
                                          "your camera will retry")
                return self._ingest(b)

            # A stranger's judgement about one crop. It is written to
            # label_votes.db and nowhere else - not to sightings, not to the
            # bank. Consensus and the decision happen later, on his machine.
            if p == "/api/help/vote":
                return community.help_vote(self)

            if p == "/api/heartbeat":
                return node_lifecycle.heartbeat(self)

            if p == "/api/heartbeat/bulk":
                return node_lifecycle.heartbeat_bulk(self)

            if p == "/api/node/progress":
                return node_lifecycle.node_progress(self)

            if p == "/api/node/label":
                # 🚨 THE CAMERA OPERATOR'S VERDICT, ARRIVING FROM THE CAMERA.
                #
                # labelbank runs on the machine that owns the camera and used to
                # apply this judgement to its OWN local sqlite file. Once the box
                # became the single source of truth that file stopped being the
                # map: the crops camctl labels carry sighting ids issued HERE, and
                # 0 of the last 30 of them existed locally. So every "Yes -
                # government" answered at the camera since the cutover wrote a
                # perfectly good training label and moved nothing, while the popup
                # said it had moved the sighting. An invisible failure - the popup
                # closes, the label is real, the map just never changes.
                #
                # Same auth as /api/node/progress, plus two rules this route needs
                # that a heartbeat does not:
                #   - a token is REQUIRED. _token_ok passes a tokenless node, which
                #     is tolerable for telemetry and not for publishing.
                #   - the sighting must BELONG to the calling node. Ids are
                #     sequential and printed next to sightings on the public map,
                #     so without this any camera could publish or retract any
                #     other camera's vehicles by naming an id.
                b = self._body()
                nd = db.node(str(b.get("node_id") or ""))
                if not nd:
                    return self._err(404, "unknown node")
                if nd["status"] != "active":
                    return self._err(403, f"node is {nd['status']}")
                if not nd.get("token"):
                    return self._err(401, "this node has no token; re-enroll it")
                if not self._token_ok(nd):
                    return self._err(401, "bad node token")
                try:
                    sid = int(b.get("sighting_id"))
                except (TypeError, ValueError):
                    return self._err(400, "bad sighting_id")
                row = db.sighting(sid)
                if not row:
                    return self._err(404, "no such sighting")
                if (row.get("node_id") or "") != nd["id"]:
                    return self._err(403, "not your camera's sighting")
                label = str(b.get("label") or "")
                undo = str(b.get("undo") or "")
                out = node_label.apply(sid, row, label, undo=undo)
                if out.get("error"):
                    return self._err(400, out["error"])

                # 🚨 THE PICTURE THE HUMAN WAS LOOKING AT, AT FULL RESOLUTION.
                #
                # Every patrol car published through THIS route was a 160-200px
                # smudge - measured live: 51182 was 160x61, 50945 192x95, and
                # the newest camera-labelled ones 200px to the edge. The reason
                # is that nothing here ever attached a photograph, so the row
                # kept the sub-resolution PEN copy ingest had stored, and 200px
                # is not a thumbnail size chosen for the map: it is the size
                # that DESTROYS A PLATE (snapshot.SUBRES_MAX_EDGE). It is
                # exactly right for an unreviewed candidate and exactly wrong
                # for a published government vehicle, which is the one case the
                # plate and the livery are meant to survive.
                #
                # The full-resolution original is not on this machine and must
                # not be: core.EVIDENCE is home-only and mirror.evidence_write
                # refuses here, because a public box holding un-degraded
                # pictures of whatever drove past is the thing that rule exists
                # to prevent. So the CLIENT keeps it - a drive phone holds its
                # own crop in memory, a camera has it banked - and sends it
                # with the verdict. It arrives only AFTER a person has said
                # "yes, police", at which point it is a public-tier photograph
                # of a police vehicle: precisely what a mirror is allowed to
                # store, and nothing is held here pending anything.
                #
                # ⚠️ ONLY WHEN THE VERDICT PUBLISHES. `published` is the state
                # machine's own answer, not the label that was asked for - a
                # council pickup answered "gov" is recorded and NOT published
                # (node_label._publishes), and attaching an un-degraded picture
                # to a private-tier row would put a legible plate on a row the
                # deployment just decided to keep private.
                if out.get("published") and b.get("snap_b64"):
                    try:
                        # decode_upload's cap and EXIF strip run in here, before
                        # anything touches the disk.
                        full = snapshot.decode_bytes(str(b["snap_b64"]))
                        review_api.attach_confirmed_photo(
                            sid, row, full,
                            ts=row.get("ts"),
                            node_name=nd.get("name") or "a camera",
                            vclass=("police" if label == "police" else "gov"))
                    except Exception as exc:
                        # The vehicle is on the map either way. A rejected
                        # picture must not un-publish it, and the operator has
                        # already been told their answer landed.
                        print(f"[label] full-res photo not attached to {sid}: {exc}")

                # 🚨 AND THE PEN ENTRY GOES, BECAUSE THE DECISION IS MADE.
                # review_api.verdict deletes it on every outcome; this route
                # deleted it on none, so a vehicle the camera operator had
                # already judged sat in /rv/pool waiting to be judged again. Two
                # front ends onto one decision, and only one of them closed it.
                # It also silently undid the photograph above: a pool reviewer
                # confirming the leftover item republishes it from the 200px pen
                # crop, putting the smudge back over the full-resolution picture
                # the person who saw the vehicle had just supplied.
                #
                # Keyed on ANSWERED, not on `did`. A label that changed nothing
                # because the map already agreed is still a person having
                # looked, and leaving that one in the queue is the same wasted
                # second look. `unsure` is deliberately not an answer, and an
                # undo must put nothing back - it is a reversal, not a verdict.
                if undo:
                    pass
                elif label in node_label.VALID and label != "unsure":
                    try:
                        review_api._delete_pen(sid)
                    except Exception:
                        pass
                if out.get("did"):
                    db.audit(f"node_label:{out['did']}", str(sid),
                             actor=f"camera {nd['id']}",
                             ip=privacy.audit_ip(self.client_ip))
                return self._json(out)

            if p == "/api/bug":
                return operator_bugs.bug_report(self)

            if p == "/api/bug/close":
                return operator_bugs.bug_close(self)

            if p == "/api/bug/delete":
                return operator_bugs.bug_delete(self)

            if p == "/api/node/whoami":
                return node_self.node_whoami(self)

            if p == "/api/node/parked":
                return node_self.node_parked(self)

            if p == "/api/node/key":
                return node_credentials.node_key(self)

            if p == "/api/node/span":
                return node_self.node_span(self)

            if p == "/api/node/confirm":
                # 🚨 THE OPERATOR SAYING "YES, THAT WAS A PATROL CAR" ABOUT A
                # PASS THE GATE THREW AWAY.
                #
                # /api/node/label moves a sighting that already exists. This one
                # exists because most confirmations have nothing to move: the
                # posting gate runs when the vehicle leaves frame, and a marked
                # patrol car with an unreadable plate clears only one marker, so
                # the pass is dropped and no sighting is ever created. The human
                # then confirms the crop and the map cannot change, because
                # there is no row. Every such confirmation was silently lost.
                #
                # This is NOT a way to publish anything a camera fancies. It is
                # the same claim /api/sightings accepts, from the same node
                # token, with one addition the SERVER makes: human_confirmed,
                # which classify.py weighs at 4.0 and which waives the
                # two-marker rule. The submitter cannot assert it (it is
                # stripped in _ingest and re-set only from this flag), and the
                # sighting still goes through classify() exactly like any other
                # - so `public_tiers` still decides what becomes public, and a
                # confirmed council truck still stays private.
                b = self._body()
                nd = db.node(str(b.get("node_id") or ""))
                if not nd:
                    return self._err(404, "unknown node")
                if nd["status"] != "active":
                    return self._err(403, f"node is {nd['status']}")
                if not nd.get("token"):
                    return self._err(401, "this node has no token; re-enroll it")
                if not self._token_ok(nd):
                    return self._err(401, "bad node token")
                if not rate_ok("/api/sightings", self.client_ip):
                    return self._err(429, "posting too fast")
                # The node may only confirm its OWN camera's crop, and the
                # source is fixed here rather than taken from the body.
                b["node_id"] = nd["id"]
                b["source"] = "camera"

                # 🚨 A BANKED CROP IS ALREADY THE VEHICLE, SO THERE IS NOTHING
                # TO CROP TO - AND _ingest CORRECTLY REFUSES TO GUESS.
                # It requires a `vehicle_box` because a camera normally posts a
                # whole FRAME, and storing that uncropped would publish the
                # street and the neighbours' houses. Right rule, wrong shape of
                # input: what arrives here is the already-tight crop the
                # labeller looked at. Without this the row is created with
                # `snap: None` - a public claim with no photograph behind it,
                # which is both less useful and less checkable.
                #
                # store_subresolution is the same call review_api._publish uses
                # to attach a pen crop, for the same reason: the image is
                # already small and already cropped, so it needs storing, not
                # cropping. Sub-resolution is enforced inside it, so an
                # oversized image is refused rather than quietly published.
                if b.get("snap_b64"):
                    try:
                        # ⚠️ DOWNSCALE FIRST. store_subresolution ENFORCES a
                        # 200px longest edge and raises otherwise - correctly,
                        # it is the guard that keeps a published crop
                        # unidentifiable. Banked training crops are 512px wide,
                        # so every operator confirmation hit that ValueError,
                        # got caught by the except below, and the sighting was
                        # created with snap=None: on the map, confirmed, with no
                        # photograph behind it. The measurement, not the guess:
                        # "sub-resolution submission is 512x229".
                        import base64 as _b64
                        _small = snapshot.downscale_to_subres(b["snap_b64"])
                        b["snap"] = snapshot.store_subresolution(
                            "data:image/jpeg;base64," + _b64.b64encode(_small).decode(),
                            {"ts": b.get("ts") or now(), "node_id": nd["id"],
                             "node_name": nd.get("name") or "a camera",
                             "tier": "public", "vclass": "police",
                             "watermark": "CONFIRMED"})
                    except Exception as exc:
                        # The sighting is worth more than the picture. Losing
                        # the image must not lose the report.
                        print(f"[confirm] could not store crop: {exc}")
                    b.pop("snap_b64", None)
                db.audit("node_confirm", str(b.get("bank_ref") or "?"),
                         actor=f"camera {nd['id']}",
                         ip=privacy.audit_ip(self.client_ip))
                return self._ingest(b, operator_confirmed=True)

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
                # ⚠️ A PAUSED OR REVOKED NODE MUST BE TOLD, NOT COUNTED.
                # It used to beat happily forever: counted as online, inflating
                # heartbeats_total and the public "hours watched", while
                # _ingest 403d every sighting it sent. The camera had no way to
                # learn it had been switched off - the only endpoint it could
                # reach kept answering {"ok": true}.
                if nd["status"] != "active":
                    return self._json({"ok": True, "posting": False,
                                       "status": nd["status"],
                                       "note": "this camera is not active, so "
                                               "its sightings are refused"})
                db.heartbeat(nd["id"])
                # 🚨 ASK FOR THE GOOD PICTURE HERE. Everything a camera uploads
                # is capped at 200px so a private plate cannot survive the trip,
                # and that must never change. But once the head decides a
                # vehicle IS a government one it belongs in the public tier,
                # where the plate is legible on purpose - and the only copy the
                # server has by then is the deliberately ruined one.
                #
                # The camera that took it is the only device that ever had the
                # original, so it has to be asked, and this beat is already
                # going back to exactly that device every few seconds.
                want = []
                try:
                    want = db.wants_fullres(nd["id"])
                except Exception as exc:
                    print(f"[beat] wants_fullres failed for {nd['id']}: {exc}")
                return self._json({"ok": True, "posting": True, "ts": now(),
                                   "want_full": want})

            if p == "/api/signals":
                # 🚨 AN OUTSIDE SENSOR NETWORK REPORTING WHAT A VEHICLE
                # BROADCASTS. It reports what it HEARD - a time, a place, a
                # band, an opaque signature. It never says what the vehicle
                # was, and it cannot write to `sightings`. What an observation
                # MEANS is decided here, later, against published rows only.
                #
                # ⚠️ TOKEN-GATED PER PARTNER, revocable on its own. One
                # partner, one token: if somebody downstream of them abuses it,
                # that token dies and nobody else is affected. That is the
                # whole point - it means a partner is never blamed for a third
                # party using their system.
                auth = self.headers.get("Authorization", "")
                tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
                # ⚠️ review_auth._hash, not a second sha256 here. One
                # definition of how a token becomes a stored hash; two
                # would drift and the drift would look like a bad token.
                partner = (db.signal_token(review_auth._hash(tok))
                           if tok else None)
                if not partner:
                    return self._err(401, "a signal partner token is required")

                b = self._body()
                rows = b.get("observations")
                if not isinstance(rows, list) or not rows:
                    return self._err(400, "send {\"observations\": [ ... ]}")
                if len(rows) > db.SIGNAL_MAX_BATCH:
                    return self._err(
                        400, f"at most {db.SIGNAL_MAX_BATCH} observations per request")
                kept = db.add_signal_obs(int(partner["id"]), rows)
                # ⚠️ Report BOTH numbers. Silently dropping malformed rows and
                # answering "ok" is how a partner spends a week sending
                # something we never stored.
                return self._json({"ok": True, "received": len(rows),
                                   "stored": kept,
                                   "dropped": len(rows) - kept})

            if p == "/api/sighting/fullres":
                return node_credentials.sighting_fullres(self)

            if p == "/api/heartbeat/bulk":
                # 🚨 ONE PROCESS, THOUSANDS OF CAMERAS, ONE REQUEST.
                #
                # The public-camera poller holds ~4,400 credentials. Beating
                # them individually is 4,400 requests and 4,400 commits every
                # five minutes at a box that is two cores and has already been
                # taken down once by concurrency it was not sized for. This is
                # the same operation, batched, and it is the ONLY reason it
                # exists - it grants no authority the single endpoint does not.
                #
                # ⚠️ EVERY ENTRY IS AUTHENTICATED SEPARATELY. There is no
                # "trusted caller" here: a batch of a thousand is a thousand
                # token checks, and one bad token fails that entry alone.
                #
                # ⚠️ AND THE TOKEN IS IN THE BODY, WHICH IS A DEPARTURE.
                # _token_ok deliberately reads the Authorization HEADER only,
                # because a body-supplied token was once a way round it. That
                # rule is intact: this route does not call _token_ok, it
                # compares each entry's token against that node's own record
                # with compare_digest, and a batch can only ever beat cameras
                # whose tokens the caller already holds. One header cannot
                # carry a thousand different credentials, so a body field is
                # the only shape available - and it authorises nothing beyond
                # "this camera is awake".
                import hmac as _hmac
                b = self._body()
                items = b.get("nodes")
                if not isinstance(items, list):
                    return self._err(400, "nodes must be a list")
                if len(items) > BULK_BEAT_MAX:
                    return self._err(413, f"at most {BULK_BEAT_MAX} per request")
                ok, bad, inactive = [], 0, 0
                for it in items:
                    if not isinstance(it, dict):
                        bad += 1
                        continue
                    nd = db.node(str(it.get("node_id") or ""))
                    if not nd:
                        bad += 1
                        continue
                    tok = str(nd.get("token") or "")
                    if tok and not _hmac.compare_digest(str(it.get("token") or ""),
                                                        tok):
                        bad += 1
                        continue
                    # Same rule as the single endpoint: a paused or revoked
                    # camera must not beat, or it counts as online while every
                    # sighting it sends is refused.
                    if nd["status"] != "active":
                        inactive += 1
                        continue
                    ok.append(nd["id"])
                db.heartbeat_many(ok)
                return self._json({"ok": True, "beat": len(ok),
                                   "rejected": bad, "inactive": inactive,
                                   "ts": now()})

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
                # 🚨 AND PUT IT IN FRONT OF A HUMAN.
                # This used to end at the table. The only reader of `reports`
                # is the OPERATOR queue, which is local-only and does not exist
                # on a public mirror - so on the live site every public flag
                # landed where nothing running there could show it. Parking it
                # in the review pen routes it to the reviewer app, and the
                # existing "not a cop" verdict already retracts the row and
                # resolves the flag from there.
                #
                # Best-effort on purpose: the flag is recorded either way, and
                # a sighting with no stored photo simply cannot be re-judged
                # visually. Never let the queueing failure lose the report.
                try:
                    review_api.park_reported(sid, row, reason)
                except Exception:
                    traceback.print_exc()
                # 🚨 A PRIVACY FLAG ACTS FIRST AND ASKS AFTERWARDS.
                # Every other reason above is a claim about a VEHICLE and can
                # safely wait in the queue. This one is a claim about a PERSON
                # who never asked to be photographed - a passenger's arm, a
                # face, a watch, caught alongside the patrol car - and the harm
                # accrues for as long as the queue takes. The photograph is
                # moved out of the served directory now; the sighting itself
                # (dot, class, time) does not move, and a reviewer decides
                # whether the picture comes back cropped, whole, or not at all.
                # See review_api.hold_photo for why this is recoverable and the
                # opposite failure is not.
                held = False
                if reason == db.PRIVACY_REPORT:
                    try:
                        held = review_api.hold_photo(sid, row, reason)
                    except Exception:
                        traceback.print_exc()
                return self._json({"ok": True, "held": held})

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
                return node_credentials.key_qr(self)

            if p == "/api/key/rotate":
                return node_credentials.key_rotate(self)

            if p == "/api/operator/login":
                return operator_admin.operator_login(self)
            if p == "/api/operator/logout":
                return operator_admin.operator_logout(self)

            # --- reviewer session + verdicts (token-gated) --------------------
            if p == "/api/rv/login":
                return reviewer_read.rv_login(self)
            if p == "/api/rv/logout":
                return reviewer_read.rv_logout(self)
            if p == "/api/rv/retracted/delete":
                return reviewer_mutation.rv_retracted_delete(self)

            if p == "/api/rv/held/fix":
                return reviewer_mutation.rv_held_fix(self)

            if p == "/api/rv/verdict":
                return reviewer_mutation.rv_verdict(self)

            if p == "/api/drive/report":
                return community.drive_report(self)
            if p == "/api/drive/vote":
                return community.drive_vote(self, p)

            if p == "/api/rv/my-token":
                # A camera fetches its OWN reviewer token, proving ownership with
                # its node token. Lets a volunteer who enrolled before tokens
                # existed get theirs automatically the next time their node runs,
                # with no operator involvement. Minted once (ensure_own_token), so
                # calling it repeatedly does not spawn tokens.
                b = self._body()
                nd = db.node(str(b.get("node_id") or ""))
                if not nd:
                    return self._err(404, "unknown node")
                if not self._token_ok(nd):
                    return self._err(401, "bad node token")
                # 🚨 TWO TOKENS, TWO PAGES, ON PURPOSE.
                # `own` sees only this camera and is what the owner wants most
                # of the time. `pool` is the shared queue of everyone's pending
                # government calls - self-service, because a crowd-labelling
                # queue only the operator can reach is not a crowd. They are
                # separate tokens rather than one widened token so that either
                # can be revoked without taking the other with it: if a camera
                # owner starts publishing rubbish into the pool, the pool token
                # goes and they keep their own camera.
                want = str(b.get("scope") or "own")
                url = "/rv/pool" if want == "pool" else "/rv/mine"
                # 🚨 A WAY OUT OF "ALREADY ISSUED". The token is stored as a
                # hash and cannot be shown twice, so an owner who lost theirs
                # was told "this camera already has that token" for ever. The
                # node token in this very request is the same secret that lets
                # the camera publish sightings, so replacing its reviewer token
                # grants nothing new - and the old one is revoked, because a
                # token that was lost to somebody else must not stay live.
                if b.get("regenerate"):
                    tok = review_auth.reissue(
                        nd["id"], nd.get("name") or "a camera", want,
                        created_by="self")
                    return self._json({"ok": True, "new": True, "scope": want,
                                       "replaced": True,
                                       "review_token": tok, "review_url": url})
                if want == "pool":
                    tok = review_auth.ensure_pool_token(
                        nd["id"], nd.get("name") or "a camera")
                else:
                    tok = review_auth.ensure_own_token(
                        nd["id"], nd.get("name") or "a camera",
                        created_by="self")
                if tok:
                    return self._json({"ok": True, "new": True, "scope": want,
                                       "review_token": tok, "review_url": url})
                return self._json({"ok": True, "new": False, "scope": want,
                                   "review_url": url,
                                   "note": "this camera already has that token"})

            # --- token administration (operator only) -------------------------
            if p == "/api/rv/tokens/new":
                return operator_admin.rv_tokens_new(self)
            if p == "/api/rv/tokens/revoke":
                return operator_admin.rv_tokens_revoke(self)
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
                return operator_admin.purge(self)

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
    def _ingest(self, ev: dict, operator_confirmed: bool = False) -> None:
        """Accept a detection from a node.

        `operator_confirmed` may ONLY be set by /api/node/confirm, which proves
        the caller owns the camera the crop came from. It is a parameter rather
        than a field in `ev` on purpose: anything inside `ev` is attacker-
        controlled, and this one grants the right to publish.

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

        # 🚨 THE OPERATOR'S CONFIRMATION, RESTORED AFTER THE STRIP AND NEVER
        # BEFORE IT. The strip above is right: `human_confirmed` is the single
        # strongest signal classify.py has (weight 4.0, and it WAIVES the
        # two-marker police rule), so a submitter who could assert it could
        # publish a neighbour's car. It is set here instead, from a flag this
        # server derived by checking a node token against the node that owns
        # the crop - never from anything the caller typed.
        #
        # 📌 WHY THIS PATH HAS TO EXIST AT ALL. The posting gate runs at the
        # moment a vehicle leaves frame, and it drops passes that clear no two
        # markers. That is correct and it is also why a confirmed patrol car
        # could never reach the map: a real one was scored by the trained head
        # at 0.987, failed the gate because the plate read disagreed (0.34 <
        # 0.55) and no second marker fired, and was discarded. The operator then
        # pressed "Yes - government" on the crop and nothing happened, because
        # there was no sighting to promote. The human arrived AFTER the gate.
        # classify.py has always known how to weigh a human; it simply never got
        # told, because the row it would have gone on was never created.
        if operator_confirmed:
            evidence["human_confirmed"] = True

        # A public mirror cannot score a phone crop (no GPU, no trained head),
        # so it parks a plate-less copy for the home classifier to pull. Captured
        # HERE, before the mirror drops the image below, and written to the inbox
        # after the row exists so it can be keyed by the sighting id. The size is
        # re-verified (subresolution_bytes), so an oversized crop is refused, not
        # quarantined. See mirror.quarantine_write.
        relay_crop = None
        if (source == "phone_node" and ev.get("snap_b64")
                and mirror.relay_enabled()):
            try:
                relay_crop = snapshot.subresolution_bytes(ev["snap_b64"])
            except Exception:
                relay_crop = None

        c = classify.classify(evidence)

        # A public SIGHTING and a published PLATE are different decisions.
        # `sightable` puts "a marked patrol unit was here" on the public map -
        # no identifier, so nothing to protect. `tierable` is what allows the
        # plate TEXT through, and it keeps the strict bar. Gating both on
        # `tierable` meant a plate-blind camera could never contribute
        # anything, which is most cameras. See classify.py rule 4.
        # 🚨 A HUMAN SUBMISSION IS A CLAIM, NOT A RECORD (his call: nothing
        # auto-publishes without the trained head first).
        #
        # This used to reach the public tier directly on the argument that a
        # person looking at a marked patrol car beats any classifier. True of an
        # honest person, and that is the whole problem: the submitter chooses
        # the markers, so "two distinct visual markers" is two taps, and the map
        # would publish whatever a stranger asserted about a vehicle they picked.
        # Every other route to the public tier is gated by a model that cannot
        # be argued with. This one was gated by the submitter's own honesty.
        #
        # So it is recorded PRIVATE and routed to the pen, where the trained head
        # scores its crop and a human confirms it. Nothing is thrown away and
        # nothing is distrusted - the claim simply has to survive the same gate
        # everything else survives before it names a vehicle in public.
        #
        # ⚠️ THIS MUST HAPPEN BEFORE `tier` IS COMPUTED. Clearing the flags after
        # the tier line reads as a fix, changes the reason string, and publishes
        # exactly as before - the failure this codebase keeps producing: a check
        # that runs and is not applied to the thing it governs.
        if source == "phone":
            c["why"] = f"human-submitted by {nid}, awaiting review; " + c["why"]
            c["tierable"] = False
            c["sightable"] = False

        # A public SIGHTING and a published PLATE are different decisions.
        # `sightable` puts "a marked patrol unit was here" on the public map -
        # no identifier, so nothing to protect. `tierable` is what allows the
        # plate TEXT through, and it keeps the strict bar. Gating both on
        # `tierable` meant a plate-blind camera could never contribute
        # anything, which is most cameras. See classify.py rule 4.
        tier = "public" if (c["tierable"] or c["sightable"]) else "private"

        # 🚨 THE PUBLIC TIER IS ENTERED BY A PERSON, NEVER BY INGEST.
        # 33 of the 34 sightings ever auto-published came through here: the
        # classifier judged a submission sightable and the row went public with
        # nobody having looked at it. That is the claim the project is now
        # making publicly - that a human decides what appears on the map - and a
        # claim has to be true in the code, not merely usual in practice.
        #
        # The classification is NOT discarded. `c` still carries vclass and the
        # reason, the crop is still parked in the review pen below, and a
        # reviewer promotes it with one press. All that changes is that the
        # default is private and the publish step needs a person.
        #
        # ⚠️ This is deliberately AFTER `tier` is computed, so the classifier's
        # own opinion is still what routes the crop to the pen. Clearing the
        # flags earlier would have made every candidate invisible instead of
        # merely unpublished - the difference between "waiting for review" and
        # "silently dropped".
        # ⚠️ REMEMBER THAT THIS WAS A CANDIDATE. Downstream code needs to know
        # "the classifier would have published this" AFTER the tier has been
        # rewritten to private, and the tier can no longer answer that. Two
        # separate behaviours were silently switched off by reading `tier`
        # here: fragment merging, and the pen write itself.
        candidate = (tier == "public")
        # 🚨 AN OPERATOR CONFIRMATION IS THE HUMAN STEP. DO NOT HOLD IT FOR ONE.
        #
        # The hold above exists because nobody has looked yet. On this path
        # somebody has: /api/node/confirm is only reached by the owner of the
        # camera, authenticated with its token, answering the popup about a
        # vehicle they just watched go past. Holding it for review asked the
        # same person the same question twice.
        #
        # It also made the publish depend on a SECOND request succeeding
        # (labelbank then calls /api/node/label to promote it). If that call
        # failed the sighting sat private with no pen card - the confirmation
        # reaching neither the map nor the queue, which is the exact silent loss
        # /api/node/confirm was built to end.
        #
        # `public_tiers` still decides: a confirmed council truck is not public
        # here either, because `tier` came from privacy.tier_for(vclass) above.
        if candidate and not operator_confirmed:
            c["why"] = (c.get("why") or "") + " - held for human review"
            tier = "private"
        elif candidate:
            c["why"] = (c.get("why") or "") + " - confirmed by the camera operator"
            # 🚨 RECORD THE DECISION. A public row with reviewed IS NULL is
            # indistinguishable from one that reached the map unreviewed, which
            # is the single claim this project makes about itself - "nothing is
            # published without a person". The audit checks for exactly this
            # ("public tier with no human decision"), and it would have started
            # counting these.
            ev["_reviewed"] = "confirmed"
            ev["_decided_by"] = "human"

        # A camera node scores its own crop, so its GOVERNMENT candidates go
        # straight to the review pen for a human to confirm - captured here as a
        # sub-resolution, plate-less crop BEFORE the mirror strips the image
        # below. Phone-node crops take the inbox path instead (box_puller pulls
        # and scores them at home first), so they are excluded here.
        # `phone` (a human submission) is included here now that it no longer
        # reaches the public tier by itself: the pen is where its claim gets
        # looked at. `phone_node` is still excluded because it has its own route
        # - the inbox, which box_puller pulls and scores at home.
        review_crop = None
        evidence_crop = None
        if (source != "phone_node" and mirror.relay_enabled()
                and ev.get("snap_b64") and c["vclass"] in ("police", "gov_dot")):
            try:
                # 🚨 CROP TO THE VEHICLE FIRST. A camera node posts its whole
                # FRAME (store_submitted crops it server-side), so merely
                # downscaling it parked a 200px photograph of the street - and
                # the neighbours' houses with it - in front of every reviewer.
                # The published snapshot was already being cropped correctly;
                # only this second reader of the same field was not.
                _vb = ev.get("vehicle_box")
                if _vb:
                    review_crop = snapshot.crop_to_subres(ev["snap_b64"],
                                                          tuple(_vb))
                    # And the same crop WITHOUT the 200px shrink, for the
                    # reviewer and for whatever gets published if they say yes.
                    # Built here because this is the last point the original
                    # frame is still in hand - below, the mirror strips the
                    # image and the redaction path rewrites it. Failing to
                    # produce it must never cost the pen its card, so the pen
                    # crop above is computed first and this cannot unset it.
                    try:
                        evidence_crop = snapshot.crop_full(ev["snap_b64"],
                                                           tuple(_vb))
                    except Exception:
                        evidence_crop = None
                else:
                    # No box means nothing to crop to. Park no picture rather
                    # than a bystander's - the same call the snapshot path
                    # already makes a few lines below. The candidate still
                    # reaches the reviewer, without an image.
                    review_crop = None
            except Exception:
                review_crop = None

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
            elif tier != "public" and not pbox and not candidate:
                # We cannot redact a plate we cannot locate, and a photograph of
                # a car IS a photograph of its plate. So a private-tier image
                # with no plate box is discarded rather than stored. The
                # sighting itself still counts; only the picture is dropped.
                #
                # ⚠️ `not candidate` IS THE THIRD BEHAVIOUR THIS FILE LOST BY
                # READING `tier` AFTER IT WAS FORCED TO PRIVATE. The two named
                # above tier's rewrite are fragment merging and the pen write;
                # this is the same mistake with the worst outcome. A marked
                # patrol car whose plate the camera could not resolve - which is
                # MOST of them, at 22px against the 60 an OCR needs - hit this
                # branch and had its photograph thrown away for failing to
                # locate a plate that was never going to be legible. The
                # candidate's original is kept in core.EVIDENCE below instead,
                # where the reviewer can actually see the livery.
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

        # 🚨 CLAMP THE NODE'S CLOCK. IT DECIDES RETENTION, ORDERING AND LIVENESS.
        #
        # This took the submitted value unchecked, and three separate systems
        # read it afterwards. A camera running one hour slow has its sighting
        # stored, penned, confirmed by a human and promoted to public - and then
        # never drawn, because /api/sightings defaults to since = now() - 3600.
        # Every counter reports success and the dot is simply not on the map.
        # A back-dated row is also what the retention sweep deletes first, so a
        # skewed clock can quietly feed evidence to the janitor.
        #
        # The node's claim is kept in the response rather than thrown away: the
        # camera is the only party that can fix its own clock, and it cannot fix
        # what it is never told. `clock_skew_s` is what a node should log loudly.
        claimed = float(ev.get("ts") or now())
        server_now = now()
        skew = claimed - server_now
        # A little slack for network delay and honest drift; beyond that the
        # SERVER's clock wins, because it is the one every reader compares
        # against.
        ts = claimed if abs(skew) <= 120 else server_now

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
            "reviewed": ev.get("_reviewed"), "decided_by": ev.get("_decided_by"),
            "bank_ref": (ev.get("bank_ref") or None),
            "sig_ok": 1 if sig_ok else 0,
        }
        # 🚨 IS THIS A NEW VEHICLE, OR THE SAME ONE STILL CROSSING THE FRAME?
        # A tracker that loses a vehicle behind a window pillar and re-acquires
        # it produces several completed tracks for one pass, and each posted its
        # own sighting - three dots on the map for one patrol car. Fold them.
        # See db.merge_window_row for why the test is deliberately blunt.
        # 🚨 `candidate`, NOT `tier`. This read tier == "public" a few lines
        # after tier was forced to "private", so it was dead code and the
        # occluded-pass bug came straight back - now as THREE review cards for
        # one patrol car, which a human then confirms three times onto the map.
        # Only government candidates are folded: merging ordinary traffic by
        # class and time window would be far too blunt.
        prior = (db.merge_window_row(nid, c["vclass"], ts)
                 if candidate else None)
        if prior:
            db.bump_detections(prior["id"], ts, c["conf"])
            # Liveness is a SERVER observation: "this node spoke to me just
            # now". Passing the node's own timestamp let a fast clock pin a
            # dead camera online and a slow one flap a working camera offline,
            # while /api/heartbeat recorded the same event correctly - so the
            # two paths disagreed about the same node.
            db.heartbeat(nid)
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
        # Park the plate-less crop for the home classifier, keyed by this row.
        # After strip_sighting, so nothing about the mirror's own record changed.
        if relay_crop is not None:
            mirror.quarantine_write(rec["id"], relay_crop, {
                "ts": ts, "pub_lat": s_lat, "pub_lon": s_lon,
                "node_name": nd.get("name") or "",
                "det_conf": ev.get("det_conf"), "body": ev.get("body")})
        # A camera's government candidate parks in the review pen for a human.
        if review_crop is not None:
            mirror.review_write(rec["id"], review_crop, {
                "ts": ts, "node_id": nid, "node_name": nd.get("name") or "",
                "score": c.get("conf"), "vclass": c["vclass"],
                "det_conf": ev.get("det_conf"), "body": ev.get("body")})
            # The full-resolution original beside it, at home only. This is
            # what the reviewer is shown and what a confirmation publishes, so
            # the livery survives the wait and a plate is not destroyed for a
            # vehicle whose plate is the entire point of the public tier.
            # mirror.evidence_write refuses on a mirror; core.EVIDENCE carries
            # the rails and the cost.
            if evidence_crop is not None:
                mirror.evidence_write(rec["id"], evidence_crop)

        # A node that is posting is self-evidently awake, so a submission is
        # also a heartbeat. Detectors that never learn to beat still show
        # online while they are actually working.
        db.heartbeat(nid)
        FEED.publish(rec)
        # 🚨 TELL THE CAMERA ITS CROP IS WAITING ON A HUMAN.
        # A phone detects VEHICLES; the government call happens here, after the
        # post. So the phone has never known it caught a patrol car - the
        # judgement was in this response all along and the client read only the
        # error field. A desktop node has had push_confirm since the start
        # ("ask the owner to confirm, NOW, while the vehicle is still in
        # sight"), and a phone volunteer got nothing until they opened /rv
        # hours later to a still image of a car they no longer remember.
        # `parked` is the honest signal: not "this is a cop", but "a person is
        # being asked about this one", which is exactly when it is worth asking
        # the person who is standing there.
        out = {"id": rec["id"], "tier": tier, "vclass": c["vclass"],
               "why": c["why"], "parked": review_crop is not None}
        if abs(skew) > 120:
            # Said plainly, because the node cannot see this any other way and
            # the consequence - its sightings landing outside every default
            # time window - is invisible from its side.
            out["clock_skew_s"] = round(skew, 1)
            out["note"] = (f"your clock is {abs(skew):.0f}s "
                           f"{'ahead of' if skew > 0 else 'behind'} the hub; "
                           f"the server time was used instead")
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
