"""Sparrow Send - the dumb relay half of an end-to-end encrypted messenger.

🚨 THE SERVER NEVER SEES A MESSAGE. Every byte stored here is ciphertext the
client sealed to the recipient's public key; the hub only holds opaque envelopes
in per-mailbox queues and hands them over to whoever can PROVE they own the
mailbox, then deletes them. There is nothing here to read, subpoena or leak -
the same guarantee the map is built on, applied to messages.

A mailbox id is the SHA-256 of the owner's signing public key. Anyone who has
your address can work out your mailbox id and drop ciphertext in it (that is how
you receive mail), so FETCHING must be authenticated or a stranger could drain
your inbox: the client signs a short-lived server challenge with the key whose
hash is the mailbox, and only then are the envelopes returned and cleared.

Limits, stated plainly (the page says them too):
  - metadata: the hub sees THAT a mailbox received an envelope and roughly when
    and how big. It never sees content, and with sealed sender it need not see
    who sent it. Full metadata resistance (a mixnet) is out of scope.
  - no forward secrecy yet: keys are static, so this is a sealed-box MVP. A
    ratchet is the follow-up. Short server TTL limits exposure meanwhile.
"""

from __future__ import annotations

import base64
import glob
import hmac
import os
import re
import secrets
import time
from hashlib import sha256

import core

SEND = core.DATA / "send"
# One process-lifetime secret for challenge MACs. A hub restart invalidates
# outstanding challenges, which is harmless - the client just asks for another.
_SECRET = secrets.token_bytes(32)

MSG_MAX_BYTES = 1024 * 1024       # one envelope; text is tiny, this allows a
                                 # downscaled image attachment (encrypted) too
MAILBOX_MAX = 300                # queued messages per mailbox before oldest drop
GLOBAL_MAX = 60000               # total queued envelopes across all mailboxes
TTL_S = 14 * 86400               # undelivered mail is dropped after two weeks
CHALLENGE_TTL_S = 90.0
# \Z not $ - in Python $ also matches just before a trailing newline, which would
# let "aaaa...\n" pass and create a second on-disk variant of a mailbox.
_HEX = re.compile(r"^[0-9a-f]{16,64}\Z")


def _b64u_dec(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _valid_mb(mb: str) -> bool:
    return bool(mb and _HEX.match(mb))


def _box(mb: str):
    return SEND / mb


def issue_challenge(mb: str) -> str:
    """A stateless, short-lived challenge the client signs to prove it owns mb."""
    if not _valid_mb(mb):
        return ""
    payload = "%s:%d:%s" % (mb, int(time.time()), secrets.token_hex(8))
    mac = hmac.new(_SECRET, payload.encode(), sha256).hexdigest()[:32]
    return payload + ":" + mac


def _challenge_ok(mb: str, challenge: str) -> bool:
    try:
        m, ts, rnd, mac = challenge.split(":")
    except (ValueError, AttributeError):
        return False
    if m != mb:
        return False
    payload = "%s:%s:%s" % (m, ts, rnd)
    good = hmac.new(_SECRET, payload.encode(), sha256).hexdigest()[:32]
    if not hmac.compare_digest(good, mac):
        return False
    try:
        return (time.time() - int(ts)) <= CHALLENGE_TTL_S
    except ValueError:
        return False


def mailbox_for(signing_pub_raw: bytes) -> str:
    return sha256(signing_pub_raw).hexdigest()[:32]


# ---- proof-of-work on SEND (hashcash) --------------------------------------
# Sending is unauthenticated by design (anyone with your address can write to
# you). That left three cheap floods open: evicting a victim's queued mail,
# filling GLOBAL_MAX to refuse mail network-wide, and monopolising the shared
# rate bucket. A small per-message cost closes all three without adding
# accounts. The sender finds a nonce so SHA-256 of the stamp has POW_BITS
# leading zero bits; we verify with ONE hash here. `ts` bounds precomputation
# and replay; the envelope hash binds the stamp to THIS message.
# 🚨 POW_BITS MUST equal POW_BITS in public/sparrowsend-pow.js.
POW_VERSION = "SP1"
POW_BITS = 17               # base difficulty for a near-empty mailbox
POW_STEP = 14               # +1 required bit per this many already-queued msgs
POW_SKEW_S = 300.0          # accept a stamp within 5 min of now (also clock skew)


def queue_depth(mb: str) -> int:
    if not _valid_mb(mb):
        return 0
    box = _box(mb)
    if not box.is_dir():
        return 0
    try:
        return sum(1 for _ in box.glob("*.env"))
    except OSError:
        return 0


def required_bits(depth: int) -> int:
    """Progressive PoW: the fuller a mailbox already is, the more work each new
    message costs. Packing a mailbox toward eviction then grows super-linearly
    and is expensive even for a native hasher, while a normal (near-empty)
    mailbox stays cheap at the base difficulty. The client sends at the base,
    and the hub replies with `need_bits` when more is required."""
    return POW_BITS + max(0, depth) // POW_STEP


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        b, c = byte, 0
        while b < 128:
            c += 1
            b <<= 1
        return bits + c
    return bits


def pow_ok(mb: str, env: str, ts, nonce, bits: int = POW_BITS) -> bool:
    """True if (ts, nonce) is a valid proof-of-work stamp for this envelope."""
    try:
        ts_i = int(ts)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts_i) > POW_SKEW_S:
        return False
    ch = sha256((env or "").encode("utf-8")).hexdigest()
    pre = "%s|%s|%d|%s|%s" % (POW_VERSION, mb, ts_i, ch, str(nonce))
    return _leading_zero_bits(sha256(pre.encode("utf-8")).digest()) >= bits


