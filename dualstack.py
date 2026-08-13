"""A ThreadingHTTPServer that answers on IPv6 *and* IPv4.

⚠️ THIS FIXES A 2-4 SECOND DELAY ON EVERY REQUEST. It is not a nicety.

Binding to 0.0.0.0 listens on IPv4 only. Modern clients resolve a name to its
AAAA record first, so `localhost` is tried as ::1 and `sparrow-box` as its
IPv6 address BEFORE either falls back to IPv4. Nothing is listening there, so
every request pays a connection-failure timeout first.

Measured against an IPv4-only bind on this machine:

    http://127.0.0.1:8160/api/presets          1 ms
    http://localhost:8160/api/presets       2062 ms
    http://sparrow-box:8160/api/presets 4100 ms

The endpoint was identical in all three cases and never touched the camera, so
the entire difference is the failed IPv6 attempt. It is invisible in testing
because everyone tests on 127.0.0.1, and it lands squarely on the one case that
matters - a phone opening the page by hostname.

Binding to :: with IPV6_V6ONLY cleared accepts both families on one socket, so
IPv4 clients keep working unchanged and IPv6 clients stop waiting.
"""

from __future__ import annotations

import socket
from http.server import ThreadingHTTPServer


class DualStackServer(ThreadingHTTPServer):
    """Listens on :: for both IPv6 and IPv4 (falls back to IPv4 if unavailable)."""

    address_family = socket.AF_INET6
    daemon_threads = True

    # 🚨 THE DEFAULT BACKLOG IS 5, AND IT SILENTLY DROPS CAMERAS.
    #
    # socketserver ships request_queue_size = 5, so once five connections are
    # waiting to be accepted the kernel refuses the sixth. Behind Caddy that
    # surfaces at the far end as "EOF occurred in violation of protocol" - a
    # TLS error, on a machine whose TLS is fine.
    #
    # Measured on the live box while volunteers were testing: 14 of the last 40
    # posts from a single camera failed that way, and ingest went 267 -> 241 ->
    # 50 -> 0 sightings per ten minutes while every camera stayed ONLINE,
    # because heartbeats are small and lucky and a sighting carries a JPEG.
    # The hub was listening with a queue of 5 next to caddy's 4096 and uvicorn's
    # 2048.
    #
    # A burst is exactly the normal shape of this traffic: cameras post when a
    # vehicle LEAVES frame, so a busy road delivers clumps, and a wave of new
    # volunteers testing at once delivers a much bigger one. Nothing here is
    # slow - the connections were never accepted at all.
    request_queue_size = 256

    # ⚠️ MUST BE FALSE ON WINDOWS.
    #
    # SO_REUSEADDR does NOT mean the same thing on Windows as it does on Unix.
    # On Unix it lets you rebind a port still in TIME_WAIT. On Windows it lets a
    # SECOND LIVE PROCESS bind a port another process is already listening on,
    # and the two then split incoming connections unpredictably.
    #
    # That is not theoretical: it let three copies of the camera app run at
    # once, each fighting the others for exclusive access to the webcam, which
    # presented as "the camera only manages 5 fps" and "the camera will not
    # open". With this False, a second instance fails immediately and loudly,
    # which is the correct behaviour for anything owning a single device.
    allow_reuse_address = False

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            # Some stacks refuse; the IPv4-only path below still works.
            pass
        return super().server_bind()


def serve(handler, port: int, host: str = "::"):
    """Build a dual-stack server, degrading to IPv4 if IPv6 is unavailable.

    Raises SystemExit with a clear message if the port is already taken, so a
    second copy of a single-device app cannot start silently.

    🚨 AN EXPLICIT HOST IS HONOURED EXACTLY, NEVER WIDENED.
    This used to fall back to "0.0.0.0" whenever the IPv6 bind failed - and an
    IPv4 address like 127.0.0.1 ALWAYS fails on an AF_INET6 socket. So asking
    for loopback-only silently produced a listener on every interface: the
    deployment would have exposed 8150 straight to the internet, past nginx and
    therefore past every security header, the rate limiter and the auth cookie.
    A bind that quietly listens more widely than asked is the worst direction
    for that mistake to go.
    """
    # An explicit IPv4 address gets an IPv4 socket. No dual-stack, no fallback,
    # no widening.
    if host and ":" not in host and host not in ("", "0.0.0.0"):
        # ⚠️ THIS BRANCH IS THE DEPLOYED ONE. The box runs SPARROW_BIND=127.0.0.1
        # behind Caddy, so it never touches DualStackServer - raising the
        # backlog only on that class would have fixed everything except the
        # server that was actually dropping cameras.
        class _Queued(ThreadingHTTPServer):
            request_queue_size = DualStackServer.request_queue_size
        try:
            srv = _Queued((host, port), handler)
        except OSError as exc:
            raise SystemExit(f"cannot bind {host}:{port}: {exc}")
        srv.daemon_threads = True
        return srv
    try:
        return DualStackServer((host, port), handler)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or exc.errno in (48, 98):
            raise SystemExit(
                f"port {port} is already in use - another copy is running.\n"
                f"  Stop it first:  Get-NetTCPConnection -LocalPort {port} "
                f"-State Listen | %{{ Stop-Process -Id $_.OwningProcess }}")
        try:
            # Only reached for a wildcard request, so widening to 0.0.0.0 is
            # what was asked for rather than a surprise.
            srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
        except OSError:
            raise SystemExit(f"cannot bind port {port}: {exc}")
        srv.daemon_threads = True
        srv.allow_reuse_address = False
        print(f"[http] IPv6 unavailable, IPv4 only on {port} "
              f"(expect a delay for clients that resolve AAAA first)")
        return srv
