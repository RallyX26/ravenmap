"""Does TRAINING at low resolution rescue a camera set back from the road?

The fair objection, and it is the right question to ask: the resolution floor was
measured with a classifier trained on full-size crops from his own window, then
shown far smaller vehicles. Of course it fell over - it had never seen one.
Train it on small crops and it should adapt.

That is domain adaptation and it is a real effect, so this measures it rather
than guessing. For each width: degrade every labelled crop to that width, embed
the degraded image, fit the same logistic head, and score it with the same
group-aware folds. Train small, test small.

Read it against tools\\resolution_floor.py, which is the OTHER experiment -
train big, test small. The gap between the two is exactly what training buys.

⚠️ The comparison that matters is not "does it beat the full-size model" - it
will not. It is "at this size, is the operating point good enough to publish a
claim about somebody's vehicle". Recall at >=95% precision is the only number
that answers that, so it is the one printed, and AP is shown for direction.

Usage:
    python tools\\resolution_train.py
    python tools\\resolution_train.py --widths 120 69
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
WIDTHS = [None, 200, 120, 69, 40]


def degrade(bgr, width):
    """Scale + JPEG + sensor noise, down then up. Resizing alone flatters it."""
    import cv2
    if width is None:
        return bgr
    h, w = bgr.shape[:2]
    if w <= width:
        return None
    s = width / float(w)
    small = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))),
                       interpolation=cv2.INTER_AREA)
    ok, enc = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 45])
    if ok:
        small = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    small = np.clip(small.astype(np.float32)
                    + np.random.normal(0, 2.0, small.shape), 0, 255).astype(np.uint8)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)


def load():
    import cv2
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
        if d.get("source") == "scraped" or (d.get("sampling") or "") == "handheld":
            continue
        im = cv2.imread(str(img))
        if im is None or im.shape[1] < 120:
            continue
        out.append((im, 1 if lab in ("police", "gov") else 0,
                    d.get("node_id") or "", float(d.get("ts") or 0.0)))
    return out


def groups_of(meta):
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
    ap.add_argument("--widths", type=int, nargs="*")
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args()
    widths = [None] + list(a.widths) if a.widths else WIDTHS

    np.random.seed(0)
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
        im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        with torch.no_grad():
            inp = vid.proc(images=im, return_tensors="pt").to(vid.device)
            f = vid.model.get_image_features(**inp)
            f = f / f.norm(dim=-1, keepdim=True)
        return f.squeeze(0).float().cpu().numpy()

    data = load()
    print(f"{len(data)} local labelled crops, {sum(r[1] for r in data)} government\n")
    print(f"{'trained at':>12}{'crops':>8}{'gov':>6}{'AP':>18}{'recall@95%prec':>16}")
    for wpx in widths:
        rows = [(degrade(im, wpx), y, n, t) for im, y, n, t in data]
        rows = [r for r in rows if r[0] is not None]
        if not rows:
            continue
        X = np.array([embed(r[0]) for r in rows])
        y = np.array([r[1] for r in rows])
        g = groups_of([(r[2], r[3]) for r in rows])
        aps, recs = [], []
        for seed in range(a.seeds):
            skf = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
            sc, yy = np.zeros(len(y)), np.zeros(len(y))
            for tr, te in skf.split(X, y, g):
                s = StandardScaler().fit(X[tr])
                clf = LogisticRegression(C=0.5, max_iter=2000,
                                         class_weight="balanced")
                clf.fit(s.transform(X[tr]), y[tr])
                sc[te] = clf.decision_function(s.transform(X[te]))
                yy[te] = y[te]
            aps.append(average_precision_score(yy, sc))
            order = np.argsort(-sc)
            tp = np.cumsum(yy[order])
            prec = tp / np.arange(1, len(yy) + 1)
            ok = np.where(prec >= 0.95)[0]
            recs.append(tp[ok[-1]] / yy.sum() if len(ok) else 0.0)
        label = "full size" if wpx is None else f"{wpx}px"
        print(f"{label:>12}{len(y):>8}{int(y.sum()):>6}"
              f"{np.mean(aps):>12.3f} ±{np.std(aps):.3f}{100*np.mean(recs):>14.0f}%")
    print("\nCompare with tools\\resolution_floor.py (trained BIG, tested small).")
    print("The difference between the two is what training at that size buys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
