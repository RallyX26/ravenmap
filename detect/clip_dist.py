"""What does CLIP zero-shot actually say about ordinary traffic?

Written because `visual_police` was allowed to publish a sighting on its own,
on the reasoning that a trained model is better evidence than a hand-written
colour rule. That reasoning was half right: CLIP IS better, and it is still not
good enough to publish alone, because it is being used ZERO-SHOT on a task it
was never trained for. Its prior appears to be roughly "dark sedan or SUV =
police", which on a residential street is a description of most of the traffic.

This prints the distribution of CLIP's police score over every banked vehicle,
so the bar for publishing can be set from the data rather than from an opinion.

    python detect\\clip_dist.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402


def main() -> None:
    rows = []
    for f in (DATA / "training").rglob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        c = d.get("clip") or {}
        if c.get("scores"):
            rows.append((float(c["scores"].get("police", 0.0)),
                         c.get("vclass"), float(c.get("conf") or 0),
                         float(c.get("margin") or 0), d.get("label"), f.stem))
    if not rows:
        raise SystemExit("nothing banked yet - run the node for a while")
    rows.sort(reverse=True)

    print(f"{len(rows)} banked vehicles\n")
    print(f"{'P(police)':>9}  {'clip_says':<10} {'conf':>6} {'margin':>7}  {'label':<9} file")
    for r in rows[:20]:
        print(f"{r[0]:>9.3f}  {r[1] or '-':<10} {r[2]:>6.3f} {r[3]:>7.3f}  "
              f"{str(r[4] or 'unlabelled'):<9} {r[5]}")

    pol = [r for r in rows if r[1] == "police"]
    print(f"\nCLIP called POLICE on {len(pol)} of {len(rows)} vehicles "
          f"({100 * len(pol) / len(rows):.0f}%)")
    if pol:
        print(f"  their confidences: {min(p[2] for p in pol):.3f} .. "
              f"{max(p[2] for p in pol):.3f}")
        print(f"  their margins    : {min(p[3] for p in pol):.3f} .. "
              f"{max(p[3] for p in pol):.3f}")
    print("\nOn a residential street the true rate of police vehicles is on the")
    print("order of a fraction of a percent. Any figure far above that is the")
    print("model's prior talking, not the traffic.")


if __name__ == "__main__":
    main()
