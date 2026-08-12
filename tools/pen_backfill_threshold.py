"""Stamp the head's operating point onto review-pen items that predate it.

    python3 tools/pen_backfill_threshold.py --threshold 0.45475          # dry run
    python3 tools/pen_backfill_threshold.py --threshold 0.45475 --apply

WHY THIS EXISTS
`review_api.head_rejected()` hides a pen item only when it carries BOTH the
head's score and the threshold that score was judged against. Items written
before box_publish.py started recording the threshold have the score and not
the operating point, so they cannot be judged and are shown - which is the
correct failure direction, and also means the fix does nothing for the backlog
until this has run.

Designed to run ON THE PUBLIC BOX, like box_publish.py, and for the same
reason: the box cannot load the trained head (no numpy there), so the number
has to be carried to it. It is stdlib-only for that reason.

⚠️ ONLY SAFE WHILE THE PEN WAS SCORED BY ONE HEAD.
A threshold is meaningful only for the model that produced it. Retraining the
head moves the operating point, and stamping today's number onto scores from a
previous model would silently re-judge them against a ruler they never met. So
this refuses to touch an item that already carries a threshold, and prints the
age span of what it is about to stamp - check that span sits inside the life of
the current head before passing --apply.

It writes nothing except the missing `threshold` key: no crop is moved, nothing
is deleted, and re-running it is a no-op. Undo is to delete the key again.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent / "data" / "review"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, required=True,
                    help="the current head's operating point, from head.status()")
    ap.add_argument("--apply", action="store_true",
                    help="write the change; without it, only report")
    ap.add_argument("--dir", default=str(REVIEW))
    a = ap.parse_args()

    pen = Path(a.dir)
    if not pen.exists():
        print(json.dumps({"ok": False, "error": f"no pen at {pen}"}))
        return

    todo, already, no_score, ages = [], 0, 0, []
    for jp in sorted(pen.glob("*.json")):
        try:
            meta = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        h = meta.get("head")
        if not isinstance(h, dict) or h.get("conf") is None:
            no_score += 1
            continue
        if h.get("threshold") is not None:
            already += 1
            continue
        todo.append((jp, meta))
        if h.get("at"):
            ages.append(float(h["at"]))

    span = ""
    if ages:
        span = (f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(min(ages)))}"
                f" .. {time.strftime('%Y-%m-%d %H:%M', time.localtime(max(ages)))}")

    # What the filter will actually do once these carry a threshold. Reported
    # BEFORE writing, because "how many will this hide" is the only number that
    # decides whether the change is right.
    would_hide = sum(1 for _, m in todo
                     if float((m["head"] or {}).get("conf") or 0) < a.threshold)

    print(json.dumps({
        "pen": str(pen), "items": already + no_score + len(todo),
        "already_stamped": already, "no_head_score": no_score,
        "to_stamp": len(todo), "scored_between": span,
        "threshold": a.threshold,
        "would_be_hidden": would_hide,
        "would_remain_visible": len(todo) - would_hide,
        "applied": bool(a.apply),
    }, indent=1))

    if not a.apply:
        print("\ndry run - nothing written. Re-run with --apply to write.")
        return

    n = 0
    for jp, meta in todo:
        meta["head"]["threshold"] = a.threshold
        meta["head"]["threshold_backfilled_at"] = time.time()
        tmp = jp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        tmp.replace(jp)          # atomic: a reader never sees a half-written file
        n += 1
    print(json.dumps({"ok": True, "stamped": n}))


if __name__ == "__main__":
    main()
