"""Train the classifier head, and report the numbers that actually decide.

⚠️ THIS SCRIPT DELIBERATELY NEVER PRINTS ACCURACY.

About 1 vehicle in 500 on a residential street is a police vehicle. A model
that answers "not police" every single time is 99.8% accurate and completely
worthless, and accuracy is the number most likely to be quoted at a launch.
What matters is:

    precision  of the vehicles we CALL police, how many are?
               a false positive here is a public accusation.
    recall     of the police vehicles that passed, how many did we catch?
               a false negative here is a missed record.

SparrowMap's asymmetry is not the usual one. A missed patrol car is a gap; a
civilian published as police is the failure that discredits the whole map. So
the operating threshold is chosen for HIGH PRECISION and recall is whatever it
turns out to be - and both get reported, so the trade is visible instead of
buried in a single figure.

    python train\\fit.py

Trains on the public set, validates on LOCAL data if any is labelled. Refuses
to claim anything about local performance when there are no local positives,
because that is exactly the situation the whole project got into last time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402

MODEL_OUT = DATA / "models" / "vehicle_head.npz"


def load_public() -> tuple[np.ndarray, np.ndarray]:
    from train.embed import embed_dir
    root = DATA / "training_public"
    if not root.is_dir():
        raise SystemExit("no data/training_public - run train\\prepare.py first")
    e = embed_dir(root)
    y = np.array([1 if p.replace("\\", "/").startswith("police/") else 0
                  for p in e["paths"]])
    return e["vecs"], y


def load_local() -> tuple[np.ndarray, np.ndarray, int]:
    """Locally labelled crops from the node's own bank. The only real test."""
    from train.embed import embed_dir
    root = DATA / "training"
    if not root.is_dir():
        return np.zeros((0, 512)), np.zeros(0), 0
    e = embed_dir(root)
    X, y = [], []
    for vec, rel in zip(e["vecs"], e["paths"]):
        side = (root / rel).with_suffix(".json")
        if not side.exists():
            continue
        try:
            lab = json.loads(side.read_text(encoding="utf-8")).get("label")
        except Exception:
            continue
        if lab in ("police", "civilian"):
            X.append(vec); y.append(1 if lab == "police" else 0)
    if not X:
        return np.zeros((0, 512)), np.zeros(0), 0
    return np.array(X), np.array(y), int(sum(y))


def pr_curve(scores: np.ndarray, y: np.ndarray) -> list[tuple]:
    out = []
    for t in np.unique(np.round(scores, 3)):
        pred = scores >= t
        tp = int((pred & (y == 1)).sum())
        fp = int((pred & (y == 0)).sum())
        fn = int((~pred & (y == 1)).sum())
        if tp + fp == 0:
            continue
        out.append((float(t), tp / (tp + fp), tp / max(1, tp + fn), tp, fp, fn))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-precision", type=float, default=0.97,
                    help="operating point: a false positive is a public accusation")
    args = ap.parse_args()

    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split

    X, y = load_public()
    print(f"\npublic set: {len(y)} crops, {int(y.sum())} police, "
          f"{len(y) - int(y.sum())} civilian")
    if y.sum() == 0 or y.sum() == len(y):
        raise SystemExit("need both classes in the public set")

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25,
                                          stratify=y, random_state=7)
    # Balanced, because the public set will not match the street's 1-in-500 and
    # pretending otherwise bakes the wrong prior into the head.
    clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")
    clf.fit(Xtr, ytr)

    s = clf.predict_proba(Xte)[:, 1]
    print("\nHELD-OUT PUBLIC DATA (says nothing about his street)")
    print(f"{'thresh':>7} {'precision':>10} {'recall':>8} {'tp':>5} {'fp':>5} {'fn':>5}")
    chosen = None
    for t, p, r, tp, fp, fn in pr_curve(s, yte):
        if abs(t * 20 % 1) < 1e-9 or p >= args.min_precision:
            print(f"{t:>7.3f} {p:>10.3f} {r:>8.3f} {tp:>5} {fp:>5} {fn:>5}")
        if p >= args.min_precision and chosen is None:
            chosen = (t, p, r)

    Xl, yl, n_pos = load_local()
    print(f"\nLOCAL labelled data: {len(yl)} crops, {n_pos} police")
    if len(yl) == 0 or n_pos == 0:
        print("  ⚠️  NO LOCAL POSITIVES. Nothing here can tell you whether this")
        print("      model works on his camera, and public numbers are not a")
        print("      substitute - the domain gap is the entire risk. This model")
        print("      MUST NOT be promoted on the strength of the table above.")
        print("      See train/README.md and detect/calibrate.py.")
    else:
        sl = clf.predict_proba(Xl)[:, 1]
        for t, p, r, tp, fp, fn in pr_curve(sl, yl)[:12]:
            print(f"{t:>7.3f} {p:>10.3f} {r:>8.3f} {tp:>5} {fp:>5} {fn:>5}")

    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez(MODEL_OUT, w=clf.coef_, b=clf.intercept_,
             threshold=(chosen[0] if chosen else 0.9),
             trained_on="public", local_positives=n_pos)
    print(f"\nsaved {MODEL_OUT}")
    print("NOT wired into the detector. Promotion is a separate, gated step:")
    print("  detect\\calibrate.py must show a real precision AND recall on")
    print("  local data before config.json publish_public_tier goes true.")


if __name__ == "__main__":
    main()
