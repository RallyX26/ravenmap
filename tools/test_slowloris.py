"""A trickling client must not hold an admission permit indefinitely.

🚨 THE PER-RECV TIMEOUT WAS NOT A BOUND ON THE REQUEST.
dualstack sets a socket timeout so an idle keep-alive connection cannot pin a
thread forever, and that is correct - but it applies to each recv individually.
A client sending one byte every 19 seconds resets it every time, so _body() sat
inside an admission permit for as long as the client cared to keep dribbling.

A few hundred of those at ~10 B/s and every POST, every no-store route and all
map data past its micro-TTL returns 503, while the page shell and tiles keep
serving normally - which makes it harder to diagnose, not milder. It is a
slow-loris aimed at the one resource the whole site shares.

The fix is a wall-clock deadline on the whole body read. This asserts the permit
comes back: a trickling client is cut off, and a normal request served during
the trickle still succeeds.

    python tools/test_slowloris.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import hub
    from dualstack import serve

    class H(hub.Handler):
        def _do_POST_inner(self):
            body = self._body()
            out = b'{"got":%d}' % len(body)
            self._send(200, out, "application/json")

        def _do_GET_inner(self):
            self._send(200, b'{"ok":true}', "application/json")

    srv = serve(H, 0, "127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    # Claim a large body, then send almost nothing, slowly. Before the fix this
    # held a permit for as long as the loop ran.
    CLAIM = 200_000
    stop = threading.Event()

    def trickle():
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=30)
            s.sendall(
                b"POST /slow HTTP/1.1\r\nHost: x\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: %d\r\n\r\n" % CLAIM)
            while not stop.is_set():
                try:
                    s.sendall(b" ")     # one byte, then wait
                except Exception:
                    return
                time.sleep(2.0)
        except Exception:
            pass

    t0 = time.time()
    threading.Thread(target=trickle, daemon=True).start()
    time.sleep(1.0)

    # How many permits are free while the trickler is mid-body?
    taken = []
    while hub.Handler._INFLIGHT.acquire(blocking=False):
        taken.append(1)
    free_during = len(taken)
    for _ in taken:
        hub.Handler._INFLIGHT.release()

    # A normal visitor during the trickle must still be served.
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=20) as r:
            normal = r.status
    except Exception as exc:
        normal = f"failed: {type(exc).__name__}"

    # The permit must come back on its own, without the client finishing.
    released_after = None
    for _ in range(30):
        time.sleep(1.0)
        got = []
        while hub.Handler._INFLIGHT.acquire(blocking=False):
            got.append(1)
        n = len(got)
        for _ in got:
            hub.Handler._INFLIGHT.release()
        if n >= hub.MAX_REQUESTS:
            released_after = round(time.time() - t0, 1)
            break

    stop.set()
    srv.shutdown()

    cap = hub.MAX_REQUESTS
    print(f"  permits free while trickling : {free_during}/{cap}")
    print(f"  normal request during it     : {normal}")
    print(f"  all permits back after       : "
          f"{released_after if released_after is not None else 'NEVER (>30s)'}s")

    bad = []
    if normal != 200:
        bad.append(f"a normal request was refused during the trickle ({normal})")
    if released_after is None:
        bad.append("the permit was never released - the body read has no "
                   "wall-clock bound")

    if bad:
        print("\nFAIL: " + "; ".join(bad))
        return 1
    print("\nok: a trickling client cannot hold a permit indefinitely")
    return 0


if __name__ == "__main__":
    sys.exit(main())
