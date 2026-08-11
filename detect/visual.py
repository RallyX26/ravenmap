"""SparrowMap - visual signals for the police/civilian classifier.

⚠️ READ THIS BEFORE TRUSTING ANY NUMBER THIS MODULE PRODUCES.

The full visual classifier is Phase 3. It needs a few hundred locally labelled
crops and a small multi-label head, and until that exists most of the signals
in `classify.SIGNALS` simply cannot be observed from a frame. Rather than fake
them, this module returns only what it can actually measure, and returns
nothing otherwise.

That has a consequence worth stating plainly, because it looks like a failure
and is not: **on real video with no visual classifier, almost nothing reaches
the public tier.** `classify.classify` refuses to publish on plate text alone,
so with no corroborating visual signal every vehicle stays private. That is
rule 2 working exactly as designed. It is also the honest state of the system:
we can read plates today, and we cannot yet tell a patrol car from a Corolla
by looking at it.

What IS implemented is one signal, chosen because it is real, cheap, and has an
extremely low false-positive rate on ordinary traffic:

    emergency_lights - a lit red/blue light bar.

Nothing else on a public road produces saturated red and saturated blue pixels
side by side in a horizontal band across the top of a vehicle. Brake lights are
red without blue. Sky reflections are blue without red. It only fires when the
lights are actually ON, so it misses every patrol car driving normally, which
is most of them. Narrow, but when it fires it is almost never wrong - and that
is the correct shape for a signal that can make someone's plate public.
"""

from __future__ import annotations

import cv2
import numpy as np

# Upper fraction of the vehicle box searched for a light bar.
#
# ⚠️ WAS 0.34 AND THAT PUBLISHED A CIVILIAN SUV AS A POLICE VEHICLE.
# A detector box routinely extends above the roofline, so a third of the box
# includes sky. Red tail-light plus blue sky at similar heights satisfied the
# old test perfectly. A real light bar sits ON the roof, in the top sliver.
ROOF_BAND = 0.18

# HSV gates. OpenCV hue is 0-179.
RED_LO_1, RED_HI_1 = (0, 120, 120), (10, 255, 255)
RED_LO_2, RED_HI_2 = (170, 120, 120), (179, 255, 255)
BLUE_LO, BLUE_HI = (100, 120, 120), (130, 255, 255)

# Both colours must occupy at least this fraction of the roof band.
MIN_FRACTION = 0.004

# ⚠️ AND NO MORE THAN THIS. THE MISSING BOUND IS WHY IT KEEPS FIRING.
#
# Measured on his own street, 2026-08-08, on five vehicles he confirmed were
# not police: a bright blue Chevrolet Equinox put 19.8% of the roof band into
# the blue mask, and another frame put ~5% into each of red and blue. The old
# test asked only whether both colours cleared a 0.4% floor and sat side by
# side, which a blue CAR under a blue SKY passes without difficulty.
#
# A light bar is a pair of LAMPS. Lamps are small. The blue lens on a roof bar
# occupies a low single-digit percentage of the band it sits in - never a
# fifth of it. A colour covering a large field is paint or sky, and the test
# for "is this a lamp" has to include "is it lamp-SIZED".
MAX_FRACTION = 0.06

# A lamp is also COMPACT: its pixels cluster. Paint and sky are spread across
# the whole band. Measured as the share of the colour's pixels falling inside
# the tightest quarter-width column around their own centroid - a lamp keeps
# nearly all of them, a painted roof keeps roughly a quarter.
MIN_COMPACTNESS = 0.60


