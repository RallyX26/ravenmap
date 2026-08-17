"""The header's public count must follow the SELECTED window, not a fixed 24h.

🚨 THE BUG. He reported "top of map ui says 88 sightings but there are 188".
Measured on the live API at the time: 88 public sightings in the last 24h, 183
all time. BOTH numbers were correct. The header printed the 24h figure while the
map drew whatever window was selected - and `everything` is the DEFAULT, so
every visitor met a header that disagreed with the dots underneath it.

⚠️ THIS TESTS THE ARITHMETIC, NOT THE RENDERING. The page cannot be executed
here, so the check is: for each window, does the server's own data give the
number the page will now compute? If these two ever disagree, the header is
lying again.

    python tools/test_public_count.py            # against the live map
    python tools/test_public_count.py --base http://127.0.0.1:8150
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

FAILED = []

# Must match WINDOW_LABEL in public/app.js and the <option value>s in
# index.html. A window the page offers and this test does not check is a window
# nobody has verified.
WINDOWS = {0: "all time", 300: "5m", 3600: "1h",
           21600: "6h", 86400: "24h", 604800: "7d"}


def check(name: str, cond: bool, detail: str = "") -> None:
    # ⚠️ DETAIL ON FAILURE ONLY. Printing the failure text beside "ok" produced
    # lines like "ok  1h is not smaller than the one before it   7 < 0", which
    # reads as a contradiction and trains you to skim past the output - the
    # opposite of what a test is for.
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://map.sparrowmap.com")
    a = ap.parse_args()

    def get(path):
        # ⚠️ ALWAYS SEND A User-Agent. Cloudflare answers 1010 without one, and
        # that once looked exactly like an ingest outage.
        req = urllib.request.Request(a.base + path, headers={
            "User-Agent": "SparrowMap-test-public-count/1.0"})
        return json.loads(urllib.request.urlopen(req, timeout=90).read())

    stats = get("/api/stats")
    now = time.time()
    # The same request the page makes with the widest window, so every narrower
    # window is a subset of these rows - exactly how the page counts them.
    rows = get("/api/sightings?since=0&vclass=public&limit=2000")
    print(f"{len(rows)} public rows, server says public_24h={stats['public_24h']}\n")

    ages = [now - (r.get("ts") or now) for r in rows]

    # 🚨 The one that would have caught the bug: the 24h subset must equal the
    # figure the header used to print unconditionally.
    in24 = sum(1 for x in ages if x <= 86400)
    print(f"  page's 24h count {in24} vs server's public_24h {stats['public_24h']}")
    check("the 24h subset matches the server's public_24h",
          in24 == stats["public_24h"],
          f"page would show {in24}, server says {stats['public_24h']}")

    # And the one that shows why a fixed label was wrong.
    allt = len(rows)
    print(f"  a fixed '24h' label would misreport all time by "
          f"{allt - in24} sighting(s)")
    check("'all time' is a DIFFERENT number from 24h", allt != in24,
          f"both are {allt}, so this test proves nothing today")

    print()
    prev = -1
    for secs in sorted(WINDOWS):
        n = allt if secs == 0 else sum(1 for x in ages if x <= secs)
        print(f"  window {WINDOWS[secs]:9} -> {n}")
        # Windows are nested, so counts must never decrease as the window grows.
        # 0 sorts first and means "no limit", so it is checked against the end.
        if secs and prev >= 0:
            check(f"{WINDOWS[secs]} is not smaller than the window before it",
                  n >= prev, f"{n} is fewer than the previous window's {prev}")
        if secs:
            prev = n
    check("no window exceeds all time", prev <= allt,
          f"the widest bounded window has {prev}, more than all time's {allt}")

    if len(rows) >= 2000:
        print("\n⚠️ the feed hit its 2000-row cap, so 'all time' here is a FLOOR."
              "\n   The page prints a + in that case; this test cannot verify"
              "\n   the true total without a wider limit.")

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
