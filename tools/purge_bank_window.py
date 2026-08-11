"""Drop training crops written while more than one detector was running.

On 2026-08-08 four detector processes ran against the same camera at once (see
detect.run_live.claim_single_instance). Each independently tracked, cropped and
banked the same vehicles, so the bank contains several near-identical crops of
one car under different track ids.

That is a worse problem for training than it looks. Near-duplicates are not
extra data - they are the same data counted twice, which:

  * over-weights whatever happened to drive past during the overlap, and
  * puts near-identical images on BOTH sides of a train/test split, which
    inflates validation scores by letting the model recognise a picture it has
    effectively already seen. A model that looks good for that reason is the
    same class of mistake as the classifier this project just had to retract.

They cannot be de-duplicated reliably by content hash, because they are
different frames of the same pass rather than identical files. So the honest
move is to drop the whole contaminated window: the node is running and will
re-bank clean data within the hour, which is much cheaper than reasoning about
a quietly poisoned dataset for weeks.

    python tools\\purge_bank_window.py --from 15:05 --to 15:54 --day 2026-08-08
    python tools\\purge_bank_window.py ... --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402


def mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="YYYY-MM-DD folder under data/training")
    ap.add_argument("--from", dest="start", required=True, help="HH:MM inclusive")
    ap.add_argument("--to", dest="end", required=True, help="HH:MM exclusive")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    d = DATA / "training" / args.day
    if not d.is_dir():
        raise SystemExit(f"no such day: {d}")
    lo, hi = mins(args.start), mins(args.end)

    doomed = []
    for f in sorted(d.glob("*.jpg")):
        stamp = f.stem.split("_")[0]            # HHMMSS
        try:
            t = int(stamp[:2]) * 60 + int(stamp[2:4])
        except ValueError:
            continue
        if lo <= t < hi:
            doomed.append(f)

    kept = len(list(d.glob("*.jpg"))) - len(doomed)
    print(f"{args.day}  {args.start}-{args.end}")
    print(f"  dropping {len(doomed)} crops, keeping {kept}")
    if not args.apply:
        print("  [--apply to write]")
        return
    for f in doomed:
        f.unlink(missing_ok=True)
        f.with_suffix(".json").unlink(missing_ok=True)
    print(f"  dropped {len(doomed)} crops and their sidecars")


if __name__ == "__main__":
    main()
