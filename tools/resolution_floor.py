"""At what pixel width does a police vehicle stop being recognisable?

🚨 THE QUESTION CAMERA PLACEMENT TURNS ON, AND IT IS ANSWERABLE TODAY.
A volunteer camera watching a wide street, or one placed further back than the
reference node, renders vehicles far smaller than the crops this classifier was
built on. Knowing where the call breaks decides where a camera can usefully be
placed - and it separates two tasks that fail at very different sizes:

  * a VAN differs from a car by SILHOUETTE - tall, boxy, flat front. A silhouette
    survives brutal downscaling; you can read it in a 20 px thumbnail.
  * a PATROL CAR differs from a civilian car by LIVERY and a LIGHT BAR. Both are
    sedans or SUVs. The shape is the same shape.

So "I could tell a van" is real evidence about silhouettes and no evidence about
liveries, and the honest way to settle it is not to argue - it is to take the
crops this camera has ALREADY confirmed as government vehicles, shrink them to
the width a distant camera actually delivers, and see where the call breaks.

Nothing here waits for a patrol car to drive past a distant camera. It reuses
the ground truth that already exists.

⚠️ Degrading is not just resizing. A distant vehicle on a cheap encoder loses
detail to SCALE, to JPEG at a low bitrate, and to sensor noise. Resizing alone
flatters the result, so all three are applied - and the pipeline is
down-then-up, because the classifier's input size never changes; what changes is
how much information survives.

Usage:
    python tools\\resolution_floor.py                 # all widths
    python tools\\resolution_floor.py --widths 69 120 --strip
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BANK = ROOT / "data" / "training"
# 120 px is what the live pipeline asks for; the smaller widths are what a
# camera set back from the road actually delivers.
WIDTHS = [200, 120, 90, 69, 45, 25, 12]


def degrade(bgr, width: int, jpeg_q: int = 45, noise: float = 2.0):
    import cv2
    h, w = bgr.shape[:2]
    if w <= width:
        return None                     # already smaller; it would be upscaling
    scale = width / float(w)
    small = cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))),
                       interpolation=cv2.INTER_AREA)
    ok, enc = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, jpeg_q])
    if ok:
        small = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    if noise:
        small = np.clip(small.astype(np.float32)
                        + np.random.normal(0, noise, small.shape), 0, 255
                        ).astype(np.uint8)
    # Back up to the original size: the classifier's input is fixed, so the
    # experiment must vary INFORMATION, not the tensor it is handed.
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)


def load_labelled(limit_per_class: int = 60):
    """Confirmed government crops and civilian crops from the same camera.

    Same camera, same lens, same weather, same compression - which is the one
    condition train/prepare.py refuses to run without, and here it comes free.
    """
    import cv2
    pos, neg = [], []
    for j in sorted(BANK.rglob("*.json")):
        img = j.with_suffix(".jpg")
        if not img.exists():
            continue
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        lab = d.get("label")
        if lab not in ("police", "gov", "civilian"):
            continue
        # Scraped press photographs are not this camera and would answer a
        # different question. Handheld is close-up by construction.
        if d.get("source") == "scraped" or (d.get("sampling") or "") == "handheld":
            continue
        bucket = pos if lab in ("police", "gov") else neg
        if len(bucket) >= limit_per_class:
            continue
        im = cv2.imread(str(img))
        if im is None or im.shape[1] < 120:
            continue                    # too small to degrade honestly
        bucket.append((str(j.relative_to(BANK)), im))
    return pos, neg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--widths", type=int, nargs="*", default=WIDTHS)
    ap.add_argument("--strip", action="store_true",
                    help="write a visual strip so a human can judge too")
    a = ap.parse_args()

    np.random.seed(0)                   # a resolution floor must reproduce
    import cv2
    from detect import vehicle_id, head

    pos, neg = load_labelled()
    print(f"government crops {len(pos)} | civilian crops {len(neg)} "
          f"(this camera only; scraped and handheld excluded)")
    if not pos:
        print("no camera-view government crops to test")
        return 1
    vid = vehicle_id.get()
    print(f"trained head: {'LIVE' if head.available() else 'NOT AVAILABLE'}"
          f"  threshold {head.threshold():.3f}" if head.available() else
          "trained head: NOT AVAILABLE (zero-shot only)")

    print(f"\n{'width':>6} {'gov called gov':>16} {'civ called gov':>16} "
          f"{'mean police conf':>18}")
    rows = []
    for wpx in [None] + list(a.widths):
        got_pos = tot_pos = got_neg = tot_neg = 0
        confs = []
        for _rel, im in pos:
            x = im if wpx is None else degrade(im, wpx)
            if x is None:
                continue
            r = vid.classify(x)
            tot_pos += 1
            confs.append(float((r.get("scores") or {}).get("police", 0.0)))
            if r.get("vclass") in ("police", "gov_dot", "emergency"):
                got_pos += 1
        for _rel, im in neg:
            x = im if wpx is None else degrade(im, wpx)
            if x is None:
                continue
            r = vid.classify(x)
            tot_neg += 1
            if r.get("vclass") in ("police", "gov_dot", "emergency"):
                got_neg += 1
        label = "full" if wpx is None else f"{wpx}px"
        rec = {"width": label,
               "recall": round(got_pos / tot_pos, 3) if tot_pos else None,
               "false_positive_rate": round(got_neg / tot_neg, 3) if tot_neg else None,
               "mean_police_conf": round(float(np.mean(confs)), 3) if confs else None,
               "n_pos": tot_pos, "n_neg": tot_neg}
        rows.append(rec)
        print(f"{label:>6} {got_pos:>7}/{tot_pos:<8} {got_neg:>7}/{tot_neg:<8} "
              f"{rec['mean_police_conf'] if rec['mean_police_conf'] is not None else '-':>18}")

    # ⚠️ RECALL ALONE CANNOT ANSWER THIS. A model that calls everything police
    # scores perfect recall, so the false-positive column is the half that says
    # whether the call still MEANS anything at that size.
    print("\nRecall is the government column; the civilian column is what the same")
    print("call costs. A width where both rise together has not kept the signal,")
    print("it has lost the ability to say no.")

    if a.strip:
        # Let him judge with his own eyes, which is the correct response to a
        # conclusion delivered as a number.
        out = ROOT.parent / "sparrow_review" / "resolution_strip.png"
        sample = pos[0][1]
        tiles = [sample]
        for wpx in a.widths:
            d = degrade(sample, wpx)
            tiles.append(d if d is not None else np.zeros_like(sample))
        hgt = 160
        tiles = [cv2.resize(t, (int(t.shape[1] * hgt / t.shape[0]), hgt)) for t in tiles]
        strip = np.hstack(tiles)
        for i, name in enumerate(["full"] + [f"{w}px" for w in a.widths]):
            x = sum(t.shape[1] for t in tiles[:i]) + 4
            cv2.putText(strip, name, (x, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (0, 255, 255), 1)
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), strip)
        print(f"\nvisual strip -> {out}")

    (ROOT.parent / "sparrow_review" / "resolution_floor.json").write_text(
        json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
