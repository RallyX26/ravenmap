"""Turn review verdicts into training labels.

A reviewer answering "cop" or "not a cop" is the most valuable signal the project
produces: a human judgement on a real camera's view of a real vehicle, in the
conditions the model actually fails in. But the reviewer app runs on the public
mirror, and a mirror deliberately banks nothing - so the verdict lands in the
box's audit log while the crop it refers to sits unlabelled on the machine that
banked it. The loop was open.

This closes it. It reads the box's review verdicts over the existing SSH admin
channel, matches each one to the locally banked crop by the sighting id that
box_puller wrote into the sidecar, and writes the label:

    review:confirm  ->  label 'police'    (a human said this is a government vehicle)
    review:reject   ->  label 'civilian'  (a human said it is not)

Rejections matter more than confirmations here. They are the hard negatives -
the unmarked SUVs and dark sedans the classifier is most likely to be wrong
about - and there is no other way to collect them.

    python tools/sync_review_labels.py --box root@host --key ~/.ssh/key
    python tools/sync_review_labels.py --box ... --key ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA                       # noqa: E402

BANK = DATA / "training"
# What a verdict means in the label vocabulary the trainer reads.
VERDICT_LABEL = {"review:confirm": "police", "review:reject": "civilian"}


def ssh(args, cmd: str, timeout: int = 60) -> str:
    return subprocess.run(
        ["ssh", "-i", args.key, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", args.box, cmd],
        capture_output=True, text=True, timeout=timeout).stdout


def fetch_verdicts(args) -> list[dict]:
    """Every review verdict on the box, newest first."""
    remote = (
        f"cd {args.remote} && ./.venv/bin/python -c \""
        "import db,json,time;"
        "c=db.connect();"
        f"rows=[dict(r) for r in c.execute("
        f"\\\"SELECT action,target,actor,ts FROM audit WHERE action IN "
        f"('review:confirm','review:reject') AND ts>? ORDER BY ts DESC\\\","
        f"(time.time()-{int(args.days)}*86400,))];"
        "print(json.dumps(rows))\"")
    out = ssh(args, remote, timeout=90).strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else []
    except Exception:
        print(f"could not read verdicts: {out[:300]}", file=sys.stderr)
        return []


def sidecars_by_sighting() -> dict:
    """{sighting_id: sidecar path} for every locally banked crop that carries one."""
    idx = {}
    for jf in BANK.rglob("*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = d.get("sighting_id")
        if sid is not None:
            idx[str(sid)] = jf
    return idx


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--remote", default="/opt/sparrowmap")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--relabel", action="store_true",
                    help="overwrite a label that disagrees (default: leave it)")
    args = ap.parse_args()

    verdicts = fetch_verdicts(args)
    print(f"{len(verdicts)} review verdict(s) on the box in the last "
          f"{args.days:.0f} days")
    if not verdicts:
        return
    idx = sidecars_by_sighting()
    print(f"{len(idx)} locally banked crop(s) carry a sighting id")

    applied = already = missing = conflict = 0
    for v in verdicts:
        want = VERDICT_LABEL.get(v["action"])
        jf = idx.get(str(v["target"]))
        if not want:
            continue
        if not jf:
            missing += 1
            continue
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            missing += 1
            continue
        have = d.get("label")
        if have == want:
            already += 1
            continue
        if have and not args.relabel:
            # A human already said something else about this crop. Two humans
            # disagreeing is a real signal; silently overwriting it is not.
            conflict += 1
            print(f"  #{v['target']}: labelled '{have}' but the review said "
                  f"'{want}' - left alone (use --relabel to overwrite)")
            continue
        if args.dry_run:
            applied += 1
            continue
        d["label"] = want
        d["labelled_at"] = time.time()
        d["labelled_from"] = "rv_review"
        d["labelled_by"] = v.get("actor") or "reviewer"
        d["sampling"] = "review"     # a judgement on what the system published
        jf.write_text(json.dumps(d, indent=1), encoding="utf-8")
        applied += 1

    print(f"\napplied {applied}, already correct {already}, "
          f"no local crop {missing}, conflicts {conflict}"
          f"{' (dry run)' if args.dry_run else ''}")
    if missing:
        print("  'no local crop' is normal for sightings from another person's "
              "camera that this machine never banked.")


if __name__ == "__main__":
    main()
