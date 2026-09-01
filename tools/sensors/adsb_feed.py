"""Feed aircraft from a LOCAL ADS-B receiver (dump1090 on a Raspberry Pi) into
SparrowMap's aircraft layer, so your Pi's coverage augments the OpenSky feed.

    Aircraft broadcast their position on 1090 MHz unencrypted by design; you are
    only receiving. Police helicopters and fixed-wing show up, and an ORBIT over
    one spot is the tell. This pushes what your receiver sees to the hub, which
    merges it with the aggregator feed and runs the same orbit detection on it.

Typical use (dump1090-fa serves JSON at :8080/data/aircraft.json):

    python tools/sensors/adsb_feed.py --hub https://map.sparrowmap.com \
        --node n_abcd1234 --key D:/LLM/.ssh/mynode.token \
        --dump1090 http://localhost:8080/data/aircraft.json

See it work with no receiver (prints the payload, posts nothing):

    python tools/sensors/adsb_feed.py --simulate

STATUS: BETA. The aircraft layer is a STAGED preview (enabled per-hub), so the
hub returns 404 on ingest until it is switched on. Tested against dump1090's
JSON shape; not yet against a live hub with the preview enabled.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

POLL_S = 5.0


def read_dump1090(url):
    """Return [{icao,lat,lon,alt_m,track,call}] from a dump1090 aircraft.json."""
    with urllib.request.urlopen(url, timeout=10) as r:
        doc = json.loads(r.read().decode("utf-8", "replace"))
    out = []
    for a in doc.get("aircraft", []):
        lat, lon = a.get("lat"), a.get("lon")
        if lat is None or lon is None:
            continue
        icao = str(a.get("hex") or "").strip().lower()
        if not icao:
            continue
        alt = a.get("alt_geom") or a.get("alt_baro")
        alt_m = None
        try:
            if isinstance(alt, (int, float)):
                alt_m = round(float(alt) * 0.3048)     # feet -> metres
        except (TypeError, ValueError):
            alt_m = None
        out.append({"icao": icao, "lat": lat, "lon": lon, "alt_m": alt_m,
                    "track": a.get("track"),
                    "call": (a.get("flight") or "").strip()})
    return out


def post(hub, node, token, craft, do_post):
    payload = {"node_id": node, "aircraft": craft}
    if not do_post:
        print("would POST /api/aircraft/ingest %d aircraft" % len(craft))
        if craft:
            print("  e.g. " + json.dumps(craft[0]))
        return
    req = urllib.request.Request(
        hub.rstrip("/") + "/api/aircraft/ingest",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "sparrow-adsb-feed/0.1"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
            print("sent %d, hub accepted %s" % (len(craft), d.get("accepted")))
    except Exception as e:
        print("post failed: %s" % e)


def main():
    ap = argparse.ArgumentParser(description="Feed local ADS-B to SparrowMap")
    ap.add_argument("--hub")
    ap.add_argument("--node")
    ap.add_argument("--key", help="file holding the node token")
    ap.add_argument("--token")
    ap.add_argument("--dump1090", default="http://localhost:8080/data/aircraft.json")
    ap.add_argument("--interval", type=float, default=POLL_S)
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--post", action="store_true",
                    help="actually POST (default prints unless a hub is set)")
    a = ap.parse_args()

    token = a.token
    if a.key:
        token = open(a.key, "r", encoding="utf-8").read().strip()
    do_post = bool(a.hub and a.node and token) and (a.post or not a.simulate)
    if a.hub and not (a.node and token):
        sys.exit("--hub needs --node and --key/--token")

    while True:
        if a.simulate:
            craft = [{"icao": "a1b2c3", "lat": 42.73, "lon": -84.55,
                      "alt_m": 300, "track": 210, "call": "N123PD"}]
        else:
            try:
                craft = read_dump1090(a.dump1090)
            except Exception as e:
                print("could not read dump1090 (%s)" % e)
                time.sleep(a.interval)
                continue
        if craft:
            post(a.hub, a.node, token, craft, do_post)
        if a.simulate:
            time.sleep(a.interval)
            if do_post:
                continue
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
