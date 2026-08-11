"""Phase 2: score the public mirror's phone crops on the home node.

A public phone contributor's crop lands on the mirror, which cannot tell a
patrol car from a minivan - it has no GPU and no trained head. The mirror parks
the crop, already destroyed below plate legibility, in its inbox
(mirror.quarantine_write). This runs on the HOME node, where the head lives:

    pull the mirror's inbox  ->  CLIP + trained head on each crop
      head says government    ->  create a home review item (private, unpublished)
      head says ordinary      ->  discard
    delete every pulled crop from the mirror either way

Nothing is published. A government call only puts the crop in the operator
review queue at home, where a human confirms it before it reaches any map -
the same human-confirmed rule the rest of the project runs on. The mirror grows
no HTTP surface for this: the crop is pulled over the existing SSH admin channel
and deleted, so a mirror breach still yields only the public record.

    # against your mirror, over ssh:
    python -m detect.box_puller --once \
      --box root@your-mirror-host --key ~/.ssh/your_key
    # against a local staging mirror's inbox (no ssh):
    python -m detect.box_puller --once --inbox /path/to/mirror/data/inbox
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                       # noqa: E402
import snapshot                                 # noqa: E402
from detect import bank                         # noqa: E402
from detect import head as _head                # noqa: E402
from detect.vehicle_id import VehicleIdentifier  # noqa: E402

RELAY_NODE = "box-relay"


# --------------------------------------------------------------------------
# Pulling: either a local staging dir, or the live box over ssh.
# --------------------------------------------------------------------------
def _ssh(key: str, box: str, remote_cmd: str) -> str:
    return subprocess.run(
        ["ssh", "-i", key, "-o", "ConnectTimeout=15",
         "-o", "StrictHostKeyChecking=accept-new", box, remote_cmd],
        capture_output=True, text=True, timeout=60).stdout


def _scp(key: str, box: str, remote: str, local: Path) -> None:
    subprocess.run(
        ["scp", "-i", key, "-o", "ConnectTimeout=15", "-r",
         f"{box}:{remote}/.", str(local)],
        capture_output=True, text=True, timeout=120, check=False)


def pull(args) -> tuple[Path, bool]:
    """Return (dir holding the crops, is_local). For a remote box the crops are
    scp'd into a temp dir; for --inbox they are read in place."""
    if args.inbox:
        return Path(args.inbox), True
    tmp = Path(tempfile.mkdtemp(prefix="box_inbox_"))
    _scp(args.key, args.box, args.remote, tmp)
    return tmp, False


def delete_remote(args, stems: list[str], local_dir: Path, is_local: bool) -> None:
    """Delete processed crops from wherever they came from."""
    if not stems:
        return
    if is_local:
        for s in stems:
            (local_dir / f"{s}.jpg").unlink(missing_ok=True)
            (local_dir / f"{s}.json").unlink(missing_ok=True)
        return
    files = " ".join(f"{args.remote}/{s}.jpg {args.remote}/{s}.json" for s in stems)
    _ssh(args.key, args.box, f"rm -f {files}")


