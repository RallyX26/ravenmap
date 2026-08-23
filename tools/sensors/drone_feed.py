"""Feed drones that broadcast Remote ID into SparrowMap's live drone layer.

    US law now makes most consumer drones broadcast Remote ID over Wi-Fi and
    Bluetooth - the drone's position, and often the operator's. An ESP32 running
    OpenDroneID / WiFi-RemoteID firmware hears it and prints JSON; this reads that
    and posts each drone as a live point. Local/state police drones must
    broadcast; federal can fly dark and home-built ones may not comply, so this is
    "drones that announced themselves," not every drone.

Typical use (ESP32 sniffer on a serial/USB port printing one JSON object/line):

    python tools/sensors/drone_feed.py --hub https://map.sparrowmap.com \
        --node n_abcd1234 --key D:/LLM/mynode.token --serial COM6

Or pipe a Remote ID scanner's JSON on stdin. See it work with no hardware:

    python tools/sensors/drone_feed.py --simulate

STATUS: BETA. Remote ID JSON field names vary by firmware - parse_drone() covers
the common ones (OpenDroneID / the popular ESP32 sniffers); adapt it to yours.
Not yet tested against a physical broadcaster.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

SEND_EVERY_S = 3.0


def parse_drone(obj):
    """Best-effort map of a Remote ID JSON object -> {id, lat, lon, label}.
    Handles a few common shapes. Returns None if there's no usable position."""
    if not isinstance(obj, dict):
        return None
    # Position: try the drone's own location under several common keys.
    lat = obj.get("lat", obj.get("latitude", obj.get("drone_lat")))
    lon = obj.get("lon", obj.get("lng", obj.get("longitude", obj.get("drone_lon"))))
    try:
        lat = float(lat); lon = float(lon)
    except (TypeError, ValueError):
        return None
    if lat == 0 and lon == 0:
        return None
    did = str(obj.get("id", obj.get("uas_id", obj.get("basic_id", "")))) or None
    # A short human label: id + any operator hint the firmware gives.
    parts = []
    if did:
        parts.append(did[:24])
    if obj.get("op_lat") is not None or obj.get("operator_lat") is not None:
        parts.append("operator located")
    label = " · ".join(parts)
    return {"id": did, "lat": lat, "lon": lon, "label": label}


def post_hit(hub, node, token, d, do_post):
    payload = {"node_id": node, "kind": "drone",
               "lat": d["lat"], "lon": d["lon"], "label": d["label"]}
    if d.get("id"):
        payload["id"] = d["id"]
    if not do_post:
        print("would POST /api/sensor/hit " + json.dumps(payload))
        return
    req = urllib.request.Request(
        hub.rstrip("/") + "/api/sensor/hit",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "sparrow-drone-feed/0.1"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("sent drone %s" % (d.get("id") or "?"))
    except Exception as e:
        print("post failed: %s" % e)


def autodetect_serial():
    """First available serial/USB port, so --serial can be omitted."""
    try:
        from serial.tools import list_ports
    except Exception:
        return None
    ports = list(list_ports.comports())
    if not ports:
        return None
    for p in ports:
        d = (p.description or "").lower()
        if any(w in d for w in ("usb", "uart", "serial", "cp210", "ch340", "esp")):
            return p.device
    return ports[0].device


def lines(a):
    if a.simulate:
        import math
        t0 = time.time()
        while True:
            dt = time.time() - t0
            yield json.dumps({"id": "DRONE-TEST-1",
                              "lat": 42.73 + 0.002 * math.sin(dt / 4),
                              "lon": -84.55 + 0.002 * math.cos(dt / 4),
                              "op_lat": 42.729})
            time.sleep(1.0)
    if not a.serial:
        found = autodetect_serial()
        if found:
            a.serial = found
            print("auto-detected board on %s" % found)
    if a.serial:
        try:
            import serial  # type: ignore
        except Exception:
            sys.exit("pyserial not installed: pip install pyserial (or --simulate)")
        with serial.Serial(a.serial, a.baud, timeout=1) as ser:
            while True:
                raw = ser.readline().decode("utf-8", "replace")
                if raw.strip():
                    yield raw
    else:
        for raw in sys.stdin:
            if raw.strip():
                yield raw


def main():
    ap = argparse.ArgumentParser(description="Feed Remote ID drones to SparrowMap")
    ap.add_argument("--hub")
    ap.add_argument("--node")
    ap.add_argument("--key")
    ap.add_argument("--token")
    ap.add_argument("--serial", help="ESP32 sniffer port (COM6, /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--post", action="store_true")
    a = ap.parse_args()

    token = a.token
    if a.key:
        token = open(a.key, "r", encoding="utf-8").read().strip()
    do_post = bool(a.hub and a.node and token) and (a.post or not a.simulate)
    if a.hub and not (a.node and token):
        sys.exit("--hub needs --node and --key/--token")

    last = {}
    for raw in lines(a):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        d = parse_drone(obj)
        if not d:
            continue
        k = d.get("id") or "%.4f,%.4f" % (d["lat"], d["lon"])
        now = time.time()
        if now - last.get(k, 0) < SEND_EVERY_S:
            continue
        last[k] = now
        post_hit(a.hub, a.node, token, d, do_post)


if __name__ == "__main__":
    main()
