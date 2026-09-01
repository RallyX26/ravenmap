"""Fetch amenity=police from OpenStreetMap into data/police_stations.json.

Overpass answers this desktop but refuses the box (the same split road snapping
lives with), so this runs HERE and the result is copied to the box, which serves
it bounded by viewport at /api/police. Police stations rarely move, so this is a
manual/occasional refresh, not a scheduled fill.

    python tools/police_fetch.py                         # continental US
    python tools/police_fetch.py --bbox 42.5,-84.2,43.4,-83.2   # one region
    python tools/police_fetch.py --push --box root@HOST --key PATH   # + scp

Output rows are compact [lat, lon, name] with 5-decimal coords, ~50 KB per 1000
stations. The box's hub._police_stations() reads exactly this shape.
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

OUT = DATA / "police_stations.json"
OVERPASS = "https://overpass-api.de/api/interpreter"
# Continental US. Alaska/Hawaii would be two more calls; add if coverage moves.
US_BBOX = (24.5, -125.0, 49.5, -66.9)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def fetch(bbox: tuple) -> list:
    s, w, n, e = bbox
    b = f"({s},{w},{n},{e})"
    q = (f'[out:json][timeout:280];'
         f'(node["amenity"="police"]{b};way["amenity"="police"]{b};);'
         f'out center tags;')
    req = urllib.request.Request(
        OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "SparrowMap/0.1 (police-stations map layer)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = json.loads(r.read().decode("utf-8", "replace"))
    rows, seen = [], set()
    for el in raw.get("elements", []):
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        t = el.get("tags") or {}
        name = (t.get("name") or t.get("operator") or "")[:60]
        row = [round(lat, 5), round(lon, 5), name]
        k = (row[0], row[1], row[2])
        if k in seen:
            continue
        seen.add(k)
        rows.append(row)
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
    print(f"fetching amenity=police for {bbox} ...")
    rows = fetch(bbox)
    named = sum(1 for r in rows if r[2])
    OUT.write_text(json.dumps(rows, separators=(",", ":"), ensure_ascii=False),
                   encoding="utf-8")
    print(f"wrote {OUT}  ({len(rows)} stations, {named} named, "
          f"{OUT.stat().st_size // 1024} KB)")

    if a.push:
        if not (a.box and a.key):
            raise SystemExit("--push needs --box and --key")
        dest = f"{a.box}:/opt/sparrowmap/data/police_stations.json"
        subprocess.run(["scp", "-i", a.key, "-o", "BatchMode=yes", "-q",
                        str(OUT), dest], check=True, creationflags=_NO_WINDOW)
        print(f"pushed to {dest} - restart or let the hub pick it up on next load")


if __name__ == "__main__":
    main()