# --------------------------------------------------------------------------
# Scoring + landing a review item at home.
# --------------------------------------------------------------------------
def _relay_to_review(stem: str, jpg: bytes, meta: dict, r: dict,
                     head_conf: float) -> int:
    """Create a home review item for one government-classified public crop.

    Reuses the exact machinery a phone node's own crop uses: the crop is stored
    as a sub-resolution snapshot (for the review card) and banked with the head
    verdict written into its sidecar (what the review queue's head-first gate
    reads). The sighting is private and unreviewed, so it lands in "possibly
    missed", never on the map, until a human lifts it.
    """
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    snap_meta = {"ts": meta.get("ts") or time.time(), "node_id": RELAY_NODE,
                 "tier": "private", "vclass": r["vclass"], "watermark": "RELAY"}
    snap = snapshot.store_subresolution(data_url, snap_meta)

    banked = bank.bank_remote(jpg, RELAY_NODE, {
        "ts": meta.get("ts") or time.time(),
        "cls_name": meta.get("body") or "car",
        "det_conf": meta.get("det_conf")})
    if banked:
        sc = bank.sidecar(banked)
        if sc:
            d = json.loads(sc.read_text(encoding="utf-8"))
            d["clip"] = {"vclass": r["vclass"], "conf": r["conf"],
                         "margin": r["margin"], "scores": r["scores"],
                         "head_conf": head_conf, "head_gov": True,
                         "by": "box_puller", "at": time.time()}
            sc.write_text(json.dumps(d, indent=1), encoding="utf-8")

    rec = {
        "node_id": RELAY_NODE, "ts": float(meta.get("ts") or time.time()),
        "lat": meta.get("pub_lat"), "lon": meta.get("pub_lon"),
        "tier": "private", "vclass": r["vclass"], "vclass_conf": head_conf,
        "vclass_why": (f"relayed from the public map ({meta.get('node_name') or 'a phone'}); "
                       f"the home head scored it {r['vclass']} at {head_conf:.2f}"),
        "body": meta.get("body"), "snap": snap, "source": "box_relay",
        "sig_ok": 0, "bank_ref": banked,
    }
    return db.insert_sighting(rec)


def run_once(vid: VehicleIdentifier, args) -> dict:
    import cv2
    import numpy as np

    src, is_local = pull(args)
    metas = sorted(src.glob("*.json"))
    hthr = _head.threshold() if _head.available() else None
    done, gov, discarded = [], 0, 0

    for jm in metas:
        stem = jm.stem
        jpg_path = jm.with_suffix(".jpg")
        if not jpg_path.exists():
            continue
        try:
            meta = json.loads(jm.read_text(encoding="utf-8"))
        except Exception:
            continue
        jpg = jpg_path.read_bytes()

        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            done.append(stem)          # unreadable: drop it from the box too
            continue
        r = vid.classify(img)
        call = VehicleIdentifier.gov_call(r)
        is_gov = (call.get("source") == "head" and call.get("gov")) or (
            call.get("source") != "head"
            and r["vclass"] in ("police", "gov_dot", "emergency"))
        hc = call["conf"] if call.get("source") == "head" else None

        mark = ""
        if is_gov:
            if not args.dry_run:
                sid = _relay_to_review(stem, jpg, meta, r,
                                       hc if hc is not None else r["conf"])
                mark = f"  -> REVIEW #{sid}"
            else:
                mark = "  -> REVIEW (dry)"
            gov += 1
        else:
            discarded += 1
        done.append(stem)
        print(f"  {stem}: {r['vclass']} conf={r['conf']:.2f} "
              f"margin={r['margin']:.2f}"
              f"{f' head={hc:.2f}' if hc is not None else ''}{mark}")

    if not args.dry_run:
        delete_remote(args, done, src, is_local)
    return {"pulled": len(metas), "gov": gov, "discarded": discarded}


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
    ap.add_argument("--dry-run", action="store_true",
                    help="score and print, but neither relay nor delete")
    args = ap.parse_args()
    if not args.inbox and not (args.box and args.key):
        ap.error("give --inbox <dir> for a local mirror, or both --box and --key "
                 "to pull over ssh")

    st = _head.status()
    print("trained head:", "ready" if st["ok"] else f"UNAVAILABLE ({st['why']})",
          f"(threshold {st['threshold']:.2f})" if st["ok"] else
          "- falling back to zero-shot")
    print("loading CLIP...")
    vid = VehicleIdentifier()

    if args.interval and not args.once:
        print(f"polling the box every {args.interval:.0f}s; Ctrl-C to stop")
        while True:
            s = run_once(vid, args)
            if s["pulled"]:
                print(f"pulled {s['pulled']}, {s['gov']} to review, "
                      f"{s['discarded']} discarded at {time.strftime('%H:%M:%S')}")
            time.sleep(args.interval)
    else:
        s = run_once(vid, args)
        print(f"done: pulled {s['pulled']}, {s['gov']} to review, "
              f"{s['discarded']} discarded"
              f"{' (dry run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
