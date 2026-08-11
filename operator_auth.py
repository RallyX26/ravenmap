"""Operator authentication, for when the hub stops being private.

🚨 THE REASON THIS FILE EXISTS, IN ONE SENTENCE:
`is_operator_addr` trusts the socket address, and behind a reverse proxy EVERY
request arrives from 127.0.0.1 - so the moment sparrowmap.com points at a box
running nginx or Caddy in front of this hub, every visitor on the internet
becomes an operator.

What an operator can do, and therefore what a stranger could do:

  * retract any published sighting;
  * PROMOTE a private vehicle onto the public map as a government vehicle;
  * CONFIRM a sighting, which is what makes its plate searchable - so a false
    positive on a private car, confirmed by a stranger, publishes a private
    citizen's plate. That is the precise harm this whole project exists to
    prevent, reachable by anyone who finds the URL.

The existing check already carried the warning in its own docstring. This is
the mechanism it said would be needed.

## The design, and why it is this small

A shared secret in a file, a cookie, and constant-time comparison. No user
table, no password hashing, no sessions in a database - there is exactly one
operator on a deployment like this and inventing an account system for one
person adds attack surface rather than removing it.

  * the token is generated on first use and written to `data/operator.token`
    with the file readable only where the hub runs;
  * it is never printed to a log, never sent to a page, and never placed in a
    URL - a query string ends up in proxy logs and browser history;
  * comparison is `secrets.compare_digest`, because a naive `==` on a secret
    leaks its length and prefix through timing;
  * the cookie is HttpOnly and SameSite=Strict, so a script on another origin
    cannot read it and a link from elsewhere cannot use it.

## Failing closed

If `operator_requires_auth` is true and no token file can be created, every
operator route refuses. A deployment that cannot authenticate must not fall
back to trusting the network - that is how "temporarily open" becomes
permanent.
"""

from __future__ import annotations

import http.cookies
import secrets
import time
from pathlib import Path
from typing import Optional

from core import CONFIG, DATA, is_operator_addr

TOKEN_FILE = DATA / "operator.token"
COOKIE = "sparrow_op"

# Sessions are just the token in a cookie; there is no separate session store to
# get out of sync. This only bounds how long a stolen cookie stays useful.
MAX_AGE = 14 * 24 * 3600


def required() -> bool:
    """Is real authentication switched on for this deployment?

    🚨 BEHIND A PROXY, IP-BASED OPERATOR DETECTION IS MEANINGLESS AND MUST NOT
    BE THE FALLBACK. `behind_tls` is set exactly when this hub sits behind nginx
    or Cloudflare - and then every request's socket address is the proxy's
    (typically 127.0.0.1 on the same box), so `is_operator_addr` would hand
    operator power to every visitor on the internet. Any TLS/proxy deployment
    therefore REQUIRES real authentication regardless of what
    `operator_requires_auth` says. Turning on TLS can only make auth stricter,
    never weaker - the safe direction.
    """
    return bool(CONFIG.get("operator_requires_auth", False)
                or CONFIG.get("behind_tls", False))


def token() -> Optional[str]:
    """The deployment's operator secret, created once on first use."""
    try:
        if TOKEN_FILE.exists():
            t = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if t:
                return t
        t = secrets.token_urlsafe(32)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(t, encoding="utf-8")
        try:                                  # best effort; no-op on Windows
            TOKEN_FILE.chmod(0o600)
        except Exception:
            pass
        return t
    except Exception:
        return None                           # caller fails closed


def _presented(headers) -> Optional[str]:
    """The token this request is offering, from a cookie or a header.

    Deliberately NOT from the query string. A token in a URL is written to
    every proxy log between here and the browser, kept in history, and leaked
    in the Referer header of any outbound link on the page.
    """
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


def check(headers, client_ip: str) -> bool:
    """May this request act as the operator?

    When auth is off, this is the old address rule - correct for a hub on a
    private tailnet and stated as such. When auth is on, the address is
    IGNORED ENTIRELY, because behind a proxy it is the proxy's address and
    carries no information about who is calling.
    """
    if not required():
        # 🚨 A FORWARDING HEADER PROVES A PROXY IS IN FRONT, and then the socket
        # address is the proxy's - meaningless for "is this the operator". This
        # closes the one gap the bind backstop cannot see: a proxy in front of a
        # LOOPBACK bind with behind_tls unset, where every internet request
        # arrives from 127.0.0.1 and would otherwise be trusted as the operator.
        # An honest direct LAN/loopback client never sends one of these, so their
        # presence is proof we must fail closed rather than trust the address.
        for _h in ("X-Forwarded-For", "X-Real-IP", "Forwarded",
                   "X-Forwarded-Host", "X-Forwarded-Proto"):
            if headers.get(_h):
                return False
        return is_operator_addr(client_ip)
    want = token()
    got = _presented(headers)
    if not want or not got:
        return False
    return secrets.compare_digest(want, got)


def cookie_header(value: str, clear: bool = False) -> str:
    """A Set-Cookie value for the operator session."""
    parts = [f"{COOKIE}={'' if clear else value}",
             "Path=/", "HttpOnly", "SameSite=Strict"]
    parts.append("Max-Age=0" if clear else f"Max-Age={MAX_AGE}")
    # Secure only when the deployment is actually behind TLS. Setting it on a
    # plain-http LAN hub would make the cookie silently never arrive, which
    # reads as "login is broken" rather than "you need https".
    if CONFIG.get("behind_tls", False):
        parts.append("Secure")
    return "; ".join(parts)


def login(body: dict) -> Optional[str]:
    """Check a submitted token; returns the cookie value to set, or None."""
    want, got = token(), (body or {}).get("token", "")
    if not want or not got:
        return None
    # Sleep a little on every attempt, right or wrong. This endpoint is the
    # only guessable thing on the deployment and a bare loop would otherwise
    # get thousands of tries a second.
    time.sleep(0.3)
    return want if secrets.compare_digest(want, str(got)) else None
