"""Feed SparrowMap from PUBLIC traffic cameras, at home, one process for many.

    python tools\\public_cams.py survey --limit 400      # find the usable ones
    python tools\\public_cams.py enrol  --limit 8        # register them as nodes
    python tools\\public_cams.py run                     # poll them, post crops

🚨 THIS WAS TRIED, MEASURED, AND DELETED IN AUGUST - READ WHY BEFORE CHANGING IT.
Michigan's 806 cameras gave ~15px vehicles against the ~120px the classifier
needs, DOT cameras were found to be standard definition as a category, and
training at low resolution could not rescue them. All the code was removed.

What changed is not the argument, it is the hardware: HD cameras now exist
inside the same public networks. Measured 2026-08-15 on New York City's 966
online cameras - 87% are 352x240 and useless, 2.5% are 1920x1080 and give
205-375px vehicles, comfortably past the bar. So this does not "add thousands of
cameras". It finds the handful that can actually see, and ignores the rest. The
survey step is not optional garnish; it is the entire reason this is viable.

WHERE IT RUNS, AND WHY THAT MATTERS
At HOME, never on the box. The box has no OpenCV and two vCPUs, and putting
continuous inference on the public server is the wrong shape regardless. This is
the same relationship a business camera has: the pixels are handled here, and
only a crop that cannot carry a plate is uploaded.

⚠️ AND THEY ARE LABELLED AS WHAT THEY ARE. SparrowMap's whole argument is that a
volunteer pointed a camera at their own street. A government traffic camera is
not that, and quietly mixing the two would make the project's own description of
itself untrue. Every node enrolled here is named "Public traffic camera - ..."
and carries kind="public_cam" so the map, the review page and anyone reading the
API can tell them apart.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                      # noqa: E402
from PIL import Image                   # noqa: E402

STATE = ROOT / "data" / "public_cams.json"
UA_SURVEY = "Mozilla/5.0 (SparrowMap camera survey)"
UA_NODE = "SparrowMap-node/1.0"
VEHICLE = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

CONF = 0.45              # matches the live node
SEND_EDGE = 200          # the plate-illegible cap, same as detect/relay
MIN_VEHICLE_PX = 120     # the bar: below this the head is guessing
POLL_S = 20.0            # per camera; DOT snapshots refresh slower than this
MIN_HD_WIDTH = 1280


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def nyc_index() -> list:
    """NYC DOT's public camera list. No key, publishes lat/lon and an online flag."""
    req = urllib.request.Request("https://webcams.nyctmc.org/api/cameras/",
                                 headers={"User-Agent": UA_SURVEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        idx = json.loads(r.read())
    out = []
    for c in idx:
        if str(c.get("isOnline")).lower() != "true":
            continue
        if c.get("latitude") in (None, 0) or c.get("longitude") in (None, 0):
            continue
        out.append({"src": "nyc", "ref": c["id"], "name": c.get("name") or c["id"],
                    "lat": float(c["latitude"]), "lon": float(c["longitude"]),
                    "url": f"https://webcams.nyctmc.org/api/cameras/{c['id']}/image"})
    return out


SOURCES = {"nyc": nyc_index}


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def fetch(url: str, ua: str = UA_SURVEY, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_model():
    import onnxruntime as ort
    from detect import relay
    p = relay.model_path("https://map.sparrowmap.com")
    sess = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    shape = sess.get_inputs()[0].shape
    size = int(shape[2]) if isinstance(shape[2], int) else 320
    return sess, size


def detect(sess, size, img: Image.Image, conf: float = CONF):
    """Vehicle boxes in ORIGINAL image pixels, widest first."""
    w, h = img.size
    s = min(size / w, size / h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    ox, oy = (size - nw) // 2, (size - nh) // 2
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(img.resize((nw, nh), Image.BILINEAR), (ox, oy))
    x = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    d = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
    boxes = []
    for i in range(d.shape[1]):
        best, bs = -1, 0.0
        for c in VEHICLE:
            v = float(d[4 + c, i])
            if v > bs:
                bs, best = v, c
        if bs < conf:
            continue
        cx, cy, bw, bh = (float(d[0, i]), float(d[1, i]),
                          float(d[2, i]), float(d[3, i]))
        x0 = (cx - bw / 2 - ox) / s
        y0 = (cy - bh / 2 - oy) / s
        boxes.append({"cls": VEHICLE[best], "conf": bs,
                      "box": (x0, y0, x0 + bw / s, y0 + bh / s),
                      "w": bw / s})
    boxes.sort(key=lambda b: -b["w"])
    # crude NMS: drop anything mostly inside a bigger keeper
    keep = []
    for b in boxes:
        if not any(_iou(b["box"], k["box"]) > 0.45 for k in keep):
            keep.append(b)
    return keep


def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x1 - x0) * max(0, y1 - y0)
    ua = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)
    return i / ua if ua > 0 else 0.0


def crop_b64(img: Image.Image, box) -> str:
    """The vehicle, shrunk below plate legibility BEFORE it leaves this machine.

    Deliberately identical to detect/relay.crop_of. A public camera is not a
    special case: the same cap protects everybody in the frame who is not the
    vehicle being reported.
    """
    x0, y0, x1, y1 = box
    pw, ph = (x1 - x0) * 0.06, (y1 - y0) * 0.06
    sx, sy = max(0, int(x0 - pw)), max(0, int(y0 - ph))
    ex, ey = min(img.width, int(x1 + pw)), min(img.height, int(y1 + ph))
    if ex - sx < 8 or ey - sy < 8:
        return ""
    sub = img.crop((sx, sy, ex, ey))
    s = min(1.0, SEND_EDGE / max(sub.size))
    if s < 1.0:
        sub = sub.resize((max(1, int(sub.width * s)), max(1, int(sub.height * s))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    sub.convert("RGB").save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"cams": {}}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_survey(args) -> int:
    cams = SOURCES[args.source]()
    print(f"{len(cams)} online cameras with coordinates; probing {args.limit}\n")
    sess, size = load_model()
    st = load_state()
    good = 0
    for c in cams[:args.limit]:
        try:
            img = Image.open(io.BytesIO(fetch(c["url"]))).convert("RGB")
        except Exception:
            continue
        # Resolution is a free filter and 87% fail it, so spend no inference
        # on a camera that cannot possibly clear the bar.
        if img.width < MIN_HD_WIDTH:
            continue
        boxes = detect(sess, size, img)
        widest = boxes[0]["w"] if boxes else 0.0
        ok = widest >= MIN_VEHICLE_PX
        print(f"  {'USABLE ' if ok else 'too small'}  {c['name'][:44]:<46} "
              f"{img.width}x{img.height}  widest {widest:>6.1f}px")
        if ok:
            good += 1
            key = f"{c['src']}:{c['ref']}"
            prev = st["cams"].get(key, {})
            st["cams"][key] = {**prev, **c, "widest_px": round(widest, 1),
                               "surveyed": time.time()}
    save_state(st)
    print(f"\n{good} usable camera(s) recorded in {STATE}")
    return 0


def cmd_enrol(args) -> int:
    st = load_state()
    todo = [(k, c) for k, c in st["cams"].items() if not c.get("node_id")]
    print(f"{len(todo)} camera(s) to enrol; doing {min(len(todo), args.limit)}\n")
    for key, c in todo[:args.limit]:
        # ⚠️ NAMED AND TYPED AS A PUBLIC CAMERA. Anyone reading the map, the
        # review queue or the API must be able to tell this apart from a
        # volunteer's window without asking.
        body = {"name": f"Public traffic camera - {c['name']}"[:80],
                "lat": c["lat"], "lon": c["lon"], "kind": "public_cam",
                "reach_m": 60}
        req = urllib.request.Request(
            args.hub.rstrip("/") + "/api/enroll", method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": UA_NODE})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                nd = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"  FAILED {c['name'][:40]}: {e.code} {e.read()[:120]}")
            continue
        c["node_id"], c["token"] = nd["id"], nd["token"]
        c["status"] = nd.get("status")
        print(f"  {nd['id']}  {nd.get('status'):<8} {c['name'][:46]}")
        save_state(st)
        time.sleep(0.5)
    print("\n⚠️ new nodes may need approving (auto_approve_nodes is off).")
    return 0


def cmd_run(args) -> int:
    st = load_state()
    cams = [c for c in st["cams"].values() if c.get("node_id")]
    if not cams:
        print("no enrolled cameras - run survey then enrol first")
        return 1
    sess, size = load_model()
    print(f"polling {len(cams)} camera(s) every {POLL_S:.0f}s "
          f"(vehicles must be >= {MIN_VEHICLE_PX}px)\n")
    sent = skipped = 0
    seen_last = {}
    try:
        while True:
            for c in cams:
                try:
                    img = Image.open(io.BytesIO(fetch(c["url"]))).convert("RGB")
                except Exception as exc:
                    print(f"  {c['name'][:34]:<36} unreachable ({str(exc)[:30]})")
                    continue
                boxes = detect(sess, size, img)
                # 🚨 ONLY VEHICLES BIG ENOUGH TO BE JUDGED. A snapshot of a
                # motorway holds dozens of 30px blobs; posting them would bury
                # the review queue in things no human could rule on either.
                big = [b for b in boxes if b["w"] >= MIN_VEHICLE_PX]
                skipped += len(boxes) - len(big)
                if not big:
                    continue
                # One per poll: these are stills seconds apart, so several
                # boxes are usually the same traffic seen twice.
                b = big[0]
                crop = crop_b64(img, b["box"])
                if not crop:
                    continue
                key = c["node_id"]
                if time.time() - seen_last.get(key, 0) < POLL_S * 0.9:
                    continue
                seen_last[key] = time.time()
                body = {"node_id": c["node_id"], "ts": time.time(),
                        "source": "phone_node", "body": b["cls"],
                        "det_conf": round(b["conf"], 3), "plate_text": "",
                        "plate_conf": 0, "evidence": {}, "snap_b64": crop,
                        "lat": c["lat"], "lon": c["lon"]}
                req = urllib.request.Request(
                    args.hub.rstrip("/") + "/api/sightings", method="POST",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + c["token"],
                             "User-Agent": UA_NODE})
                try:
                    with urllib.request.urlopen(req, timeout=25) as r:
                        out = json.loads(r.read() or b"{}")
                    sent += 1
                    print(f"  sent  {c['name'][:34]:<36} {b['cls']:<6} "
                          f"{b['w']:>5.0f}px  id={out.get('id')}")
                except urllib.error.HTTPError as e:
                    print(f"  {e.code}   {c['name'][:34]:<36} {e.read()[:80]}")
                if args.once:
                    continue
            print(f"  -- cycle done: {sent} sent, {skipped} too small --")
            if args.once:
                return 0
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print(f"\nstopped. {sent} sent, {skipped} skipped as too small.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="https://map.sparrowmap.com")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("survey"); s.add_argument("--source", default="nyc")
    s.add_argument("--limit", type=int, default=200); s.set_defaults(fn=cmd_survey)
    e = sub.add_parser("enrol"); e.add_argument("--limit", type=int, default=5)
    e.set_defaults(fn=cmd_enrol)
    r = sub.add_parser("run"); r.add_argument("--once", action="store_true")
    r.set_defaults(fn=cmd_run)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
