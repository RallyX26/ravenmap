"""Repair spans that were never based on a road.

    python tools/fix_fabricated_spans.py --box root@HOST --key path/to/key
    python tools/fix_fabricated_spans.py --box ... --key ... --apply

WHAT IT REPAIRS
Two kinds of span claim a road that was never checked:

  span_source='aim'   No road was ever matched. span_from_aim pushes a line out
                      along the heading and knows nothing about streets, so
                      with the usual heading of 0 it is a line pointing DUE
                      NORTH matching no street at all - and road_name is empty,
                      because there is no road. 10 of 52 live spans.

  a STALE span        The node has since been moved or re-aimed, so the stored
                      span describes a position the camera no longer occupies.
                      Found on a camera with 10,030 sightings: its span said
                      Hamrick Street while its current aim resolved to nothing.

Both look identical on the map to a correct span: a confident green line on a
public street. That is the problem with them.

HOW IT REPAIRS
Re-runs road.resolve() with the node's CURRENT position and aim - the same call
enrolment makes - and writes what it returns. resolve() now returns None rather
than inventing a line (see road.py), so:

  a road is found  -> the span is corrected
  no road is found -> the span is CLEARED, and the node draws nothing

Clearing is the right answer, not a failure. A camera with no span still counts,
still beats, still contributes sightings; it simply stops asserting a street
nobody verified. "Publishing nothing is recoverable. Publishing a fabricated
road is not."

🚨 IT DOES NOT TOUCH SIGHTINGS. Their positions were computed at ingest and are
stored per row, so past sightings keep the positions they were recorded at.
Repairing a span changes where FUTURE sightings land and what line is drawn -
it does not rewrite history, and it must not: those dots are where the vehicles
actually were according to the camera at the time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

REMOTE = "/opt/sparrowmap"


def ssh(args, cmd: str, timeout: int = 300) -> str:
    r = subprocess.run(
        ["ssh", "-i", args.key, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", args.box, cmd],
        capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and not r.stdout.strip():
        sys.exit(f"ssh failed: {r.stderr[:300]}")
    return r.stdout


# Runs ON the box, where road.py and the database are. Overpass rate-limits
# readily, so this goes one node at a time with a pause - a 429 mid-run would
# produce exactly the "no road found" answer that clears a span, which would
# turn a rate limit into data loss.
REMOTE_SCRIPT = r'''
import json, time, sys
import db, road, nodes as nodes_mod
apply = {APPLY}
c = db.connect()
rows = [dict(r) for r in c.execute(
    "SELECT id,name,lat,lon,heading,fov,reach_m,kind,road_name,span_source,"
    "sightings FROM nodes WHERE span_lat1 IS NOT NULL")]
out = []
for n in rows:
    if n["kind"] == "mobile":
        continue
    stale = False
    if n["span_source"] != "aim":
        # A span is stale if re-resolving from the CURRENT position and aim
        # disagrees about the road. Only checked for named roads - an unnamed
        # way cannot be compared by name.
        continue
    got = None
    err = None
    try:
        got = road.resolve(n["lat"], n["lon"], n["heading"] or 0.0,
                           n["fov"] or 60, n["reach_m"] or 45, online=True)
    except Exception as exc:
        err = str(exc)[:80]
    rec = {"id": n["id"], "name": n["name"], "sightings": n["sightings"],
           "was": n["span_source"], "was_road": n["road_name"],
           "now_road": (got or {}).get("road_name"),
           "now_src": (got or {}).get("source"), "err": err}
    if err:
        rec["action"] = "SKIPPED (lookup failed - try again later)"
    elif got:
        rec["action"] = "corrected"
        if apply:
            (a_lat, a_lon), (b_lat, b_lon) = got["span"]
            c.execute("UPDATE nodes SET span_lat1=?,span_lon1=?,span_lat2=?,"
                      "span_lon2=?,road_name=?,span_source=? WHERE id=?",
                      (a_lat, a_lon, b_lat, b_lon, got["road_name"],
                       got["source"], n["id"]))
    else:
        rec["action"] = "cleared (no road could be verified)"
        if apply:
            c.execute("UPDATE nodes SET span_lat1=NULL,span_lon1=NULL,"
                      "span_lat2=NULL,span_lon2=NULL,road_name=NULL,"
                      "span_source=NULL WHERE id=?", (n["id"],))
    out.append(rec)
    time.sleep(1.5)
if apply:
    c.commit()
print(json.dumps(out))
'''


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    script = REMOTE_SCRIPT.replace("{APPLY}", "True" if a.apply else "False")
    b64 = __import__("base64").b64encode(script.encode()).decode()
    out = ssh(a, f"cd {REMOTE} && echo {b64} | base64 -d | "
                 f"sudo -u sparrow ./.venv/bin/python -")
    try:
        res = json.loads(out.strip().splitlines()[-1])
    except Exception:
        sys.exit(f"could not read the result: {out[:400]}")

    if not res:
        print("no fabricated spans found")
        return
    print(f"{len(res)} fabricated span(s)\n")
    for r in res:
        print(f"  {r['name'][:26]:<26} {r['id']}  sightings={r['sightings']}")
        print(f"      was: {r['was']} / road={r['was_road']!r}")
        print(f"      now: {r['action']}"
              + (f"  -> {r['now_road']} [{r['now_src']}]" if r.get('now_road') else ""))
    if not a.apply:
        print("\ndry run - nothing written. Re-run with --apply.")


if __name__ == "__main__":
    main()
