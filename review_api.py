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


def head_rejected(meta: dict) -> bool:
    """Did the trained head already rule this crop out?

    🚨 THE PEN WAS SHOWING HUMANS CARS THE MODEL HAD ALREADY DISMISSED.
    Contributor crops are parked here by box_puller, which used to admit
    anything CLIP's bare argmax called a marked class - even when the head had
    scored it and said no. The head's number was written into the `why` string
    the reviewer reads, and then ignored by the gate that put it there. 477 of
    485 live pen items arrived that way from one browser contributor, CLIP at
    ~0.50 against a head at ~0.00, and a queue full of ordinary cars reads as
    the classifier getting worse rather than as a filter that was never applied.

    Both numbers must be present to hide anything. A score without its operating
    point is not a judgement, and this box cannot load the head to supply one -
    so an item that does not carry its own threshold is SHOWN, not hidden.
    Failing towards a human looking at an extra car is the safe direction; the
    opposite failure hides a real patrol unit for ever.
    """
    h = meta.get("head") or {}
    conf, thr = h.get("conf"), h.get("threshold")
    if conf is None or thr is None:
        return False
    try:
        return float(conf) < float(thr)
    except (TypeError, ValueError):
        return False


def queue(reviewer: dict, scope: str = "pool", limit: int = 60,
          rejected: bool = False) -> dict:
    """Pending pool items this reviewer may see, newest first.

    `rejected=True` returns ONLY the items the trained head threw out - the
    pile the normal queue hides.

    🚨 THIS IS WHERE A MISSED PATROL CAR WOULD BE. The filter is right: a
    reviewer seeing an extra car costs seconds, and hiding a real patrol unit
    defeats the project, so the default queue stays filtered. But held-out
    recall is about 86%, so roughly one government vehicle in seven lands in
    this pile - and until now there was no way to look at it. A filter nobody
    can audit is a filter nobody should trust.

    They are returned SEPARATELY rather than merged in. Mixing them would lose
    the one fact that matters about them: the classifier already said no, and
    the reviewer is second-guessing it deliberately rather than working a queue.
    """
    allowed = reviewer.get("nodes")            # None = all nodes
    if not mirror.REVIEW.exists():
        return {"items": [], "count": 0, "hidden": 0}
    items = []
    hidden = 0
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
        #
        # ⚠️ SCOPE IS APPLIED BEFORE THE HEAD FILTER ON PURPOSE. It used to be
        # counted after, so `hidden` included other people's cameras and a
        # single-camera volunteer was told hundreds of their crops were held
        # back when almost none of them were theirs. A number about someone
        # else's cameras is worse than no number.
        if allowed is not None and node_id not in allowed:
            continue
        if scope == "mine" and allowed is not None and node_id not in allowed:
            continue
        was_rejected = head_rejected(meta)
        if was_rejected:
            hidden += 1
        if was_rejected != bool(rejected):
            continue
        items.append({
            "id": sid, "node_id": node_id,
            "node_name": node_name or "a camera",
            "score": _score(meta, row),
            "ts": meta.get("ts") or (row or {}).get("ts"),
            "crop": f"/api/rv/crop/{sid}",
            "rejected": was_rejected,
        })
    if rejected:
        # 🚨 NEWEST-FIRST IS THE WRONG ORDER FOR AN AUDIT.
        # 89% of this pile scores below 0.01 - the head was not hesitating, it
        # was certain. Confirming a 0.0002 teaches nothing and changes no
        # decision, so a reviewer working newest-first spends their attention
        # on the least informative crops in the set. He did 60 that way and
        # found nothing, which was the expected outcome and therefore weak
        # evidence.
        #
        # The only crops where a miss can plausibly hide are the ones the head
        # nearly accepted. Highest score first puts those on top: ten of them
        # say more about whether the filter is safe than sixty from the floor,
        # and they are also the labels worth having, because a confirmed
        # negative next to the threshold moves the boundary and one at 0.0002
        # does not.
        items.sort(key=lambda x: x.get("score") or 0.0, reverse=True)
    else:
        items.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    # `hidden` is reported rather than swallowed. A filter that silently shrinks
    # a queue is indistinguishable from a queue that is genuinely empty, and
    # this one is capable of hiding hundreds of items at once.
    return {"items": items[:limit], "count": len(items), "hidden": hidden}


def crop_bytes(sid: int) -> Optional[bytes]:
    p = mirror.REVIEW / f"{int(sid)}.jpg"
    if p.exists():
        try:
            return p.read_bytes()
        except Exception:
            return None
    return None


