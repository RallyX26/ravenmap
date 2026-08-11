"""Would training a detector on a specific MARKER beat hand-written geometry?

His proposal: instead of hand-writing light-bar / livery / spotlight detectors,
TRAIN them. The obvious blocker is that no marker labels exist - every label in
the bank names the VEHICLE (police/gov/fleet/civilian), not what is on it, so
there is nothing to fit a "has a light bar" model to.

But the hypothesis is testable today without labelling anything new, by using
the vehicle labels as WEAK supervision for a REGION:

    crop the roof band of every labelled crop -> embed just that band ->
    fit the same logistic head -> does "roof band alone" separate government
    vehicles from ordinary ones?

If a model shown ONLY the roof can tell them apart, then the roof genuinely
carries the signal and a trained marker detector is worth building. If it
cannot, the marker is not recoverable at this resolution and no amount of
labelling would have helped - which is the same question the hand-written
detectors failed, asked in a way that separates "my geometry was bad" from
"the pixels are not there".

⚠️ WEAK SUPERVISION CUTS BOTH WAYS AND THE RESULT MUST BE READ CAREFULLY.
A roof-band model could score well by reading the vehicle's COLOUR or the sky
behind it rather than any light bar. So the whole-vehicle head is run on the
same folds as the control: the roof band is only interesting if it retains most
of the whole-vehicle signal from a fraction of the pixels.

Usage:
    python tools\\region_head.py                     # roof band
    python tools\\region_head.py --region flank      # door livery band
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

# Fractions of the crop, as (y0, y1, x0, x1). The roof band matches
# detect/visual.ROOF_BAND, which was narrowed to 0.18 after a wider band let sky
# in and published a civilian SUV.
REGIONS = {
    "roof":  (0.00, 0.22, 0.00, 1.00),   # light bar
    "roof_tall": (0.00, 0.38, 0.00, 1.00),  # roofline often sits low in the box
    "flank": (0.35, 0.75, 0.00, 1.00),   # door livery / agency decal
    "front": (0.45, 1.00, 0.00, 1.00),   # push bumper, grille
    "whole": (0.00, 1.00, 0.00, 1.00),   # control
}

# 🚨 A THIN STRIP PUSHED THROUGH CLIP IS SQUASHED, NOT LOOKED AT.
# CLIP resizes its input to 224x224. The roof band of a 512x200 crop is about
# 512x45, so a plain resize compresses it ~10x horizontally and stretches it 5x
# vertically - a light bar stops being a bar-shaped thing before the model ever
# sees it. That is a flaw in the EXPERIMENT, not evidence about the roof, and it
# would have made any thin region look empty.
#
# Letterboxing keeps the aspect ratio and pads the rest, so shape survives. It
# is the honest way to ask "is the signal in these pixels".
def letterbox(bgr, size: int = 224):
    import cv2
    import numpy as np
    h, w = bgr.shape[:2]
    s = min(size / w, size / h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    r = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_CUBIC)
    out = np.full((size, size, 3), 114, np.uint8)
    y, x = (size - nh) // 2, (size - nw) // 2
    out[y:y + nh, x:x + nw] = r
    return out


def rows(region: str):
    import cv2
    y0f, y1f, x0f, x1f = REGIONS[region]
    out = []
    for j in sorted(BANK.rglob("*.json")):
        img = j.with_suffix(".jpg")
        if not img.exists():
            continue
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        lab = d.get("label")
        if lab not in ("police", "gov", "civilian", "fleet"):
            continue
        # Same exclusions as train/fit_local: public data trains, local
        # validates, and handheld was measured to hurt.
        if d.get("source") == "scraped" or (d.get("sampling") or "") == "handheld":
            continue
        im = cv2.imread(str(img))
        if im is None:
            continue
        h, w = im.shape[:2]
        sub = im[int(h * y0f):max(1, int(h * y1f)), int(w * x0f):max(1, int(w * x1f))]
        if sub.size == 0 or sub.shape[0] < 8 or sub.shape[1] < 8:
            continue
        out.append((sub, 1 if lab in ("police", "gov") else 0,
                    d.get("node_id") or "", float(d.get("ts") or 0.0)))
    return out


def pass_groups(meta):
    """One id per VEHICLE PASS. Without this the evaluation lied by 17 points:
    the tracker fragments one car into several crops and a random fold trains
    and tests on the same vehicle from the same second."""
    order = sorted(range(len(meta)), key=lambda i: (meta[i][0], meta[i][1]))
    g = np.zeros(len(meta), dtype=int)
    cur, last = 0, None
    for i in order:
        node, ts = meta[i]
        if last is None or node != last[0] or ts - last[1] > 10.0:
            cur += 1
        g[i] = cur
        last = (node, ts)
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", nargs="*", default=["roof", "flank", "front", "whole"])
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--pad", action="store_true",
                    help="letterbox the region instead of letting CLIP squash it")
    a = ap.parse_args()

    import cv2
    from PIL import Image
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedGroupKFold
    from sklearn.metrics import average_precision_score
    from sklearn.preprocessing import StandardScaler
    from detect import vehicle_id

    vid = vehicle_id.get()
    torch = vid.torch

    def embed(bgr):
        if a.pad:
            bgr = letterbox(bgr)
        im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        with torch.no_grad():
            inp = vid.proc(images=im, return_tensors="pt").to(vid.device)
            f = vid.model.get_image_features(**inp)
            f = f / f.norm(dim=-1, keepdim=True)
        return f.squeeze(0).float().cpu().numpy()

    print(f"{'region':<8}{'crops':>7}{'gov':>6}{'passes':>8}"
          f"{'AP':>16}{'recall@95%prec':>16}")
    results = {}
    for region in a.region:
        data = rows(region)
        if not data:
            print(f"{region:<8} no data")
            continue
        X = np.array([embed(s) for s, _y, _n, _t in data])
        y = np.array([r[1] for r in data])
        groups = pass_groups([(r[2], r[3]) for r in data])
        aps, recs = [], []
        for seed in range(a.seeds):
            skf = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
            sc, yy = np.zeros(len(y)), np.zeros(len(y))
            for tr, te in skf.split(X, y, groups):
                s = StandardScaler().fit(X[tr])
                clf = LogisticRegression(C=0.5, max_iter=2000,
                                         class_weight="balanced")
                clf.fit(s.transform(X[tr]), y[tr])
                sc[te] = clf.decision_function(s.transform(X[te]))
                yy[te] = y[te]
            aps.append(average_precision_score(yy, sc))
            # Recall at the tightest threshold still holding 95% precision -
            # the only operating point that matters for publishing.
            order = np.argsort(-sc)
            tp = np.cumsum(yy[order])
            prec = tp / np.arange(1, len(yy) + 1)
            ok = np.where(prec >= 0.95)[0]
            recs.append(tp[ok[-1]] / yy.sum() if len(ok) else 0.0)
        results[region] = (float(np.mean(aps)), float(np.mean(recs)))
        print(f"{region:<8}{len(y):>7}{int(y.sum()):>6}{len(set(groups)):>8}"
              f"{np.mean(aps):>10.3f} ±{np.std(aps):.3f}"
              f"{100*np.mean(recs):>13.0f}%")

    if "whole" in results and len(results) > 1:
        wap = results["whole"][0]
        print("\nAgainst the whole-vehicle control:")
        for r, (apv, _rc) in results.items():
            if r == "whole":
                continue
            print(f"  {r:<7} keeps {100*apv/wap:.0f}% of the whole-vehicle AP "
                  f"from {int(100*(REGIONS[r][1]-REGIONS[r][0]))}% of the pixels")
        print("\nA region that keeps most of the signal is worth a trained")
        print("detector. One that does not means the marker is not recoverable")
        print("here, and no amount of labelling would have changed that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