# ---------------------------------------------------------------------------
# PUSH BUMPER and A-PILLAR SPOTLIGHT
#
# 🚨 THESE ARE CRUDE SIGNALS AND CRUDE SIGNALS HAVE PUBLISHED CIVILIANS HERE.
# `light_bar` - the only other hand-written detector in this file - fired twice
# in 1,595 labelled crops and BOTH were vehicles the operator labelled civilian.
# So neither of these reaches `classify.py` on the strength of looking sensible.
# Each is gated by MEASURED precision on the local labelled bank
# (tools\measure_visual.py) and stays behind ENABLE below until it earns it.
#
# Why they are worth building anyway: the two-marker publishing rule requires
# two DISTINCT visual markers before a police sighting goes public, and only one
# visual marker was implemented - so in practice the trained head was the only
# thing that could fire and "two markers" collapsed into one observation counted
# twice. That is the exact defect the rule exists to stop.
#
# 📐 THE MINIMUM SIZES ARE NOT GUESSED. They come from tools\feature_budget.py:
# a feature of real width W metres on a vehicle ~5 m long rendered `crop_px`
# wide is rendered `crop_px * W / 5` px, and each task has a px floor.
#   push bumper   1.60 m, needs 20 px (shape)        -> crop >=  63 px
#   A-pillar lamp 0.18 m, needs 14 px (shape)        -> crop >= 389 px
# Below those the answer is "we did not look", never "there is none".
MIN_CROP_PUSH_BUMPER = 63
MIN_CROP_SPOTLIGHT = 389

# Set from the measurement, not from taste. A signal that has not been measured
# on this camera does not fire.
#
# 🚨 BOTH NEW DETECTORS ARE OFF, AND THE MEASUREMENT IS WHY (2026-08-10,
# tools\measure_visual.py over 56 government and 1,235 ordinary local crops):
#
#                    recall   fires on ordinary traffic   of firings, actually gov
#   push_bumper       17.9%            23.35%                      0.2%
#   pillar_spotlight   3.9%            11.50%                      0.1%
#
# The last column is the one that decides it. Ordinary traffic outnumbers
# government vehicles about 500 to 1 on this street, so a detector firing on a
# quarter of ordinary cars produces ~1,000 false claims for every true one. A
# balanced-sample "precision 3.7%" already sounds bad; the real figure is 0.2%.
#
# Masking the vehicle was a large, real improvement - the spotlight fell from
# 86.9% to 11.5% false firing once it stopped reading sky - and it was still not
# close. The honest conclusion is that at this scale these two markers are not
# separable by hand-written geometry, and no threshold search fixes that. What a
# threshold search WOULD do is find a cut that flatters 56 positives.
ENABLE = {"light_bar": True, "push_bumper": False, "pillar_spotlight": False}


def _plate_bar_mask(bgr: np.ndarray) -> np.ndarray:
    """Where a stored crop has had its plate blacked out.

    🚨 THE REDACTION IS A PERFECT FAKE PUSH BUMPER, AND IT IS ONLY IN THE
    MEASUREMENT SET. `detect/bank.py` pixelates the plate and then draws a
    SOLID BLACK RECTANGLE over it. Its two vertical sides are a pair of tall,
    parallel, perfectly dark edges sitting low on the front of the vehicle -
    which is the literal description of what a push-bumper detector looks for.
    At runtime `run_live.py` calls signals() on the RAW frame, so production
    never sees this; only the banked crops do. Measuring against it would have
    scored a detector on an artefact that does not exist in production, and the
    result would have looked like a working signal.

    Exact zero is the tell. The bar is drawn (0,0,0); real shadow almost never
    reaches all three channels at zero.
    """
    return (bgr.max(axis=2) == 0).astype(np.uint8)


