"""What the trained head actually gets right and wrong, on THIS street.

    python tools\\confusion.py
    python tools\\confusion.py --sweep      # precision/recall across thresholds

⚠️ THIS SCRIPT DELIBERATELY NEVER PRINTS ACCURACY, for the same reason
train/fit.py does not: about 1 vehicle in 500 here is a government vehicle, so
"not police" every time scores 99.8% and is worthless. See train/fit.py.

WHY THIS EXISTS SEPARATELY FROM fit.py
fit.py reports how the model did on a held-out slice AT TRAINING TIME. That is
a claim about the model that was trained; it is not a claim about the model
that is DEPLOYED, on the crops that have actually gone past since. This reads
the head on disk, the embeddings already cached, and the labels a human gave -
and joins them. Nothing is embedded, nothing is trained, nothing is written.

🚨🚨 THE DEPLOYED HEAD WAS FITTED ON EVERYTHING, SO SCORING IT HERE IS NOT A TEST
train/fit_local.py ends with "Final model on everything, for deployment" - the
shipped head is fitted on every camera-view row it had. Almost every crop this
script can score is therefore a crop the model was TRAINED on, and running a
model over its own training data measures memorisation. The first version of
this script printed 98.4% precision / 95.5% recall from exactly that, which is
the most flattering and least defensible number in the project - precisely what
a sceptic would ask for and then take apart.

So it prints TWO things and never lets them be confused:

    fitted    - agreement on data the head was fitted on. Not performance.
                Useful only as a sanity check: if THIS is bad, something is
                broken, but good numbers here prove nothing.
    held out  - cross-validated on the same crops, refitting the same model
                type on each fold and scoring only the rows left out. This is
                the number that may be quoted.

🚨 THE CAVEAT THAT MUST TRAVEL WITH EVERY NUMBER THIS PRINTS
The labelled crops are NOT a random sample of traffic. Most were labelled
because they reached a review queue, and they reached it because a model
thought they were interesting. Measuring a model on the cases it selected
flatters it: the civilians it confidently ignored are largely absent from the
denominator, so recall here is an upper bound on real recall, not an estimate
of it. The honest reading is "of the crops a human has ruled on, here is the
agreement" - which is a real and useful number, and is not the same claim as
"here is how the detector performs on the street".

It is printed with the counts beside it so the reader can see how thin the
evidence is, rather than being handed a percentage that hides n=36.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA                       # noqa: E402
from detect import head                     # noqa: E402

BANK = DATA / "training"
CACHE = DATA / "embeddings" / "training.npz"

# What counts as a government vehicle for this measurement. These are the
# labels a human can give in the review app; 'unsure' is excluded from the
# denominator entirely rather than being guessed at in either direction.
POSITIVE = {"police", "emergency", "gov_dot", "gov"}
NEGATIVE = {"civilian", "fleet", "not_police", "no"}


def load_embeddings() -> dict:
    """{crop stem: 512-d vector} from the cache train/embed.py already wrote."""
    if not CACHE.exists():
        return {}
    z = np.load(CACHE, allow_pickle=True)
    out = {}
    for p, v in zip(z["paths"], z["vecs"]):
        out[Path(str(p)).stem] = v
    return out


def rows(emb: dict) -> list[dict]:
    """Every human-labelled crop we can also score. Joined, not re-derived."""
    got = []
    for j in BANK.rglob("*.json"):
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        label = (d.get("label") or "").strip().lower()
        if label not in POSITIVE and label not in NEGATIVE:
            continue                       # unlabelled, or 'unsure'
        vec = emb.get(j.stem)
        clip = d.get("clip") or {}
        scores = clip.get("scores") or {}
        if vec is None or not scores:
            continue                       # cannot score it the way the node did
        # The SAME function the detector uses. Re-implementing the feature
        # layout here is how a measurement quietly stops describing the thing
        # it is measuring.
        p = head.score(vec, scores, float(clip.get("conf") or 0.0),
                       float(clip.get("margin") or 0.0))
        if p is None:
            continue
        got.append({"stem": j.stem, "label": label, "p": p,
                    "truth": label in POSITIVE,
                    "clip_says": clip.get("vclass"),
                    "node": d.get("node_id"),
                    "sighting_id": d.get("sighting_id"),
                    # Kept so the honest, held-out estimate can be refitted
                    # without embedding anything again. Same layout as
                    # detect/head.score: 512 CLIP dims, the 5 class scores,
                    # then conf and margin.
                    "x": np.concatenate([
                        np.asarray(vec, dtype=np.float64).reshape(-1),
                        np.array([float(scores.get(c, 0.0))
                                  for c in head.status().get("classes")
                                  or ["police", "emergency", "gov_dot",
                                      "fleet", "civilian"]]
                                 + [float(clip.get("conf") or 0.0),
                                    float(clip.get("margin") or 0.0)])])})
    return got


def held_out_scores(got: list[dict], folds: int = 5,
                    seed: int = 0) -> np.ndarray | None:
    """Out-of-fold probability for every row, or None if it cannot be done."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold
        from sklearn.preprocessing import StandardScaler
    except Exception:
        return None
    y = np.array([r["truth"] for r in got], dtype=int)
    if y.sum() < folds or (len(y) - y.sum()) < folds:
        return None
    X = np.vstack([r["x"] for r in got])
    oof = np.zeros(len(y), dtype=float)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for tr, te in skf.split(X, y):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(max_iter=5000, C=0.5, class_weight="balanced")
        clf.fit(sc.transform(X[tr]), y[tr])
        oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
    return oof


