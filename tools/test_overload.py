"""Prove the hub sheds load instead of collapsing under it.

🚨 WRITTEN AFTER A CONGESTION COLLAPSE THAT THE DESCRIPTOR LIMIT HAD BEEN HIDING.

With the descriptor limit at its 1024 default, the process died at roughly 340
connections. That looked purely like a leak bug, and it was also an accidental
load shedder. Raising the limit to 65536 was correct and it removed the brake,
so CPU became the binding constraint: measured live, Caddy held 1098 upstream
connections with ~12 more per second arriving, 588 Python threads, and every
thread made every other thread slower. The watchdog restarted it three times in
ten minutes and it climbed straight back each time.

What this asserts is NOT "every request succeeds". Under real overload that is
the wrong goal and chasing it is what causes the collapse. It asserts:

  * the thread count stays bounded,
  * excess connections are refused FAST rather than queued slowly,
  * and the server is still serving normally once the flood stops.

    python tools/test_overload.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FLOOD = 300          # simultaneous connections, well past MAX_INFLIGHT


def main() -> int:
    import db
    import dualstack
    from dualstack import serve
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            # Slow on purpose. A fast handler never builds a queue, so a test
            # against one would pass with no admission control at all.
            time.sleep(0.25)
            db.connect().execute("SELECT 1").fetchone()
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = serve(H, 0, "127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    peak_threads = 0
    stop = threading.Event()

    def watch():
        nonlocal peak_threads
        while not stop.is_set():
            peak_threads = max(peak_threads, threading.active_count())
            time.sleep(0.02)

    threading.Thread(target=watch, daemon=True).start()

    served, refused, errors = [], [], []
    lock = threading.Lock()

    def hit():
        t0 = time.time()
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=20)
            s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            data = s.recv(200)
            s.close()
            dt = time.time() - t0
            with lock:
                if b"503" in data:
                    refused.append(dt)
                elif b"200" in data:
                    served.append(dt)
                else:
                    errors.append(dt)
        except Exception:
            with lock:
                errors.append(time.time() - t0)

    threads = [threading.Thread(target=hit) for _ in range(FLOOD)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    flood_s = time.time() - t0
    stop.set()

    # Once the flood is over the server must be immediately usable again. A
    # server that survives a flood by wedging has not survived it.
    time.sleep(0.5)
    recovered = False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            recovered = r.status == 200
    except Exception:
        recovered = False
    srv.shutdown()

    cap = dualstack.MAX_INFLIGHT
    avg = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    print(f"flood of {FLOOD} simultaneous connections against a cap of {cap}, "
          f"{flood_s:.1f}s")
    print(f"  served   : {len(served):4}  avg {avg(served)*1000:7.0f} ms")
    print(f"  refused  : {len(refused):4}  avg {avg(refused)*1000:7.0f} ms  (503, fast on purpose)")
    print(f"  errors   : {len(errors):4}")
    print(f"  peak threads: {peak_threads}")
    print(f"  serving again after the flood: {recovered}")

    bad = []
    # The ceiling must hold. The allowance covers the flood's own client
    # threads, which live in this same process.
    if peak_threads > cap + FLOOD + 40:
        bad.append(f"thread count unbounded ({peak_threads})")
    if not recovered:
        bad.append("server did not recover after the flood")
    if refused and avg(refused) > avg(served) and served:
        bad.append("refusals are slower than successes - shedding is not cheap")
    if not served:
        bad.append("nothing was served at all - the cap is shutting everyone out")

    if bad:
        print("\nFAIL: " + "; ".join(bad))
        return 1
    print("\nok: bounded, sheds fast, and still serving afterwards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
