"""The background strip, with no OpenCV. The fallback that keeps the box honest.

🚨 WHY THIS EXISTS: THE PUBLIC MIRROR HAS NO cv2, SO IT COULD NOT STORE A
PHOTOGRAPH AT ALL - AND SAID NOTHING.

`isolate.py` imports cv2 and numpy at module level. The box is deliberately a
stdlib-plus-Pillow machine (see DEPLOY.md: the detector runs at home, the mirror
carries claims), so that import raises, `store_crop` turned the ImportError into
`ValueError("cannot remove snapshot background ...")`, and every caller that
wrapped image storage in a best-effort `except` created the row with `snap=None`.

That refusal was the right instinct and the wrong outcome. It was written to
stop an UNMASKED image being published; what it actually did on the only
machine that serves the public map was stop EVERY image being published, in a
way nothing measured: `/api/node/confirm` swallowed it, the operator was told
`{"ok": true}`, and the sighting went on the map with no photograph behind it.
Measured, not inferred - on the box, `store_subresolution` on a real 512x229
banked crop raises `No module named 'cv2'` every single time.

## What this does instead

`isolate.strip()` already degrades honestly: when segmentation is unavailable it
falls back to method `"box"`, which keeps the vehicle's bounding box and blanks
everything outside it. That fallback is pure geometry - a solid backdrop with
one rectangle pasted into it - and needs nothing from OpenCV. It is reproduced
here in Pillow so the mirror can do the same thing the home hub does when
segmentation fails, rather than doing nothing.

⚠️ THIS IS A DOWNGRADE AND IT MUST NEVER BE A QUIET ONE. A box-masked crop still
carries whatever is inside the box: sky above the roofline, a sliver of kerb,
whatever shows through the windows. It removes the porch, the railings and the
parked cars that let someone place the lens - which is the threat isolate.py was
written for - but it is weaker than segmentation. Every caller stamps the method
into the snapshot's metadata (`_isolation`) and prints it, so "we masked it" is
never assumed and can always be checked after the fact.

The alternative was installing OpenCV on a 2 vCPU box that has already been
melted once by CPU it did not have (see [[sparrow-outage-playbook]]), and paying
segmentation cost per crop, to get a stronger mask than the failure path the
code already accepts. Geometry is free.
"""

from __future__ import annotations

from typing import Optional

from PIL import Image

# isolate.BACKDROP is BGR because it lives in an OpenCV file. Pillow is RGB.
# Same colour, stated once here so the two files cannot drift into two greys.
# 🚨 WAS (38, 30, 26) - a warm brown that baked into every published crop and
# read as an ugly stain around the vehicle (the operator spotted it in the
# aliasing at the box edge). Now matched to the detail-card background (#0d1420)
# so the removed area VANISHES into the card - the vehicle reads as sitting on
# the card with no visible backdrop, which is the point. It is still a real
# colour (a car outline is not a rectangle, so the removed region must be
# filled) - just one that disappears where the photo is shown.
BACKDROP_RGB = (13, 20, 32)


def strip_box(img: Image.Image,
              box: Optional[tuple] = None) -> tuple[Image.Image, str]:
    """Blank everything outside `box`. Returns (image, method).

    `method` is `"box-pil"` or `"none"`, and the caller is expected to look at
    it for the same reason `isolate.strip` returns one: a function that quietly
    weakens its own privacy guarantee is the failure this whole feature exists
    to correct. `"none"` means nothing was done and the image is untouched -
    treat it exactly as you would treat a refusal.
    """
    if box is None:
        return img, "none"
    w, h = img.size
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return img, "none"
    rgb = img.convert("RGB")
    out = Image.new("RGB", rgb.size, BACKDROP_RGB)
    out.paste(rgb.crop((x0, y0, x1, y1)), (x0, y0))
    return out, "box-pil"
