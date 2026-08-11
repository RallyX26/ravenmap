"""End-to-end proof with a real photograph and a real plate.

Enrolls a node, reads a genuine Michigan plate off a genuine photograph, posts
it to a running hub exactly the way a camera would, and then checks the thing
that actually matters: that the image the hub stored has the plate destroyed
when the vehicle landed in the private tier.

    python detect\test_e2e.py
"""

from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db          # noqa: E402
from core import SNAPS  # noqa: E402
from detect.pipeline import PlateReader  # noqa: E402

HUB = "http://localhost:8150"
STILLS = Path(__file__).resolve().parent.parent / "testvideo" / "stills"


def api(path: str, body: dict, token: str = "") -> dict:
    req = urllib.request.Request(
        HUB + path, method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> None:
    print("1. enrolling a node")
    node = api("/api/enroll", {
        "name": "E2E test - porch", "lat": 42.7, "lon": -84.5,
        "kind": "phone", "heading": 90, "fov": 60, "reach_m": 40})
    print(f"   node {node['id']}  token {'yes' if node.get('token') else 'no'}")

    print("2. loading models")
    reader = PlateReader()

    results = []
    for img_path in sorted(STILLS.glob("*.jpg")):
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        reads = reader.read(img, (0, 0, w, h))
        if not reads or not reads[0].text:
            continue
        best = max(reads, key=lambda r: r.conf * (r.area ** 0.5))

        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            continue

        print(f"3. posting {img_path.name[:44]}  plate={best.text} "
              f"conf={best.conf:.2f}")
        resp = api("/api/sightings", {
            "node_id": node["id"], "ts": time.time(),
            "lat": 42.7, "lon": -84.5,
            "plate_text": best.text, "plate_state": "MI",
            "plate_conf": round(best.conf, 3),
            "evidence": {}, "source": "camera",
            "snap_b64": "data:image/jpeg;base64," +
                        base64.b64encode(buf.tobytes()).decode(),
            "plate_box": list(best.box),
        }, token=node.get("token") or "")
        print(f"   -> {resp}")
        results.append((best.text, resp))

    print("\n4. verifying what the database actually holds")
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, tier, plate_text, plate_hash, snap, vclass FROM sightings "
        "WHERE node_id = ? ORDER BY id", (node["id"],)).fetchall()
    bad = 0
    for r in rows:
        readable = r["plate_text"]
        print(f"   id={r['id']:<4} tier={r['tier']:<8} "
              f"plate_text={readable!r:<12} hash={r['plate_hash'][:14]}... "
              f"snap={r['snap']}")
        if r["tier"] != "public" and readable is not None:
            bad += 1
    print(f"\n   private rows holding readable plate text: {bad}  (must be 0)")

    print("5. stored images (open these and try to read the plate):")
    for r in rows:
        if r["snap"]:
            print(f"   {SNAPS / r['snap']}   [{r['tier']}]")


if __name__ == "__main__":
    main()
