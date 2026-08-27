"""Sparrow Send - the tag-routed air relay (internet twin of the LoRa gateway).

The LoRa mesh is a broadcast pool: a frame names no recipient and every peer
trial-decrypts it, which gives sealed sender for free but does not scale past a
neighbourhood. To let the SAME encrypted messenger reach anyone over the
internet, delivery is keyed by a ROUTING TAG instead of a mailbox:

    tag = HMAC-SHA256( ECDH(sender_id, recipient_id),
                       "sparrow-air-v1" | epoch | recipient_pub )[:16]

Both parties - and only they - can compute the pairwise ECDH secret, so both can
compute the tag; the hub cannot. The tag rotates every epoch, so the hub never
builds a lasting map of who talks to whom: within one epoch it can see that two
anonymous endpoints share a tag, and nothing across epochs. That is strictly
more than the broadcast pool revealed (nothing) and far less than a mailbox
(a permanent recipient id) - the price of routing 1:1 at internet scale without
decrypting, and it is stated plainly in the UI.

The tag is DIRECTION-SEPARATED by the recipient's public key: Alice->Bob and
Bob->Alice derive different tags off the same shared secret, so a sender never
sees its own frames echoed back and each side polls only the tag keyed to ITS
own public key.

🚨 THE HUB NEVER DECRYPTS. A frame is opaque bytes under an opaque tag. This
service holds them in memory only and drops them after RETAIN seconds, so an
unattended relay does not accumulate a log of the neighbourhood. Durable,
offline mail (and photos) keep using the mailbox path in send_relay; the air is
for small, in-transit, unlinkable text delivery and is capped accordingly.
"""
from __future__ import annotations

import base64
import re
import threading
import time

import send_relay  # reuse the hardened, tested proof-of-work

# A routing tag is 16 bytes, rendered hex. \Z (not $) so a trailing newline is
# rejected rather than forking a second variant of the same tag.
TAG_RE = re.compile(r"^[0-9a-f]{32}\Z")
AIR_MSG_MAX = 8192          # one frame; a ratchet text envelope, never a photo
RETAIN_S = 6 * 3600         # a frame stays pullable for six hours, then vanishes
TAG_MAX = 100               # frames held per tag before the oldest is dropped
GLOBAL_MAX = 50000          # frames across all tags (RAM ceiling)
POW_STEP = send_relay.POW_STEP

_lock = threading.Lock()
_tags = {}                  # tag -> [{'s': seq, 'b': b64, 't': when}]
_seq = {}                   # tag -> next sequence number (kept even when emptied
                            # so a returning reader's cursor stays monotonic)
_total = 0


def valid_tag(t) -> bool:
    return bool(t and isinstance(t, str) and TAG_RE.match(t))


def depth(tag: str) -> int:
    with _lock:
        return len(_tags.get(tag, ()))


def required_bits(tag: str) -> int:
    """Progressive hashcash, same shape as the mailbox path: the fuller a tag
    already is, the more work each new frame costs, so packing one toward
    eviction grows super-linearly while a normal tag stays at the base cost."""
    return send_relay.POW_BITS + max(0, depth(tag)) // max(1, POW_STEP)


def _prune_locked(now: float) -> None:
    global _total
    cut = now - RETAIN_S
    for tag in list(_tags.keys()):
        q = _tags[tag]
        while q and q[0]["t"] < cut:
            q.pop(0)
            _total -= 1
        if not q:
            _tags.pop(tag, None)   # _seq[tag] deliberately survives (monotonic cursor)


def put(tag: str, frame_b64: str):
    """Store one opaque frame under a routing tag. Returns its sequence number
    (an int >= 0) or None if the tag/frame is malformed or the relay is full.

    Non-destructive and idempotent: an identical frame arriving twice (a retry
    or a two-bridge echo) returns the sequence it already has rather than
    storing a duplicate."""
    global _total
    if not valid_tag(tag):
        return None
    b = (frame_b64 or "").strip()
    try:
        raw = base64.b64decode(b, validate=True)
    except Exception:
        return None
    if not raw or len(raw) > AIR_MSG_MAX:
        return None
    b = base64.b64encode(raw).decode()   # canonical form for dedup comparison
    now = time.time()
    with _lock:
        _prune_locked(now)
        if _total >= GLOBAL_MAX:
            return None
        q = _tags.setdefault(tag, [])
        for it in q:
            if it["b"] == b:
                return it["s"]           # already carried this exact frame
        s = _seq.get(tag, 0)
        q.append({"s": s, "b": b, "t": now})
        _seq[tag] = s + 1
        _total += 1
        while len(q) > TAG_MAX:
            q.pop(0)
            _total -= 1
        return s


def get(tag: str, since: int, limit: int = 100):
    """Frames for a tag with sequence >= since, plus the current head so a reader
    can advance its cursor. Returns (head, [{'s','b'}, ...]).

    No ownership proof: the tag IS the capability, and only the two conversation
    parties can compute it. Reads are non-destructive - a second device with its
    own cursor still gets its mail, and a frame vanishes on RETAIN, not on read
    (so a failed onward hop never loses the message)."""
    if not valid_tag(tag):
        return 0, []
    try:
        since = int(since)
    except (TypeError, ValueError):
        since = 0
    with _lock:
        _prune_locked(time.time())
        q = _tags.get(tag, [])
        head = _seq.get(tag, 0)
        out = [{"s": it["s"], "b": it["b"]} for it in q if it["s"] >= since][:max(1, limit)]
        return head, out


def stats():
    with _lock:
        return {"tags": len(_tags), "frames": _total}
