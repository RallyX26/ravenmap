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

MSG_MAX_BYTES = 64 * 1024        # one envelope; plenty for text, caps abuse
MAILBOX_MAX = 500                # queued messages per mailbox before oldest drop
GLOBAL_MAX = 200000              # total queued envelopes across all mailboxes
TTL_S = 14 * 86400               # undelivered mail is dropped after two weeks
CHALLENGE_TTL_S = 90.0
_HEX = re.compile(r"^[0-9a-f]{16,64}$")


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


def put(mb: str, env: str) -> bool:
    """Store one opaque ciphertext envelope in a mailbox. Returns False if the
    mailbox id is malformed, the envelope is too big, or the relay is full."""
    if not _valid_mb(mb):
        return False
    raw = (env or "").encode("utf-8")
    if not raw or len(raw) > MSG_MAX_BYTES:
        return False
    if _count_global() >= GLOBAL_MAX:
        return False
    box = _box(mb)
    try:
        box.mkdir(parents=True, exist_ok=True)
        existing = sorted(box.glob("*.env"))
        # Bounded queue: drop the oldest rather than let one mailbox grow forever.
        for old in existing[:max(0, len(existing) + 1 - MAILBOX_MAX)]:
            try:
                old.unlink()
            except OSError:
                pass
        name = "%d_%s.env" % (int(time.time() * 1000), secrets.token_hex(6))
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
    """Drop envelopes past the TTL. Cheap; safe to call on a timer."""
    cut = time.time() - TTL_S
    n = 0
    for f in glob.glob(str(SEND / "*" / "*.env")):
        try:
            if os.path.getmtime(f) < cut:
                os.unlink(f)
                n += 1
        except OSError:
            pass
    return n
