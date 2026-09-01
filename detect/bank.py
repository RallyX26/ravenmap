"""Keep every vehicle the node sees, so the system can eventually LEARN.

🚨 THE GAP THIS CLOSES.
the operator asked whether running the node longer makes it better. Today it does
not, and that is worth stating plainly: nothing in SparrowMap learns. The police
call comes from CLIP used zero-shot - a general model asked "is this more like
'a police car' or 'an ordinary car'", never trained on his street - plus one
hand-written colour rule which has now been wrong five times out of five. Hours
of runtime produced sightings and produced no improvement, because every crop
was posted and then thrown away.

So this module banks them. Each completed vehicle pass is written to disk with
the evidence that was computed about it, which turns runtime into a growing
labelled dataset instead of into nothing. That matters most RIGHT NOW: the
traffic going past his window this afternoon cannot be collected retroactively,
and a model trained next month will be trained on whatever we started keeping
today.

## What is kept, and what is not

  * the vehicle CROP, not the frame - same rule as everything else here, the
    neighbours are not part of the training set;
  * the plate REDACTED, because a plate says nothing about whether a vehicle is
    a patrol car and keeping it would build the exact readable-plate archive
    the whole project refuses to build;
  * the computed evidence beside it, so a label can be attached later without
    re-running the models.

## Where it lives

`data/training/`, which is OPERATOR-ONLY. The hub serves `data/snaps` and
nothing else, and this directory must never be added to that. It is a private
workbench on the camera owner's own machine, not a published collection.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from core import DATA

BANK = DATA / "training"

# Disk is finite and a node is meant to run for weeks. At the traffic measured
# on his street this is roughly a fortnight of banking before the oldest day
# starts being dropped, which is long enough to gather a dataset and short
# enough that nobody accidentally builds an archive.
MAX_BYTES = 4 * 1024 * 1024 * 1024        # 4 GB
CROP_MAX_EDGE = 512                        # plenty for a classifier


def _day_dir() -> Path:
    d = BANK / time.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def bank_remote(img_bytes: bytes, node_id: str, meta: dict) -> Optional[str]:
    """Bank a crop that arrived from a remote node, for later labelling.

    the operator's reason for building phone nodes at all: every camera someone puts
    in a window is a stream of real vehicles in real conditions, which is the
    training data the classifier has been starving for. Eight distinct vehicle
    passes from one window is the whole local dataset; ten volunteers make that
    a different problem.

    Crops land in a per-node day folder so a bad or malicious node can be
    dropped in one delete, and so nobody's contribution is silently mixed into
    the set that measures the model. Sub-resolution by the time it gets here -
    see snapshot.store_subresolution - which is the size the training pipeline
    wants anyway.

    No label and no CLIP block: nothing has guessed, and the labelling page
    must not be able to anchor a human to a machine's opinion.
    """
    try:
        d = BANK / f"remote_{time.strftime('%Y-%m-%d')}"
        d.mkdir(parents=True, exist_ok=True)
        stem = hashlib.blake2b(img_bytes, digest_size=8).hexdigest()
        f = d / f"{stem}.jpg"
        if f.exists():
            return stem
        f.write_bytes(img_bytes)
        (d / f"{stem}.json").write_text(json.dumps({
            "ts": meta.get("ts") or time.time(),
            "node_id": node_id,
            "cls_name": meta.get("cls_name") or "car",
            "det_conf": meta.get("det_conf"),
            "source": "remote_node",
            "label": None,
            "sampling": "remote",
        }, indent=1), encoding="utf-8")
        return stem
    except Exception:
        return None          # banking is a bonus; never fail a sighting for it


def bank_pass(vp, evidence: dict, verdict: dict,
              node_id: str = "", redact: bool = True) -> Optional[str]:
    """Write one vehicle's crop plus everything computed about it.

    Returns the stem written, or None if there was nothing worth keeping.
    Never raises: banking is a background convenience and must not be able to
    take down a node that is doing its actual job.
    """
    try:
        if vp.best_frame is None or not vp.best_vehicle_box:
            return None
        frame = vp.best_frame
        h, w = frame.shape[:2]

        # EVERY plate-like box from the best frame. Redacting only the
        # detector's favourite guess left the real plate readable on a marked
        # patrol car while blacking out its livery - the winner is chosen by
        # area and the door lettering is bigger than the plate. See
        # VehiclePass.best_plate_boxes.
        boxes = list(getattr(vp, "best_plate_boxes", None) or [])
        if vp.best_plate_box and vp.best_plate_box not in boxes:
            boxes.append(vp.best_plate_box)
        if redact and boxes:
            frame = frame.copy()
            for _b in boxes:
                px0, py0, px1, py1 = (int(v) for v in _b)
                px0, py0 = max(0, px0), max(0, py0)
                px1, py1 = min(w, px1), min(h, py1)
                if px1 <= px0 or py1 <= py0:
                    continue
                # Pixelate then bar it, the same order snapshot.redact_plate
                # uses. A plate is a tiny fixed-layout character set, so
                # pixelation alone is brute-forceable.
                roi = frame[py0:py1, px0:px1]
                small = cv2.resize(roi, (max(1, (px1 - px0) // 12),
                                         max(1, (py1 - py0) // 12)),
                                   interpolation=cv2.INTER_LINEAR)
                frame[py0:py1, px0:px1] = cv2.resize(
                    small, (px1 - px0, py1 - py0), interpolation=cv2.INTER_NEAREST)
                cv2.rectangle(frame, (px0, py0), (px1, py1), (0, 0, 0), -1)

        x0, y0, x1, y1 = (int(v) for v in vp.best_vehicle_box)
        # Pad so the WHOLE vehicle lands in the crop; the detector box is tight
        # and clips wheels/mirrors/roof (6% left cars cut off). Top-heavy on
        # purpose, matching drive.html: the LIGHT BAR/aerial sits just above the
        # box and is the most diagnostic feature on a marked car, while nothing
        # diagnostic hangs off the bottom and widening the sides only buys
        # pavement (a privacy + resolution cost). Clamped to the frame edges.
        bw, bh = x1 - x0, y1 - y0
        pad_x = max(int(bw * 0.14), 20)
        pad_top = max(int(bh * 0.30), 28)
        pad_bot = max(int(bh * 0.12), 18)
        crop = frame[max(0, y0 - pad_top):min(h, y1 + pad_bot),
                     max(0, x0 - pad_x):min(w, x1 + pad_x)]
        if crop.size == 0:
            return None
        if max(crop.shape[:2]) > CROP_MAX_EDGE:
            s = CROP_MAX_EDGE / max(crop.shape[:2])
            crop = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)),
                              interpolation=cv2.INTER_AREA)

        d = _day_dir()
        stem = f"{time.strftime('%H%M%S')}_{vp.track_id:05d}"
        cv2.imwrite(str(d / f"{stem}.jpg"), crop,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])

        plate, ocr_conf, agree = vp.vote()
        (d / f"{stem}.json").write_text(json.dumps({
            "ts": vp.first_ts or time.time(),
            "node_id": node_id,
            "track_id": vp.track_id,
            "cls_name": vp.cls_name,
            "frames_seen": vp.frames_seen,
            "plate_reads": len(vp.reads),
            # The plate TEXT is kept out on purpose. It is irrelevant to "is
            # this a patrol car" and keeping it would quietly build the
            # readable-plate archive this project exists to not have.
            "plate_agreement": round(agree, 3),
            "evidence": {k: (v if not isinstance(v, (np.floating, np.integer))
                             else float(v))
                         for k, v in evidence.items() if not k.startswith("_")},
            # 🚨 STRIP THE UNDERSCORE KEYS HERE TOO.
            # `evidence` above already drops them; this block did not, and the
            # moment classify() started returning the 512-float CLIP embedding
            # for the trained head, every sidecar would have gained a
            # stringified numpy array several kilobytes wide - written by
            # `default=str`, so it would not even have raised. Silent bloat
            # across the whole bank, and a `clip` block no longer shaped the
            # way train/fit_local.py expects to read it.
            "clip": {k: v for k, v in (evidence.get("_visual") or {}).items()
                     if not k.startswith("_")},
            "auto_verdict": {"vclass": verdict.get("vclass"),
                             "conf": verdict.get("conf"),
                             "sightable": verdict.get("sightable"),
                             "fired": verdict.get("fired")},
            # Filled in by a human later. This is the whole point.
            "label": None,
        }, indent=1, default=str), encoding="utf-8")
        return stem
    except Exception as exc:                      # never take the node down
        print(f"  ! bank failed: {exc}")
        return None


# Newest-first day directories, cached briefly. The bank is one directory per
# day, so "which day is this crop in" is a very small search - but only if you
# look in day order instead of walking every file.
_DAYS_CACHE: list = []
_DAYS_AT = 0.0


def _day_dirs() -> list:
    global _DAYS_CACHE, _DAYS_AT
    import time as _t
    if _t.time() - _DAYS_AT > 30 or not _DAYS_CACHE:
        try:
            _DAYS_CACHE = sorted((d for d in BANK.iterdir() if d.is_dir()),
                                 key=lambda d: d.name, reverse=True)
        except Exception:
            _DAYS_CACHE = []
        _DAYS_AT = _t.time()
    return _DAYS_CACHE


def _find_sidecar(stem: str):
    """Path to a crop's sidecar, without walking the whole bank.

    🚨 rglob(f"{stem}.json") WALKS EVERY FILE TO FIND ONE.
    The bank holds ~48,000 sidecars across day folders, and three separate
    functions did this per VEHICLE PASS - so a busy road cost hundreds of
    thousands of directory entries a minute, and the review page (which does up
    to 400 of these per load) has already hung on it once.

    A crop's sidecar lives in exactly one day directory, and every caller here
    is looking for one that was banked seconds ago. Checking the newest days
    directly answers it in one or two stat() calls; the walk stays as a
    fallback so an old stem is still found.
    """
    for d in _day_dirs():
        cand = d / f"{stem}.json"
        if cand.exists():
            return cand
    _j = _find_sidecar(stem)
    for j in ([_j] if _j else []):
        return j
    return None


def note_not_posted(stem: str, reason: str) -> None:
    """Record WHY this crop never became a sighting.

    🚨 66% OF THE BANK (21,079 of 31,881 crops) HAS NO `sighting_id`, AND
    NOTHING SAID WHY. Three completely different things produce that same
    silence in post_one():
        * the gate declined it  - working exactly as designed, a civilian car
        * there was no frame to encode - rare, and silent
        * THE POST FAILED - a network blip, and a patrol car the head scored
          0.871 is simply gone, with no retry and no queue
    Those are indistinguishable after the fact, so "a head-positive crop that
    reached nobody" could not be triaged - it might be the system working or
    the system losing evidence, and there was no way to tell which. The same
    shape as every other bug in this project: the decision gets made and the
    artifact a human reads does not record it.

    Additive and best-effort: a failure to annotate must never affect
    detection, so everything here is swallowed.
    """
    if not stem:
        return
    _j = _find_sidecar(stem)
    for j in ([_j] if _j else []):
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
            if d.get("sighting_id"):
                return                      # it did post; nothing to explain
            d["not_posted"] = str(reason)[:80]
            j.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
        except Exception:
            pass
        return


def link_sighting(stem: str, sighting_id: int) -> None:
    """Record which published sighting this crop became.

    The link is what turns "that dot is wrong" into a training label. Without
    it a correction on the map fixes one row and teaches the system nothing,
    which is how a project ends up making the same mistake for months.
    """
    _j = _find_sidecar(stem)
    for j in ([_j] if _j else []):
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
            d["sighting_id"] = int(sighting_id)
            j.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
            _INDEX[int(sighting_id)] = j       # keep the cache honest
        except Exception:
            pass
        return


def sidecar(stem: str) -> Optional[Path]:
    """The sidecar for a bank stem. One glob, no parsing, no scan.

    This is how the review queue resolves a crop now: the sighting row carries
    `bank_ref`, so there is nothing to search for. See db.FIELDS.
    """
    if not stem:
        return None
    _j = _find_sidecar(stem)
    for j in ([_j] if _j else []):
        return j
    return None


_INDEX: dict = {}
_INDEX_STAMP: list = [0.0, 0]


def sighting_index(max_age: float = 20.0) -> dict:
    """{sighting_id: sidecar path}, built in ONE pass and cached briefly.

    🚨 THIS REPLACED AN ACCIDENTALLY QUADRATIC SCAN THAT HUNG THE REVIEW PAGE.
    `find_by_sighting` walked every sidecar in the bank looking for one id, and
    the review queue called it once per private sighting. At 818 crops and 400
    sightings that is over 300,000 file reads for a single page load - the API
    stopped answering and the browser sat there with an empty page, which read
    as "the feature is broken" rather than "the feature is slow".

    It also got worse every hour the node ran, because the bank only grows.
    That is the shape worth recognising: work repeated over an input that did
    not change between iterations.
    """
    import time
    n = sum(1 for _ in BANK.rglob("*.json")) if BANK.exists() else 0
    fresh = (time.time() - _INDEX_STAMP[0]) < max_age and _INDEX_STAMP[1] == n
    if _INDEX and fresh:
        return _INDEX
    idx = {}
    for j in BANK.rglob("*.json"):
        try:
            sid = json.loads(j.read_text(encoding="utf-8")).get("sighting_id")
        except Exception:
            continue
        if sid is not None:
            idx[int(sid)] = j
    _INDEX.clear()
    _INDEX.update(idx)
    _INDEX_STAMP[0], _INDEX_STAMP[1] = time.time(), n
    return _INDEX


def find_by_sighting(sighting_id: int) -> Optional[Path]:
    """The banked crop for a published sighting, if there is one."""
    return sighting_index().get(int(sighting_id))


def label_by_sighting(sighting_id: int, label: str) -> bool:
    """Write a human's verdict on a published sighting back into the dataset.

    Marked `sampling='review'` because it IS a review-mode judgement: the
    operator was shown what the system published and asked whether it was
    right. That makes it usable for measuring, unlike the handheld photographs.
    """
    j = find_by_sighting(sighting_id)
    if not j:
        return False
    import time
    d = json.loads(j.read_text(encoding="utf-8"))
    d["label"] = label
    d["labelled_at"] = time.time()
    d["sampling"] = "review"
    d["labelled_from"] = "map_review"
    j.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
    return True


def usage() -> dict:
    """How much has been banked, and how close to the cap."""
    files = list(BANK.rglob("*.jpg"))
    total = sum(f.stat().st_size for f in files) if files else 0
    labelled = 0
    for j in BANK.rglob("*.json"):
        try:
            if json.loads(j.read_text(encoding="utf-8")).get("label"):
                labelled += 1
        except Exception:
            pass
    return {"crops": len(files), "bytes": total, "labelled": labelled,
            "days": len([d for d in BANK.glob("*") if d.is_dir()])}


def prune() -> int:
    """Drop the oldest DAYS until the bank is under the cap. Returns days cut.

    Whole days at a time, so the set never becomes a biased sample of a day -
    deleting individual files by age would quietly over-represent whatever
    time of day happened to survive.
    """
    if not BANK.exists():
        return 0
    cut = 0
    while usage()["bytes"] > MAX_BYTES:
        days = sorted(d for d in BANK.glob("*") if d.is_dir())
        if len(days) <= 1:
            break
        for f in days[0].iterdir():
            f.unlink(missing_ok=True)
        days[0].rmdir()
        cut += 1
    return cut
