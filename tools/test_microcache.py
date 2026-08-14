"""Prove identical public requests collapse to one computation.

🚨 WHY THIS EXISTS. hub._cache_control states the design: the origin caps near
55 req/s on map data and survives a crowd only because "thousands of viewers
collapse to about one origin fetch per window". That assumed an edge cache in
front. map.sparrowmap.com resolves straight to the box, so nothing was
collapsing anything - measured during a live outage: 128 requests in flight, 254
threads, and /api/nodes and /api/sightings each holding a permit for 10-21
SECONDS. Plain reads, starved of CPU by the crowd doing them simultaneously.

So the origin collapses them itself. This asserts that it does, and - just as
importantly - that it does NOT do it for the private paths.

    python tools/test_microcache.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONCURRENT = 40


def main() -> int:
    import hub
    from dualstack import serve

    calls = {"n": 0}
    lock = threading.Lock()

    class H(hub.Handler):
        def _do_GET_inner(self):
            # Stand in for the real route: count how many times the work is
            # actually performed, and make it slow enough that a stampede of
            # uncollapsed requests would be obvious.
            with lock:
                calls["n"] += 1
            time.sleep(0.3)
            body = json.dumps({"ok": True}).encode()
            self._send(200, body, "application/json")

    srv = serve(H, 0, "127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    def hammer(path, n):
        def one():
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                       timeout=20).read()
            except Exception:
                pass
        ts = [threading.Thread(target=one) for _ in range(n)]
        t0 = time.time()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return time.time() - t0

    # A CACHEABLE public path. /api/stats carries max-age, so a burst of
    # identical requests must cost one computation, not forty.
    calls["n"] = 0
    took = hammer("/api/stats", CONCURRENT)
    cacheable_calls = calls["n"]

    # A NO-STORE path must NOT be collapsed. /api/plate is a search: caching it
    # would build exactly the record of who looked up what that no-store exists
    # to prevent, so every request must reach the handler.
    calls["n"] = 0
    hammer("/api/plate?q=ABC123", CONCURRENT)
    private_calls = calls["n"]

    srv.shutdown()

    print(f"{CONCURRENT} simultaneous identical requests\n")
    print(f"  cacheable /api/stats      -> handler ran {cacheable_calls} time(s), "
          f"{took:.1f}s")
    print(f"  no-store  /api/plate?q=.. -> handler ran {private_calls} time(s)")

    bad = []
    # A few is fine: requests that arrive before the first one finishes all miss.
    if cacheable_calls > 5:
        bad.append(f"public path not collapsing ({cacheable_calls} of {CONCURRENT})")
    # 🚨 The privacy guard. A search must never be served from a shared cache.
    if private_calls < CONCURRENT:
        bad.append(f"a no-store SEARCH path was cached "
                   f"({private_calls} of {CONCURRENT} reached the handler)")

    if bad:
        print("\nFAIL: " + "; ".join(bad))
        return 1
    print("\nok: public requests collapse, private searches never do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
