"""Phase 2: score the public mirror's phone crops on the home node, then publish.

A public phone contributor's crop lands on the mirror, which cannot tell a
patrol car from a minivan - it has no GPU. The mirror parks the crop, already
destroyed below plate legibility, in its inbox (mirror.quarantine_write). This
runs on the HOME node, where CLIP lives:

    pull the mirror's inbox  ->  CLIP on each crop
      clearly MARKED law enforcement  ->  publish on the public map
      anything else                   ->  discard
    delete every pulled crop from the mirror either way

WHY MARKED-ONLY, AND NOT THE TRAINED HEAD.
The head answers is-it-government, trained on the home camera's catches. It
learned to fire on unmarked black SUVs - it scored a civilian Cadillac Escalade
0.98 and put it on the public map. Measured on the real crops: an Escalade
false-positive reached CLIP police conf 0.923, HIGHER than two genuine patrol
cars - so neither the head nor a loose CLIP threshold can separate marked from
unmarked. What does separate is the strict zero-shot gate whose prompts are all
marked-specific (light bar, door decals, emergency lights): at conf >= 0.96 and
margin >= 0.90 it sits just above every Escalade in the set and admits only
clearly-marked units (about one crop in 335 of ordinary traffic, all genuinely
marked). Low recall, high precision - correct for a pipeline that publishes with
no human in the loop. Unmarked government vehicles are simply not a target; a
photograph of a black SUV is a photograph of somebody's neighbour.

The mirror grows no HTTP surface for this: crops are pulled and the verdict is
applied over the existing SSH admin channel, so a mirror breach still yields
only the public record. Wrong calls are retracted after the fact with
tools/box_retract.py.

    # against your mirror, over ssh:
    python -m detect.box_puller --once \
      --box root@your-mirror-host --key ~/.ssh/your_key
    # against a local staging mirror's inbox (no ssh):
    python -m detect.box_puller --once --inbox /path/to/mirror/data/inbox
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detect.vehicle_id import VehicleIdentifier    # noqa: E402

REMOTE_PY = "/opt/sparrowmap/.venv/bin/python"
REMOTE_PUBLISH = "/opt/sparrowmap/tools/box_publish.py"

# The marked-law-enforcement gate. These classes' prompts are all marked-
# specific (see vehicle_id.CLASSES); the thresholds are measured to sit above
# every unmarked false positive in the ground-truth set. Deliberately strict:
# this publishes with no human review.
MARKED_CLASSES = {"police", "gov_dot"}
MIN_CONF, MIN_MARGIN = 0.96, 0.90


# --------------------------------------------------------------------------
# Pulling: either a local staging dir, or the live box over ssh.
# --------------------------------------------------------------------------
def pull(args) -> tuple[Path, bool]:
    """Return (dir holding the crops, is_local).

    A remote inbox is streamed as a single tar over ssh - not scp'd file by
    file. scp -r opens a fresh round trip per file, and against a high-latency
    box a full inbox (hundreds of tiny files) blows past any timeout while
    transferring only a few hundred KB. One tar = one round trip.
    """
    if args.inbox:
        return Path(args.inbox), True
    tmp = Path(tempfile.mkdtemp(prefix="box_inbox_"))
    proc = subprocess.run(
        ["ssh", "-i", args.key, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", args.box,
         f"tar -C {args.remote} -cf - . 2>/dev/null || true"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if proc.stdout:
        try:
            subprocess.run(["tar", "-C", str(tmp), "-xf", "-"],
                           input=proc.stdout, capture_output=True, timeout=60)
        except Exception as exc:                              # noqa: BLE001
            print(f"tar extract failed: {exc}", file=sys.stderr)
    return tmp, False


# --------------------------------------------------------------------------
# Applying the verdict: publish marked crops, discard the rest, and clear every
# processed crop from the mirror.
# --------------------------------------------------------------------------
def apply_verdict(args, publish: list[dict], review: list[dict],
                  discard: list[int], local_dir: Path, is_local: bool) -> dict:
    if not publish and not review and not discard:
        return {"ok": True, "published": 0, "reviewed": 0, "discarded": 0,
                "errors": []}
    payload = json.dumps({"publish": publish, "review": review,
                          "discard": discard})

    if is_local:
        repo = Path(local_dir).resolve().parent.parent
        proc = subprocess.run(
            [sys.executable, str(repo / "tools" / "box_publish.py")],
            input=payload, capture_output=True, text=True, timeout=120,
            cwd=str(repo))
    else:
        proc = subprocess.run(
            ["ssh", "-i", args.key, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=accept-new", args.box,
             REMOTE_PY + " " + REMOTE_PUBLISH],
            input=payload, capture_output=True, text=True, timeout=180)

    out = (proc.stdout or "").strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else {
            "ok": False, "error": "no output", "stderr": proc.stderr}
    except Exception:
        return {"ok": False, "error": "unparseable output", "raw": out,
                "stderr": proc.stderr}


def run_once(vid: VehicleIdentifier, args) -> dict:
    import cv2
    import numpy as np

    src, is_local = pull(args)
    metas = sorted(src.glob("*.json"))
    if args.limit:
        metas = metas[:args.limit]

    publish, review, discard = [], [], []
    for jm in metas:
        stem = jm.stem
        jpg_path = jm.with_suffix(".jpg")
        if not jpg_path.exists():
            continue
        try:
            meta = json.loads(jm.read_text(encoding="utf-8"))
        except Exception:
            continue
        try:
            sid = int(meta.get("sighting_id") or stem)
        except (TypeError, ValueError):
            continue

        img = cv2.imdecode(np.frombuffer(jpg_path.read_bytes(), np.uint8),
                           cv2.IMREAD_COLOR)
        if img is None:
            discard.append(sid)            # unreadable: drop it from the box too
            continue

        r = vid.classify(img)
        call = VehicleIdentifier.gov_call(r)
        head_pos = call.get("source") == "head" and call.get("gov")
        hc = call["conf"] if call.get("source") == "head" else None
        marked = (r["vclass"] in MARKED_CLASSES
                  and r["conf"] >= MIN_CONF and r["margin"] >= MIN_MARGIN)
        candidate = r["vclass"] in MARKED_CLASSES or head_pos

        if marked:
            vclass = "police" if r["vclass"] == "police" else "gov"
            publish.append({
                "id": sid, "vclass": vclass, "conf": r["conf"],
                "why": (f"contributor ({meta.get('node_name') or 'a phone'}); "
                        f"clearly-marked {r['vclass']} "
                        f"(conf {r['conf']:.2f}, margin {r['margin']:.2f})")})
            tag = "  -> PUBLISH"
        elif candidate:
            vclass = "police" if r["vclass"] == "police" else "gov"
            review.append({
                "id": sid, "vclass": vclass, "head_conf": hc,
                "node_name": meta.get("node_name"),
                "why": (f"contributor ({meta.get('node_name') or 'a phone'}); "
                        f"{r['vclass']} conf {r['conf']:.2f}"
                        + (f", head {hc:.2f}" if hc is not None else "")
                        + " - needs a human")})
            tag = "  -> REVIEW"
        else:
            discard.append(sid)
            tag = ""

        print(f"  #{sid}: {r['vclass']} conf={r['conf']:.2f} "
              f"margin={r['margin']:.2f}"
              f"{f' head={hc:.2f}' if hc is not None else ''}{tag}"
              f"{'  (dry)' if args.dry_run else ''}")

    if args.dry_run:
        return {"pulled": len(metas), "marked": len(publish),
                "review": len(review), "discarded": len(discard), "dry": True}

    res = apply_verdict(args, publish, review, discard, src, is_local)
    return {"pulled": len(metas), "marked": len(publish),
            "review": len(review), "discarded": len(discard), "box": res}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", default=None,
                    help="ssh target of the public mirror, e.g. root@your-host "
                         "(required unless --inbox is given)")
    ap.add_argument("--key", default=None,
                    help="ssh identity file for the box (e.g. ~/.ssh/id_ed25519)")
    ap.add_argument("--remote", default="/opt/sparrowmap/data/inbox",
                    help="the mirror's inbox path on the box")
    ap.add_argument("--inbox", default=None,
                    help="read a LOCAL inbox dir instead of ssh (staging)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=0,
                    help="process at most N crops this run (0 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="score and print, but neither publish nor delete")
    args = ap.parse_args()
    if not args.inbox and not (args.box and args.key):
        ap.error("give --inbox <dir> for a local mirror, or both --box and --key "
                 "to pull over ssh")

    # Lean: this reloads a ~600MB CLIP per run and fires every few minutes, but
    # the inbox is empty on most runs. Peek first over a cheap ssh, and skip
    # loading the model when there is nothing to score.
    if args.once and not args.inbox:
        try:
            cnt = subprocess.run(
                ["ssh", "-i", args.key, "-o", "ConnectTimeout=15",
                 "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                 args.box, f"ls {args.remote}/*.json 2>/dev/null | wc -l"],
                capture_output=True, text=True, timeout=40).stdout.strip()
            if cnt == "0":
                print(f"inbox empty at {time.strftime('%H:%M:%S')}; nothing to do")
                return
        except Exception as exc:                              # noqa: BLE001
            print(f"inbox peek failed ({exc}); proceeding to a full run",
                  file=sys.stderr)

    print(f"marked-law-enforcement gate: classes {sorted(MARKED_CLASSES)}, "
          f"conf >= {MIN_CONF}, margin >= {MIN_MARGIN}")
    print("loading CLIP...")
    vid = VehicleIdentifier()

    def report(s: dict) -> None:
        box = s.get("box") or {}
        extra = ""
        if not s.get("dry") and box:
            extra = (f"; box published {box.get('published', '?')}, "
                     f"queued {box.get('reviewed', '?')} for review, "
                     f"discarded {box.get('discarded', '?')}"
                     + (f", ERRORS {box['errors']}" if box.get("errors") else ""))
        print(f"pulled {s['pulled']}, {s['marked']} publish, "
              f"{s.get('review', 0)} review, "
              f"{s['discarded']} discarded{' (dry run)' if s.get('dry') else ''}"
              f"{extra} at {time.strftime('%H:%M:%S')}")

    if args.interval and not args.once:
        print(f"polling the box every {args.interval:.0f}s; Ctrl-C to stop")
        while True:
            report(run_once(vid, args))
            time.sleep(args.interval)
    else:
        report(run_once(vid, args))


if __name__ == "__main__":
    main()
