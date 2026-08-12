"""The reviewer API: serve the pending pool, cast verdicts, manage tokens.

The pool is the review pen (mirror.REVIEW): sub-resolution, plate-less crops of
government CANDIDATES that a human has to confirm before they reach the map -
his own camera's ambiguous calls (parked at ingest) and contributors' calls
that were not clearly-marked enough to auto-publish (parked by box_puller). A
reviewer confirms the real ones onto the map and rejects the rest; every verdict
is audited under their name and is reversible.

The hub wires HTTP routes to these functions and enforces who may call them
(a valid reviewer token for the queue/verdict, the operator secret for token
management). Nothing here trusts the caller on its own.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from pathlib import Path
from typing import Optional

import db
import mirror
import snapshot
import review_auth
from core import SNAPS


def _pen_meta(sid: int) -> dict:
    mp = mirror.REVIEW / f"{sid}.json"
    if mp.exists():
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _score(meta: dict, row: Optional[dict]) -> Optional[float]:
    if meta.get("score") is not None:
        return meta["score"]
    head = meta.get("head") or {}
    if head.get("conf") is not None:
        return head["conf"]
    return (row or {}).get("vclass_conf")


def queue(reviewer: dict, scope: str = "pool", limit: int = 60) -> dict:
    """Pending pool items this reviewer may see, newest first."""
    allowed = reviewer.get("nodes")            # None = all nodes
    if not mirror.REVIEW.exists():
        return {"items": [], "count": 0}
    items = []
    for jp in mirror.REVIEW.glob("*.json"):
        try:
            sid = int(jp.stem)
        except ValueError:
            continue
        meta = _pen_meta(sid)
        row = db.sighting(sid) if hasattr(db, "sighting") else None
        node_id = meta.get("node_id") or (row or {}).get("node_id") or ""
        node_name = meta.get("node_name")
        if not node_name and node_id:
            nd = db.node(node_id)
            node_name = (nd or {}).get("name")
        # 'own' scope, or a pool token that names specific nodes, filters here.
        if allowed is not None and node_id not in allowed:
            continue
        if scope == "mine" and allowed is not None and node_id not in allowed:
            continue
        items.append({
            "id": sid, "node_id": node_id,
            "node_name": node_name or "a camera",
            "score": _score(meta, row),
            "ts": meta.get("ts") or (row or {}).get("ts"),
            "crop": f"/api/rv/crop/{sid}",
        })
    items.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return {"items": items[:limit], "count": len(items)}


def crop_bytes(sid: int) -> Optional[bytes]:
    p = mirror.REVIEW / f"{int(sid)}.jpg"
    if p.exists():
        try:
            return p.read_bytes()
        except Exception:
            return None
    return None


def may_touch(reviewer: dict, sid: int) -> bool:
    """May this reviewer see or rule on this pen item?

    🚨 SCOPE HAS TO BE CHECKED ON EVERY ACCESS, NOT JUST ON THE LISTING.
    `queue()` filters an 'own'-scoped reviewer down to their own cameras, and
    that was the only place the check existed - so a token scoped to one camera
    could still fetch, publish or reject any other camera's crop simply by
    naming its id. Ids are sequential and appear next to sightings on the public
    map, so guessing one is not a feat. A pool token is unrestricted by design.
    """
    allowed = reviewer.get("nodes")
    if allowed is None:                       # pool scope: the whole queue
        return True
    meta = _pen_meta(sid)
    node_id = meta.get("node_id")
    if not node_id:
        row = db.sighting(sid) if hasattr(db, "sighting") else None
        node_id = (row or {}).get("node_id") or ""
    return node_id in allowed


def _delete_pen(sid: int) -> None:
    (mirror.REVIEW / f"{sid}.jpg").unlink(missing_ok=True)
    (mirror.REVIEW / f"{sid}.json").unlink(missing_ok=True)


def _publish(sid: int, reviewer: dict) -> None:
    meta = _pen_meta(sid)
    row = db.sighting(sid) if hasattr(db, "sighting") else None
    vclass = meta.get("vclass") or (row or {}).get("vclass") or "gov"
    vclass = "police" if vclass == "police" else "gov"
    why = f"confirmed by reviewer {reviewer.get('label')} as a government vehicle"

    # Attach the pen crop as the published photo (plate-less already).
    crop = crop_bytes(sid)
    snap = None
    if crop:
        try:
            data_url = "data:image/jpeg;base64," + base64.b64encode(crop).decode()
            snap = snapshot.store_subresolution(data_url, {
                "ts": meta.get("ts") or time.time(), "node_id": "review",
                "node_name": meta.get("node_name") or "a camera",
                "tier": "public", "vclass": vclass, "watermark": "CONFIRMED"})
        except Exception:
            try:
                name = f"{int(meta.get('ts') or time.time())}_{secrets.token_hex(4)}.jpg"
                SNAPS.mkdir(parents=True, exist_ok=True)
                (SNAPS / name).write_bytes(crop)
                snap = name
            except Exception:
                snap = None

    db.promote_sighting(sid, vclass, _score(meta, row), why)
    if snap:
        conn = db.connect()
        conn.execute("UPDATE sightings SET snap=? WHERE id=?", (snap, sid))
        conn.commit()
    _delete_pen(sid)


def verdict(reviewer: dict, sid: int, call: str, ip: str = "") -> dict:
    sid = int(sid)
    call = (call or "").lower()
    who = reviewer.get("label") or "reviewer"
    if not may_touch(reviewer, sid):
        return {"ok": False, "error": "not your camera"}
    if call == "cop":
        _publish(sid, reviewer)
        db.audit("review:confirm", str(sid), actor=who, ip=ip)
        return {"ok": True, "id": sid, "verdict": "cop"}
    if call == "not":
        # Never published, so nothing to retract: drop the crop, leave the row
        # private. The verdict is logged so the call is attributable and the crop
        # can be harvested as a civilian training label at home.
        _delete_pen(sid)
        db.audit("review:reject", str(sid), actor=who, ip=ip)
        return {"ok": True, "id": sid, "verdict": "not"}
    if call == "skip":
        db.audit("review:skip", str(sid), actor=who, ip=ip)
        return {"ok": True, "id": sid, "verdict": "skip"}
    return {"ok": False, "error": "unknown verdict"}


# --- token administration (operator-gated in the hub) ----------------------
def issue_token(label: str, scope: str, nodes: Optional[list]) -> dict:
    tok = review_auth.issue(label, scope, nodes, created_by="operator")
    return {"ok": True, "token": tok, "label": label, "scope": scope,
            "nodes": nodes or []}


def revoke_token(token_id: int) -> dict:
    return {"ok": review_auth.revoke(token_id)}


def list_tokens() -> dict:
    return {"tokens": review_auth.listing()}
