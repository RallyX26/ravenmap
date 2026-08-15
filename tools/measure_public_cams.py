"""Are public traffic cameras big enough to be worth ingesting? Measure, do not argue.

    python tools\\measure_public_cams.py --source nyc --n 25
    python tools\\measure_public_cams.py --url https://example/cam.jpg

🚨 THIS QUESTION HAS BEEN ANSWERED BEFORE AND THE ANSWER WAS NO.
Michigan's 806 public cameras were measured in August: vehicles arrived about
15 px wide against roughly the 120 px the classifier needs to tell a patrol car
from a hatchback, and a follow-up found there is no hidden HD feed - DOT
cameras are standard definition as a category. Training at low resolution was
tested too, and could not rescue them. All the integration code was then
deleted.

So this tool exists to make RE-ASKING cheap rather than to re-litigate. Point it
at any camera, anywhere, paid or free, and it answers in one number: how wide is
a vehicle, in pixels, in the picture that camera actually produces. Everything
else about a feed - price, coverage, uptime, licence - is irrelevant until that
number clears the bar.

WHAT THE NUMBER MEANS
  < 40 px   the detector may find a blob; there is no livery to read. Useless.
  40-90 px  detection works, classification is guesswork.
  > 120 px  the range this project's own cameras work at.

It measures the SOURCE image, not a resized copy, because the whole question is
what the sensor and the lens delivered - downscaling later cannot add detail
back, and upscaling a 15 px car just produces a larger blur.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                      # noqa: E402
from PIL import Image                   # noqa: E402

UA = "Mozilla/5.0 (SparrowMap camera survey)"
VEHICLE = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
CONF = 0.35          # deliberately lower than the live 0.45: we want to see
                     # what a generous detector finds, not to publish anything.


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def nyc_cameras(limit: int) -> list:
    """New York City's traffic cameras: a public JSON index, no key.

    Chosen as the free sample because it needs no account, publishes lat/lon and
    an online flag, and is a dense URBAN network - which is the BEST case for
    this question. A city intersection camera sees closer, slower vehicles than
    a highway pole does. If the number fails here it fails everywhere.
    """
    idx = json.loads(fetch("https://webcams.nyctmc.org/api/cameras/"))
    live = [c for c in idx if str(c.get("isOnline")).lower() == "true"]
    out = []
    for c in live[:limit]:
        out.append({"name": c.get("name"), "id": c.get("id"),
                    "url": f"https://webcams.nyctmc.org/api/cameras/{c['id']}/image"})
    return out


def load_model():
    import onnxruntime as ort
    from detect import relay
    p = relay.model_path("https://map.sparrowmap.com")
    s = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    shape = s.get_inputs()[0].shape
    size = int(shape[2]) if isinstance(shape[2], int) else 320
    return s, size


def detect(sess, size, img: Image.Image):
    """Vehicle boxes in ORIGINAL image pixels."""
    w, h = img.size
    s = min(size / w, size / h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    ox, oy = (size - nw) // 2, (size - nh) // 2
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(img.resize((nw, nh), Image.BILINEAR), (ox, oy))
    x = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    d = out[0]                      # (84, N)
    n = d.shape[1]
    boxes = []
    for i in range(n):
        best, bs = -1, 0.0
        for c in VEHICLE:
            v = float(d[4 + c, i])
            if v > bs:
                bs, best = v, c
        if bs < CONF:
            continue
        cx, cy, bw, bh = (float(d[0, i]), float(d[1, i]),
                          float(d[2, i]), float(d[3, i]))
        # back out of the letterbox, into source pixels
        boxes.append({"cls": VEHICLE[best], "conf": round(bs, 3),
                      "w": round(bw / s, 1), "h": round(bh / s, 1),
                      "x": round((cx - ox) / s, 1), "y": round((cy - oy) / s, 1)})
    boxes.sort(key=lambda b: -b["w"])
    return boxes


def verdict(width: float) -> str:
    if width >= 120:
        return "USABLE      (this project's own cameras work here)"
    if width >= 90:
        return "MARGINAL    (detection fine, livery doubtful)"
    if width >= 40:
        return "DETECT ONLY (a blob, no livery to read)"
    return "USELESS     (nothing to classify)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="nyc", choices=["nyc"])
    ap.add_argument("--url", action="append", default=[],
                    help="measure a specific image URL (repeatable)")
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()

    cams = ([{"name": u, "url": u} for u in args.url] if args.url
            else nyc_cameras(args.n))
    print(f"loading detector…")
    sess, size = load_model()
    print(f"model input {size}x{size}; measuring {len(cams)} camera(s)\n")

    widest = []
    for c in cams:
        try:
            raw = fetch(c["url"])
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as exc:
            print(f"  --  {c['name'][:44]:<46} unreachable ({str(exc)[:34]})")
            continue
        boxes = detect(sess, size, img)
        if not boxes:
            print(f"  --  {c['name'][:44]:<46} {img.width}x{img.height}  no vehicles")
            continue
        top = boxes[0]["w"]
        widest.append(top)
        print(f"      {c['name'][:44]:<46} {img.width}x{img.height}  "
              f"{len(boxes):>2} vehicles  widest {top:>6.1f}px  {verdict(top)}")

    print()
    if not widest:
        print("no vehicles seen in any frame - try again when traffic is moving")
        return 0
    widest.sort()
    med = widest[len(widest) // 2]
    print(f"cameras with vehicles : {len(widest)}")
    print(f"widest vehicle, median: {med:.1f} px      {verdict(med)}")
    print(f"best single camera    : {widest[-1]:.1f} px    {verdict(widest[-1])}")
    print()
    print("The bar is ~120 px of vehicle width. Below it the head is guessing,")
    print("and no amount of paying for a feed changes what the lens delivered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
