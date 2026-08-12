"""Put government sightings that never reached the review pen into it.

The pen is filled at ingest, so anything the camera caught BEFORE that path
existed - or while it was broken - is stranded: a private, unreviewed row on the
mirror with no crop to judge it by, because a mirror keeps no private-tier
imagery. The picture is not lost, though. The node banked its own copy locally
when it made the call, so this matches those crops back to the rows by
timestamp and parks them in the pen where the reviewer app can see them.

Run it on the machine that owns the camera (the bank lives there):

    python tools/backfill_pen.py --box root@your-host --key ~/.ssh/your_key
    python tools/backfill_pen.py --box ... --key ... --hours 48 --dry-run
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

import snapshot                          # noqa: E402
from core import DATA                    # noqa: E402

BANK = DATA / "training"
GOV = ("police", "gov_dot")
# A banked crop and its posted row share a timestamp, but not to the microsecond
# (the node stamps once and posts a moment later), so match within a window.
MATCH_S = 3.0


def ssh(args, cmd: str, stdin: str | None = None, timeout: int = 60) -> str:
    p = subprocess.run(
        ["ssh", "-i", args.key, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", args.box, cmd],
        input=stdin, capture_output=True, text=True, timeout=timeout)
    return p.stdout


def unreviewed_gov(args) -> list[dict]:
    """Government rows on the box with no verdict and nothing in the pen."""
    remote = (
        f"cd {args.remote} && ./.venv/bin/python -c \""
        "import db,json,os,time;"
        "c=db.connect();"
        "pen=set();"
        "d='data/review';"
        "pen={f.split('.')[0] for f in os.listdir(d)} if os.path.isdir(d) else set();"
        f"rows=[dict(r) for r in c.execute(\\\"SELECT id,ts,vclass,node_id FROM sightings "
        f"WHERE vclass IN ('police','gov_dot') AND reviewed IS NULL AND ts>?\\\","
        f"(time.time()-{int(args.hours)}*3600,))];"
        "print(json.dumps([r for r in rows if str(r['id']) not in pen]))\"")
    out = ssh(args, remote, timeout=90).strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else []
    except Exception:
        print(f"could not read the box's rows: {out[:300]}", file=sys.stderr)
        return []


def local_gov_crops(hours: float) -> list[tuple[float, Path]]:
    """(ts, jpg) for every locally banked crop this node called government."""
    cut = time.time() - hours * 3600
    out = []
    for jf in BANK.rglob("*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = float(d.get("ts") or 0)
        if ts < cut:
            continue
        if (d.get("clip") or {}).get("vclass") not in GOV:
            continue
        jpg = jf.with_suffix(".jpg")
        if jpg.exists():
            out.append((ts, jpg))
    out.sort()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", required=True, help="ssh target of the mirror")
    ap.add_argument("--key", required=True, help="ssh identity file")
    ap.add_argument("--remote", default="/opt/sparrowmap")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = unreviewed_gov(args)
    print(f"{len(rows)} unreviewed government row(s) on the box, none in the pen")
    if not rows:
        return
    crops = local_gov_crops(args.hours)
    print(f"{len(crops)} locally banked government crop(s) to match against")

    staged, missed = [], 0
    for r in rows:
        ts = float(r["ts"])
        best, bestd = None, MATCH_S
        for cts, jpg in crops:
            d = abs(cts - ts)
            if d < bestd:
                best, bestd = jpg, d
        if not best:
            missed += 1
            continue
        try:
            raw = best.read_bytes()
            url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
            small = snapshot.downscale_to_subres(url)
        except Exception as exc:
            print(f"  #{r['id']}: could not prepare the crop ({exc})")
            missed += 1
            continue
        staged.append((r, small))
        print(f"  #{r['id']}  {r['vclass']}  matched {best.name} "
              f"({bestd:.1f}s apart)")

    print(f"\n{len(staged)} to park, {missed} with no matching crop")
    if args.dry_run or not staged:
        print("(dry run)" if args.dry_run else "")
        return

    # Ship them in one payload; the box writes them into its own pen.
    payload = json.dumps([{  # base64 so it survives the ssh pipe as text
        "id": r["id"], "ts": r["ts"], "vclass": r["vclass"],
        "node_id": r.get("node_id") or "",
        "jpg_b64": base64.b64encode(b).decode()} for r, b in staged])
    remote = (
        f"cd {args.remote} && ./.venv/bin/python -c \""
        "import sys,json,base64,mirror;"
        "items=json.load(sys.stdin);"
        "n=0;\n"
        "for it in items:\n"
        "    mirror.review_write(it['id'], base64.b64decode(it['jpg_b64']), "
        "{'ts': it['ts'], 'node_id': it['node_id'], 'vclass': it['vclass'], "
        "'node_name': 'backfilled', 'score': None});\n"
        "    n+=1\n"
        "print(json.dumps({'parked': n}))\"")
    out = ssh(args, remote, stdin=payload, timeout=180).strip()
    print("box:", out or "(no output)")


if __name__ == "__main__":
    main()
