"""Put the photograph back on a published sighting that lost it.

🚨 WHY THIS EXISTS. For one afternoon the box could not store an image at all:
`snapshot.store_crop` needs cv2 to strip the background, the mirror is
Pillow-and-stdlib on purpose, and the ImportError became a ValueError that every
caller swallowed. `/api/node/confirm` answered `{"ok": true}` and wrote
`snap=None`. So there are public rows on the map, confirmed by a human, with
nothing behind them - which is both less useful and less checkable, and the
operator noticed the way anyone would: he clicked one and there was no picture.

The fix (isolate_pil, 43f2dfd) only helps the NEXT confirmation. These rows are
already written. But a camera-node confirmation carries `bank_ref` - the stem of
the training crop the reviewer was actually looking at - and that crop is still
on the home machine. So the photograph is not lost, it just never made the trip.

    python tools/backfill_snaps.py --box root@HOST --key path/to/key --dry-run
    python tools/backfill_snaps.py --box root@HOST --key path/to/key

WHAT IT WILL AND WILL NOT TOUCH

  * PUBLIC tier only. A private-tier row's image is withheld deliberately.
  * `snap IS NULL` only. It never replaces a photograph that is already there.
  * `bank_ref` must resolve to a real crop on THIS machine. No bank_ref, no
    backfill - there is nothing to send and inventing one is not an option.

🚨 THE IMAGE IS PREPARED BY THE BOX, NOT BY THIS TOOL. It ships the crop and
calls the box's own `downscale_to_subres` + `store_subresolution`, which is the
same pair `/api/node/confirm` runs. Doing the resize here and writing the file
directly would mean the published photograph never passed the sub-resolution
guard or the background strip - a backfill that quietly opts out of the privacy
controls is worse than a missing picture. If the box refuses, this reports the
refusal and moves on; it does not fall back to anything.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BANK = ROOT / "data" / "training"

# Run on the box, one sighting at a time, over stdin. Kept deliberately small
# and readable: this writes to the live public database.
REMOTE = r'''
import base64, json, sqlite3, sys
sys.path.insert(0, "/opt/sparrowmap")
import snapshot

payload = json.load(sys.stdin)
sid, b64, meta = payload["id"], payload["b64"], payload["meta"]

con = sqlite3.connect("/opt/sparrowmap/data/sparrow.db")
con.row_factory = sqlite3.Row
row = con.execute("select id, tier, snap from sightings where id=?", (sid,)).fetchone()
if row is None:
    print(json.dumps({"id": sid, "ok": False, "why": "no such sighting"})); raise SystemExit
if row["tier"] != "public":
    print(json.dumps({"id": sid, "ok": False, "why": f"tier={row['tier']}, not public"})); raise SystemExit
if row["snap"]:
    print(json.dumps({"id": sid, "ok": False, "why": "already has a photograph"})); raise SystemExit

try:
    small = snapshot.downscale_to_subres("data:image/jpeg;base64," + b64)
    name = snapshot.store_subresolution(
        "data:image/jpeg;base64," + base64.b64encode(small).decode(), meta)
except Exception as exc:
    print(json.dumps({"id": sid, "ok": False, "why": f"box refused: {exc}"})); raise SystemExit

con.execute("update sightings set snap=? where id=? and snap is null", (name, sid))
con.commit()
print(json.dumps({"id": sid, "ok": True, "snap": name,
                  "isolation": meta.get("_isolation")}))
'''

FIND = r'''
import json, sqlite3
con = sqlite3.connect("/opt/sparrowmap/data/sparrow.db")
con.row_factory = sqlite3.Row
rows = con.execute(
    "select id, ts, node_id, bank_ref, vclass from sightings "
    "where tier='public' and snap is null and bank_ref is not null "
    "order by ts desc limit 200").fetchall()
names = {r["id"]: (r["name"] or "a camera") for r in
         con.execute("select id, name from nodes")}
print(json.dumps([dict(r, node_name=names.get(r["node_id"], "a camera"))
                  for r in rows]))
'''


def _ssh(box: str, key: str, script: str, stdin: str = "") -> str:
    out = subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", box,
         _remote_cmd(script)],
        input=stdin, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"ssh failed: {out.stderr.strip()[:400]}")
    return out.stdout.strip()


def _remote_cmd(script: str) -> str:
    """Ship the script base64-encoded.

    ⚠️ NOT interpolated into the command line. `python -c "<script>"` puts a
    multi-line program through ssh's shell AND the local shell, and every
    newline, quote and backslash gets a chance to be re-read by one of them -
    the first version of this arrived on the box as one line beginning `\\n` and
    failed to parse. Base64 has no characters either shell cares about.
    """
    blob = base64.b64encode(script.encode()).decode()
    return ("/opt/sparrowmap/.venv/bin/python -c "
            f"'exec(__import__(\"base64\").b64decode(\"{blob}\").decode())'")


def _run_remote(box: str, key: str, script: str, payload: dict) -> dict:
    """Run the script on the box with its JSON payload on stdin."""
    proc = subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", box,
         _remote_cmd(script)],
        input=json.dumps(payload), capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "why": f"ssh failed: {proc.stderr.strip()[:300]}"}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "why": f"unreadable reply: {proc.stdout[:200]!r}"}


def _crop_for(stem: str) -> Path | None:
    """The banked crop this sighting was confirmed from, if it is still here."""
    hits = sorted(BANK.glob(f"*/{stem}.jpg"))
    return hits[-1] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", required=True, help="ssh target, e.g. root@host")
    ap.add_argument("--key", required=True, help="path to the ssh key")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would be sent and send nothing")
    ap.add_argument("--only", type=int, nargs="*",
                    help="restrict to these sighting ids")
    a = ap.parse_args()

    print("asking the box which public rows have no photograph...")
    rows = json.loads(_ssh(a.box, a.key, FIND) or "[]")
    if a.only:
        rows = [r for r in rows if r["id"] in set(a.only)]
    if not rows:
        print("nothing to do - every public sighting with a bank_ref has a photo.")
        return 0

    print(f"{len(rows)} candidate(s):")
    todo = []
    for r in rows:
        crop = _crop_for(r["bank_ref"])
        state = str(crop.relative_to(ROOT)) if crop else "❌ crop not on this machine"
        print(f"  {r['id']}  {r['vclass']:8}  bank={r['bank_ref']}  {state}")
        if crop:
            todo.append((r, crop))

    if not todo:
        print("\nno crops available here; nothing can be backfilled.")
        return 1
    if a.dry_run:
        print(f"\n--dry-run: would send {len(todo)}. Nothing was changed.")
        return 0

    ok = 0
    for r, crop in todo:
        meta = {"ts": r["ts"], "node_id": r["node_id"],
                "node_name": r.get("node_name") or "a camera",
                "tier": "public", "vclass": r["vclass"],
                "watermark": "CONFIRMED"}
        res = _run_remote(a.box, a.key, REMOTE, {
            "id": r["id"],
            "b64": base64.b64encode(crop.read_bytes()).decode(),
            "meta": meta})
        if res.get("ok"):
            ok += 1
            print(f"  ✅ {r['id']} -> {res['snap']} "
                  f"(isolation={res.get('isolation')})")
        else:
            print(f"  ❌ {r['id']}: {res.get('why')}")

    print(f"\n{ok}/{len(todo)} backfilled.")
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