def held_out(got: list[dict], thr: float, folds: int = 5,
             seed: int = 0) -> dict | None:
    """Cross-validated precision/recall - the number that may be quoted.

    Refits the SAME model type and regularisation as train/fit_local.py on each
    fold and scores only the rows held out, so no row is ever predicted by a
    model that saw it. Anything else is measuring memory.
    """
    oof = held_out_scores(got, folds=folds, seed=seed)
    if oof is None:
        return None
    fake = [{"truth": r["truth"], "p": float(p)} for r, p in zip(got, oof)]
    out = matrix(fake, thr)
    out["folds"] = folds
    return out


def matrix(got: list[dict], thr: float) -> dict:
    tp = sum(1 for r in got if r["truth"] and r["p"] >= thr)
    fp = sum(1 for r in got if not r["truth"] and r["p"] >= thr)
    fn = sum(1 for r in got if r["truth"] and r["p"] < thr)
    tn = sum(1 for r in got if not r["truth"] and r["p"] < thr)
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": prec, "recall": rec}


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sweep", action="store_true",
                    help="precision/recall across thresholds")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    st = head.status()
    if not st.get("ok"):
        print(f"head unavailable: {st.get('why')}")
        sys.exit(2)
    thr = float(st["threshold"])

    emb = load_embeddings()
    got = rows(emb)
    if not got:
        print("nothing to measure: no human-labelled crop has both a cached "
              "embedding and a stored clip score.")
        sys.exit(2)

    m = matrix(got, thr)
    pos = sum(1 for r in got if r["truth"])

    if a.json:
        print(json.dumps({"threshold": thr, "n": len(got), "positives": pos,
                          **m}, indent=1))
        return

    print(f"head threshold {thr:.4f}   ·   {len(got)} human-labelled crops "
          f"scored   ·   {pos} of them government")
    print(f"cache covers {len(emb)} crops; {len(got)} could be both labelled "
          f"and scored")

    print("\n--- FITTED (the head scoring data it was fitted on - NOT a test) ---")
    print("                     called gov    called not")
    print(f"  IS gov          {m['tp']:>10}    {m['fn']:>10}")
    print(f"  IS not gov      {m['fp']:>10}    {m['tn']:>10}")
    print(f"  precision {pct(m['precision'])}   recall {pct(m['recall'])}"
          "   <- do not quote this")

    ho = held_out(got, thr)
    print("\n--- HELD OUT (cross-validated - THIS is the number) ---")
    if ho is None:
        print("  unavailable: too few of one class to split, or sklearn missing")
    else:
        print("                     called gov    called not")
        print(f"  IS gov          {ho['tp']:>10}    {ho['fn']:>10}")
        print(f"  IS not gov      {ho['fp']:>10}    {ho['tn']:>10}")
        print(f"  precision  {pct(ho['precision'])}   "
              f"of the {ho['tp'] + ho['fp']} it called government, how many were")
        print(f"  recall     {pct(ho['recall'])}   "
              f"of the {pos} government vehicles, how many it caught")
        print(f"  ({ho['folds']}-fold, refitting the same model per fold; no row "
              f"predicted by a model that saw it)")
    print()
    # Say how thin it is, in the same breath as the number.
    if pos < 100:
        print(f"  ⚠️  {pos} positives is a small denominator. One more mistake "
              f"moves recall by {100.0 / max(pos, 1):.1f} points.")
    print("  ⚠️  These crops were mostly selected BY a model for review, so "
          "this is\n      agreement with the humans who ruled on them - not "
          "performance on the street.")

    by_node = Counter(r["node"] for r in got if r["truth"])
    if by_node:
        print(f"\n  government examples come from {len(by_node)} camera(s): "
              + ", ".join(f"{n}={c}" for n, c in by_node.most_common(4)))
        if len(by_node) == 1:
            print("  ⚠️  every positive is from ONE camera - this measures one "
                  "street, one\n      angle and one set of lighting conditions.")

    if a.sweep:
        # Swept on the HELD-OUT scores. Sweeping a threshold over data the
        # model was fitted on picks the threshold that best flatters the
        # memorised answers, which is how an operating point ends up worse in
        # the field than on the report that chose it.
        oof = held_out_scores(got)
        if oof is None:
            print("\n  sweep unavailable (sklearn missing or too few positives)")
            return
        fake = [{"truth": r["truth"], "p": float(p)} for r, p in zip(got, oof)]
        print("\n  held-out sweep - precision is the one that matters here:")
        print("  threshold   precision   recall    tp   fp   fn")
        for t in [0.3, 0.4, thr, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]:
            s = matrix(fake, t)
            mark = "  <- operating point" if abs(t - thr) < 1e-9 else ""
            print(f"  {t:9.4f}   {pct(s['precision']):>9}   "
                  f"{pct(s['recall']):>6}   {s['tp']:>3}  {s['fp']:>3}  "
                  f"{s['fn']:>3}{mark}")


if __name__ == "__main__":
    main()
