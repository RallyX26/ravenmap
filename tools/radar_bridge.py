"""Feed police-radar / speed-trap detections from a paired detector into
SparrowMap's live radar layer.

    A radar EMISSION is physical evidence, unlike the tap-a-coordinate "patrol
    here" that was withdrawn in Aug 2026. So every hit this bridge sends is
    authenticated with your NODE TOKEN (Authorization: Bearer ...), the same way
    a camera authenticates a sighting - the dot is attributable and your node
    revocable. The hub decides trust from the BAND: Ka (33-35 GHz) is police-only
    and reads red; K (24 GHz) is shared with cars' blind-spot radar and X is
    mostly doors, so those only firm up when several drivers agree.

Typical use (a Bluetooth-capable detector paired as a serial port, plus a USB
GPS read via gpsd):

    python tools/radar_bridge.py --hub https://map.sparrowmap.com \
        --node n_abcd1234 --key D:/LLM/.ssh/mynode.token \
        --serial COM5 --gpsd

Without hardware, see the pipeline end to end (prints payloads, posts nothing
unless you also pass --hub AND --post):

    python tools/radar_bridge.py --simulate

STATUS: BETA. The detector-protocol parser below is a best-effort stub - real
Uniden/Escort/Radenso Bluetooth formats differ and this has NOT been tested
against a physical detector yet. Adapt parse_alert() to your unit; everything
else (auth, GPS, posting, rate-friendly de-dup) is ready.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request

BANDS = {"ka", "k", "x", "laser"}
# Don't spam the hub: one hit per (band) at most every SEND_EVERY_S while the
# alert persists. The hub decays dots on its own, so a steady stream is wasteful.
SEND_EVERY_S = 4.0


def parse_alert(line: str):
    """Best-effort parse of one detector alert line -> (band, strength 0..1).

    Handles a few shapes seen in the wild and a plain "band,strength" for
    testing. Returns None for anything that isn't an alert. ADAPT THIS to your
    detector's actual output.
    """
    s = line.strip().lower()
    if not s:
        return None
    # plain test form: "ka,0.8" or just "ka"
    m = re.match(r"^(ka|laser|k|x)\s*[,: ]?\s*([0-9.]+)?$", s)
    if m:
        band = m.group(1)
        strg = float(m.group(2)) if m.group(2) else None
        if strg is not None and strg > 1:      # 0..8 bar scale -> 0..1
            strg = min(1.0, strg / 8.0)
        return band, strg
    # generic "band=ka strength=6" style
    band = None
    for b in ("laser", "ka", "k", "x"):
        if re.search(r"\b" + b + r"\b", s):
            band = b
            break
    if not band:
        return None
    sm = re.search(r"(strength|sig|bars?)\D{0,3}([0-9.]+)", s)
    strg = None
    if sm:
        strg = float(sm.group(2))
        if strg > 1:
            strg = min(1.0, strg / 8.0)
    return band, strg


def gps_from_gpsd():
    """Read one fix from a local gpsd, if the `gps` module is present. Returns
    (lat, lon, heading) or None. Optional - falls back to a fixed position."""
    try:
        import gps  # type: ignore
    except Exception:
        return None
    try:
        session = gps.gps(mode=gps.WATCH_ENABLE)
        for _ in range(20):
            report = session.next()
            if getattr(report, "class", "") == "TPV" and hasattr(report, "lat"):
                return (float(report.lat), float(report.lon),
                        float(getattr(report, "track", 0.0)) or None)
    except Exception:
        return None
    return None


def post_hit(hub, node, token, lat, lon, band, strength, heading, post=True):
    payload = {"node_id": node, "lat": lat, "lon": lon, "band": band}
    if strength is not None:
        payload["strength"] = strength
    if heading is not None:
        payload["heading"] = heading
    if not post or not hub:
        print("would POST /api/radar/hit " + json.dumps(payload))
        return True
    req = urllib.request.Request(
        hub.rstrip("/") + "/api/radar/hit",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "sparrow-radar-bridge/0.1"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status == 200
            print(("sent " if ok else "hub %d " % r.status) + band
                  + (" %.2f" % strength if strength is not None else ""))
            return ok
    except Exception as e:
        print("post failed: %s" % e)
        return False


def source_lines(args):
    """Yield raw alert lines from the chosen source."""
    if args.simulate:
        # A Ka source that ramps up, passes, and clears - on a loop.
        while True:
            for bars in (2, 4, 6, 8, 6, 3):
                yield "ka,%d" % bars
                time.sleep(0.8)
            time.sleep(3.0)
            yield ""   # clear
    if args.serial:
        try:
            import serial  # type: ignore
        except Exception:
            sys.exit("pyserial not installed: pip install pyserial (or use --simulate)")
        with serial.Serial(args.serial, args.baud, timeout=1) as ser:
            while True:
                raw = ser.readline().decode("utf-8", "replace")
                if raw:
                    yield raw
    else:
        # stdin - pipe a detector's log, or type "ka 6" to test.
        for raw in sys.stdin:
            yield raw


def main():
    ap = argparse.ArgumentParser(description="Feed radar detections to SparrowMap")
    ap.add_argument("--hub", help="hub base URL, e.g. https://map.sparrowmap.com")
    ap.add_argument("--node", help="your node id (n_...)")
    ap.add_argument("--key", help="file holding the node token")
    ap.add_argument("--token", help="node token inline (prefer --key)")
    ap.add_argument("--serial", help="detector serial/Bluetooth port (COM5, /dev/rfcomm0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--gpsd", action="store_true", help="read position from local gpsd")
    ap.add_argument("--lat", type=float, help="fixed latitude if no GPS")
    ap.add_argument("--lon", type=float, help="fixed longitude if no GPS")
    ap.add_argument("--simulate", action="store_true", help="emit a fake Ka pass")
    ap.add_argument("--post", action="store_true",
                    help="actually POST (default prints only unless a hub is set)")
    args = ap.parse_args()

    token = args.token
    if args.key:
        with open(args.key, "r", encoding="utf-8") as f:
            token = f.read().strip()
    post = bool(args.hub and args.node and token) and (args.post or not args.simulate)
    if args.hub and not (args.node and token):
        sys.exit("--hub needs --node and --key/--token to authenticate")

    last_sent = {}
    for raw in source_lines(args):
        alert = parse_alert(raw)
        if not alert:
            continue
        band, strength = alert
        if band not in BANDS:
            continue
        pos = gps_from_gpsd() if args.gpsd else None
        if pos:
            lat, lon, heading = pos
        elif args.lat is not None and args.lon is not None:
            lat, lon, heading = args.lat, args.lon, None
        else:
            # No position: we can't place a dot. Skip rather than guess.
            print("no GPS fix and no --lat/--lon; skipping " + band)
            continue
        now = time.time()
        if now - last_sent.get(band, 0) < SEND_EVERY_S:
            continue
        last_sent[band] = now
        post_hit(args.hub, args.node, token, lat, lon, band, strength, heading, post)


if __name__ == "__main__":
    main()
