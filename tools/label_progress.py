"""How close is the label set to training a detector that runs on a phone?

The phone runs a generic model that knows "car" and "truck". Teaching it to know
"marked patrol car" is the difference between a phone that ships a crop and waits
for someone else's desktop, and a phone that is a real node - which is also the
only way a phone could ever be trusted to keep a government plate, since the
device has to know what it is looking at before it can be allowed to keep it.

What blocks that is not compute. It is labelled examples, and specifically:

  * counted in PASSES, not crops. One vehicle fragments into several crops
    seconds apart, and counting those as separate examples is how an evaluation
    once read 87% recall against a true 70%.
  * from REAL CAMERAS, not press photographs. Public images teach a model to
    recognise photography (sharp, close, hero-framed) rather than policing.
  * from SEVERAL cameras. A model trained on one street learns that street: its
    lighting, its angle, its one police department's livery.

So this counts the thing that actually matters and says how far off it is.

    python tools/label_progress.py
    python tools/label_progress.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA                       # noqa: E402

BANK = DATA / "training"
POSITIVE = {"police", "gov"}
NEGATIVE = {"civilian", "fleet"}
# Sources that do NOT count toward the target, and why.
NOT_CAMERA = {"scraped", "handheld"}
# One vehicle crossing the frame is one example, however many crops it made.
SAME_PASS_S = 10.0

# ── The target. An estimate, and labelled as one.
#
# The desktop head works from roughly 50 camera-view government passes, but it
# stands on a large pretrained model that already knows what a car is, and it
# only has to be right on ONE street. A phone model has to generalise across
# other people's cameras, so it needs both more examples and more variety.
# These numbers are a starting bar to attempt a first model, not a guarantee.
TARGET_PASSES = 300
TARGET_CAMERAS = 4


def load():
    rows = []
    for jf in BANK.rglob("*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        lab = d.get("label")
        if lab not in POSITIVE and lab not in NEGATIVE:
            continue
        src = "scraped" if d.get("source") == "scraped" else (d.get("sampling") or "review")
        rows.append({
            "label": lab,
            "src": src,
            "node": d.get("node_id") or "",
            "ts": float(d.get("ts") or 0.0),
            "camera": src not in NOT_CAMERA,
        })
    return rows


def passes(rows):
    """Collapse crops of the same vehicle into one example."""
    rows = sorted(rows, key=lambda r: (r["node"], r["ts"]))
    out, prev = 0, None
    for r in rows:
        if (prev is None or r["node"] != prev["node"] or not r["ts"]
                or abs(r["ts"] - prev["ts"]) > SAME_PASS_S):
            out += 1
        prev = r
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = load()
    cam = [r for r in rows if r["camera"]]
    pos = [r for r in cam if r["label"] in POSITIVE]
    neg = [r for r in cam if r["label"] in NEGATIVE]
    pos_passes, neg_passes = passes(pos), passes(neg)
    cameras = sorted({r["node"] for r in pos if r["node"]})
    other = [r for r in rows if not r["camera"]]

    pct = min(100, round(100 * pos_passes / TARGET_PASSES))
    cams_ok = len(cameras) >= TARGET_CAMERAS

    if args.json:
        print(json.dumps({
            "government_passes": pos_passes, "ordinary_passes": neg_passes,
            "cameras": len(cameras), "target_passes": TARGET_PASSES,
            "target_cameras": TARGET_CAMERAS, "percent": pct,
            "ready": pos_passes >= TARGET_PASSES and cams_ok,
        }, indent=1))
        return

    bar = "#" * (pct * 30 // 100) + "." * (30 - pct * 30 // 100)
    print()
    print("  TRAINING A PHONE TO SEE PATROL CARS")
    print("  " + "-" * 46)
    print(f"  government passes   {pos_passes:5d}  of {TARGET_PASSES}   [{bar}] {pct}%")
    print(f"  distinct cameras    {len(cameras):5d}  of {TARGET_CAMERAS}   "
          f"{'ok' if cams_ok else 'need more streets'}")
    print(f"  ordinary passes     {neg_passes:5d}  (negatives - plenty)")
    if other:
        print(f"  excluded            {len(other):5d} crops from press photos or "
              f"handheld shots")
        print(f"                            they train, but they cannot COUNT: a model "
              f"fed them")
        print(f"                            learns photography, not policing")
    print()
    if pos_passes >= TARGET_PASSES and cams_ok:
        print("  READY TO ATTEMPT a first phone model.")
    else:
        need = max(0, TARGET_PASSES - pos_passes)
        print(f"  {need} more government passes"
              + ("" if cams_ok else f", and {TARGET_CAMERAS - len(cameras)} more camera(s)")
              + " to attempt it.")
        print("  Every cop / not-a-cop answered in the reviewer app adds one.")
    print()


if __name__ == "__main__":
    main()
