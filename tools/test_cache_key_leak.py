"""Prove a response can never be cached under a PREVIOUS request's key.

🚨 THE BUG THIS EXISTS FOR WAS A PRIVACY BUG, NOT A PERFORMANCE ONE.

The single-flight micro-cache stashes the cache key on the handler instance and
reads it back in _send. One handler instance serves EVERY request on a keep-alive
connection, and the key was only cleared inside the `code == 200` branch. So a
non-200 left the key set, and the next 200 on that same connection was stored
under the previous request's key.

_too_busy() made it reachable rather than theoretical: it writes its 503 straight
to wfile and never enters _send, so it cannot clear anything - and it fires
exactly during overload, when connections are being reused hardest.

The worst case is not a stale tile. /api/node/me is the ONE endpoint that returns
a camera's true lat/lon, and every other route publishes only a jittered point
and a road span. A body from that endpoint cached under a public key like
/api/nodes would hand a camera's real position to anyone.

    python tools/test_cache_key_leak.py
"""

from __future__ import annotations

import http.client
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PUBLIC = "/api/stats"        # cacheable, public
PRIVATE = "/api/node/me"     # no-store, returns a camera's TRUE position


def main() -> int:
    import hub
    from dualstack import serve

    class H(hub.Handler):
        def _do_GET_inner(self):
            p = self.path.split("?")[0]
            if p == PUBLIC:
                body = b'{"public":true}'
            elif p == PRIVATE:
                # Stand-in for the real thing: a secret that must never be
                # served to anyone else, under any key.
                body = b'{"lat":42.8156,"lon":-83.7824,"secret":"TRUE_POSITION"}'
            else:
                body = b'{"other":true}'
            # ⚠️ MUST go through _send. That is where the cache is filled, so a
            # stub writing straight to wfile would exercise none of the code
            # under test - the first version of this file did exactly that and
            # passed against a knowingly broken build.
            self._send(200, body, "application/json")

    srv = serve(H, 0, "127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    hub.Handler._MICRO.clear()

    # Drain the permits so the NEXT request is refused by _too_busy - the path
    # that bypasses _send and therefore could never clear the key.
    taken = 0
    while hub.Handler._INFLIGHT.acquire(blocking=False):
        taken += 1

    # ONE keep-alive connection, three requests in order:
    #   1. the cacheable public path  -> refused (503) while permits are gone
    #   2. the private path           -> 200, and must NOT be cached
    #   3. the public path again      -> must NOT return the private body
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request("GET", PUBLIC)
    r1 = c.getresponse(); b1 = r1.read()

    for _ in range(taken):           # give the permits back
        hub.Handler._INFLIGHT.release()

    c.request("GET", PRIVATE)
    r2 = c.getresponse(); b2 = r2.read()
    c.close()

    cached_under_public = hub.Handler._MICRO.get(PUBLIC)
    srv.shutdown()

    print(f"on ONE keep-alive connection:")
    print(f"  1. GET {PUBLIC:14} -> {r1.status} (503 = refused by the gate)")
    print(f"  2. GET {PRIVATE:14} -> {r2.status}")
    print(f"  3. cache entry stored under '{PUBLIC}': "
          f"{'NONE' if cached_under_public is None else cached_under_public[1][:48]}")

    bad = []
    if r1.status != 503:
        print("\nSKIP: could not force the refusal path; test inconclusive")
        return 0
    if cached_under_public is not None:
        body = cached_under_public[1]
        if b"TRUE_POSITION" in body:
            bad.append("A CAMERA'S TRUE POSITION WAS CACHED UNDER A PUBLIC KEY")
        else:
            bad.append(f"a response was cached under a stale key: {body[:60]!r}")

    if bad:
        print("\nFAIL: " + "; ".join(bad))
        return 1
    print("\nok: the refused request left no key behind, so nothing was "
          "cached under it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
