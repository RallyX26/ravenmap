"""Fetch Flock/ALPR surveillance cameras from OpenStreetMap into
data/surveillance_cameras.json.

Same split as road snapping and the police-station layer: Overpass answers this
desktop but refuses the box, so this runs HERE and the result is copied to the
box, which serves it bounded by viewport at /api/cameras. Community-mapped (the
DeFlock project tags automated plate readers `man_made=surveillance` +
`surveillance:type=ALPR`), so it changes over time - re-run occasionally, and the
report/RF-confirm flow on the map is what keeps the LIVE ones honest between runs.

    python tools/cameras_fetch.py                        # continental US
    python tools/cameras_fetch.py --bbox 40.5,-90,45,-82 # one region
    python tools/cameras_fetch.py --push --box root@HOST --key PATH   # + scp

Each row is compact [id, lat, lon, dir] with 5-decimal coords: id is the OSM
element ('n<nodeid>' / 'w<wayid>') so a camera keeps a stable identity across
refreshes - the removed/present reports and RF confirmations key on it. dir is
the facing bearing in degrees (or "" if untagged).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import DATA  # noqa: E402

OUT = DATA / "surveillance_cameras.json"
OVERPASS = "https://overpass-api.de/api/interpreter"
US_BBOX = (24.5, -125.0, 49.5, -66.9)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def fetch(bbox: tuple) -> list:
    s, w, n, e = bbox
    b = f"({s},{w},{n},{e})"
    q = (f'[out:json][timeout:280];'
         f'(node["man_made"="surveillance"]["surveillance:type"="ALPR"]{b};'
         f'way["man_made"="surveillance"]["surveillance:type"="ALPR"]{b};);'
         f'out center tags;')
    req = urllib.request.Request(
        OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "SparrowMap/0.1 (Flock/ALPR camera layer)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = json.loads(r.read().decode("utf-8", "replace"))
    rows, seen = [], set()
    for el in raw.get("elements", []):
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        oid = ("n" if el.get("type") == "node" else "w") + str(el.get("id"))
        if oid in seen:
            continue
        seen.add(oid)
        # A camera can carry several bearings ("185;70"); keep the first number.
        d = str((el.get("tags") or {}).get("direction", "")).split(";")[0].strip()
        rows.append([oid, round(lat, 5), round(lon, 5), d])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", help="S,W,N,E (default: continental US)")
    ap.add_argument("--push", action="store_true", help="scp the result to the box")
    ap.add_argument("--box", help="ssh target, e.g. root@host")
    ap.add_argument("--key", help="ssh identity file")
    a = ap.parse_args()

    bbox = US_BBOX
    if a.bbox:
        bbox = tuple(float(x) for x in a.bbox.split(","))
    print(f"fetching ALPR cameras for {bbox} ...")
    rows = fetch(bbox)
    OUT.write_text(json.dumps(rows, separators=(",", ":"), ensure_ascii=False),
                   encoding="utf-8")
    print(f"wrote {OUT}  ({len(rows)} cameras, {OUT.stat().st_size // 1024} KB)")

    if a.push:
        if not (a.box and a.key):
            raise SystemExit("--push needs --box and --key")
        dest = f"{a.box}:/opt/sparrowmap/data/surveillance_cameras.json"
        subprocess.run(["scp", "-i", a.key, "-o", "BatchMode=yes", "-q",
                        str(OUT), dest], check=True, creationflags=_NO_WINDOW)
        print(f"pushed to {dest}")


if __name__ == "__main__":
    main()
