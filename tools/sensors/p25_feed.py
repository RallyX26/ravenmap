"""Feed police-radio activity from a local scanner into SparrowMap.

    An RTL-SDR + trunk-recorder can follow P25 trunked police radio where it is
    unencrypted. That tells you there is ACTIVITY in an area, not a GPS pin - so
    this posts a "radio active here" pulse at YOUR receiver's location, deduped so
    one scanner is one pulse, with the talkgroup as a label. Many departments now
    encrypt; check your county before relying on it.

Typical use (point it at trunk-recorder's call JSON, and give the receiver's
fixed location):

    python tools/sensors/p25_feed.py --hub https://map.sparrowmap.com \
        --node n_abcd1234 --key D:/LLM/mynode.token \
        --lat 42.73 --lon -84.55 --calls /var/trunk-recorder/calls

Or pipe activity lines / JSON on stdin. See it work with no radio:

    python tools/sensors/p25_feed.py --simulate --lat 42.73 --lon -84.55

STATUS: BETA. trunk-recorder can be wired many ways (upload script, JSON logs,
its status socket); parse_activity() handles a plain talkgroup line and a JSON
call object. Point --calls at a directory of *.json call files, or feed stdin.
Not yet tested against a live trunk-recorder.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.request

SEND_EVERY_S = 20.0     # one pulse per this window; the hub lingers radio for 5 min


def parse_activity(text):
    """-> a short label (talkgroup / description) for an active call, or None."""
    s = text.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        tg = obj.get("talkgroup_description") or obj.get("talkgroup_tag") \
            or obj.get("talkgroup") or obj.get("tg")
        return ("TG " + str(tg))[:80] if tg is not None else "radio active"
    except ValueError:
        pass
    return s[:80]


def post(hub, node, token, lat, lon, label, do_post):
    payload = {"node_id": node, "kind": "radio", "lat": lat, "lon": lon,
               "label": label}
    if not do_post:
        print("would POST /api/sensor/hit " + json.dumps(payload))
        return
    req = urllib.request.Request(
        hub.rstrip("/") + "/api/sensor/hit",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "sparrow-p25-feed/0.1"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("sent radio activity: " + label)
    except Exception as e:
        print("post failed: %s" % e)


def sources(a):
    if a.simulate:
        tgs = ["Dispatch 1", "Patrol East", "Tac 3"]
        i = 0
        while True:
            yield json.dumps({"talkgroup_description": tgs[i % len(tgs)]})
            i += 1
            time.sleep(4.0)
    if a.calls:
        seen = set()
        while True:
            for f in sorted(glob.glob(os.path.join(a.calls, "*.json"))):
                if f in seen:
                    continue
                seen.add(f)
                try:
                    yield open(f, "r", encoding="utf-8").read()
                except OSError:
                    pass
            time.sleep(2.0)
    else:
        for raw in sys.stdin:
            yield raw


def main():
    ap = argparse.ArgumentParser(description="Feed police-radio activity to SparrowMap")
    ap.add_argument("--hub")
    ap.add_argument("--node")
    ap.add_argument("--key")
    ap.add_argument("--token")
    ap.add_argument("--lat", type=float, required=True, help="receiver latitude")
    ap.add_argument("--lon", type=float, required=True, help="receiver longitude")
    ap.add_argument("--calls", help="directory of trunk-recorder *.json call files")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--post", action="store_true")
    a = ap.parse_args()

    token = a.token
    if a.key:
        token = open(a.key, "r", encoding="utf-8").read().strip()
    do_post = bool(a.hub and a.node and token) and (a.post or not a.simulate)
    if a.hub and not (a.node and token):
        sys.exit("--hub needs --node and --key/--token")

    last = 0.0
    for raw in sources(a):
        label = parse_activity(raw)
        if not label:
            continue
        now = time.time()
        if now - last < SEND_EVERY_S:
            continue
        last = now
        post(a.hub, a.node, token, a.lat, a.lon, label, do_post)


if __name__ == "__main__":
    main()
