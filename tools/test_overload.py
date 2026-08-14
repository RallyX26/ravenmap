"""Prove the hub sheds load instead of collapsing under it.

🚨 WRITTEN AFTER A CONGESTION COLLAPSE, THEN REWRITTEN AFTER THE FIX FOR IT WAS
ITSELF WRONG TWICE. Both mistakes are the reason this file is shaped as it is.

The collapse: with the descriptor limit at its 1024 default the process died at
roughly 340 connections, which looked purely like a leak and was also, by
accident, load shedding. Raising the limit removed that brake, so CPU became
binding - 1098 upstream connections, ~12 more per second, 588 threads on 2 vCPUs,
every thread making every other thread slower.

The fix that was wrong twice: it gated CONNECTIONS. At a cap of 48 it refused
real visitors within minutes; raised to 250 it refused them again the moment the
proxy's pool reached 250 - measured live at 254 threads with the box at load 5
doing nothing. A connection is not work. Keep-alive means a proxy holds idle
sockets open by design, each costing a thread and no CPU.

⚠️ SO THIS TEST EXERCISES `hub.Handler`, NOT A LOCAL STAND-IN. An earlier version
defined its own handler, which meant that once the gate moved into hub.Handler
the test was flooding a 1200-connection backstop and passing while testing
nothing. A test that cannot fail is worse than no test, and this one had already
quietly become that once.

What it asserts is NOT "every request succeeds". Under real overload that is the
wrong goal and chasing it is what causes the collapse. It asserts:

  * concurrency is actually bounded at the configured number,
  * excess is refused FAST rather than queued slowly,
  * idle keep-alive connections do NOT consume permits (the bug that shipped),
  * and the server is serving normally once the flood stops.

    python tools/test_overload.py
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

HOLD_S = 0.4          # how long a request occupies a permit


def main() -> int:
    import db
    import hub
    from dualstack import serve

    cap = hub.MAX_REQUESTS
    flood = max(120, cap * 4)

    # Subclassing the REAL handler means the real gate is under test. Only the
    # dispatch body is replaced - do_GET, the semaphore and _too_busy are hub's.
    peak_concurrent = 0
    concurrent = 0
    clock = threading.Lock()

    class H(hub.Handler):
        def _do_GET_inner(self):
            nonlocal concurrent, peak_concurrent
            with clock:
                concurrent += 1
                peak_concurrent = max(peak_concurrent, concurrent)
            try:
                # Slow on purpose: a fast handler never builds a queue, so the
                # test would pass with no admission control at all.
                time.sleep(HOLD_S)
                db.connect().execute("SELECT 1").fetchone()
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                with clock:
                    concurrent -= 1

    srv = serve(H, 0, "127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    served, refused, errors = [], [], []
    lock = threading.Lock()

    def hit(keep_alive: bool):
        t0 = time.time()
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=30)
            conn = b"keep-alive" if keep_alive else b"close"
            s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: " + conn + b"\r\n\r\n")
            data = s.recv(200)
            dt = time.time() - t0
            with lock:
                (refused if b"503" in data else
                 served if b"200" in data else errors).append(dt)
            if keep_alive:
                # Sit on the connection AFTER the response, idle. This is the
                # shape that broke it: a pool of live-but-idle sockets. They must
                # not hold permits.
                time.sleep(2.0)
            s.close()
        except Exception:
            with lock:
                errors.append(time.time() - t0)

    # Phase 1: build a POOL of idle keep-alive connections larger than the
    # request cap. Every one must be served - they are one quick request each,
    # then silence.
    #
    # ⚠️ STAGGERED, AND THAT IS NOT A FUDGE. Firing all 52 at once means 52
    # requests genuinely in flight against a cap of 32, so 20 refusals would be
    # the cap working correctly - and the first version of this phase asserted
    # they should all succeed, which made the TEST wrong rather than the code.
    # What is under test here is whether a connection still costs a permit AFTER
    # its request is done, so the requests must not overlap past the cap.
    stagger = HOLD_S / max(cap // 4, 1)
    idlers = []
    for _ in range(cap + 20):
        t = threading.Thread(target=hit, args=(True,))
        t.start()
        idlers.append(t)
        time.sleep(stagger)
    time.sleep(1.0)                      # let the last ones finish and go idle
    idle_served, idle_refused = len(served), len(refused)

    # Phase 2: with all those sockets still open and idle, a normal visitor must
    # still get through. This is the exact case that 503'd in production.
    visitor_code = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=15) as r:
            visitor_code = r.status
    except Exception as exc:
        visitor_code = f"FAILED: {type(exc).__name__}"

    for t in idlers:
        t.join()
    served.clear(); refused.clear(); errors.clear()

    # Phase 3: a real flood of concurrent WORK, which must be capped and shed.
    peak_concurrent = 0
    threads = [threading.Thread(target=hit, args=(False,)) for _ in range(flood)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    flood_s = time.time() - t0

    time.sleep(0.5)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=15) as r:
            recovered = r.status == 200
    except Exception:
        recovered = False
    srv.shutdown()

    avg = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    print(f"request cap = {cap}\n")
    print(f"phase 1  {cap + 20} idle keep-alive connections")
    print(f"         served {idle_served}, refused {idle_refused}")
    print(f"phase 2  a normal visitor while they all sit idle -> {visitor_code}")
    print(f"phase 3  flood of {flood} concurrent requests, {flood_s:.1f}s")
    print(f"         served  : {len(served):4}  avg {avg(served)*1000:7.0f} ms")
    print(f"         refused : {len(refused):4}  avg {avg(refused)*1000:7.0f} ms  (503, fast)")
    print(f"         errors  : {len(errors):4}")
    print(f"         peak concurrent in handler: {peak_concurrent}  (cap {cap})")
    print(f"         serving after the flood: {recovered}")

    bad = []
    # 🚨 The regression guard. Idle sockets holding permits is what took the site
    # down, and it passed every earlier version of this test.
    if idle_refused:
        bad.append(f"{idle_refused} idle keep-alive connections were refused")
    if visitor_code != 200:
        bad.append(f"a visitor was locked out by idle connections ({visitor_code})")
    if peak_concurrent > cap:
        bad.append(f"cap breached: {peak_concurrent} concurrent > {cap}")
    if not recovered:
        bad.append("server did not recover after the flood")
    if refused and served and avg(refused) > avg(served):
        bad.append("refusals slower than successes - shedding is not cheap")
    if not served:
        bad.append("nothing served at all - the cap is shutting everyone out")

    if bad:
        print("\nFAIL: " + "; ".join(bad))
        return 1
    print("\nok: bounded at the cap, idle connections cost nothing, sheds fast, recovers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
