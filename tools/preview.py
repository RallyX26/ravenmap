"""Serve public/ locally so a page can be looked at before it is published.

A preview must not be a deploy. This binds a separate port, serves the working
copy straight from disk with no caching, and touches nothing on the live box -
so a page can be read on a phone on the same network and still not exist to the
internet.

    python tools/preview.py            # http://<this machine>:8777/guide.html
    python tools/preview.py --port 9001
"""

from __future__ import annotations

import argparse
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dualstack                       # noqa: E402
from core import PUBLIC                # noqa: E402


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Never cache a preview: the whole point is seeing the current edit.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):     # keep the console readable
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()

    handler = partial(Handler, directory=str(PUBLIC))
    srv = dualstack.serve(handler, args.port)
    print(f"preview of {PUBLIC} on port {args.port}")
    print(f"  http://localhost:{args.port}/guide.html")
    print("  (same address with this machine's name works from a phone)")
    print("Ctrl-C to stop. This serves the working copy only - nothing is deployed.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\npreview stopped")


if __name__ == "__main__":
    main()
