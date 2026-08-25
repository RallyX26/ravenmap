"""Web Push for Sparrow Send - the ONLY way an iPhone (or any browser) shows a
notification when the app is closed. iOS never fires a background web
notification without a server-sent push, which is why the in-page Notification
API only worked while the app was open.

Privacy: a push here is CONTENT-FREE - it carries no message, just a nudge that
says "new message"; the client fetches and decrypts as usual. Enabling it stores
the device's push subscription (a Google/Apple push-service URL + keys) on the
hub, keyed by mailbox. That is metadata the hub already sees (it knows a mailbox
received an envelope); it is strictly opt-in and never reveals content. Stale
subscriptions (404/410 from the push service) are deleted on the next send.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import core

VAPID = core.DATA / "vapid.json"
SUBS = core.DATA / "send_subs"
SUB_CONTACT = "https://map.sparrowmap.com"   # VAPID contact URI (RFC 8292; https or mailto)
_MB = re.compile(r"^[0-9a-f]{16,64}\Z")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _load_or_make_vapid():
    """One ES256 (P-256) VAPID keypair for this deployment, persisted."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    if VAPID.exists():
        d = json.loads(VAPID.read_text("utf-8"))
        priv = serialization.load_pem_private_key(d["priv"].encode(), None)
        return priv, d["pub"]
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    pub = _b64u(pub_raw)
    pem = priv.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    VAPID.parent.mkdir(parents=True, exist_ok=True)
    VAPID.write_text(json.dumps({"priv": pem, "pub": pub}), encoding="utf-8")
    return priv, pub


try:
    _PRIV, _PUB = _load_or_make_vapid()
except Exception:
    _PRIV, _PUB = None, ""


def public_key() -> str:
    return _PUB


def _vapid_jwt(aud: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from cryptography.hazmat.primitives import hashes
    header = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    payload = _b64u(json.dumps({"aud": aud, "exp": int(time.time()) + 12 * 3600,
                                "sub": SUB_CONTACT}).encode())
    signing_input = (header + "." + payload).encode()
    der = _PRIV.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    sig = _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return header + "." + payload + "." + sig


def subscribe(mb: str, sub) -> bool:
    if not (mb and _MB.match(mb)):
        return False
    if not isinstance(sub, dict) or not isinstance(sub.get("endpoint"), str):
        return False
    if not sub["endpoint"].startswith("https://"):
        return False
    try:
        SUBS.mkdir(parents=True, exist_ok=True)
        (SUBS / (mb + ".json")).write_text(json.dumps(sub), encoding="utf-8")
        return True
    except OSError:
        return False


def unsubscribe(mb: str) -> bool:
    try:
        (SUBS / (mb + ".json")).unlink()
        return True
    except OSError:
        return False


def _post(sub: dict):
    """Send one content-free push. Returns True ok, None if the sub is gone."""
    endpoint = sub["endpoint"]
    u = urlparse(endpoint)
    aud = "%s://%s" % (u.scheme, u.netloc)
    req = urllib.request.Request(endpoint, data=b"", method="POST", headers={
        "Authorization": "vapid t=%s, k=%s" % (_vapid_jwt(aud), _PUB),
        "TTL": "2419200",
        "Content-Length": "0",
        "Urgency": "high",
    })
    try:
        urllib.request.urlopen(req, timeout=8)
        return True
    except urllib.error.HTTPError as e:
        return None if e.code in (404, 410) else False
    except Exception:
        return False


def notify(mb: str):
    if not _PRIV or not (mb and _MB.match(mb)):
        return
    f = SUBS / (mb + ".json")
    if not f.exists():
        return
    try:
        sub = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return
    if _post(sub) is None:            # 404/410 -> the subscription is dead
        try:
            f.unlink()
        except OSError:
            pass


def notify_async(mb: str):
    """Fire-and-forget so a slow push service never blocks the send path."""
    if not _PRIV:
        return
    try:
        threading.Thread(target=notify, args=(mb,), daemon=True).start()
    except Exception:
        pass
