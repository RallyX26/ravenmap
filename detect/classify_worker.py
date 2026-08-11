"""The trained head, applied to phone crops after the fact.

A phone runs only the tiny in-browser detector. It can find a vehicle and read
its body shape, but it cannot run CLIP or the trained head, so a phone crop
arrives with a body class and nothing else: no idea whether the thing it saw
was a patrol car. bank_remote stores it deliberately blank.

This worker is the head those crops never got. It runs where the GPU and the
trained weights already are - the home hub - reads freshly banked phone crops,
and scores each one through the EXACT CLIP + head path a fixed node uses
(VehicleIdentifier.classify -> gov_call), then writes the verdict into the
crop's sidecar.

It publishes NOTHING and it moves no tier. A government verdict only causes the
crop to surface in the operator review queue (hub.py builds that "missed" list
from each sidecar's `clip` block); a human still confirms before anything
reaches the public map. That is the same human-confirmed rule the rest of the
project runs on - the worker's whole job is to hand the reviewer the phone
catches worth a look instead of leaving them buried as max-uncertainty noise.

Run once after a batch of phone traffic, or on a poll:

    python -m detect.classify_worker --once
    python -m detect.classify_worker --interval 120
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detect import bank                       # noqa: E402
from detect.vehicle_id import VehicleIdentifier  # noqa: E402


def _pending(node: str | None) -> list[Path]:
    """Phone sidecars that have no machine verdict yet.

    A crop qualifies when it came from a remote node and carries no `clip`
    block. A human `label` is left alone either way: the label is ground truth
    the trainer reads, and the review queue keys on `clip`, so the two never
    collide. Skipping already-scored crops keeps the worker idempotent - it can
    run every two minutes and only ever touch new arrivals.
    """
    out = []
    for j in sorted(bank.BANK.rglob("*.json")):
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("source") != "remote_node":
            continue
        if d.get("clip"):                      # already scored
            continue
        if node and d.get("node_id") != node:
            continue
        if not j.with_suffix(".jpg").exists():  # sidecar without its image
            continue
        out.append(j)
    return out


def _score_one(vid: VehicleIdentifier, sidecar: Path) -> dict | None:
    """Run CLIP + the head on one crop and fold the verdict into its sidecar.

    The `clip` block is written in the shape the labelbank and the review queue
    already read (vclass/conf/margin/scores). `head_conf` records the trained
    head's probability when a head decided, and `by` marks the verdict as a
    post-hoc worker call rather than an at-capture guess, so the provenance is
    never ambiguous later.
    """
    img = cv2.imread(str(sidecar.with_suffix(".jpg")))
    if img is None:
        return None
    r = vid.classify(img)
    call = VehicleIdentifier.gov_call(r)       # head if present, else zero-shot

    clip = {
        "vclass": r["vclass"],
        "conf": r["conf"],
        "margin": r["margin"],
        "scores": r["scores"],
        "by": "classify_worker",
        "at": time.time(),
    }
    if call.get("source") == "head":
        clip["head_conf"] = call["conf"]
        clip["head_gov"] = bool(call["gov"])

    # Atomic rewrite: never leave a half-written sidecar if the box dies mid-run.
    d = json.loads(sidecar.read_text(encoding="utf-8"))
    d["clip"] = clip
    tmp = sidecar.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(sidecar)
    return clip


def run_once(vid: VehicleIdentifier, node: str | None, dry: bool) -> int:
    todo = _pending(node)
    if not todo:
        return 0
    hits = 0
    for j in todo:
        clip = None
        if dry:
            img = cv2.imread(str(j.with_suffix(".jpg")))
            if img is not None:
                r = vid.classify(img)
                call = VehicleIdentifier.gov_call(r)
                clip = {"vclass": r["vclass"], "conf": r["conf"],
                        "margin": r["margin"],
                        "head_conf": call["conf"] if call["source"] == "head"
                        else None}
        else:
            clip = _score_one(vid, j)
        if clip is None:
            continue
        hits += 1
        # Mark exactly what the review queue will surface: the head's verdict
        # when it scored the crop, the zero-shot gate only as a fallback. This
        # keeps the preview honest about the head overruling CLIP.
        from detect import head as _head
        hthr = _head.threshold() if _head.available() else None
        hc = clip.get("head_conf")
        if hc is not None and hthr is not None:
            review = hc >= hthr
        else:
            review = (clip.get("vclass") in ("police", "gov_dot", "emergency")
                      and (clip.get("conf") or 0) >= 0.50
                      and (clip.get("margin") or 0) >= 0.20)
        mark = "  <- REVIEW" if review else ""
        print(f"  {j.parent.name}/{j.stem}: {clip['vclass']} "
              f"conf={clip['conf']:.2f} margin={clip['margin']:.2f}"
              f"{f' head={hc:.2f}' if hc is not None else ''}{mark}")
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true",
                    help="score the current backlog and exit (default)")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="poll every N seconds instead of exiting")
    ap.add_argument("--node", default=None,
                    help="only score crops from this node id")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the verdicts without writing any sidecar")
    args = ap.parse_args()

    from detect import head as _head
    st = _head.status()
    print("trained head:", "ready" if st["ok"] else f"UNAVAILABLE ({st['why']})",
          "- falling back to zero-shot" if not st["ok"] else
          f"(threshold {st['threshold']:.2f})")
    print("loading CLIP (first run pulls ~600 MB)...")
    vid = VehicleIdentifier()

    if args.interval and not args.once:
        print(f"polling every {args.interval:.0f}s; Ctrl-C to stop")
        while True:
            n = run_once(vid, args.node, args.dry_run)
            if n:
                print(f"scored {n} crop(s) at {time.strftime('%H:%M:%S')}")
            time.sleep(args.interval)
    else:
        n = run_once(vid, args.node, args.dry_run)
        print(f"done: scored {n} phone crop(s)"
              f"{' (dry run, nothing written)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
