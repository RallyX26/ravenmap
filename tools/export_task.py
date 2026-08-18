"""Build a community labelling bundle and ship it to the box.

    python tools/export_task.py --n 400 --gold 40

WHAT THIS IS FOR

The classifier is capped by labels, not by images, and there are 70,666 crops in
the patrol queue where CLIP said police and the trained head refused. That is
more than one person can work through, so this packages a slice of it as a task
strangers can help with.

🚨 THREE RULES THIS FILE EXISTS TO ENFORCE.

1. PUBLIC CAMERAS ONLY. Every crop in a bundle comes from a public traffic
   camera, never from a volunteer's node. A volunteer put a camera in their own
   window; their street is not something to hand to a crowd, even as a
   plate-illegible 200px crop. There are 70,666 public-camera crops available
   against 13,828 volunteer ones, so this costs nothing and settles the question
   permanently rather than case by case.

2. NOTHING IDENTIFYING TRAVELS. The bundle carries an opaque random id per
   crop. No day folder, no stem, no node, no timestamp, no coordinates - the day
   and stem alone would leak when and roughly where. The mapping from id back to
   the crop stays on this machine.

3. GOLD CROPS ARE MIXED IN. A slice of every bundle is crops whose answer is
   already known from his own review-mode labels. Nobody is told which. That is
   the only way to tell a careful labeller from a careless one, or from somebody
   deliberately poisoning the set, and without it consensus just measures how
   many people agreed about nothing.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bank_index                                      # noqa: E402
import labelbank                                       # noqa: E402
from core import DATA                                  # noqa: E402

OUT = DATA / "label_task"
MAP = DATA / "label_task_map.json"

PUBLIC_ONLY = "node_id LIKE 'Public traffic%'"
UNLABELLED = "(label IS NULL OR label = '')"


def pick_work(n: int, thr: float) -> list:
    db = bank_index.read()
    rows = list(db.execute(
        "SELECT * FROM (SELECT *, ROW_NUMBER() OVER "
        "(PARTITION BY node_id ORDER BY clip_conf DESC) rn FROM crops "
        "WHERE " + UNLABELLED + " AND " + PUBLIC_ONLY +
        " AND clip_vclass IN ('police','emergency') "
        " AND head_conf IS NOT NULL AND head_conf < ?) "
        "WHERE rn <= 3 ORDER BY clip_conf DESC LIMIT ?", (thr, n * 2)))
    db.close()
    return rows


# 🚦 THE STRATIFIED RANDOM DRAW. This is the half that can MEASURE.
#
# A purely random sample cannot be afforded: government vehicles are about 1.07%
# of the bank, so a random draw costs roughly 93 labels for every positive found
# and a 40-positive test set means labelling about 3,700 crops. That is why the
# test set was never rebuilt after the camera fleet arrived, and why it still
# measures crops captured 08-08 to 08-12 - a world that stopped existing.
#
# Sampling randomly WITHIN confidence bands and reweighting by band size gives an
# unbiased estimate for a few hundred labels, because randomness is preserved
# inside each stratum. The sparse band is not optional: it is the only place a
# MISSED patrol car can be found, and without it recall cannot be measured at
# all, only precision.
#
# ⚠️ THE WEIGHTS MUST TRAVEL WITH THE LABELS. A stratified sample analysed as if
# it were simple random is worse than no sample, because it looks rigorous and
# reports a number inflated by however hard the top band was oversampled. The
# band and its population size go into the local map, and import_votes carries
# them through.
STRATA = [
    ("A", "clip_vclass IN ('police','gov_dot','emergency') AND clip_conf >= 0.90"),
    ("B", "clip_vclass IN ('police','gov_dot','emergency') "
          "AND clip_conf >= 0.60 AND clip_conf < 0.90"),
    ("C", "NOT (clip_vclass IN ('police','gov_dot','emergency') "
          "AND clip_conf >= 0.60)"),
]


def pick_random(per_band: dict) -> list:
    """Random crops within each band, with the band and its size attached."""
    db = bank_index.read()
    out = []
    for band, where in STRATA:
        pop = db.execute("SELECT COUNT(*) c FROM crops WHERE " + UNLABELLED +
                         " AND " + PUBLIC_ONLY + " AND " + where).fetchone()["c"]
        want = per_band.get(band, 0)
        if not want or not pop:
            continue
        rows = list(db.execute(
            "SELECT * FROM crops WHERE " + UNLABELLED + " AND " + PUBLIC_ONLY +
            " AND " + where + " ORDER BY RANDOM() LIMIT ?", (want * 2,)))
        for r in rows:
            out.append((r, band, pop))
    db.close()
    return out


def pick_gold(n: int) -> list:
    """Crops HE answered, whose answers are therefore known.

    🚨 `review` OR `confirmed`, AND THE SECOND ONE IS WHY THIS WORKS.
    Gold has to be both human-answered and from a public camera, and on the
    first run that intersection was **two crops** - because his review-mode
    labelling was almost all done on his OWN camera, which is exactly the
    footage a community task must never show. Two gold crops cannot score a
    voter, so consensus would have been a headcount.

    Crops he approves or corrects on the proof page are tagged `confirmed`, and
    those ARE public-camera crops, because that is where the machine queue draws
    from. So proofing the machine pass produces the gold set as a side effect.
    Neither tag may ever enter `measurable`; both are fine as a known answer.
    """
    db = bank_index.read()
    rows = list(db.execute(
        "SELECT * FROM crops WHERE label IN ('police','gov','civilian','fleet') "
        "AND COALESCE(sampling,'review') IN ('review','confirmed') AND " + PUBLIC_ONLY +
        " ORDER BY RANDOM() LIMIT ?", (n * 2,)))
    db.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400, help="crops to be labelled")
    ap.add_argument("--gold", type=int, default=40, help="known-answer crops mixed in")
    ap.add_argument("--random-a", type=int, default=0, help="stratified draw, band A (clip >= 0.90)")
    ap.add_argument("--random-b", type=int, default=0, help="stratified draw, band B (0.60-0.90)")
    ap.add_argument("--random-c", type=int, default=0, help="stratified draw, band C (the rest)")
    ap.add_argument("--push", action="store_true", help="scp the bundle to the box")
    ap.add_argument("--box", default="")
    ap.add_argument("--key", default="")
    a = ap.parse_args()

    thr = labelbank._head_threshold()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    mapping, manifest = {}, []
    used = set()

    def add(r, gold_label=None, pool="work", band=None, band_pop=None):
        key = (r["day"], r["stem"])
        if key in used:
            return False
        p = labelbank.image_path(r["day"], r["stem"])
        if not p or not p.exists():
            return False
        used.add(key)
        iid = secrets.token_hex(8)
        shutil.copy2(p, OUT / (iid + ".jpg"))
        # Local only: how to get back to the crop. Never shipped.
        # 🚦 WHICH POOL THIS CAME FROM, AND IT DECIDES WHAT THE LABEL MAY DO.
        # `work` is the patrol queue: biased toward the model's mistakes on
        # purpose, so its labels may TRAIN (once he approves them) and may never
        # measure. `random` is a stratified random draw, so its labels are the
        # only community ones allowed to MEASURE. Recording it here, at the
        # moment the crop is chosen, is the only place the distinction is
        # knowable - by the time a vote comes back there is nothing to infer it
        # from.
        mapping[iid] = {"day": r["day"], "stem": r["stem"], "gold": gold_label,
                        "pool": pool, "band": band, "band_pop": band_pop}
        # Shipped: an id and nothing else.
        manifest.append({"id": iid})
        return True

    work = pick_work(a.n, thr)
    n_work = 0
    for r in work:
        if n_work >= a.n:
            break
        if add(r):
            n_work += 1

    n_rand = 0
    want = {"A": a.random_a, "B": a.random_b, "C": a.random_c}
    if any(want.values()):
        seen_band = {k: 0 for k in want}
        for r, band, pop in pick_random(want):
            if seen_band[band] >= want[band]:
                continue
            if add(r, pool="random", band=band, band_pop=pop):
                seen_band[band] += 1
                n_rand += 1
        print("  stratified random draw: %s" % seen_band)

    gold = pick_gold(a.gold)
    n_gold = 0
    for r in gold:
        if n_gold >= a.gold:
            break
        if add(r, gold_label=r["label"], pool="gold"):
            n_gold += 1

    # Shuffle so the gold is not detectable by position.
    import random
    random.shuffle(manifest)

    (OUT / "task.json").write_text(
        json.dumps({"items": manifest}, indent=1), encoding="utf-8")
    MAP.write_text(json.dumps(mapping, indent=1), encoding="utf-8")

    size = sum(f.stat().st_size for f in OUT.glob("*.jpg"))
    print("bundle : %s" % OUT)
    print("  to label      : %d  (patrol queue, TRAINS once you approve)" % n_work)
    print("  random slice  : %d  (stratified, MEASURES)" % n_rand)
    print("  gold (hidden) : %d" % n_gold)
    print("  total images  : %d  (%.1f MB)" % (len(manifest), size / 1048576))
    print("  map (LOCAL)   : %s" % MAP)
    print()
    print("manifest carries ONLY opaque ids:", list(manifest[0].keys()))

    if a.push:
        if not (a.box and a.key):
            raise SystemExit("--push needs --box and --key")
        print()
        print("pushing to %s ..." % a.box)
        subprocess.run(["ssh", "-i", a.key, "-o", "BatchMode=yes", a.box,
                        "mkdir -p /opt/sparrowmap/data/label_task && "
                        "rm -f /opt/sparrowmap/data/label_task/*"], check=True)
        subprocess.run(["scp", "-i", a.key, "-o", "BatchMode=yes", "-q",
                        *[str(p) for p in OUT.iterdir()],
                        "%s:/opt/sparrowmap/data/label_task/" % a.box], check=True)
        subprocess.run(["ssh", "-i", a.key, "-o", "BatchMode=yes", a.box,
                        "chown -R sparrow:sparrow /opt/sparrowmap/data/label_task"],
                       check=True)
        print("pushed.")


if __name__ == "__main__":
    main()
