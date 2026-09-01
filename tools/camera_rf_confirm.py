"""Confirm mapped Flock/ALPR cameras that the RF beta has actually heard.

The RF scanner parks a candidate whenever it hears a surveillance camera's WiFi
(mirror.rf_park -> the box's data/rf_pen). This matches those detections against
the OSM camera layer: any mapped camera within RADIUS_M of an RF detection is a
camera someone's scanner physically heard, so it is marked confirmed-present
(a green tick on the map). Purely additive - it never removes a camera.

    python tools/camera_rf_confirm.py --box root@HOST --key PATH [--push]

Runs on the desktop (it needs the local 135k-camera dataset, which is too big to
query on the box), reads the box's rf_pen over ssh, and writes / pushes
data/cameras_confirmed.json (a list of osm_ids). Safe to run on a timer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import DATA  # noqa: E402

CAMERAS = DATA / "surveillance_cameras.json"
OUT = DATA / "cameras_confirmed.json"
RADIUS_M = 120.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# Read the box's rf_pen and emit only SURVEILLANCE-camera detections with a
# position: not police in-car gear (police_conf set), not a drone (is_drone).
_REMOTE = r'''
import json, os, glob
pen = "/opt/sparrowmap/data/rf_pen"
out = []
for f in glob.glob(os.path.join(pen, "*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("police_conf") or d.get("is_drone"):
        continue
    lat, lon = d.get("lat"), d.get("lon")
    if lat is None or lon is None:
        continue
    out.append([lat, lon])
print(json.dumps(out))
'''


def _hav(a, b, c, d):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    r = subprocess.run(
        ["ssh", "-i", a.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         a.box, "/opt/sparrowmap/.venv/bin/python -c " + _shq(_REMOTE)],
        capture_output=True, timeout=90, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise SystemExit("ssh/rf_pen read failed: %s"
                         % r.stderr.decode("utf-8", "replace")[:200])
    detections = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    print(f"{len(detections)} RF surveillance detections on the box")

    cams = json.loads(CAMERAS.read_text(encoding="utf-8"))
    # Union with what is already confirmed - a camera stays confirmed once heard.
    confirmed = set()
    if OUT.exists():
        try:
            confirmed = set(json.loads(OUT.read_text(encoding="utf-8")))
        except Exception:
            confirmed = set()
    added = 0
    for dlat, dlon in detections:
        for row in cams:                     # row = [osm_id, lat, lon, dir]
            if row[0] in confirmed:
                continue
            if abs(row[1] - dlat) > 0.002 or abs(row[2] - dlon) > 0.003:
                continue                     # cheap bbox pre-filter (~200 m)
            if _hav(dlat, dlon, row[1], row[2]) <= RADIUS_M:
                confirmed.add(row[0])
                added += 1
    OUT.write_text(json.dumps(sorted(confirmed), separators=(",", ":")),
                   encoding="utf-8")
    print(f"confirmed {len(confirmed)} cameras (+{added} new) -> {OUT}")

    if a.push:
        dest = f"{a.box}:/opt/sparrowmap/data/cameras_confirmed.json"
        subprocess.run(["scp", "-i", a.key, "-o", "BatchMode=yes", "-q",
                        str(OUT), dest], check=True, creationflags=_NO_WINDOW)
        print(f"pushed to {dest}")


def _shq(py: str) -> str:
    return "'" + py.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    main()
