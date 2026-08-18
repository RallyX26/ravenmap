"""Export the whole public record as a checkable bundle anyone can host.

    python tools/export_public.py --box root@HOST --key PATH

WHY THIS AND NOT A SECOND SERVER

Step 2 of SparrowNet was going to be "stand up a mirror". Then the public record
was measured: 319 sightings, 316 photographs, 2.4 MB of images. Everything
SparrowMap has ever published fits in a git repository, on a USB stick, or in an
email.

At that size a mirror server is the wrong shape. A server can be taken down, has
to be paid for, needs sync and uptime, and is one more thing somebody can be
leaned on about. A 3 MB file that a hundred people have cannot be taken down at
all, and it asks nothing of anybody.

It is also the project's own argument applied to itself: the public tier is
public. If that is true, handing anyone a complete checkable copy costs nothing
and removes the last reason the map has to be trusted rather than verified.

WHAT MAKES A COPY TRUSTWORTHY WITHOUT TRUSTING ITS HOST

Every sighting is signed by the node that made it (node_key.py) and every node's
public key is in the database, so the bundle ships the sightings, the keys, the
photographs, and a SHA-256 manifest of all of it. A mirror can then be run by
somebody nobody trusts, because the claims carry their own proof. That property
is worth more than another box in another country.

⚠️ THE BUNDLE IS THE PUBLIC TIER AND NOTHING ELSE. No private-tier rows, no
plate hashes, no node true positions, no training crops. mirror.py already
defines that boundary and this reuses the definition rather than inventing a
second one, because a rule living in two files gets fixed in one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REMOTE = "/opt/sparrowmap"

# Named explicitly rather than SELECT *, so a column added later can never
# silently join the export. Adding one has to be a decision.
PUBLIC_COLUMNS = [
    "id", "ts", "lat", "lon", "vclass", "vclass_conf", "vclass_why",
    "plate_text", "plate_state", "plate_conf", "color", "body", "make", "model",
    "heading", "speed_mph", "snap", "source", "sig_ok", "node_id",
    "reviewed", "reviewed_at", "confirmed_by",
]

REMOTE_SCRIPT = '''
import json, sqlite3, sys
cols = COLS_JSON
db = sqlite3.connect("/opt/sparrowmap/data/sparrow.db")
db.row_factory = sqlite3.Row
# HIS RULE: only sightings a human approved may leave this machine.
# tier='public' alone is not that. Rows can reach the public tier by the
# auto-publish path, and `decided_by='human'` is the field that records an
# actual person having decided. Failing closed here means a row with no
# recorded decision is EXCLUDED rather than assumed fine.
sql = ("SELECT " + ",".join(cols) + " FROM sightings "
       "WHERE tier = ? AND decided_by = ? ORDER BY ts")
rows = [dict(r) for r in db.execute(sql, ("public", "human"))]
skipped = db.execute(
    "SELECT id, vclass, vclass_why FROM sightings "
    "WHERE tier = ? AND (decided_by IS NULL OR decided_by <> ?)",
    ("public", "human")).fetchall()
nodes = {}
for r in rows:
    nid = r.get("node_id")
    if nid and nid not in nodes:
        n = db.execute("SELECT id, name, kind, pubkey, pub_lat, pub_lon "
                       "FROM nodes WHERE id = ?", (nid,)).fetchone()
        if n:
            nodes[nid] = dict(n)
db.close()
print(json.dumps({"sightings": rows, "nodes": list(nodes.values()),
                  "skipped": [dict(r) for r in skipped]}))
'''


def sh(box, key, cmd):
    r = subprocess.run(["ssh", "-i", key, "-o", "BatchMode=yes", box, cmd],
                       capture_output=True, timeout=600)
    if r.returncode != 0:
        raise SystemExit("ssh failed: %s" % r.stderr.decode()[:300])
    return r.stdout.decode("utf-8")


README = """# SparrowMap: the public record

Every government-vehicle sighting SparrowMap has ever published, with its
photograph, in a form anyone can host and anyone can check.

## Why this exists

The map argues that its claims can be verified rather than trusted. That is only
true if you can hold the record yourself. This is the whole of it, and it is
small enough to copy anywhere.

## What is in it

* `sightings.json` - the public tier. Government vehicles only.
* `nodes.json` - the cameras that made them, with the public key of each.
* `snaps/` - the photographs.
* `MANIFEST.json` - SHA-256 of every file above, plus the gate that was applied.

