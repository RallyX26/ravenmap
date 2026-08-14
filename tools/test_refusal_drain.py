"""A refusal must not desync the connection, and must never hang.

🚨 TWO FAILURE MODES, OPPOSITE DIRECTIONS, SAME LINE OF CODE.

Caddy reuses upstream connections. Writing a 429 or 503 while the request body
is still unread leaves those bytes in the socket, where they are parsed as the
START of the next request on that connection - so the resulting 400 lands on an
unrelated visitor, and log_message is suppressed in hub.py so nothing records
it. That is the desync.

The obvious fix - always drain before refusing - introduces the opposite bug.
Plenty of handlers read the body and THEN refuse (unknown node, bad token, bad
json). Draining an already-consumed stream blocks waiting for bytes that are
gone, turning a tidy-up into a stall on the exact path that is shedding load.

So this asserts both: the connection stays usable after a refusal that did NOT
read the body, and a refusal that DID read the body still answers promptly.

    python tools/test_refusal_drain.py
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BODY = json.dumps({"node_id": "n_doesnotexist", "pad": "x" * 4000}).encode()


def main() -> int:
    import hub
    from dualstack import serve

    class H(hub.Handler):
        def _do_POST_inner(self):
            p = self.path
            if p == "/refuse-unread":
                # Refuse WITHOUT reading the body - the desync case.
                return self._err(429, "too fast")
            if p == "/refuse-after-read":
                # Read, then refuse - the hang case.
                self._body()
                return self._err(404, "unknown node")
            self._send(200, b'{"ok":true}', "application/json")

        def _do_GET_inner(self):
            self._send(200, b'{"ok":true}', "application/json")

    srv = serve(H, 0, "127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    results = {}

    # --- 1. refuse without reading, then REUSE the same connection ----------
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/refuse-unread", body=BODY,
              headers={"Content-Type": "application/json"})
    r1 = c.getresponse(); r1.read()
    results["refused"] = r1.status
    try:
        # If the body was left unread, THIS request is parsed from those
        # leftover bytes and comes back 400 - on a request that is perfectly
        # well-formed.
        c.request("GET", "/next")
        r2 = c.getresponse(); r2.read()
        results["next_on_same_conn"] = r2.status
    except Exception as exc:
        results["next_on_same_conn"] = f"broken: {type(exc).__name__}"
    c.close()

    # --- 2. refuse AFTER reading: must answer promptly, not hang ------------
    t0 = time.time()
    c2 = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        c2.request("POST", "/refuse-after-read", body=BODY,
                   headers={"Content-Type": "application/json"})
        r3 = c2.getresponse(); r3.read()
        results["after_read"] = r3.status
    except Exception as exc:
        results["after_read"] = f"HUNG/failed: {type(exc).__name__}"
    results["after_read_s"] = round(time.time() - t0, 2)
    c2.close()
    srv.shutdown()

    print(f"  refusal without reading the body : {results['refused']}")
    print(f"  next request on that connection  : {results['next_on_same_conn']}"
          f"   (400 or broken = desync)")
    print(f"  refusal AFTER reading the body   : {results['after_read']} "
          f"in {results['after_read_s']}s   (slow = drained a dead stream)")

    bad = []
    if results["next_on_same_conn"] != 200:
        bad.append(f"connection desynced after a refusal "
                   f"({results['next_on_same_conn']})")
    if results["after_read"] != 404:
        bad.append(f"refusal after reading did not answer ({results['after_read']})")
    if results["after_read_s"] > 3:
        bad.append(f"refusal after reading took {results['after_read_s']}s - "
                   f"the drain is blocking on a consumed stream")

    if bad:
        print("\nFAIL: " + "; ".join(bad))
        return 1
    print("\nok: refusals leave the connection usable and never block")
    return 0


if __name__ == "__main__":
    sys.exit(main())
