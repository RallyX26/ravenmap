"""Revoke a camera whose key is in someone else's hands.

    python tools/revoke_node.py --box root@HOST --key PATH --node n_xxxxxxxx
    python tools/revoke_node.py --box ... --key ... --node n_xxxxxxxx --yes

WHY THIS EXISTS

Written 2026-08-19 for a real case: a contributor in Champaign-Urbana asked for
their node to be removed because the police had taken the phone, and the phone
holds the node's key.

That is not the same as a camera going dead, and using the retire path for it
would be wrong. `retire_dead_cams.py` sets `status='paused'`, which means "this
camera stopped answering" - it is reversible bookkeeping about hardware. A
seized key is an ACTIVE CREDENTIAL IN A STRANGER'S POCKET, and the thing that
matters is that it can never post again.

WHAT THIS DOES, AND WHY EACH PART

* `status='revoked'` - the hub refuses any post from a node that is not
  `active` (four separate endpoints check it), and `db.nodes(active_only=True)`
  filters on `status='active'`, so the camera also leaves the public map. No
  code anywhere branches on a specific status VALUE, only on whether it equals
  'active', so a new value is safe and says the true thing.
* `token = NULL` - belt and braces. Status alone would be enough while nobody
  flips it back, but a compromised credential should stop existing, not stop
  being honoured.

🚨 THE PUBLIC KEY IS DELIBERATELY KEPT. Nodes sign their sightings and the
signature is verified against the pubkey stored here, so deleting it would make
this person's HONEST past sightings unverifiable - it would look like they had
been faked. Revoking the ability to post is the goal; rewriting the past is not.

⚠️ SIGHTINGS ARE LEFT ALONE BY DEFAULT and that is a decision, not laziness.
Removing a camera and deleting its history are different requests, and the
second one can destroy a public record somebody else is relying on. Ask, then
use --purge-sightings if that is genuinely what was asked for.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REMOTE = "/opt/sparrowmap"

REMOTE_OP = r'''
import json, sqlite3, sys, time
node_id = NODE_JSON
do_it = DO_IT
purge = PURGE
db = sqlite3.connect("/opt/sparrowmap/data/sparrow.db")
db.row_factory = sqlite3.Row
n = db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
if not n:
    print(json.dumps({"error": "no such node: %s" % node_id})); sys.exit(0)
row = dict(n)
sight = db.execute("SELECT COUNT(*) c, SUM(tier='public') pub, SUM(snap IS NOT NULL) snaps "
                   "FROM sightings WHERE node_id = ?", (node_id,)).fetchone()
out = {
    "id": row.get("id"), "name": row.get("name"), "kind": row.get("kind"),
    "status": row.get("status"), "has_token": bool(row.get("token")),
    "has_pubkey": bool(row.get("pubkey")),
    "sightings": sight["c"], "public": sight["pub"] or 0, "with_photo": sight["snaps"] or 0,
}
if not do_it:
    print(json.dumps({"preview": out})); sys.exit(0)

# Keep the row we are about to change, so this is reversible if it was a mistake.
bak = "/opt/sparrowmap/data/revoked_%s_%d.json" % (node_id, int(time.time()))
open(bak, "w").write(json.dumps({k: (v if isinstance(v, (int, float, str, type(None))) else str(v))
                                 for k, v in row.items()}, indent=1))

db.execute("UPDATE nodes SET status = 'revoked', token = NULL WHERE id = ?", (node_id,))
purged = 0
if purge:
    purged = db.execute("SELECT COUNT(*) FROM sightings WHERE node_id = ?", (node_id,)).fetchone()[0]
    db.execute("DELETE FROM sightings WHERE node_id = ?", (node_id,))
db.commit()
after = db.execute("SELECT status, token IS NOT NULL AS t FROM nodes WHERE id = ?", (node_id,)).fetchone()
out.update({"now_status": after["status"], "now_has_token": bool(after["t"]),
            "backup": bak, "purged": purged})
try:
    sys.path.insert(0, "/opt/sparrowmap")
    import db as _db
    _db.audit("node:revoked", node_id, actor="operator")
except Exception as exc:
    out["audit_error"] = str(exc)
print(json.dumps({"done": out}))
'''


def sh(box: str, key: str, cmd: str) -> str:
    r = subprocess.run(["ssh", "-i", key, "-o", "BatchMode=yes", box, cmd],
                       capture_output=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit("ssh failed: %s" % r.stderr.decode()[:300])
    return r.stdout.decode("utf-8", "replace")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--node", required=True)
    ap.add_argument("--yes", action="store_true", help="actually do it")
    ap.add_argument("--purge-sightings", action="store_true",
                    help="ALSO delete this node's sightings. Only if asked for.")
    a = ap.parse_args()

    script = (REMOTE_OP.replace("NODE_JSON", json.dumps(a.node))
                       .replace("DO_IT", "True" if a.yes else "False")
                       .replace("PURGE", "True" if a.purge_sightings else "False"))
    tmp = Path(tempfile.mkdtemp()) / "op.py"
    tmp.write_text(script, encoding="utf-8")
    subprocess.run(["scp", "-i", a.key, "-o", "BatchMode=yes", "-q",
                    str(tmp), "%s:/tmp/_revoke.py" % a.box], check=True)
    res = json.loads(sh(a.box, a.key,
                        "cd %s && .venv/bin/python /tmp/_revoke.py" % REMOTE))

    if "error" in res:
        raise SystemExit("  " + res["error"])
    if "preview" in res:
        p = res["preview"]
        print("  WOULD REVOKE:")
        for k, v in p.items():
            print("    %-14s %s" % (k, v))
        print("\n  Nothing changed. Re-run with --yes to revoke.")
        if p.get("public"):
            print("  ⚠️ %d of its sightings are PUBLIC - revoking does not unpublish "
                  "them." % p["public"])
        return
    d = res["done"]
    print("  REVOKED %s (%s)" % (d["id"], d["name"]))
    print("    status   %s -> %s" % (d["status"], d["now_status"]))
    print("    token    %s -> %s" % (d["has_token"], d["now_has_token"]))
    print("    pubkey   kept (%s) so its past sightings stay verifiable"
          % d["has_pubkey"])
    print("    sightings %d (%d public, %d with a photo)%s"
          % (d["sightings"], d["public"], d["with_photo"],
             " - PURGED %d" % d["purged"] if d["purged"] else " - left in place"))
    print("    backup   %s" % d["backup"])
    if d.get("audit_error"):
        print("    ⚠️ audit entry failed: %s" % d["audit_error"])


if __name__ == "__main__":
    main()
