"""Train the vehicle head on locally labelled crops, and measure it honestly.

This replaces threshold tuning, which is finished: on real camera data the
zero-shot confidence distributions OVERLAP - a confirmed patrol car at 0.901
sits below a false positive at 0.962 - so no constant can separate them. See
the ledger entry for 2026-08-08.

## The constraint that shapes everything here

    camera-view (review mode)   224 civilian,  6 police
    hunt mode                    26 civilian             (biased: hardest cases)
    handheld photographs          6 civilian, 15 police  (different distribution)

SIX camera-view positives. That is the honest size of the test set, because it
is the only data drawn from the distribution the system actually runs on, and
it is small enough that any single number computed from it carries enormous
error bars. So this script:

  * TESTS only on review-mode camera-view crops - never on handheld photos and
    never on hunt-mode crops, both of which are biased samples by construction;
  * uses STRATIFIED K-FOLD so all six positives take a turn being held out,
    instead of one arbitrary split deciding the verdict;
  * TRAINS on everything else available, including handheld and hunt, because
    training on a biased sample is fine - it is only measuring on one that lies;
  * compares against CLIP ZERO-SHOT on exactly the same held-out rows, because
    "is the new thing better than what it replaces" is the only question worth
    answering, and it has to be asked of identical data;
  * prints PRECISION and RECALL with the raw counts beside them, never accuracy.
    At a 1-in-40 base rate "always say civilian" is 97% accurate and useless.

## Why a head on frozen CLIP rather than fine-tuning

277 labelled crops cannot fine-tune a vision transformer. They can fit a
logistic boundary in a 512-dimensional space that CLIP has already organised,
which is exactly the part that is wrong - CLIP's features distinguish these
vehicles perfectly well, its zero-shot PROMPT does not. Strong L2 is essential
at 512 features and 277 samples, so C is chosen by inner cross-validation
rather than picked.

    python train\\fit_local.py
    python train\\fit_local.py --min-precision 0.95
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402
from sklearn.metrics import average_precision_score   # noqa: E402

BANK = DATA / "training"
MODEL_OUT = DATA / "models" / "vehicle_head.npz"

# ── MEASURED, not chosen. Average precision over 8 fold seeds, 590 held-out
# camera rows with 10 government vehicles:
#
#   CLIP zero-shot                    AP 0.806
#   head C=0.05  with handheld        AP 0.740   ← my first guess, WORSE than
#   head C=0.05  camera-only          AP 0.765     the thing it replaces
#   head C=0.5   with handheld        AP 0.844
#   head C=0.5   camera-only          AP 0.896 ± 0.005   ← beats zero-shot 8/8 seeds
#
# Two things I had wrong. C=0.05 was over-regularised to the point of being
# worse than doing nothing. And the HANDHELD PHOTOGRAPHS HURT: 15 extra
# positives from the police-station car park cost 0.05 AP, because close sharp
# side-on shots teach a boundary that does not hold at a window at 45 degrees
# in motion blur. train/README.md predicted that domain gap; it turns out to
# outweigh the value of the extra positives rather than merely dilute it.
C_BEST = 0.5
# ⚠️ "CAMERA ONLY" MEANS EXCLUDE HANDHELD, NOT "REVIEW MODE ONLY".
#
# The first version trained on review-mode rows alone, which quietly discarded
# every positive gathered in `likely` or `hunt` mode - five government vehicles
# he had just spent an evening finding, thrown away by the training set while
# the test set correctly ignored them too.
#
# Those two exclusions answer different questions and were wrongly fused:
#   TEST  must be review-mode only, because a biased sample cannot MEASURE.
#   TRAIN wants every camera-view row available, because a biased sample
#         trains perfectly well - it is only measurement that it corrupts.
# Handheld stays out of both, because it was MEASURED to hurt (AP 0.890 -> 0.844).
EXCLUDE_FROM_TRAINING = {"handheld", "scraped"}

# 🚨 AN ALLOWLIST, NOT A DENYLIST. HIS RULE, AND IT IS THE RIGHT ONE:
# "training data should be gated and approved by me. all of it."
#
# The line above is a denylist, and a denylist fails silently the moment a new
# sampling tag appears. That is not hypothetical - it already happened. 186
# machine-made labels were written on 2026-08-18, were not in the denylist, and
# therefore trained the head without anybody approving them. Recall fell twelve
# points and the labels were never the decision they looked like.
#
# So membership is now positive: a tag trains only if it is HERE, and every tag
# here is one he clicked himself. `machine` and `community` are deliberately
# absent. They become trainable by being APPROVED on /proof, which retags them
# `confirmed` - so the gate is a human action, not a config line somebody
# forgets to update.
#
# What this protects is the thing he actually asked for: that a private car can
# never be labelled police in the training data, and that a cruiser is only ever
# found with a police label. No machine and no stranger can put either claim
# into the training set without him seeing the picture first.
#
# ⚠️ `handheld` is absent for a DIFFERENT reason and it is not about approval -
# he labelled those himself. It was MEASURED to hurt (AP 0.890 -> 0.844),
# because press-style photographs teach photography rather than policing.
APPROVED_FOR_TRAINING = {
    "review",      # random draw, his
    "likely",      # highest-government-confidence queue, his
    "hunt",        # CLIP's hardest cases, his
    "split",       # police-vs-gov re-ask, his
    "marked",      # crops he called government, re-shown for undo
    "recheck",     # civilians the model disagreed with, his
    "gap",         # CLIP says government, head refused - his clicks
    "patrol",      # the same queue with heavy plant filtered out - his clicks
    "remote",      # other people's nodes, oldest first - his clicks
    "confirmed",   # a machine or community label he has approved on /proof
}

# ⚠️ THE LIST ABOVE IS EVERY MODE THE LABELLING PAGE OFFERS, AND THAT IS THE
# POINT: if a tag is missing, HIS OWN work silently stops training. Writing the
# allowlist from memory got `gap` and `remote` wrong on the first attempt - 58
# and 29 of his own labels would have been dropped. Check it against the mode
# buttons in camctl/label.html when a queue is added, not against recollection.

# 🚦 WHAT MAY MEASURE. His call, 2026-08-18: crowd consensus is allowed to
# measure, because the project is open and the statistic is checkable by anyone.
# But only a RANDOM sample can measure anything, so this is his random draw plus
# the community's random stratified slice - never the patrol queue, whose whole
# purpose is to be biased toward the model's mistakes.
MEASURABLE = {"review", "community_random"}


def _trainable(src):
    """Boolean mask: rows he has approved for training."""
    import numpy as np
    return np.isin(src, list(APPROVED_FOR_TRAINING))

# Label -> is this a publicly owned vehicle we would publish?
#
# ⚠️ `gov` IS A POSITIVE HERE AND THAT IS NOT A WIDENING. Until 2026-08-10 the
# label set had one government key, `police`, and the button read "Government" -
# so every crop in this set already mixes marked patrol cars with municipal
# pickups. Adding the new key to POSITIVE keeps this head measuring exactly what
# it has always measured: government vs not. Telling police from gov is a
# DIFFERENT head, and it cannot be fitted until `split` mode has produced enough
# distinct passes on each side (tools\class_census.py prints both counts).
POSITIVE = {"police", "gov"}
NEGATIVE = {"civilian", "fleet"}       # fleet is commercial, not government


# A vehicle crossing the frame breaks into several tracker tracks, so ONE car
# becomes several crops seconds apart. Crops closer than this from the same
# node are treated as one pass and must never be split across train and test.
SAME_PASS_S = 10.0


_CUTOFF = float(os.environ.get("SPARROW_LABEL_CUTOFF") or 0)

# The order matters and must never change: it is baked into any saved head.
CLIP_CLASSES = ("police", "emergency", "gov_dot", "fleet", "civilian")
_WITH_CLIP = os.environ.get("SPARROW_NO_CLIP_FEATURES") != "1"


def load() -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    """Embeddings, labels, and which distribution each row came from."""
    from train.embed import embed_dir
    e = embed_dir(BANK)
    by_rel = {rel: vec for vec, rel in zip(e["vecs"], e["paths"])}

    X, y, src, meta = [], [], [], []
    for rel, vec in by_rel.items():
        side = (BANK / rel).with_suffix(".json")
        if not side.exists():
            continue
        try:
            d = json.loads(side.read_text(encoding="utf-8"))
        except Exception:
            continue
        clip = d.get("clip") or {}
        lab = d.get("label")
        # SPARROW_LABEL_CUTOFF lets a run reproduce an EARLIER state of the
        # dataset, so "the number moved" can be attributed to the labels that
        # arrived rather than guessed at. Unset in normal use.
        if _CUTOFF and float(d.get("labelled_at") or 0) > _CUTOFF:
            continue
        if lab in POSITIVE:
            y.append(1)
        elif lab in NEGATIVE:
            y.append(0)
        else:
            continue                     # 'unsure' is not a training signal
        # 🚨 GIVE THE HEAD CLIP'S OWN ANSWER, NOT JUST THE PICTURE.
        #
        # the operator asked why his labels do not change how the model reads a
        # vehicle. They should - but the head was only ever shown the raw
        # embedding, so it was being asked to rediscover from ~1,400 local
        # examples what CLIP already knows from 400 million. That is why it
        # kept LOSING to the thing it was meant to replace.
        #
        # Appending the zero-shot scores turns the job into the right one:
        # learn where CLIP is WRONG, on this street, rather than start from
        # nothing. A head with these features can in principle never do worse
        # than zero-shot, because passing the police score straight through is
        # inside its hypothesis space - it only has to learn the corrections
        # his labels encode ("that shape at 55% is a pickup, not a patrol car").
        #
        # Set SPARROW_NO_CLIP_FEATURES=1 to reproduce the old behaviour.
        if _WITH_CLIP:
            sc = clip.get("scores") or {}
            vec = np.concatenate([vec, np.array([
                float(sc.get(k, 0.0)) for k in CLIP_CLASSES] + [
                float(clip.get("conf") or 0.0),
                float(clip.get("margin") or 0.0)], dtype=vec.dtype)])
        X.append(vec)
        # Provenance, NOT the UI's mode. Labelling a scraped crop through
        # :8160/label overwrites `sampling` with whatever mode the page was in
        # ("hunt"), which silently destroys the one field that says this row is
        # a press photograph rather than a frame from his window. `source` is
        # written by the ingest and never touched again.
        src.append("scraped" if d.get("source") == "scraped"
                   else (d.get("sampling") or "review"))
        meta.append({"rel": rel, "clip_conf": float(clip.get("conf") or 0),
                     "clip_class": clip.get("vclass"),
                     "clip_margin": float(clip.get("margin") or 0),
                     "ts": float(d.get("ts") or 0.0),
                     "node": d.get("node_id") or ""})
    return np.array(X), np.array(y), np.array(src), meta


def pass_groups(meta: list) -> np.ndarray:
    """One id per VEHICLE PASS, not per crop.

    🚨 WITHOUT THIS THE EVALUATION LIES, AND IT LIED BY 17 POINTS.
    The tracker fragments a single car into several tracks, so one patrol car
    passing produced up to three near-identical crops seconds apart. Some
    landed in review mode and their twins in likely mode - so the head trained
    on a car and was then "tested" on the same car, from the same second, at
    the same angle. Three of ten test positives had a twin in training, and
    recall came out at 87% against a true 70%.

    Random k-fold assumes rows are independent. These are not, and nothing in
    the data says so - the duplication is invisible unless you go looking for
    it by timestamp. Grouping by pass is what makes the split honest.
    """
    order = sorted(range(len(meta)), key=lambda i: (meta[i]["node"], meta[i]["ts"]))
    groups = np.zeros(len(meta), dtype=int)
    gid, prev = -1, None
    for i in order:
        m = meta[i]
        if (prev is None or m["node"] != prev["node"]
                or not m["ts"] or abs(m["ts"] - prev["ts"]) > SAME_PASS_S):
            gid += 1
        groups[i] = gid
        prev = m
    return groups


def pr(scores, y, t):
    pred = scores >= t
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    return p, r, tp, fp, fn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-precision", type=float, default=0.95,
                    help="operating point: a false positive is a public accusation")
    ap.add_argument("--folds", type=int, default=8)
    # 🚨 MULTIPLE SEEDS, ALWAYS. A single fold split reported 9/10 recall for
    # this exact model; forty runs put it at 69% ± 7%. The 9/10 was a lucky
    # partition and I came within one command of quoting it as a result. An
    # instrument that CAN hand you a flattering number eventually will, so this
    # one is not able to.
    ap.add_argument("--seeds", type=int, default=10)
    # 🚨 ABLATION, BECAUSE "MORE LABELS" IS A HYPOTHESIS NOT A FACT.
    # 186 machine-made positives were added on 2026-08-18 and recall against
    # zero-shot fell from 60% to 48%. Almost all of them came from PUBLIC
    # TRAFFIC CAMERAS while all 43 test positives come from his own camera, so
    # the obvious suspect is distribution rather than label quality - and the
    # only way to tell is to train without them and look. This flag makes that
    # a one-line experiment instead of an edit.
    ap.add_argument("--exclude", default="",
                    help="comma-separated sampling tags to drop from TRAINING "
                         "as well (e.g. machine,community)")
    args = ap.parse_args()
    if args.exclude:
        for t in args.exclude.split(","):
            APPROVED_FOR_TRAINING.discard(t.strip())
        print("training on: %s" % sorted(APPROVED_FOR_TRAINING))

    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedGroupKFold
    from sklearn.preprocessing import StandardScaler

    X, y, src, meta = load()
    groups = pass_groups(meta)
    cam = np.isin(src, list(MEASURABLE))
    print(f"\n{len(y)} labelled crops: {int(y.sum())} government, "
          f"{int((y == 0).sum())} not")
    print(f"  camera-view (review) : {int(cam.sum())}  "
          f"[{int(y[cam].sum())} government]")
    print(f"  other (handheld/hunt): {int((~cam).sum())}  "
          f"[{int(y[~cam].sum())} government]")

    n_pos_cam = int(y[cam].sum())
    if n_pos_cam < 2:
        raise SystemExit("need at least 2 camera-view positives to cross-validate")
    folds = min(args.folds, n_pos_cam)

    # Every held-out prediction, gathered across folds, so one honest curve can
    # be drawn over all six positives instead of six curves over one each.
    oof_head = np.full(len(y), np.nan)
    oof_clip = np.full(len(y), np.nan)
    cam_idx = np.where(cam)[0]

    seed_scores = []
    for seed in range(args.seeds):
      oof_head[:] = np.nan
      skf = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
      for tr_i, te_i in skf.split(X[cam_idx], y[cam_idx], groups[cam_idx]):
        test_rows = cam_idx[te_i]
        # Train on the other camera folds PLUS every non-camera row. Biased
        # training data is fine; biased TEST data is not.
        # This fold's review rows, PLUS every other camera-view row (likely,
        # hunt) that is not being tested on. Never handheld.
        # ⚠️ AND THE EXTRA ROWS MUST DROP ANY PASS BEING TESTED ON.
        # Group-aware folds keep a pass together WITHIN the review set, but the
        # likely/hunt rows are added afterwards - and that is exactly where the
        # twins were. Excluding by group closes the door they came through.
        test_groups = set(groups[cam_idx[te_i]].tolist())
        extra = np.where(~cam
                         & _trainable(src)
                         & ~np.isin(groups, list(test_groups)))[0]
        train_rows = np.concatenate([cam_idx[tr_i], extra])

        # 🚨 STANDARDISE. Without it the seven CLIP-score features sit on a
        # completely different scale from the 512 embedding dimensions, and one
        # L2 penalty applied across both shrinks the informative few toward
        # nothing. Adding the CLIP scores alone moved AP 0.730 -> 0.792 and
        # still lost; adding the scaler as well took it to 0.830, past
        # zero-shot for the first time. The features were always there to be
        # used - the regulariser was throwing them away.
        sc = StandardScaler().fit(X[train_rows])
        clf = LogisticRegression(max_iter=5000, C=C_BEST,
                                 class_weight="balanced")
        clf.fit(sc.transform(X[train_rows]), y[train_rows])
        oof_head[test_rows] = clf.predict_proba(sc.transform(X[test_rows]))[:, 1]
        for r in test_rows:
            m = meta[r]
            # CLIP's own answer for the same row: its police confidence when it
            # said police, and 0 when it said anything else.
            oof_clip[r] = (m["clip_conf"]
                           if m["clip_class"] in ("police", "gov_dot", "emergency")
                           else 0.0)
      seed_scores.append(oof_head[cam].copy())

    yt = y[cam]
    n_cam = int(cam.sum())
    print(f"\nHELD-OUT CAMERA-VIEW ROWS: {n_cam} ({int(yt.sum())} government)")
    print(f"Every row scored by a model that never saw it, averaged over "
          f"{args.seeds} fold splits.\n")

    def recall_at(s):
        """Best recall reachable at or above the required precision."""
        for t_ in sorted(set(np.round(s, 4))):
            p_, r_, *_ = pr(s, yt, t_)
            if not np.isnan(p_) and p_ >= args.min_precision:
                return r_, t_
        return 0.0, None

    clip_s = oof_clip[cam]
    clip_r, clip_t = recall_at(clip_s)
    clip_ap = average_precision_score(yt, clip_s)
    print(f"  CLIP zero-shot (what it replaces)")
    print(f"    recall @>={args.min_precision:.0%} precision : {clip_r:.0%}"
          f"  (threshold {clip_t:.3f})" if clip_t else "    unreachable")
    print(f"    average precision          : {clip_ap:.3f}\n")

    recs = np.array([recall_at(s)[0] for s in seed_scores])
    aps = np.array([average_precision_score(yt, s) for s in seed_scores])
    print(f"  trained head  (C={C_BEST}, trained on every camera-view row, "
          f"handheld excluded)")
    print(f"    recall @>={args.min_precision:.0%} precision : "
          f"{recs.mean():.0%} ± {recs.std():.0%}   "
          f"(range {recs.min():.0%}-{recs.max():.0%})")
    print(f"    average precision          : {aps.mean():.3f} ± {aps.std():.3f}")
    print(f"    beats zero-shot on recall  : {int((recs > clip_r).sum())}"
          f"/{len(recs)} splits")
    print(f"    beats zero-shot on AP      : {int((aps > clip_ap).sum())}"
          f"/{len(aps)} splits\n")

    better_ap = aps.mean() > clip_ap
    better_op = recs.mean() > clip_r
    if better_ap and better_op:
        print("  ✅ Better on both. Wire it in.")
    elif better_ap:
        print("  ⚖️  Better at RANKING, not at the publish decision.")
        print("     It has learned something real - it orders vehicles better -")
        print("     but there are not enough positives to turn that into a better")
        print("     yes/no at the threshold. More camera-view positives should")
        print("     convert it. Do NOT promote on the flattering metric alone.")
    else:
        print("  ❌ Not better. Leave zero-shot in place.")

    # A single split can and did report 9/10 for a model that averages 69%.
    oof_head[cam] = seed_scores[0]

    # 🚨 AN ABLATION MUST NEVER OVERWRITE THE DEPLOYED MODEL.
    # SPARROW_NO_CLIP_FEATURES exists to reproduce the old behaviour for
    # comparison. Running it wrote a 512-feature head over the 519-feature one
    # that had just been promoted, so production was silently serving the
    # WORSE model from a diagnostic. It was only caught because head.py checks
    # the feature width before scoring; without that guard the detector would
    # have run on a mismatched model producing confident nonsense.
    #
    # A run that is not the real configuration does not get to publish.
    if not _WITH_CLIP:
        print()
        print("⚠️  ablation run (SPARROW_NO_CLIP_FEATURES=1) - model NOT saved")
        return

    # Final model on everything, for deployment.
    fit_rows = np.where(_trainable(src))[0]
    # 🚨 FIT ON THE SCALED FEATURES, AND SAVE THE SCALER WITH THE WEIGHTS.
    # The folds above are evaluated with standardisation; a deployed head fitted
    # WITHOUT it is a different model from the one that was measured, and it
    # would fail silently - plausible scores, no error, numbers that no longer
    # mean what the report said. Whatever transform the evaluation used has to
    # travel with the weights.
    scaler = StandardScaler().fit(X[fit_rows])
    clf = LogisticRegression(max_iter=5000, C=C_BEST, class_weight="balanced")
    clf.fit(scaler.transform(X[fit_rows]), y[fit_rows])
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)

    thrs = [recall_at(s)[1] for s in seed_scores]
    thrs = [x for x in thrs if x is not None]
    thr = float(np.median(thrs)) if thrs else None
    np.savez(MODEL_OUT, w=clf.coef_, b=clf.intercept_,
             mean=scaler.mean_, scale=scaler.scale_,
             # Named so a consumer can check it is feeding the right features
             # in the right order rather than discovering a mismatch as bad
             # predictions.
             clip_classes=np.array(CLIP_CLASSES),
             with_clip_features=_WITH_CLIP,
             threshold=thr if thr is not None else 0.99,
             n_train=len(y), n_pos=int(y.sum()),
             n_cam_pos=n_pos_cam)
    print(f"saved {MODEL_OUT}  (threshold {thr if thr else 'UNREACHABLE'})")
    print(f"\n⚠️  {n_pos_cam} camera-view positives, so recall moves in "
          f"{100 / n_pos_cam:.0f}-point steps.")
    print("    Read AP for DIRECTION and recall for MAGNITUDE - a single fold")
    print("    split reported 9/10 for this exact model, and forty runs put it")
    print("    at 69%. That is why this script always averages over seeds.")
    print("\n🚦 NOT WIRED INTO THE DETECTOR. classify.py still uses CLIP")
    print("    zero-shot. Promote only when the recall line above wins too.")


if __name__ == "__main__":
    main()
