"""Measure the visual signals against locally labelled frames.

The signal weights in classify.py are guesses, and the one implemented visual
signal has now produced a false positive three separate times - each time it
was tightened by argument rather than by measurement, and each time it failed
again in a new way. This is the harness that replaces the argument.

A label file is a JSON list of {"file": ..., "truth": "police"|"civilian"}.
the operator's own confirmation is the ground truth: on 2026-08-08 he looked at the
five vehicles the system had published as police off his street and said none
of them were. That is the strongest label this project can get, and throwing it
away would mean re-deriving the same failure later.

    python detect\\calibrate.py                          # the default set
    python detect\\calibrate.py --labels data\\eval\\labels.json
    python detect\\calibrate.py --verbose                # per-vehicle detail

Reports, per signal, how often it fires on vehicles that are NOT what it
claims - which is the number that decides whether it may publish anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classify                        # noqa: E402
from core import CONFIG, DATA          # noqa: E402
from detect import visual              # noqa: E402
from detect.pipeline import VehicleTracker   # noqa: E402

LABELS = DATA / "eval" / "labels.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(LABELS))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--crops", action="store_true", default=True,
                    help="inputs are already vehicle crops (default); "
                         "--no-crops runs the detector over full frames")
    ap.add_argument("--no-crops", dest="crops", action="store_false")
    ap.add_argument("--no-clip", action="store_true",
                    help="measure the raw heuristics without the classifier")
    args = ap.parse_args()

    path = Path(args.labels)
    if not path.exists():
        raise SystemExit(f"no label file at {path} - run tools\\label_frames.py")
    rows = json.loads(path.read_text(encoding="utf-8"))

    # 🚨 TWO WAYS THIS INSTRUMENT WAS LYING TO ITSELF, BOTH FIXED HERE.
    #
    # 1. It re-ran the vehicle DETECTOR over images that are already vehicle
    #    crops. A crop of a row of five patrol cars produced five "vehicles"
    #    from one label, so 25 labelled crops became "59 vehicles" and every
    #    rate was computed over a denominator that does not exist. A labelled
    #    crop IS the unit of measurement.
    #
    # 2. It reported recall from `sightable`, which is forced False while the
    #    deployment gate is closed - so it read 0/45 recall on a set full of
    #    obvious patrol cars. Deciding whether to OPEN the gate from a number
    #    the gate suppresses is circular. It now reports the classifier's own
    #    verdict, and separately what the gate would currently allow.
    tracker = None if args.crops else VehicleTracker()
    vid = None
    if not args.no_clip:
        from detect.vehicle_id import VehicleIdentifier
        vid = VehicleIdentifier()

    # signal -> [fired on a police vehicle, fired on a civilian one]
    fires: dict[str, list[int]] = {}
    seen = {"police": 0, "civilian": 0}
    published = {"police": 0, "civilian": 0}
    gated = {"police": 0, "civilian": 0}
    n_vehicles = 0

    for row in rows:
        f = Path(row["file"])
        if not f.is_absolute():
            f = Path(__file__).resolve().parent.parent / f
        frame = cv2.imread(str(f))
        if frame is None:
            print(f"  ! unreadable: {f}")
            continue
        truth = row["truth"]
        h, w = frame.shape[:2]

        # One labelled crop, one measurement. See the note above.
        units = ([(0, 0, w, h)] if args.crops
                 else [tuple(int(v) for v in b) for _t, _c, b, _cf in tracker.track(frame)])

        for vbox in units:
            x0, y0, x1, y1 = vbox
            ev = visual.signals(frame, vbox)
            vres = None
            if vid is not None:
                crop = frame[max(0, y0):y1, max(0, x0):x1]
                if crop.size:
                    vres = vid.evidence(crop)
                    ev.update({k: v for k, v in vres.items()
                               if not k.startswith("_")})
                    ev = visual.arbitrate(ev, vres.get("_visual"))
            res = classify.classify(ev)

            n_vehicles += 1
            seen[truth] = seen.get(truth, 0) + 1
            # What the CLASSIFIER concluded, independent of the deployment
            # gate - this is the number that decides whether the gate should
            # open. `res["sightable"]` is what the gate currently permits.
            called = res["vclass"] in ("police", "emergency", "gov", "fleet")
            if called:
                published[truth] = published.get(truth, 0) + 1
            if res["sightable"]:
                gated[truth] = gated.get(truth, 0) + 1
            for k in res["fired"]:
                fires.setdefault(k, [0, 0])[0 if truth == "police" else 1] += 1

            if args.verbose:
                v = (vres or {}).get("_visual") or {}
                src = row.get("sampling") or "-"
                print(f"  {f.name[:22]:<22} truth={truth:<9} [{src:<8}]"
                      f" -> {res['vclass']:<9} conf={res['conf']:.3f}"
                      f" clip={v.get('vclass', '-')}:{v.get('conf', 0):.2f}"
                      f"/{v.get('margin', 0):.2f}"
                      f" fired={res['fired']}")

    print(f"\n{len(rows)} frames, {n_vehicles} vehicles "
          f"({seen.get('police', 0)} police, {seen.get('civilian', 0)} civilian)")

    print("\nsignal                  on police   on civilian   FALSE-POSITIVE RATE")
    print("-" * 72)
    for k in sorted(fires, key=lambda x: -fires[x][1]):
        pol, civ = fires[k]
        fp = civ / seen["civilian"] if seen.get("civilian") else 0.0
        flag = "  <-- publishes on ordinary traffic" if fp > 0 else ""
        print(f"{k:<24}{pol:>6}      {civ:>8}          {fp:>7.1%}{flag}")

    npol, nciv = seen.get("police", 0), seen.get("civilian", 0)
    tp, fp = published.get("police", 0), published.get("civilian", 0)
    print("\nCLASSIFIER VERDICT  (what the model concludes, gate aside -")
    print("                     this is the number that decides the gate)")
    if not npol:
        print("  recall          : UNKNOWN - no police vehicles in the label set.")
        print("                    Tightening measured only against negatives can")
        print("                    silence the signal entirely without showing it.")
    else:
        print(f"  recall          : {tp}/{npol} police called police ({tp/npol:.0%})")
    print(f"  false positives : {fp}/{nciv} civilians called police "
          f"({fp/max(1, nciv):.0%})")
    if tp + fp:
        print(f"  precision       : {tp}/{tp + fp} = {tp/(tp + fp):.0%}")

    print("\nWHAT THE DEPLOYMENT GATE CURRENTLY ALLOWS ONTO THE MAP")
    print(f"  public sightings: {gated.get('police', 0)} police, "
          f"{gated.get('civilian', 0)} civilian")
    if not CONFIG.get("publish_public_tier", False):
        print("  (publish_public_tier is FALSE in config.json, so nothing is")
        print("   published at all. A zero here is the gate, not the classifier.)")

    handheld = sum(1 for r in rows if (r.get("sampling") or "") == "handheld"
                   and r.get("truth") == "police")
    if npol and handheld:
        print(f"\n⚠️  {handheld} of {npol} police labels are HANDHELD photographs -")
        print("    close, sharp, side-on in a car park. Recall measured on those")
        print("    says little about a vehicle crossing a window at 45 degrees in")
        print("    motion blur. Camera-view positives are the ones that count.")


if __name__ == "__main__":
    main()
