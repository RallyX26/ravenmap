"""Prove a machine with no OpenCV still MASKS and still STORES a snapshot.

🚨 WHY THIS EXISTS. The public mirror has Pillow and no cv2 on purpose - the
detector runs at home, the box carries claims (DEPLOY.md). `snapshot.store_crop`
turned that missing import into a ValueError, and every caller that wrapped
image storage best-effort swallowed it. Result: `/api/node/confirm` answered
`{"ok": true}`, the sighting went public, `reviewed=confirmed`,
`decided_by=human` - and `snap=None`. A human-confirmed claim on the map with no
photograph behind it. 16,597 sightings on the box carried 76 images, and every
one of those had been published from home.

The two things that must BOTH hold, and which this asserts separately:

  1. an image IS produced (no cv2 must not mean no photograph), and
  2. it is MASKED anyway - the corners, which is where the neighbours' porch and
     the railings live, are the flat backdrop and not the original pixels.

(2) is the one that matters. Storing an unmasked crop would be far worse than
storing nothing: the node's coordinates are jittered by up to 60 m precisely so
nobody learns which house is watching, and a photograph of the surroundings
hands that straight back. See isolate.py.

    python tools/test_no_cv2_snapshot.py

Exit code is 1 if either property fails, so it can gate a deploy.

📌 SEEN TO FAIL FIRST. Against the previous snapshot.py this test fails at step
1 with the real ValueError ("cannot remove snapshot background"), which is the
bug it was written for. A test nobody has watched fail is not evidence.
"""

from __future__ import annotations

import base64
import builtins
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image                                   # noqa: E402

# A banked training crop is 512px on the long edge - the exact shape the
# operator's confirmations arrive as.
CROP_W, CROP_H = 512, 229


def _hide_cv2():
    """Make `import cv2` fail the way it does on the box, and nothing else."""
    real_import = builtins.__import__

    def fake(name, *a, **kw):
        if name == "cv2" or name.startswith("cv2."):
            raise ImportError("No module named 'cv2'")
        return real_import(name, *a, **kw)

    builtins.__import__ = fake
    return lambda: setattr(builtins, "__import__", real_import)


def _crop_data_url() -> tuple[str, Image.Image]:
    """A crop whose corners are UNMISTAKABLE, so masking is measurable.

    Flat colour would make "masked" and "unmasked" look alike at the corners by
    luck. Loud, distinct corners cannot be confused with the backdrop.
    """
    img = Image.new("RGB", (CROP_W, CROP_H), (120, 120, 130))
    for xy, colour in (((0, 0), (255, 0, 0)),
                       ((CROP_W - 24, 0), (0, 255, 0)),
                       ((0, CROP_H - 24), (0, 0, 255)),
                       ((CROP_W - 24, CROP_H - 24), (255, 255, 0))):
        img.paste(Image.new("RGB", (24, 24), colour), xy)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return ("data:image/jpeg;base64,"
            + base64.b64encode(buf.getvalue()).decode()), img


def main() -> int:
    url, _orig = _crop_data_url()
    restore = _hide_cv2()
    try:
        import snapshot
        # The confirm route's own two steps, in order: a 512px banked crop is
        # over the 200px sub-resolution ceiling, so it is shrunk first and then
        # stored. Doing only the second is not this bug.
        small = snapshot.downscale_to_subres(url)
        meta = {"ts": 0, "node_id": "TEST", "node_name": "test",
                "tier": "public", "vclass": "police", "watermark": "CONFIRMED"}
        try:
            name = snapshot.store_subresolution(
                "data:image/jpeg;base64," + base64.b64encode(small).decode(),
                meta)
        except ValueError as exc:
            print(f"FAIL: no image stored without cv2 - {exc}")
            print("      This is the bug: the caller swallows it and the "
                  "sighting is published with snap=None.")
            return 1
    finally:
        restore()

    path = Path(snapshot.SNAPS) / name
    print(f"stored -> {name}  (isolation={meta.get('_isolation')!r})")

    if meta.get("_isolation") in (None, "none"):
        print("FAIL: stored with no isolation recorded. An image nothing was "
              "done to must never be stored.")
        return 1

    if not path.exists():
        print("FAIL: store returned a name but no file is on disk.")
        return 1

    # The measurement, not the claim: read the pixels back.
    out = Image.open(path).convert("RGB")
    import isolate_pil
    w, h = out.size
    corners = [out.getpixel(p) for p in
               ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2))]
    # JPEG is lossy and the file is watermarked, so allow a little slack -
    # but the backdrop is a dark grey and the corners were primary colours.
    bad = [c for c in corners
           if max(abs(a - b) for a, b in zip(c, isolate_pil.BACKDROP_RGB)) > 40]
    if bad:
        print(f"FAIL: corners are not the backdrop: {bad}")
        print("      The surroundings survived. This would publish the "
              "neighbours' porch and give away the camera.")
        return 1

    print(f"PASS: masked ({meta['_isolation']}) and stored without cv2; "
          f"all four corners are the backdrop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
