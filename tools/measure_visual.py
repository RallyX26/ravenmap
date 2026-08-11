"""Measure a hand-written visual detector against the local labelled bank.

🚨 NO CRUDE SIGNAL GOES LIVE ON THE STRENGTH OF LOOKING SENSIBLE.
`light_bar` was written from first principles, read plausibly, and fired twice
across 1,595 labelled crops - both on vehicles the operator labelled CIVILIAN. A
hand-written heuristic that has not been measured on this street is a guess with
a confident tone, and in this project a guess that fires puts a claim about a
real vehicle on a public map.

So: run the detector over every labelled crop, and report how often it fires on
government vehicles versus ordinary traffic.

The number that matters is PRECISION - of the crops this fires on, what share
are actually government vehicles. Recall is secondary here on purpose: these are
CORROBORATING signals. One that fires on a tenth of patrol cars and never on a
Corolla is valuable; one that fires on most patrol cars and 5% of Corollas is
poison, because ordinary traffic outnumbers police roughly 500 to 1.

⚠️ Crops from the bank have their plate blacked out and production frames do
not, so `visual._plate_bar_mask` is applied. See the note there - the redaction
is a perfect fake push bumper.

Usage:
    python tools\\measure_visual.py
    python tools\\measure_visual.py --signal push_bumper --show-hits 8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BANK = ROOT / "data" / "training"

# Roughly how many ordinary vehicles pass for each government one. Measured on
# this camera: ~1 in 500. Used only to state what a false-positive rate COSTS in
# vehicles per day, because a rate on a balanced sample hides that completely.
TRAFFIC_RATIO = 500


def crops(limit: int = 0):
    import cv2
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
        # Scraped press photographs answer a different question, and handheld
        # crops are close-ups by construction - neither is this camera.
        if d.get("source") == "scraped" or (d.get("sampling") or "") == "handheld":
            continue
        im = cv2.imread(str(img))
        if im is None:
            continue
        yield lab, str(j.relative_to(BANK)), im
        if limit:
            limit -= 1
            if limit <= 0:
                return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", nargs="*",
                    default=["push_bumper", "pillar_spotlight"])
    ap.add_argument("--show-hits", type=int, default=6)
    a = ap.parse_args()

    import cv2
    import numpy as np
    from detect import visual
    from ultralytics import YOLO

    # 🚨 THE DETECTORS NEED A VEHICLE MASK AND THE REASON IS MEASURED: a vehicle
    # covers only 56-70% of its own bounding box on this bank, so a third of
    # every region reasoned about was road, sky and foliage. Both detectors
    # refuse to answer without one rather than guessing from a box.
    seg = YOLO(str(ROOT / "yolo11n-seg.pt"))

    def vehicle_mask(im):
        r = seg.predict(im, verbose=False, conf=0.25)[0]
        if r.masks is None:
            return None
        mk = r.masks.data.cpu().numpy()
        best = mk[mk.reshape(mk.shape[0], -1).sum(1).argmax()]
        best = cv2.resize(best, (im.shape[1], im.shape[0]))
        return (best > 0.5).astype(np.uint8)

    fns = {"push_bumper": lambda im, mk: visual.push_bumper(
               im, visual._plate_bar_mask(im), mk),
           "pillar_spotlight": lambda im, mk: visual.pillar_spotlight(im, mk)}

    tally = {s: {"gov_fire": 0, "gov_n": 0, "civ_fire": 0, "civ_n": 0,
                 "gov_eligible": 0, "civ_eligible": 0, "hits": []}
             for s in a.signal}

    nomask = 0
    for lab, rel, im in crops():
        is_gov = lab in ("police", "gov")
        mk = vehicle_mask(im)
        if mk is None:
            nomask += 1
        for s in a.signal:
            t = tally[s]
            r = fns[s](im, mk)
            why = r.get("why") or ""
            eligible = "floor" not in why and "no vehicle mask" not in why
            if is_gov:
                t["gov_n"] += 1
                t["gov_eligible"] += eligible
                if r["fired"]:
                    t["gov_fire"] += 1
            else:
                t["civ_n"] += 1
                t["civ_eligible"] += eligible
                if r["fired"]:
                    t["civ_fire"] += 1
                    if len(t["hits"]) < a.show_hits:
                        t["hits"].append(rel)

    for s in a.signal:
        t = tally[s]
        fired = t["gov_fire"] + t["civ_fire"]
        prec = t["gov_fire"] / fired if fired else None
        print(f"\n=== {s} ===")
        print(f"  government crops   {t['gov_n']:>5}  "
              f"({t['gov_eligible']} big enough to look at)")
        print(f"  ordinary crops     {t['civ_n']:>5}  "
              f"({t['civ_eligible']} big enough to look at)")
        print(f"  fired on gov       {t['gov_fire']:>5}"
              + (f"  = {100*t['gov_fire']/t['gov_eligible']:.1f}% recall"
                 if t["gov_eligible"] else ""))
        print(f"  fired on ordinary  {t['civ_fire']:>5}"
              + (f"  = {100*t['civ_fire']/t['civ_eligible']:.2f}% of traffic"
                 if t["civ_eligible"] else ""))
        if prec is None:
            print("  PRECISION           never fired at all")
        else:
            print(f"  PRECISION          {100*prec:.1f}%  "
                  f"(on a BALANCED sample, which the street is not)")
        # 🚨 THE LINE THAT DECIDES IT. A balanced-sample precision is
        # flattering by a factor of ~500 here, and quoting it is how a signal
        # that ruins the map gets called good.
        if t["civ_eligible"] and t["gov_eligible"]:
            fp = t["civ_fire"] / t["civ_eligible"]
            tp = t["gov_fire"] / t["gov_eligible"]
            real = tp / (tp + fp * TRAFFIC_RATIO) if (tp + fp) else 0.0
            print(f"  ON REAL TRAFFIC    {100*real:.1f}% of firings would be "
                  f"government, at 1 government vehicle per {TRAFFIC_RATIO}")
        if t["hits"]:
            print("  fired on these ordinary vehicles:")
            for hrel in t["hits"]:
                print(f"    {hrel}")
    print(f"\n{nomask} crop(s) produced no segmentation mask at all - those are "
          f"'not observed', not 'no marker'.")
    print("\nA corroborating signal earns its place by being RARE and RIGHT.")
    print("Firing often on ordinary traffic is disqualifying however good the")
    print("recall looks, because ordinary traffic is ~500x more common.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
