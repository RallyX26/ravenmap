"""The camera's own signing key, held on the camera and nowhere else.

🚨 THIS IS THE PIECE THAT WAS NEVER BUILT. The schema has carried
`nodes.pubkey  -- ed25519, base64. Identity of the node.` since the beginning,
_ingest verifies a signature against it, and every sighting has a `sig_ok`
column recording the result. None of it ever ran: no client generated a key, so
`pubkey` was NULL on all 157 nodes, `verify_event` returned False on the first
line every time, and the map printed "signed: no" under every photograph it has
ever published. A verification that has never once succeeded is not a weak
check, it is decoration.

Signing matters here more than it does in most systems, because the bearer
token proves only that a secret was replayed. A signature proves the specific
CLAIM came from the camera that owns the key: swap a plate hash or a position
in flight and it stops verifying, which is exactly the tamper this project has
to be able to rule out when it says a government vehicle was in a place.

🔐 THE PRIVATE KEY NEVER LEAVES THE MACHINE. Only the public half is sent, once,
at enrolment. There is deliberately no recovery path: losing the file means
re-enrolling, which is a minor inconvenience, and any mechanism that could
restore a key from the hub would mean the hub could sign as the camera.

⚠️ ONE KEY PER NODE ID, in a file named after it. A camera that moves far enough
becomes a NEW node (nodes.enroll), and reusing the old key for it would tie two
nodes to one identity - which is precisely the confusion this exists to end.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Optional

# Kept beside the rest of the node's state, not in a home directory: an
# operator running two cameras from two checkouts gets two key stores, which is
# correct, and nothing has to guess which one a process meant.
KEYS_DIRNAME = "keys"


def _key_path(state_dir: Path, node_id: str) -> Path:
    safe = "".join(c for c in (node_id or "") if c.isalnum() or c in "_-")[:40]
    return Path(state_dir) / KEYS_DIRNAME / f"{safe or 'node'}.json"


def _harden(p: Path) -> None:
    """Owner-only, best effort. A key readable by every account on a shared
    machine is not a key. Windows ignores chmod, so this is not a guarantee -
    it is the cheap half of one, and its failure must not stop the camera."""
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def load(state_dir: Path, node_id: str) -> Optional[tuple[str, str]]:
    """(private, public) base64 for this node, or None if it has no key yet."""
    p = _key_path(state_dir, node_id)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        priv, pub = d.get("priv"), d.get("pub")
        if priv and pub:
            base64.b64decode(priv)      # refuse a corrupt file loudly
            base64.b64decode(pub)
            return str(priv), str(pub)
    except Exception as exc:
        print(f"[key] {p.name} is unreadable ({exc}); the camera will keep "
              f"running unsigned rather than stop")
    return None


def create(state_dir: Path, node_id: str) -> tuple[str, str]:
    """Make and store a keypair for this node. Returns (private, public)."""
    import nodes as node_mod
    priv, pub = node_mod.new_keypair()
    p = _key_path(state_dir, node_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"node_id": node_id, "priv": priv, "pub": pub},
                            indent=1), encoding="utf-8")
    _harden(p)
    return priv, pub


def load_or_create(state_dir: Path, node_id: str) -> tuple[str, str]:
    return load(state_dir, node_id) or create(state_dir, node_id)


def public_for(state_dir: Path, node_id: str) -> Optional[str]:
    got = load(state_dir, node_id)
    return got[1] if got else None


def sign(state_dir: Path, node_id: str, event: dict) -> Optional[str]:
    """Signature for an event, or None if this node has no key.

    None rather than an exception, and rather than a placeholder string. A node
    with no key is the normal, supported case - browser and phone nodes cannot
    hold one, which is the whole reason the bearer token exists - and _ingest
    only demands a signature from a node that has REGISTERED a public key. A
    fake signature would turn "unsigned" into "signature did not verify", which
    is a 401 and a dead camera.
    """
    got = load(state_dir, node_id)
    if not got:
        return None
    try:
        import nodes as node_mod
        return node_mod.sign_event(event, got[0])
    except Exception as exc:
        # 🚨 NEVER let a signing failure lose the sighting. The vehicle really
        # did pass; an unsigned record of it beats no record, and the hub will
        # reject it anyway if this node has a key registered - which is a loud,
        # findable failure rather than a silent gap in the data.
        print(f"[key] could not sign for {node_id}: {exc}")
        return None