def push_bumper(bgr: np.ndarray, ignore: np.ndarray | None = None,
                mask: np.ndarray | None = None) -> dict:
    """Two to four tall, dark, near-vertical bars low on the vehicle's front.

    A push bumper is a small number of THICK uprights that run from below the
    bumper up across the grille. The thing it must not be confused with is a
    vertical-slatted GRILLE, which is many THIN slats confined to the grille
    aperture - so the count is bounded above as well as below, and each bar has
    to span a real share of the region's height rather than sitting inside it.

    🚨 `mask` IS REQUIRED, AND THAT REQUIREMENT IS THE WHOLE FIX.
    The first version reasoned about the bounding BOX and fired on 28.9% of
    ordinary traffic. Measured cause: **a vehicle covers only 56-70% of its own
    box** (yolo11n-seg over this bank), so a third of every "lower front region"
    was road, kerb, shadow and foliage - and dark vertical clutter is exactly
    what a road edge and a fence post look like. No amount of threshold tuning
    fixes reading the wrong pixels.

    Without a mask this returns "not observed", never "no push bumper". That is
    this module's whole contract: absent means we did not look.

    Returns the measurements as well as the verdict, so a false positive can be
    explained instead of argued about (see detect/diag_falsepos.py).
    """
    h, w = bgr.shape[:2]
    out = {"fired": False, "bars": 0, "why": ""}
    if w < MIN_CROP_PUSH_BUMPER:
        out["why"] = f"crop {w}px < {MIN_CROP_PUSH_BUMPER}px floor"
        return out
    if mask is None:
        out["why"] = "no vehicle mask; not observed"
        return out
    # Lower 55%: below the beltline. The B and C pillars are dark and vertical
    # too, and they live above it.
    y0 = int(h * 0.45)
    reg = bgr[y0:, :]
    if reg.size == 0:
        return out
    rh, rw = reg.shape[:2]
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    dark = (hsv[:, :, 2] < 80).astype(np.uint8)
    # Only pixels that ARE the vehicle. Eroded, because a mask edge traces the
    # boundary between a dark car and a bright road, and that boundary is itself
    # a tall dark line if you let it in.
    mreg = (mask[y0:, :] > 0).astype(np.uint8)
    mreg = cv2.erode(mreg, np.ones((3, 3), np.uint8), iterations=2)
    dark = dark * mreg
    if ignore is not None:
        dark[ignore[y0:, :] > 0] = 0
    # A bar is TALL and THIN: opening with a tall narrow kernel keeps uprights
    # and deletes the vehicle's own shadow, which is wide and flat.
    kh = max(3, int(rh * 0.45))
    kw = max(1, int(rw * 0.012))
    op = cv2.morphologyEx(dark, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh)))
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(op, 8)
    bars = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if ch < rh * 0.45:
            continue                       # not tall enough to cross the bumper
        if cw <= 0 or ch / cw < 3.0:
            continue                       # not thin enough to be an upright
        if cw > rw * 0.18:
            continue                       # that is a body panel, not a bar
        bars.append((x, y, cw, ch))
    out["bars"] = len(bars)
    if not (2 <= len(bars) <= 4):
        out["why"] = f"{len(bars)} bars (need 2-4)"
        return out
    # Uprights on one bumper sit at similar heights. Scattered dark slivers do
    # not, and that is what separates a bumper from clutter.
    tops = [b[1] for b in bars]
    if max(tops) - min(tops) > rh * 0.30:
        out["why"] = "bars not at a common height"
        return out
    out["fired"] = True
    return out