def contributed(reviewer: dict) -> dict:
    """What this reviewer's camera(s) have actually contributed.

    🚨 AN EMPTY QUEUE HAS TO READ AS SUCCESS, NOT AS A BROKEN PAGE.
    A volunteer opens their review link and, most of the time, correctly sees
    nothing: the head rejected everything their camera caught, which is the
    normal and desirable outcome. "Nothing waiting for review" next to a blank
    screen reads as "this doesn't work", and the person who just installed a
    camera concludes their camera is not working either.

    So the empty state gets to say what the camera HAS done. These are counts,
    never rows - no plate, no image, nothing about any individual vehicle. A
    contributor is told the size of their contribution, not shown the traffic.
    """
    allowed = reviewer.get("nodes")          # None = pool scope = the whole network
    conn = db.connect()
    if allowed is None:
        where, args = "1=1", ()
    else:
        nodes = [n for n in allowed if n]
        if not nodes:
            return {"scope": "own", "cameras": 0, "sightings": 0, "published": 0}
        where = "node_id IN (%s)" % ",".join("?" * len(nodes))
        args = tuple(nodes)
    row = conn.execute(
        f"SELECT COUNT(*) AS n, "
        f"SUM(CASE WHEN tier='public' THEN 1 ELSE 0 END) AS pub, "
        f"COUNT(DISTINCT node_id) AS cams "
        f"FROM sightings WHERE {where}", args).fetchone()
    return {
        "scope": "pool" if allowed is None else "own",
        "cameras": int(row["cams"] or 0),
        "sightings": int(row["n"] or 0),
        "published": int(row["pub"] or 0),
    }


def is_trusted(reviewer: dict) -> bool:
    """May this reviewer see the retracted-photo shelf?

    Pool scope, which today is one token. The other reviewers are 'own'-scoped
    volunteers who may only see their own camera.

    🔑 WHY THIS GATE AND NOT AN OPERATOR ONE. A mirror deliberately has NO
    operator surface - mirror.route_allowed 404s /review and /api/operator
    outright, on the argument that a disabled review page is still a review
    page to find a bug in. Adding an operator route here to reach these photos
    would reopen exactly that. A pool reviewer, meanwhile, can already see
    every crop in the pen and publish or retract anything on the map, so
    showing them the photographs of things they themselves retracted grants no
    power they did not already hold. The gate reuses an existing, audited,
    revocable mechanism instead of cutting a new hole.
    """
    return reviewer.get("nodes") is None and reviewer.get("scope") == "pool"


def retracted(reviewer: dict) -> dict:
    """Photographs still on disk for sightings the map no longer claims."""
    if not is_trusted(reviewer):
        return {"items": [], "count": 0}
    out = []
    for r in db.retracted_with_photo():
        if not (SNAPS / Path(str(r["snap"])).name).exists():
            continue          # row remembers a file that is already gone
        node = db.node(r.get("node_id") or "") or {}
        out.append({
            "id": r["id"], "ts": r.get("ts"),
            "retracted_at": r.get("reviewed_at"),
            "node_name": node.get("name") or "a camera",
            "vclass": r.get("vclass"), "why": r.get("vclass_why"),
            "photo": f"/api/rv/retracted/photo/{r['id']}",
        })
    return {"items": out, "count": len(out)}


def delete_retracted_photo(reviewer: dict, sid: int, ip: str = "") -> dict:
    """Delete the leftover photograph of a retracted sighting.

    Row reference first, file second - see db.clear_snap. The sighting itself
    is kept: the vehicle really did pass, and deleting the row would replace
    one wrong claim with another. Only the picture goes.
    """
    if not is_trusted(reviewer):
        return {"ok": False, "error": "not permitted"}
    row = db.sighting(int(sid))
    if not row or row.get("reviewed") != "retracted":
        return {"ok": False, "error": "not a retracted sighting"}
    name = db.clear_snap(int(sid))
    if not name:
        return {"ok": False, "error": "no photo to delete"}
    try:
        (SNAPS / Path(name).name).unlink(missing_ok=True)
    except Exception:
        pass                  # reference is already cleared; an orphan file is tidy-up
    db.audit("retracted:photo_delete", str(sid),
             actor=reviewer.get("label") or "reviewer", ip=ip)
    return {"ok": True, "id": int(sid)}


def retracted_photo_bytes(reviewer: dict, sid: int) -> Optional[bytes]:
    if not is_trusted(reviewer):
        return None
    row = db.sighting(int(sid))
    if not row or row.get("reviewed") != "retracted" or not row.get("snap"):
        return None
    p = SNAPS / Path(str(row["snap"])).name
    if not p.exists():
        return None
    try:
        return p.read_bytes()
    except Exception:
        return None


