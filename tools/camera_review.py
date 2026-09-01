"""Review the public 'this ALPR camera is gone / still here' reports and decide
which cameras the map should stop showing.

Every report is already proximity-gated (the reporter's GPS had to be at the
camera), so a report is real evidence - but one person can still be wrong or
determined, so taking a camera OFF the map stays a human call. This is that
human step: list the pending reports, then confirm a removal or keep a camera.

    python tools/camera_review.py --box root@HOST --key PATH            # list
    python tools/camera_review.py --box .. --key .. --remove n123456    # take off
    python tools/camera_review.py --box .. --key .. --keep   n123456    # keep / undo

It edits the box's data/cameras_removed.json in place (the box serves from it)
and stamps the matching reports reviewed, so /api/cameras reflects the verdict
on the next request - no restart, no file to copy.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _shq(py: str) -> str:
    return "'" + py.replace("'", "'\\''") + "'"


def _box_py(box: str, key: str, code: str) -> str:
    r = subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         box, "/opt/sparrowmap/.venv/bin/python -c " + _shq(code)],
        capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise SystemExit("box error: %s" % r.stderr.decode("utf-8", "replace")[:300])
    return r.stdout.decode("utf-8", "replace")


LIST_CODE = r'''
import json, os, glob, collections
pen = "/opt/sparrowmap/data/camera_status_pen"
rm = set()
try: rm = set(json.load(open("/opt/sparrowmap/data/cameras_removed.json")))
except Exception: pass
by = collections.defaultdict(lambda: {"removed":0,"present":0,"note":"","lat":None,"lon":None,"pending":False})
for f in glob.glob(os.path.join(pen, "*.json")):
    try: d = json.load(open(f))
    except Exception: continue
    e = by[d.get("id")]
    e[d.get("kind","?")] = d.get("votes",1)
    if d.get("note"): e["note"] = d["note"]
    e["lat"], e["lon"] = d.get("lat"), d.get("lon")
    if d.get("reviewed") is None: e["pending"] = True
rows = [{"id":k, **v, "on_map": k not in rm} for k,v in by.items()]
print(json.dumps({"reports": rows, "removed_count": len(rm)}))
'''


def _verdict_code(oid: str, remove: bool) -> str:
    return r'''
import json, os, glob
RM = "/opt/sparrowmap/data/cameras_removed.json"
oid = %r
remove = %s
try: cur = set(json.load(open(RM)))
except Exception: cur = set()
if remove: cur.add(oid)
else: cur.discard(oid)
json.dump(sorted(cur), open(RM, "w"))
# stamp the matching reports reviewed so they leave the pending list.
pen = "/opt/sparrowmap/data/camera_status_pen"
n = 0
for f in glob.glob(os.path.join(pen, oid + "_*.json")):
    try:
        d = json.load(open(f)); d["reviewed"] = "removed" if remove else "kept"
        json.dump(d, open(f, "w"), indent=1); n += 1
    except Exception: pass
print(json.dumps({"on_map": oid not in cur, "reports_stamped": n, "removed_total": len(cur)}))
''' % (oid, "True" if remove else "False")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--remove", metavar="OSMID", help="take this camera off the map")
    ap.add_argument("--keep", metavar="OSMID", help="keep it (or undo a removal)")
    a = ap.parse_args()

    if a.remove or a.keep:
        oid = a.remove or a.keep
        res = json.loads(_box_py(a.box, a.key, _verdict_code(oid, bool(a.remove))))
        state = "ON the map" if res["on_map"] else "OFF the map (removed)"
        print(f"{oid}: now {state}. stamped {res['reports_stamped']} report(s). "
              f"{res['removed_total']} cameras removed total.")
        return

    data = json.loads(_box_py(a.box, a.key, LIST_CODE))
    rows = sorted(data["reports"], key=lambda r: (-r["removed"], -r["present"]))
    print(f"{len(rows)} camera(s) with reports  |  {data['removed_count']} "
          f"currently removed from the map\n")
    if not rows:
        print("  (no reports yet)")
        return
    print("  osm_id            removed  present  onMap  note")
    for r in rows:
        flag = "*" if r["pending"] else " "
        print(f" {flag}{r['id']:16} {r['removed']:7} {r['present']:8} "
              f"{'yes' if r['on_map'] else 'no ':5}  {r['note'][:40]}")
    print("\n  * = has unreviewed reports. "
          "Confirm with --remove <osm_id> or --keep <osm_id>.")


if __name__ == "__main__":
    main()
