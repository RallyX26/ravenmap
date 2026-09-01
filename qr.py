"""QR codes for camera keys.

## Why this is a dependency and not hand-rolled

The first version of this file was a QR encoder written from scratch, to keep
the hub dependency-free. It produced codes that looked correct and decoded to
nothing. That is the worst possible outcome for this feature: a key fails in a
volunteer's hands, at their window, with no error message anywhere.

Shipping an encoder that cannot pass its own round-trip test is worse than
adding a well-tested pure-Python dependency, so `segno` does the encoding and
this module keeps the two things that are ours: the verification, and the
gotcha below.

## 🚨 THE GOTCHA: micro=False, ALWAYS

Given a short payload, segno will happily produce a **Micro QR** - a different,
smaller symbology that a great many phone cameras and most detectors cannot
read at all. It is not an error, there is no warning, and the picture looks
like a QR code. Verified here: a 5-byte payload became `vM3` and OpenCV
decoded nothing from it; the same payload as a normal v1 decodes fine.

## What is verified

Round-tripped through OpenCV's detector - a different implementation by
different people, which is the only kind of check worth having for a format
like this. Payloads from 5 to 173 bytes decode. A real capability URL on
sparrowmap.com is about 73.

    python qr.py        # runs the round-trip test
"""

from __future__ import annotations

import io

# The longest payload actually round-tripped. Beyond this the encoder is
# probably still fine, but nothing here has PROVEN it, and a key is not the
# place to find out.
VERIFIED_MAX = 260   # covers a Sparrow Send address (~256 B base64url); see _selftest


def png(text: str, scale: int = 8, border: int = 4) -> bytes:
    """A PNG of a scannable QR code for `text`."""
    import segno
    if len(text.encode("utf-8")) > VERIFIED_MAX:
        raise ValueError(
            f"payload is {len(text)} bytes; only up to {VERIFIED_MAX} has been "
            "round-trip verified. Shorten it or extend the test in qr.py.")
    # error='m' recovers ~15%: these get photographed off screens, printed,
    # and taped to window frames.
    q = __import__("segno").make(text, error="m", micro=False)
    buf = io.BytesIO()
    q.save(buf, kind="png", scale=scale, border=border)
    return buf.getvalue()


def _selftest() -> bool:
    import cv2
    import numpy as np
    from PIL import Image
    det = cv2.QRCodeDetector()
    # 🚨 THIS TEST USED TO CARRY A REAL NODE ID AND A REAL 32-CHARACTER TOKEN.
    # It was a working credential for a live camera, sitting in a file staged for
    # publication, and a token-holder can POST sightings as that node. It was
    # pasted in because a REAL key is the honest thing to test a QR encoder
    # with - which is exactly how it survived: it looked like test data, and
    # test data does not read as a secret.
    #
    # The test needs a string of the right SHAPE, never a live one. Both values
    # below are obviously fake and the length is what matters.
    base = "https://sparrowmap.com/node#k=n_example1."
    # A Sparrow Send address is ~256 bytes of base64url (two P-256 public keys in
    # a JSON bundle). Verify with a HIGH-ENTROPY payload like a real one: cv2's
    # detector flakes on repetitive/monotone strings of that length, but genuine
    # addresses (random keys) decode reliably. Deterministic via a hash chain.
    import base64 as _b64, hashlib as _hl
    _seed, _buf = b"sparrow-send-address-selftest", b""
    while len(_buf) < 200:
        _seed = _hl.sha256(_seed).digest(); _buf += _seed
    addr256 = _b64.urlsafe_b64encode(_buf).decode().rstrip("=")[:256]
    cases = ["short", base + "A" * 32,
             base + "x" * 60, base + "x" * 130, addr256]
    ok = True
    for t in cases:
        arr = np.array(Image.open(io.BytesIO(png(t, scale=10))).convert("L"))
        got, _pts, _ = det.detectAndDecode(arr)
        good = got == t
        ok &= good
        print(f"  {'OK  ' if good else 'FAIL'} {len(t):3d} bytes  "
              f"decoded={got[:44]!r}")
    return ok


if __name__ == "__main__":
    print("ALL DECODED" if _selftest() else "BROKEN - do not ship")