def park_reported(sid: int, row: dict, reason: str = "") -> bool:
    """Put a publicly-flagged sighting in front of a human.

    🚨 A FLAG THAT ONLY REACHES A TABLE IS A BLACK HOLE.
    /api/report wrote a `reports` row and stopped. The only reader was the
    OPERATOR queue, which is local-only and does not exist on a public mirror
    at all - so on the live site every public flag landed somewhere nothing
    running on that machine could display. Six real reports sat unresolved,
    four of them "not a government vehicle": the public correctly telling us a
    car was wrong, into a void.

    Parking it in the pen puts it in the reviewer app beside everything else,
    and the existing verdict path already does the right thing from there:
    "not a cop" calls db.review_sighting(sid, "retracted"), which demotes the
    row to private, drops the plate text, and resolves the flags.

    Note what is deliberately NOT recorded here: no `head` verdict. The queue
    filter hides an item only when it has both a score and a threshold, so an
    item with neither is always shown - which is what a human flag deserves.
    A model already had its say on this vehicle and a person is disagreeing.
    """
    if not row or row.get("tier") != "public":
        return False
    jp = mirror.REVIEW / f"{int(sid)}.json"
    if jp.exists():
        return False                      # already queued; a second flag is not a second job
    snap = row.get("snap")
    if not snap:
        return False
    src = SNAPS / Path(str(snap)).name
    if not src.exists():
        return False
    try:
        crop = snapshot.subres_from_stored(src.read_bytes())
    except Exception:
        return False
    node = db.node(row.get("node_id") or "") or {}
    return bool(mirror.review_write(int(sid), crop, {
        "ts": row.get("ts"),
        "node_id": row.get("node_id") or "",
        "node_name": node.get("name") or "a camera",
        "vclass": row.get("vclass"),
        "body": row.get("body"),
        "reported": True,
        "report_reason": reason,
        # Shown to the reviewer as the reason this is in front of them. It is
        # the public's claim, attributed as such - not the model's.
        "why": f"flagged by the public: {db.REPORT_REASONS.get(reason, reason)}",
    }))


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


def _publish(sid: int, reviewer: dict, force_vclass: Optional[str] = None) -> None:
    """Publish a pen item. ``force_vclass`` is the REVIEWER'S own call.

    Without it the class is inherited from whatever the model guessed, which
    makes the two buttons a single decision: a human who can see the vehicle is
    a better judge of police-versus-ordinary-government than the classifier
    that could not tell them apart, and the distinction matters - a patrol car
    and a council pickup are the same tier but not the same claim.
    """
    meta = _pen_meta(sid)
    row = db.sighting(sid) if hasattr(db, "sighting") else None
    vclass = force_vclass or meta.get("vclass") or (row or {}).get("vclass") or "gov"
    vclass = "police" if vclass == "police" else "gov"
    kind = "a patrol vehicle" if vclass == "police" else "a government vehicle"
    why = f"confirmed by reviewer {reviewer.get('label')} as {kind}"

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
        _publish(sid, reviewer, force_vclass="police")
        db.audit("review:confirm", str(sid), actor=who, ip=ip)
        return {"ok": True, "id": sid, "verdict": "cop"}
    if call == "gov":
        # Government but NOT police: a council truck, a state pickup, a fleet
        # vehicle with a department decal. Same public tier - a publicly owned
        # vehicle doing public work is a public record either way - but a
        # different claim, and the reviewer is the one who can actually see
        # which it is. Audited under its own action so the two calls can be
        # counted separately when the classifier's confusion matrix is
        # published.
        _publish(sid, reviewer, force_vclass="gov")
        db.audit("review:confirm_gov", str(sid), actor=who, ip=ip)
        return {"ok": True, "id": sid, "verdict": "gov"}
    if call == "not":
        # Never published, so nothing to retract: drop the crop, leave the row
        # private. The verdict is logged so the call is attributable and the crop
        # can be harvested as a civilian training label at home.
        _delete_pen(sid)
        # 🚨 AND RECORD THAT A HUMAN LOOKED. Without this the row stays
        # `reviewed IS NULL`, which is exactly what backfill_pen searches for -
        # so every rejected vehicle came back to be judged again on the next
        # sweep, and a reviewer's work quietly undid itself.
        try:
            db.review_sighting(sid, "retracted")
        except Exception:
            pass
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