def _verify_sig(signing_pub_raw: bytes, message: bytes, sig_raw: bytes) -> bool:
    """Verify a WebCrypto ECDSA P-256 signature (raw r||s) over `message`."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        from cryptography.hazmat.primitives import hashes
        pub = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), signing_pub_raw)
        if len(sig_raw) != 64:
            return False
        r = int.from_bytes(sig_raw[:32], "big")
        s = int.from_bytes(sig_raw[32:], "big")
        der = utils.encode_dss_signature(r, s)
        pub.verify(der, message, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def _count_global() -> int:
    try:
        return sum(len(files) for _, _, files in os.walk(SEND))
    except OSError:
        return 0


def _evict_global(batch: int = 128) -> bool:
    """When the relay is at GLOBAL_MAX, drop the oldest envelopes across ALL
    mailboxes to make room, rather than refusing every new message network-wide
    (which turned a full relay into a multi-day outage - the worst failure mode).
    Oldest-first by the millisecond timestamp in the filename. A batch is dropped
    at once so the next puts have headroom and don't each re-scan."""
    files = glob.glob(str(SEND / "*" / "*.env"))
    if not files:
        return False

    def _age(f):
        try:
            return int(os.path.basename(f).split("_", 1)[0])
        except (ValueError, IndexError):
            return 0

    files.sort(key=_age)
    n = 0
    for f in files[:max(1, batch)]:
        try:
            os.unlink(f)
            n += 1
        except OSError:
            pass
    return n > 0


_MID = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")   # \Z: reject a trailing newline


def put(mb: str, env: str, mid: str = "") -> bool:
    """Store one opaque ciphertext envelope in a mailbox. Returns False if the
    mailbox id is malformed, the envelope is too big, or the relay is full.

    🚨 IDEMPOTENT. A client whose send SUCCEEDED but whose response was lost will
    retry with the SAME `mid`, and a duplicate would show up as the same message
    three times (reported). The filename carries the mid, so a repeat is stored
    once. The timestamp prefix keeps delivery in send order."""
    if not _valid_mb(mb):
        return False
    raw = (env or "").encode("utf-8")
    if not raw or len(raw) > MSG_MAX_BYTES:
        return False
    box = _box(mb)
    try:
        box.mkdir(parents=True, exist_ok=True)
        safe_mid = mid if (mid and _MID.match(mid)) else ""
        if safe_mid:
            for _dup in box.glob("*_" + safe_mid + ".env"):
                return True                      # already stored this exact message
        if _count_global() >= GLOBAL_MAX:
            # Degrade gracefully: make room by dropping the globally-oldest mail
            # instead of refusing everyone. Only refuse if even that freed nothing.
            _evict_global()
            if _count_global() >= GLOBAL_MAX:
                return False
        existing = sorted(box.glob("*.env"))
        # Bounded queue: drop the oldest rather than let one mailbox grow forever.
        for old in existing[:max(0, len(existing) + 1 - MAILBOX_MAX)]:
            try:
                old.unlink()
            except OSError:
                pass
        tag = safe_mid or secrets.token_hex(6)
        name = "%d_%s.env" % (int(time.time() * 1000), tag)
        (box / name).write_bytes(raw)
        return True
    except OSError:
        return False


def fetch_and_clear(mb: str, signing_pub_b64u: str, challenge: str,
                    sig_b64u: str, limit: int = 200) -> list:
    """Prove ownership of `mb`, then return and DELETE its envelopes.

    Ownership = a valid, fresh challenge signed by the key whose SHA-256 is the
    mailbox id. Returns [] on any failure (never says WHY to the caller; the
    content is ciphertext regardless, this only stops a stranger draining it)."""
    if not _valid_mb(mb) or not _challenge_ok(mb, challenge):
        return []
    try:
        pub_raw = _b64u_dec(signing_pub_b64u)
        sig_raw = _b64u_dec(sig_b64u)
    except Exception:
        return []
    if mailbox_for(pub_raw) != mb:
        return []
    if not _verify_sig(pub_raw, challenge.encode(), sig_raw):
        return []
    box = _box(mb)
    if not box.is_dir():
        return []
    out = []
    for f in sorted(box.glob("*.env"))[:limit]:
        try:
            out.append(f.read_text("utf-8"))
            f.unlink()
        except OSError:
            pass
    return out


def prune() -> int:
    """Drop envelopes past the TTL, then reclaim emptied mailbox dirs. Cheap;
    safe to call on a timer. Without the rmdir sweep, every mailbox ever written
    left a directory behind forever, inflating the per-put os.walk cost."""
    cut = time.time() - TTL_S
    n = 0
    for f in glob.glob(str(SEND / "*" / "*.env")):
        try:
            if os.path.getmtime(f) < cut:
                os.unlink(f)
                n += 1
        except OSError:
            pass
    try:
        for d in SEND.glob("*"):
            if d.is_dir():
                try:
                    d.rmdir()          # only succeeds if now empty
                except OSError:
                    pass
    except OSError:
        pass
    return n