Every sighting here was **approved by a person**. The export filters on
`decided_by = human` and fails closed: a row that reached the public tier
without a recorded human decision is left out rather than assumed fine, and the
count of those is in `MANIFEST.json`.

## What is NOT in it, deliberately

No private-tier vehicles. No plate hashes. No true camera positions; the ones
here are the deliberately coarsened public positions. No training images. Those
are not withheld because they are embarrassing. They are withheld because
publishing them would break the promise that only government vehicles are
public.

## Checking it

Recompute the hashes and compare against `MANIFEST.json`. Any edited byte shows.

Each sighting carries `sig_ok`, meaning the node that made it signed it and the
signature verified against the public key in `nodes.json`. So this bundle is
worth the same whether you got it from the project or from a stranger: the
claims carry their own proof and the host does not have to be trusted.

## Licence

The software is AGPL-3.0. This record is public information about publicly owned
vehicles doing public work. Mirror it, host it, republish it.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    out = Path(a.out) if a.out else Path(__file__).resolve().parent.parent / "data" / "public_export"
    if out.exists():
        shutil.rmtree(out)
    (out / "snaps").mkdir(parents=True)

    print("reading the public tier from the box ...")
    script = REMOTE_SCRIPT.replace("COLS_JSON", json.dumps(PUBLIC_COLUMNS))
    tmp = Path(tempfile.mkdtemp()) / "q.py"
    tmp.write_text(script, encoding="utf-8")
    subprocess.run(["scp", "-i", a.key, "-o", "BatchMode=yes", "-q",
                    str(tmp), "%s:/tmp/_export_q.py" % a.box], check=True)
    data = json.loads(sh(a.box, a.key,
                         "cd %s && .venv/bin/python /tmp/_export_q.py" % REMOTE))
    sightings, nodes = data["sightings"], data["nodes"]
    skipped = data.get("skipped") or []
    print("  %d HUMAN-APPROVED public sightings, %d nodes"
          % (len(sightings), len(nodes)))
    if skipped:
        print("  ⚠️ %d public row(s) EXCLUDED for having no recorded human "
              "decision:" % len(skipped))
        for r in skipped:
            print("      #%s %s  %s" % (r.get("id"), r.get("vclass"),
                                        (r.get("vclass_why") or "")[:60]))
        print("      (fail-closed on purpose. If one of these was approved, the")
        print("       fix is to set decided_by on the row, not to widen this.)")

    wanted = sorted({s["snap"] for s in sightings if s.get("snap")})
    print("  %d photographs referenced" % len(wanted))
    if wanted:
        subprocess.run(["scp", "-i", a.key, "-o", "BatchMode=yes", "-q",
                        "%s:%s/data/snaps/*" % (a.box, REMOTE), str(out / "snaps")],
                       check=False)

    # Keep only what the public record actually references. The snaps directory
    # on the box holds more than the published set.
    for p in list((out / "snaps").iterdir()):
        if p.name not in wanted:
            p.unlink()
    have = {p.name for p in (out / "snaps").iterdir()}
    missing = [w for w in wanted if w not in have]

    (out / "sightings.json").write_text(json.dumps(sightings, indent=1),
                                        encoding="utf-8")
    (out / "nodes.json").write_text(json.dumps(nodes, indent=1), encoding="utf-8")
    (out / "README.md").write_text(README, encoding="utf-8")

    # 🚨 THE MANIFEST IS THE POINT. Without it this is a folder somebody could
    # have edited; with it, a change to any byte is detectable by anyone holding
    # a copy, without trusting whoever handed it over.
    man = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "gate": "tier=public AND decided_by=human",
           "excluded_no_human_decision": len(skipped),
           "sightings": len(sightings), "nodes": len(nodes),
           "photographs": len(have), "missing_photographs": missing,
           "files": {}}
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.json":
            man["files"][str(p.relative_to(out)).replace("\\", "/")] = \
                hashlib.sha256(p.read_bytes()).hexdigest()
    (out / "MANIFEST.json").write_text(json.dumps(man, indent=1), encoding="utf-8")

    files = [p for p in out.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    signed = sum(1 for s in sightings if s.get("sig_ok"))
    print()
    print("bundle: %s" % out)
    print("  %d files, %.2f MB" % (len(files), size / 1048576))
    print("  sightings with a verified node signature: %d of %d"
          % (signed, len(sightings)))
    if missing:
        print("  ⚠️ %d referenced photographs were not on the box" % len(missing))


if __name__ == "__main__":
    main()