def pillar_spotlight(bgr: np.ndarray, mask: np.ndarray | None = None) -> dict:
    """A small lamp PROTRUDING from the silhouette at the base of the A-pillar.

    ⚠️ THE WEAKEST DETECTOR IN THIS FILE AND THE ONE MOST LIKELY TO BE WRONG.
    The feature is 0.18 m - the second smallest thing on the vehicle after the
    plate - and at this camera's median crop it renders about 18 px against a
    14 px floor. That is 1.3x headroom, which is marginal.

    🚨 THE FIRST VERSION LOOKED FOR A BRIGHT COMPACT BLOB AND FIRED ON 86.9% OF
    ORDINARY TRAFFIC. It was reading the bounding box, and the "front third" of
    a box is mostly SKY and road - bright, washed out, and compact wherever it
    shows between branches. Brightness was never the signature.

    ✅ The real signature is GEOMETRIC: a spotlight sticks OUT of the vehicle's
    outline. Every car has bright highlights; almost none has a small lump
    protruding from the silhouette just ahead of the door, below the roofline.
    So this measures the outline, not the pixels, and needs the mask to do it.
    """
    h, w = bgr.shape[:2]
    out = {"fired": False, "why": ""}
    if w < MIN_CROP_SPOTLIGHT:
        out["why"] = f"crop {w}px < {MIN_CROP_SPOTLIGHT}px floor"
        return out
    if mask is None:
        out["why"] = "no vehicle mask; not observed"
        return out
    m = (mask > 0).astype(np.uint8)
    # A protrusion is what an opening REMOVES: open the silhouette with a disc
    # a little larger than the lamp, and whatever disappears was a lump.
    lamp_px = max(3, int(w * 0.18 / 5.0))          # 0.18 m on a ~5 m vehicle
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                  (lamp_px | 1, lamp_px | 1))
    smooth = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    bumps = cv2.subtract(m, smooth)
    # Only the A-pillar band: below the roofline, above the beltline. And only
    # near the front, which a crop does not identify - so both ends qualify.
    band = np.zeros_like(bumps)
    band[int(h * 0.20):int(h * 0.55), :int(w * 0.28)] = 1
    band[int(h * 0.20):int(h * 0.55), int(w * 0.72):] = 1
    bumps = bumps * band
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(bumps, 8)
    for i in range(1, n):
        _x, _y, cw, ch, area = stats[i]
        if area < max(6, lamp_px * lamp_px * 0.25):
            continue                                # noise on the mask edge
        if area > lamp_px * lamp_px * 4:
            continue                                # a mirror or a mask error
        if cw == 0 or ch == 0 or not (0.5 <= ch / cw <= 2.0):
            continue                                # a lamp is roughly round
        out["fired"] = True
        out["why"] = f"silhouette bump {cw}x{ch}px"
        return out
    out["why"] = "no lamp-sized protrusion on the A-pillar outline"
    return out


def signals(frame_bgr: np.ndarray, vbox: tuple) -> dict:
    """Return the evidence dict for one vehicle. Keys match classify.SIGNALS.

    Absent keys mean "not observed", never "observed to be false". The
    classifier treats a missing signal as no evidence, which is the correct
    reading: we did not look, so we do not know.
    """
    ev: dict = {}
    h, w = frame_bgr.shape[:2]
    x0, y0, x1, y1 = (max(0, int(vbox[0])), max(0, int(vbox[1])),
                      min(w, int(vbox[2])), min(h, int(vbox[3])))
    if x1 - x0 < 40 or y1 - y0 < 40:
        return ev

    band = frame_bgr[y0:y0 + int((y1 - y0) * ROOF_BAND), x0:x1]
    if band.size == 0:
        return ev

    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    red = (cv2.inRange(hsv, np.array(RED_LO_1), np.array(RED_HI_1)) |
           cv2.inRange(hsv, np.array(RED_LO_2), np.array(RED_HI_2)))
    blue = cv2.inRange(hsv, np.array(BLUE_LO), np.array(BLUE_HI))

    n = band.shape[0] * band.shape[1]
    r_frac, b_frac = red.sum() / 255 / n, blue.sum() / 255 / n

    lamp_sized = (MIN_FRACTION < r_frac < MAX_FRACTION
                  and MIN_FRACTION < b_frac < MAX_FRACTION)
    if lamp_sized:
        r_rows, r_cols = np.nonzero(red)
        b_rows, b_cols = np.nonzero(blue)
        if len(r_rows) and len(b_rows):
            same_height = abs(r_rows.mean() - b_rows.mean()) < band.shape[0] * 0.25
            # A light bar is a HORIZONTAL STRIP: the two colours sit side by
            # side across the roof. A tail-light and a patch of sky are
            # separated VERTICALLY and usually not adjacent horizontally. The
            # old test only checked height, which a red car under a blue sky
            # passes trivially - and one did, and it was published.
            side_by_side = abs(r_cols.mean() - b_cols.mean()) > band.shape[1] * 0.08
            # Emergency lights are LIT, so both colours should be bright, not
            # merely saturated. Sky is bright but paint is not.
            v = hsv[:, :, 2]
            bright = (v[red > 0].mean() > 150) and (v[blue > 0].mean() > 150)
            # ...and each colour must be a CLUSTER, not a field. See
            # MIN_COMPACTNESS.
            compact = (_compactness(r_cols, band.shape[1]) >= MIN_COMPACTNESS
                       and _compactness(b_cols, band.shape[1]) >= MIN_COMPACTNESS)
            if same_height and side_by_side and bright and compact:
                ev["light_bar"] = True
                ev["_emergency_lights_lit"] = True

    # The vehicle crop, which is what both detectors below reason about. The
    # runtime frame is unredacted; `_plate_bar_mask` is a no-op on it and only
    # bites on stored crops. Passing it always means the measurement path and
    # the production path run identical code.
    veh = frame_bgr[y0:y1, x0:x1]
    if veh.size:
        if ENABLE.get("push_bumper"):
            pb = push_bumper(veh, _plate_bar_mask(veh))
            if pb["fired"]:
                ev["push_bumper"] = True
        if ENABLE.get("pillar_spotlight"):
            sl = pillar_spotlight(veh)
            if sl["fired"]:
                ev["pillar_spotlight"] = True
    return ev


