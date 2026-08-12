"""SparrowMap - snapshot handling.

THE RULE HERE: SparrowMap stores a crop of the vehicle, not the frame it came
from. That single choice does most of the privacy work that face blurring is
usually asked to do, and it does it without needing a face detector to be
correct. The pedestrian on the sidewalk, the number on the house behind, the
kid in the yard - none of them are in a tight crop of a car's rear end.

Full-frame storage is possible but must be switched on deliberately, and when
it is on, face blurring becomes mandatory rather than optional.
"""

from __future__ import annotations

import hashlib
import io
import secrets
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from core import CONFIG, SNAPS

# Crop is expanded by this fraction beyond the detected vehicle box, so the
# vehicle is not clipped. Small on purpose.
CROP_PAD = 0.12
MAX_EDGE = 900          # stored snapshots are downscaled to this longest edge
JPEG_QUALITY = 82


def _font(size: int):
    for name in ("consola.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _stamp(img: Image.Image, lines: list[str], watermark: str = "") -> Image.Image:
    """Burn provenance into the image itself.

    A snapshot that travels without its origin is a rumour. Time, camera and
    the fact that the picture came from an automated system are part of the
    evidence, so they are drawn on the pixels rather than left in a sidecar
    that gets stripped the first time somebody screenshots it.
    """
    d = ImageDraw.Draw(img, "RGBA")
    f = _font(max(11, img.width // 55))
    pad = 6
    h = (f.size + 3) * len(lines) + pad * 2
    d.rectangle([0, img.height - h, img.width, img.height], fill=(0, 0, 0, 165))
    y = img.height - h + pad
    for ln in lines:
        d.text((pad, y), ln, font=f, fill=(235, 235, 235, 255))
        y += f.size + 3
    if watermark:
        wf = _font(max(14, img.width // 22))
        d.text((pad, pad), watermark, font=wf, fill=(255, 90, 90, 210))
    return img


def redact_plate(img: Image.Image, plate_box: tuple) -> Image.Image:
    """Destroy the plate characters in an image. Pixelate, then bar.

    THIS IS THE FIX FOR THE MOST OBVIOUS HOLE IN THE WHOLE DESIGN. Hashing a
    plate in the database accomplishes nothing if the snapshot stored beside it
    is a photograph of that plate at 24 point. Every plate-reader system takes a
    picture of the plate - that is the entire point of the picture - so a
    private tier that keeps the image keeps the plate.

    We already know exactly where the characters are, because the detector had
    to localise them in order to read them. So we paint over them.

    Pixelation ALONE is not enough: a plate has a tiny character set and a fixed
    layout, which makes pixelated text recoverable by brute force (render every
    candidate, downsample, compare). So the pixelation is followed by an opaque
    bar. The pixelation is what survives if a future edit removes the bar; the
    bar is what actually does the work today.
    """
    x0, y0, x1, y1 = (int(v) for v in plate_box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return img

    region = img.crop((x0, y0, x1, y1))
    small = region.resize((max(1, region.width // 14), max(1, region.height // 8)),
                          Image.BILINEAR)
    img.paste(small.resize(region.size, Image.NEAREST), (x0, y0))

    d = ImageDraw.Draw(img)
    d.rectangle([x0, y0, x1, y1], fill=(24, 26, 30))

    # Label the bar so a person looking at the image knows the plate was
    # deliberately destroyed rather than merely unreadable. Shrink to fit;
    # a plate box can be small at distance.
    msg = "PLATE NOT STORED"
    bw, bh = x1 - x0, y1 - y0
    for size in range(max(6, bh // 2), 5, -1):
        f = _font(size)
        if d.textlength(msg, font=f) <= bw - 4:
            d.text((x0 + (bw - d.textlength(msg, font=f)) / 2,
                    y0 + (bh - size) / 2), msg, font=f, fill=(122, 132, 148))
            break
    return img


def store_crop(frame: Image.Image, bbox: Optional[tuple], meta: dict,
               plate_box: Optional[tuple] = None,
               plate_boxes: Optional[list] = None) -> tuple[str, str]:
    """Crop to the vehicle, redact if private, stamp it, write it.

    ``bbox`` is the vehicle box (x0, y0, x1, y1) in frame pixels.
    ``plate_box`` is the plate box in the SAME frame coordinates. It is
    required whenever the sighting is private tier; passing None there raises,
    because silently storing a readable plate is the failure this whole module
    exists to prevent.
    """
    img = frame.convert("RGB")

    # Redact BEFORE cropping, while the boxes are still in frame coordinates.
    if meta.get("tier") != "public":
        # EVERY candidate box, not just the detector's favourite. It picks by
        # area, and a marked vehicle's door livery is a larger patch of white
        # text than its plate - so the "best" box covered the livery and left
        # the plate readable. A plate surviving is the one failure this module
        # exists to prevent; an over-painted livery is cosmetic.
        boxes = list(plate_boxes or [])
        if plate_box and tuple(plate_box) not in {tuple(b) for b in boxes}:
            boxes.append(plate_box)
        for _b in boxes:
            img = redact_plate(img, tuple(_b))
        if not boxes and meta.get("plate_text"):
            raise ValueError(
                "private-tier snapshot with a known plate but no plate_box to "
                "redact. Refusing to store a readable plate.")

    if bbox:
        x0, y0, x1, y1 = bbox
        pw, ph = (x1 - x0) * CROP_PAD, (y1 - y0) * CROP_PAD
        box = (max(0, int(x0 - pw)), max(0, int(y0 - ph)),
               min(img.width, int(x1 + pw)), min(img.height, int(y1 + ph)))
        img = img.crop(box)

        # 🚨 REMOVE THE SURROUNDINGS. See isolate.py for why this matters more
        # than it looks: the node's coordinates are jittered so nobody learns
        # which house is watching, and a photograph of the neighbours' porch
        # hands that straight back. the operator caught it, and he is currently the
        # only person exposed by it.
        #
        # Done AFTER the crop: segmentation runs on a much smaller image, and
        # the vehicle of interest is by construction the one in the middle.
        if CONFIG.get("strip_snapshot_background", True):
            try:
                import cv2
                import numpy as np

                import isolate
                w2, h2 = img.width, img.height
                arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                # Only the instance in the middle of the crop - a street scene
                # holds several vehicles and this snapshot is about one of them.
                centre = (w2 * 0.18, h2 * 0.18, w2 * 0.82, h2 * 0.82)
                arr, method = isolate.strip(arr, centre)
                img = Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
                meta["_isolation"] = method
                if method != "segment":
                    # Never let a downgrade pass unremarked. A partially-masked
                    # image still carries background inside the box, and a
                    # function that quietly weakens its own privacy guarantee
                    # is the failure this whole feature exists to correct.
                    print(f"[snapshot] background only partly removed "
                          f"(method={method})")
            except ImportError as exc:
                # Refuse rather than publish unmasked. Same posture as
                # blur_faces: a privacy control that quietly no-ops is worse
                # than not claiming to have one.
                raise ValueError(
                    f"cannot remove snapshot background ({exc}); refusing to "
                    f"store an image that would show the camera's surroundings. "
                    f"Set strip_snapshot_background=false to publish anyway."
                ) from exc
    elif CONFIG.get("crop_only", True):
        raise ValueError(
            "store_crop called with no vehicle box while crop_only is on. "
            "Refusing to store a full frame; that is how bystanders end up in "
            "a public database.")
    elif CONFIG.get("blur_faces", True):
        img = blur_faces(img)

    if max(img.size) > MAX_EDGE:
        s = MAX_EDGE / max(img.size)
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)

    ts = meta.get("ts", time.time())
    lines = [
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
        f"{meta.get('node_name', meta.get('node_id', 'node'))}  |  SparrowMap",
    ]
    if meta.get("tier") == "public" and meta.get("plate_text"):
        lines.append(f"plate {meta['plate_text']}  ({meta.get('vclass', '?')})")
    img = _stamp(img, lines, watermark=meta.get("watermark", ""))

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    name = f"{int(ts)}_{secrets.token_hex(4)}.jpg"
    (SNAPS / name).write_bytes(data)
    return name, sha


MAX_UPLOAD_BYTES = 6 * 1024 * 1024


def decode_bytes(data_url: str) -> bytes:
    """The re-encoded JPEG bytes of a submitted data URL.

    Goes through decode_upload so the EXIF strip is not bypassed - a phone
    photo's EXIF carries the exact GPS fix and the device identity of whoever
    sent it, and the bank is not exempt from that.
    """
    img = decode_upload(data_url)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def decode_upload(data_url: str) -> Image.Image:
    """Turn a submitted data URL into an image, with the size cap enforced."""
    import base64
    head, _, b64 = data_url.partition(",")
    if "image" not in head:
        raise ValueError("not an image data URL")
    raw = base64.b64decode(b64, validate=False)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"image over {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    img = Image.open(io.BytesIO(raw))
    img.load()
    # Re-encode rather than storing what arrived: strips EXIF, which on a phone
    # photo carries the exact GPS fix and the device identity of whoever
    # submitted it. Contributors should not be doxxed by their own evidence.
    return img.convert("RGB")


def store_submitted(data_url: str, meta: dict, vehicle_box: tuple,
                    plate_box: Optional[tuple] = None,
                    plate_boxes: Optional[list] = None) -> str:
    """Store a frame a CAMERA NODE sent, cropped to the vehicle it detected.

    🚨 THIS EXISTS BECAUSE CAMERA NODES WERE STORING WHOLE FRAMES.
    run_live.py posts its best frame, and the ingest path sent every submitted
    image through `store_prepared` - which crops to (0, 0, w, h), i.e. does not
    crop. So each sighting from his window stored a 900x506 view of the street:
    the neighbours' houses, whoever was on their porch, and the plates of OTHER
    parked vehicles, which are not redacted because only the tracked vehicle's
    plate box is passed in.

    Note how it got past the guard. `store_crop` refuses a missing bbox while
    crop_only is on - but a full-frame box is not missing, it is present and
    meaningless, so the check passed and did nothing. A guard that tests for
    presence cannot catch a value that is the wrong shape.

    The node knows exactly which box it detected, so it sends it and we crop
    to it here.
    """
    img = decode_upload(data_url)
    if not vehicle_box:
        raise ValueError("camera submission with no vehicle_box to crop to")
    name, _sha = store_crop(img, tuple(vehicle_box), meta, plate_box=plate_box,
                            plate_boxes=plate_boxes)
    return name


def store_prepared(data_url: str, meta: dict,
                   plate_box: Optional[tuple] = None) -> str:
    """Store an image a HUMAN framed and sent from a phone.

    ⚠️ PHONE SUBMISSIONS ONLY. There is no detector box to crop to because a
    person aimed the camera at the vehicle deliberately - their framing IS the
    crop, and it is the only case where storing the submitted extent is right.
    A camera node has a detector and must use `store_submitted`; routing one
    through here stores the whole street. See that function.
    """
    img = decode_upload(data_url)
    name, _sha = store_crop(img, (0, 0, img.width, img.height), meta,
                            plate_box=plate_box)
    return name


# A plate is roughly a tenth of a vehicle's width. Below this longest edge the
# plate occupies about 20px, which is under what any OCR - or any person -
# recovers characters from. Measured against the project's own reader: it stops
# returning anything at all well above this size.
SUBRES_MAX_EDGE = 200


def store_subresolution(data_url: str, meta: dict) -> str:
    """Store a crop that is too small to carry a readable plate.

    🚨 THE SIZE IS VERIFIED HERE, NOT TRUSTED FROM THE CLIENT.

    This exists for phone nodes. A phone can run a vehicle detector in a
    browser but not a plate detector on top of it, so it cannot mark a plate
    for redaction - and the rule elsewhere in this file is that an image whose
    plate cannot be located is discarded, because a photograph of a car is a
    photograph of its plate.

    A device can still do something better than locating the plate: destroy it.
    Downscaling the crop below plate legibility on the phone means no readable
    plate ever crosses the network at all, which is a stronger guarantee than
    redacting one after it arrives. The training pipeline degrades crops to
    140-420px anyway, so nothing of value is lost.

    The client claiming it downscaled would be worthless. The image is decoded
    and measured, and anything larger is refused outright rather than quietly
    resized - a submission over the limit means the node is not behaving as it
    claims, and silently fixing it would hide that.
    """
    img = decode_upload(data_url)
    if max(img.size) > SUBRES_MAX_EDGE:
        raise ValueError(
            f"sub-resolution submission is {img.width}x{img.height}; "
            f"longest edge must be <= {SUBRES_MAX_EDGE}px")
    name, _sha = store_crop(img, (0, 0, img.width, img.height), meta)
    return name


def subresolution_bytes(data_url: str) -> bytes:
    """The bytes of a sub-resolution (plate-illegible) crop, for the relay inbox.

    Same guarantee as store_subresolution and enforced the same way: the image
    is decoded and MEASURED, and anything a plate could be read from is refused
    rather than quietly shrunk. Returns re-encoded JPEG bytes so a mirror can
    park the crop for the home classifier without storing it as a snapshot.
    """
    import io
    img = decode_upload(data_url)
    if max(img.size) > SUBRES_MAX_EDGE:
        raise ValueError(
            f"sub-resolution submission is {img.width}x{img.height}; "
            f"longest edge must be <= {SUBRES_MAX_EDGE}px")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def downscale_to_subres(data_url: str) -> bytes:
    """Shrink a crop to plate-illegibility for the review pen.

    subresolution_bytes REFUSES an oversized crop, which is right for a phone
    node that must downscale on-device. A CAMERA node sends a full-size crop, so
    the mirror shrinks it here instead: the longest edge is brought down to
    SUBRES_MAX_EDGE, which destroys any readable plate before the crop is ever
    parked in the pen or shown to a reviewer. EXIF is stripped by decode_upload.
    """
    import io
    img = decode_upload(data_url)
    if max(img.size) > SUBRES_MAX_EDGE:
        s = SUBRES_MAX_EDGE / max(img.size)
        img = img.resize((max(1, int(img.width * s)),
                          max(1, int(img.height * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=82)
    return buf.getvalue()


def crop_to_subres(data_url: str, vehicle_box: tuple) -> bytes:
    """Crop a camera node's FRAME to its vehicle, then shrink it for the pen.

    🚨 THE PEN WAS HOLDING WHOLE STREETS.
    `downscale_to_subres` says "a CAMERA node sends a full-size crop" and only
    resizes. That belief is wrong, and `store_submitted` says so directly two
    hundred lines up: run_live.py posts its best FRAME, and the server crops it
    to the vehicle box. So the published snapshot was correctly cropped while
    the review-pen copy of the same sighting was the entire frame, merely made
    small - the neighbours' houses, their porches, other vehicles - shown to
    every reviewer holding a token.

    Two functions held contradictory beliefs about one field, and the pen
    believed the wrong one. Same failure as the 900x506 bug that created
    store_submitted, one surface later: fixing a leak on the path you are
    looking at does not fix the second path that reads the same input.

    Cropping first also makes the downscale do its real job: 200px across a
    whole street leaves a vehicle a few pixels wide, while 200px across the
    vehicle is a picture a human can actually judge - so this improves the
    review it protects.
    """
    import io
    img = decode_upload(data_url)
    x0, y0, x1, y1 = vehicle_box
    pw, ph = (x1 - x0) * CROP_PAD, (y1 - y0) * CROP_PAD
    img = img.crop((max(0, int(x0 - pw)), max(0, int(y0 - ph)),
                    min(img.width, int(x1 + pw)), min(img.height, int(y1 + ph))))
    if max(img.size) > SUBRES_MAX_EDGE:
        s = SUBRES_MAX_EDGE / max(img.size)
        img = img.resize((max(1, int(img.width * s)),
                          max(1, int(img.height * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=82)
    return buf.getvalue()


def blur_faces(img: Image.Image) -> Image.Image:
    """Blur faces in a full frame.

    Only reachable when crop-only mode has been switched off. Requires OpenCV;
    if it is missing we refuse rather than silently storing unblurred faces,
    because a privacy control that quietly no-ops is worse than not claiming
    to have one.
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "blur_faces needs OpenCV (pip install opencv-python). Refusing to "
            "store a full frame with faces intact.") from exc

    from PIL import ImageFilter
    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    for (x, y, w, h) in cascade.detectMultiScale(gray, 1.15, 5, minSize=(24, 24)):
        region = img.crop((x, y, x + w, y + h)).filter(
            ImageFilter.GaussianBlur(radius=max(w, h) / 6.0))
        img.paste(region, (x, y))
    return img


# --------------------------------------------------------------------------
# Simulated frames, for building and demoing with no hardware attached.
# --------------------------------------------------------------------------

def render_simulated(veh: dict, node_name: str, ts: float) -> tuple:
    """Draw a plausible camera frame for a simulated vehicle.

    Returns ``(image, vehicle_box, plate_box)``. The two boxes stand in for
    what a real detector hands back, so the simulator exercises the same
    redaction path that live cameras will.

    Deliberately stylised and watermarked SIMULATED. A synthetic image that
    could be mistaken for a real capture is a liability, so this one cannot be.
    """
    W, H = 640, 400
    img = Image.new("RGB", (W, H), (26, 30, 38))
    d = ImageDraw.Draw(img)

    for i in range(H // 2):                       # sky
        t = i / (H / 2)
        d.line([(0, i), (W, i)], fill=(int(26 + 22 * t), int(30 + 26 * t), int(38 + 34 * t)))
    d.rectangle([0, H // 2, W, H], fill=(38, 38, 42))   # road
    for x in range(0, W, 70):
        d.line([(x, H - 40), (x + 34, H - 40)], fill=(120, 118, 100), width=4)

    col = {"white": (225, 227, 230), "black": (32, 33, 36), "silver": (176, 180, 186),
           "blue": (48, 86, 160), "red": (162, 48, 48), "grey": (110, 113, 120),
           "green": (56, 120, 84)}.get(veh.get("color", "silver"), (176, 180, 186))

    bx0, by0, bx1, by1 = 150, 150, 490, 320
    d.rounded_rectangle([bx0, by0 + 34, bx1, by1], 14, fill=col)          # body
    d.rounded_rectangle([bx0 + 46, by0, bx1 - 46, by0 + 66], 10, fill=col)  # cabin
    d.rounded_rectangle([bx0 + 58, by0 + 10, bx1 - 58, by0 + 58], 8,
                        fill=(18, 22, 30))                                # glass
    for wx in (bx0 + 46, bx1 - 92):                                       # wheels
        d.ellipse([wx, by1 - 30, wx + 46, by1 + 16], fill=(22, 22, 24))
        d.ellipse([wx + 12, by1 - 18, wx + 34, by1 + 4],
                  fill=(190, 190, 195) if veh.get("steelies") else (70, 70, 74))

    if veh.get("light_bar"):
        d.rounded_rectangle([bx0 + 96, by0 - 18, bx1 - 96, by0 - 2], 5, fill=(28, 28, 30))
        d.rectangle([bx0 + 100, by0 - 15, bx0 + 168, by0 - 5], fill=(60, 90, 255))
        d.rectangle([bx1 - 168, by0 - 15, bx1 - 100, by0 - 5], fill=(255, 60, 60))
    if veh.get("pillar_spotlight"):
        d.ellipse([bx0 + 34, by0 + 4, bx0 + 58, by0 + 28], fill=(210, 212, 216))
    if veh.get("push_bumper"):
        d.rectangle([bx1 - 6, by0 + 60, bx1 + 16, by1 - 10], fill=(46, 48, 52))
    if veh.get("livery"):
        d.rectangle([bx0 + 10, by0 + 70, bx1 - 10, by0 + 116], fill=(245, 246, 248))
    if veh.get("agency_decal"):
        d.text((bx0 + 26, by0 + 82), veh.get("agency", "POLICE"),
               font=_font(26), fill=(20, 40, 110))

    px0, py0 = (bx0 + bx1) // 2 - 62, by1 - 44                           # plate
    d.rounded_rectangle([px0, py0, px0 + 124, py0 + 40], 5,
                        fill=(248, 249, 250), outline=(30, 30, 30), width=2)
    txt = veh.get("plate", "")
    f = _font(24)
    tw = d.textlength(txt, font=f)
    d.text((px0 + (124 - tw) / 2, py0 + 8), txt, font=f, fill=(24, 40, 96))

    vehicle_box = (bx0 - 10, by0 - 22, bx1 + 20, by1 + 20)
    plate_box = (px0, py0, px0 + 124, py0 + 40)
    return img, vehicle_box, plate_box
