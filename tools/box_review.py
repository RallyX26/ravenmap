"""The human gate for contributor government-calls, on the public mirror.

The trained head flags a contributor crop as government and box_publish parks it
in data/review/ - off the map - because the head cannot tell a marked patrol car
from an unmarked black SUV. A human confirms the clear ones onto the map and
rejects the rest.

    # what is waiting:
    /opt/sparrowmap/.venv/bin/python tools/box_review.py --list
    # confirm real, clearly-marked government vehicles onto the map:
    /opt/sparrowmap/.venv/bin/python tools/box_review.py --publish 31382 31390
    # reject the ambiguous / civilian ones (crop dropped, row stays private):
    /opt/sparrowmap/.venv/bin/python tools/box_review.py --reject 31383 31384

The crops themselves are at data/review/<id>.jpg; pull them with scp to look.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import box_publish as bp  # noqa: E402


def _load(sid: int) -> dict:
    mp = bp.REVIEW / f"{sid}.json"
    if mp.exists():
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def list_pending() -> None:
    if not bp.REVIEW.exists():
        print("no review pen yet (nothing has been flagged)")
        return
    metas = sorted(bp.REVIEW.glob("*.json"))
    if not metas:
        print("review queue empty")
        return
    print(f"{len(metas)} contribution(s) waiting for a human gate:\n")
    for m in metas:
        sid = m.stem
        d = _load(int(sid))
        head = d.get("head", {})
        age = ""
        if d.get("ts"):
            age = f"  {round((time.time() - d['ts']) / 60)}m ago"
        print(f"  #{sid}  head={head.get('conf')}  "
              f"{d.get('node_name') or 'a phone'}{age}  "
              f"[crop: data/review/{sid}.jpg]")
    print("\nlook at the crops (scp data/review/<id>.jpg), then:")
    print("  box_review.py --publish <ids...>   /   --reject <ids...>")


def _ids(args: list[str]) -> list[int]:
    out = []
    for a in args:
        try:
            out.append(int(a))
        except ValueError:
            print(f"skip {a!r}: not an id")
    return out


def publish(ids: list[int]) -> None:
    for sid in ids:
        d = _load(sid)
        head = d.get("head", {})
        vclass = head.get("vclass") or "gov"
        vclass = "police" if vclass == "police" else "gov"
        bp.publish_one({
            "id": sid, "vclass": vclass, "conf": head.get("conf"),
            "why": (f"contributor ({d.get('node_name') or 'a phone'}); "
                    f"reviewed and confirmed a marked government vehicle")})
        print(f"published #{sid} -> {vclass}")


def reject(ids: list[int]) -> None:
    for sid in ids:
        bp.reject_one(sid)
        print(f"rejected #{sid} (crop dropped, row stays private)")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list":
        list_pending()
    elif args[0] == "--publish":
        publish(_ids(args[1:]))
    elif args[0] == "--reject":
        reject(_ids(args[1:]))
    else:
        print(f"unknown option {args[0]!r}; use --list / --publish / --reject")


if __name__ == "__main__":
    main()
