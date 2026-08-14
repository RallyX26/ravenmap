"""Prove the hub does not leak a file descriptor per visitor.

🚨 THIS IS THE TEST THAT DID NOT EXIST, AND ITS ABSENCE COST THE PUBLIC SITE.

db.connect() caches one sqlite connection per thread and ThreadingHTTPServer
runs one thread per connection, so every visitor used to leave two descriptors
behind (the db and its -wal). At 1024 open files every route began failing with
"unable to open database file" - which reads like a missing database and is
actually an exhausted process. It took ~45 minutes of ordinary traffic, and
every deploy reset the clock, so it hid for days behind its own restarts.

Nothing here asserts on RESPONSES. A leak test that checks status codes passes
happily while the process fills up. It counts descriptors, which is the thing
that ran out.

    python tools/test_fd_leak.py
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUESTS = 60
# One connection per request (no keep-alive) is the shape that leaked: each one
# is a fresh thread. A keep-alive client would reuse a single thread and the
# bug would not reproduce at all - which is exactly why local testing missed it.
HEADERS = {"Connection": "close"}


def open_fds() -> int:
    """Descriptors this process holds. Linux via /proc, else psutil, else skip."""
    proc = Path(f"/proc/{os.getpid()}/fd")
    if proc.exists():
        return len(list(proc.iterdir()))
    try:
        import psutil
        return psutil.Process().num_fds() if hasattr(psutil.Process(), "num_fds") \
            else psutil.Process().num_handles()
    except Exception:
        return -1


def db_fds() -> int:
    """Descriptors pointing at the sqlite database specifically."""
    proc = Path(f"/proc/{os.getpid()}/fd")
    if not proc.exists():
        return -1
    n = 0
    for f in proc.iterdir():
        try:
            if "sparrow.db" in os.readlink(f):
                n += 1
        except OSError:
            pass
    return n


def main() -> int:
    import db
    from dualstack import serve

    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            # Touch the database exactly as a real route does, so the thread
            # acquires a connection it must then give back.
            db.connect().execute("SELECT 1").fetchone()
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # `--broken` builds the server the way it was BEFORE the fix, so the test
    # can be shown to actually detect the leak. A leak test that has never been
    # seen to fail is a decoration: this one was written after the bug it
    # catches, and that is precisely when it is easiest to fool yourself.
    if "--broken" in sys.argv:
        from http.server import ThreadingHTTPServer

        class _Leaky(ThreadingHTTPServer):
            daemon_threads = True

        srv = _Leaky(("127.0.0.1", 0), H)
        print("running WITHOUT the per-thread close (expected to FAIL)\n")
    else:
        srv = serve(H, 0, "127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # Warm up: the first requests also load the schema and run migrations, so
    # measuring from zero would count one-off setup as a leak.
    for _ in range(5):
        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/", headers=HEADERS),
            timeout=10).read()

    before, before_db = open_fds(), db_fds()
    for _ in range(REQUESTS):
        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/", headers=HEADERS),
            timeout=10).read()
    after, after_db = open_fds(), db_fds()
    srv.shutdown()

    if before < 0:
        print("SKIP: cannot count descriptors on this platform "
              "(run on the box, or pip install psutil)")
        return 0

    grew, grew_db = after - before, after_db - before_db
    print(f"{REQUESTS} requests, each on its own connection/thread")
    print(f"  all descriptors : {before} -> {after}   ({grew:+d})")
    print(f"  sparrow.db      : {before_db} -> {after_db}   ({grew_db:+d})")

    # A couple of transient descriptors is normal (sockets in TIME_WAIT, the
    # listener). One PER REQUEST is the bug. The threshold is deliberately far
    # below REQUESTS so a partial regression still fails.
    limit = 5
    if grew_db > limit or grew > REQUESTS // 2:
        print(f"\nFAIL: leaking descriptors - {REQUESTS} visitors cost {grew} "
              f"of them. At this rate the process dies at its 1024 limit and "
              f"every route returns 'unable to open database file'.")
        return 1
    print("\nok: descriptors are returned when the request thread ends")
    return 0


if __name__ == "__main__":
    sys.exit(main())
