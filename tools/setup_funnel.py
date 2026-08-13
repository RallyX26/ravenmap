"""Where does setting up a camera actually fail?

    python tools\\setup_funnel.py --box root@HOST --key path\\to\\key
    python tools\\setup_funnel.py --box ... --key ... --days 3

THE NUMBER THIS EXISTS FOR
20 of the first 36 cameras registered and never sent a single heartbeat. That
is the largest number in the project and it was also the least useful, because
"never started" was one undifferentiated bucket: a denied camera permission, a
38 MB model download that gave up on mobile data, and someone who closed the
tab all looked exactly the same from the server. They need completely
different fixes.

The client now reports how far it got - a stage name from a fixed list and a
four-way platform bucket, nothing else - and this reads it back.

⚠️ NODES ENROLLED BEFORE THIS SHIPPED HAVE NO STAGE, and they are counted
separately rather than lumped in as failures. Treating "we were not measuring"
as "they did not get there" would invent a drop-off that never happened.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

STAGES = ["registered", "starting", "camera_granted", "preview",
          "model_loading", "model_ready", "detecting", "posted"]

WHAT_IT_MEANS = {
    "registered":     "made a camera, never pressed Watch",
    "starting":       "pressed Watch - camera permission never came back",
    "camera_granted": "gave permission, preview never appeared",
    "preview":        "saw the preview, detector download never finished",
    "model_loading":  "download started and did not complete (38 MB)",
    "model_ready":    "detector loaded, then stopped before watching",
    "detecting":      "watching - this is success",
    "posted":         "posted a sighting",
}


def ssh(args, cmd: str) -> str:
    return subprocess.run(
        ["ssh", "-i", args.key, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", args.box, cmd],
        capture_output=True, text=True, timeout=90).stdout


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--remote", default="/opt/sparrowmap")
    ap.add_argument("--days", type=float, default=7)
    a = ap.parse_args()

    remote = (
        f"cd {a.remote} && ./.venv/bin/python -c \""
        "import db,json,time;"
        "c=db.connect();"
        f"rows=[dict(r) for r in c.execute("
        f"\\\"SELECT id,name,kind,created,last_beat,sightings,setup_stage,"
        f"setup_platform FROM nodes WHERE created > ?\\\","
        f"(time.time()-{float(a.days)}*86400,))];"
        "print(json.dumps(rows))\"")
    out = ssh(a, remote).strip()
    try:
        rows = json.loads(out.splitlines()[-1]) if out else []
    except Exception:
        print(f"could not read nodes: {out[:300]}", file=sys.stderr)
        sys.exit(2)

    if not rows:
        print("no cameras enrolled in that window")
        return

    unmeasured = [r for r in rows if not r.get("setup_stage")]
    measured = [r for r in rows if r.get("setup_stage")]

    print(f"{len(rows)} camera(s) enrolled in the last {a.days:.0f} days")
    if unmeasured:
        print(f"  {len(unmeasured)} enrolled before setup was instrumented - "
              f"no stage recorded, not counted as failures")
    if not measured:
        print("\nnothing measured yet. The next camera to register will be the "
              "first.")
        return

    print(f"\nof the {len(measured)} measured, furthest point reached:\n")
    counts = {s: 0 for s in STAGES}
    for r in measured:
        if r["setup_stage"] in counts:
            counts[r["setup_stage"]] += 1

    # Survival, not a histogram: "reached at least here" is the question a
    # funnel answers, and per-stage counts alone hide it.
    total = len(measured)
    remaining = total
    for s in STAGES:
        pct = 100.0 * remaining / total if total else 0
        bar = "#" * int(round(pct / 3))
        stuck = counts[s]
        note = f"   <- {stuck} stopped here: {WHAT_IT_MEANS[s]}" if stuck else ""
        print(f"  {s:<15} {remaining:>3} ({pct:5.1f}%) {bar}{note}")
        remaining -= stuck

    got_going = counts["detecting"] + counts["posted"]
    print(f"\n  reached 'watching': {got_going} of {total} "
          f"({100.0*got_going/total:.0f}%)")

    plats = {}
    for r in measured:
        p = r.get("setup_platform") or "unknown"
        d = plats.setdefault(p, {"n": 0, "ok": 0})
        d["n"] += 1
        if r["setup_stage"] in ("detecting", "posted"):
            d["ok"] += 1
    if len(plats) > 1:
        print("\n  by platform:")
        for p, d in sorted(plats.items(), key=lambda kv: -kv[1]["n"]):
            print(f"    {p:<9} {d['ok']:>3}/{d['n']:<3} reached watching")

    # The honest caveat, printed rather than remembered.
    if total < 20:
        print(f"\n  ⚠️ {total} measured is too few to read a pattern into. "
              f"Wait for more before changing anything.")


if __name__ == "__main__":
    main()