def _compactness(cols: np.ndarray, band_w: int) -> float:
    """Share of a colour's pixels within a quarter-band-wide window of its centre.

    A lamp is a blob: almost all of its pixels sit near one another. A painted
    roof or a strip of sky spans the box, so only the pixels that happen to
    fall near the middle are counted. This is the test that separates "a small
    bright thing" from "a large coloured thing", which is exactly the
    distinction the detector was missing.
    """
    if len(cols) == 0:
        return 0.0
    half = max(2.0, band_w * 0.125)
    return float(np.mean(np.abs(cols - cols.mean()) <= half))


# Crude signals: hand-written heuristics, not trained on anything, each with a
# demonstrated history of firing on ordinary traffic.
_CRUDE = ("light_bar", "police_body", "steelies", "antenna_array")


def arbitrate(ev: dict, visual_result: dict | None) -> dict:
    """A crude signal survives only if the trained model AGREES it might be one.

    🚨 THIS USED TO BE A VETO AND THE VETO WAS TOO POLITE.
    The rule was "a CLIP 'civilian' call at >=0.80 confidence and >=0.60 margin
    cancels the crude signals". On his street on 2026-08-08 CLIP called four of
    five vehicles civilian and was overruled on two of them, because it landed
    at 0.767/0.574 and 0.665/0.342 - under the bar, so the colour heuristic won
    and both went onto the public map as police. He confirmed none of the five
    was a police vehicle.

    The asymmetry was backwards. A trained classifier saying "ordinary car"
    with moderate confidence is better evidence than a colour rule that has now
    produced a false positive three separate times. So the burden is inverted:
    a crude signal is kept only when the model does NOT think the vehicle is
    ordinary. Silence from the model (no result at all) still allows it
    through, because a node with no classifier must still be able to
    contribute - but on this deployment the classifier is present and its
    opinion is now the one that carries.

    Still only ever removes signals; it never adds one. A confident CLIP
    'police' has to clear classify.py on its own evidence.
    """
    if not visual_result:
        return ev
    says_civilian = (visual_result.get("vclass") == "civilian"
                     and visual_result.get("conf", 0) >= 0.55)
    if says_civilian:
        for weak in _CRUDE:
            if ev.pop(weak, None) is not None:
                ev.setdefault("_vetoed", []).append(weak)
    return ev


def available() -> dict:
    """What this module can and cannot see. Surfaced on the eval report."""
    return {
        "implemented": ["light_bar (lit emergency lights only)"],
        # Written, measured on 56 government + 1,235 ordinary local crops, and
        # switched OFF because they fired on 23% and 12% of ordinary traffic.
        # Listed separately from "never built" because the difference matters:
        # these have a number attached, and rebuilding them the same way will
        # produce the same number.
        "built_but_disabled": {
            "push_bumper": "17.9% recall, fires on 23.4% of ordinary traffic",
            "pillar_spotlight": "3.9% recall, fires on 11.5% of ordinary traffic",
        },
        "not_implemented": ["livery", "agency_decal", "antenna_array",
                            "police_body", "steelies", "fleet_decal"],
        "consequence": ("with no visual classifier, plate text alone cannot "
                        "publish a vehicle, so the public tier stays empty on "
                        "real footage. This is classify.py rule 2, not a bug."),
    }
