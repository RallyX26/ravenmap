"""Reviewer access: many trusted people, each with their own revocable token.

Separate from operator_auth, on purpose. The operator secret is one shared key
for the person who runs the deployment. Reviewing is different: we WANT to hand
it out, to volunteers who run cameras and to people who help work the shared
pool. So each reviewer gets their own token, and the design makes trust the
default while keeping abuse recoverable rather than assumed:

  * a token is stored only as a SHA-256 hash - a database leak does not hand
    anyone a working key;
  * every verdict is written to the audit log with the reviewer's label, so a
    bad actor is identifiable after the fact and their calls are reversible
    (a wrong publish is retracted, a wrong reject re-queued);
  * a token can be REVOKED on its own without disturbing anyone else's;
  * a token can be SCOPED to 'own' - only the cameras that person runs - or
    'pool' - the shared queue of everyone's pending government calls.

Only the operator (operator_auth) can mint or revoke these. A reviewer token
grants exactly two powers: read the queue it is scoped to, and cast a verdict
that a human already had to make anyway.
"""

from __future__ import annotations

import hashlib
import http.cookies
import secrets
from typing import Optional

import db
from core import CONFIG

COOKIE = "sparrow_rv"
MAX_AGE = 30 * 24 * 3600


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


# --- minting / revoking (operator only; the hub gates the caller) ----------
def issue(label: str, scope: str = "pool", nodes: Optional[list] = None,
          created_by: str = "operator") -> str:
    """Create a reviewer token and return the PLAINTEXT once (never stored)."""
    scope = "own" if scope == "own" else "pool"
    tok = secrets.token_urlsafe(24)
    db.add_review_token(_hash(tok), label.strip() or "reviewer", scope,
                        ",".join(nodes or []), created_by)
    return tok


def revoke(token_id: int) -> bool:
    return db.revoke_review_token(int(token_id))


def listing() -> list:
    return db.list_review_tokens()


# --- identifying a request's reviewer --------------------------------------
def _presented(headers) -> Optional[str]:
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    raw = headers.get("Cookie")
    if raw:
        try:
            c = http.cookies.SimpleCookie()
            c.load(raw)
            if COOKIE in c:
                return c[COOKIE].value
        except Exception:
            pass
    return None


def identify(headers) -> Optional[dict]:
    """The reviewer this request is authenticated as, or None.

    Returns a dict with label/scope and the resolved set of node_ids the
    reviewer may see (None = all, for pool scope). Touches last_used so a stale
    token is visible in the token list.
    """
    tok = _presented(headers)
    if not tok:
        return None
    rec = db.review_token_by_hash(_hash(tok))
    if not rec:
        return None
    db.touch_review_token(rec["id"])
    nodes = [n for n in (rec.get("nodes") or "").split(",") if n]
    return {
        "id": rec["id"], "label": rec["label"], "scope": rec["scope"],
        # None means "every node"; a set means "only these".
        "nodes": set(nodes) if rec["scope"] == "own" else None,
    }


def login_value(token_str: str) -> Optional[str]:
    """Validate a submitted token; return the cookie value to set, or None."""
    if not token_str:
        return None
    rec = db.review_token_by_hash(_hash(token_str.strip()))
    return token_str.strip() if rec else None


def cookie_header(value: str, clear: bool = False) -> str:
    parts = [f"{COOKIE}={'' if clear else value}", "Path=/", "HttpOnly",
             "SameSite=Strict"]
    parts.append("Max-Age=0" if clear else f"Max-Age={MAX_AGE}")
    if CONFIG.get("behind_tls", False):
        parts.append("Secure")
    return "; ".join(parts)
