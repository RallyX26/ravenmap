"""Which vehicle features are actually reachable at a given camera scale?

The project spent a long time treating the licence plate as the thing a node
must resolve. For the PUBLIC tier that is the wrong target, and the arithmetic
says so plainly.

Two things make a plate uniquely hard:

  SIZE     a plate is 305 mm. A roof light bar is about 1300 mm, a push bumper
           1600 mm, a door livery panel 1800 mm. The plate is the SMALLEST
           thing anyone is asking the camera to see.

  TASK     reading a plate is OCR - seven characters must each be resolved,
           so it needs 60-100 px across the plate. Deciding "is there a light
           bar on the roof" is shape and colour recognition, which works from
           roughly 20 px. So the plate needs 3x the pixel density for a
           feature 4x smaller.

Multiply those and identifying a marked patrol vehicle by sight is roughly an
order of magnitude more forgiving than reading its plate. That is why a network
of ordinary porch cameras can plausibly answer "where were the police seen"
while failing completely at "which car was that".

    python tools\feature_budget.py --scale 143      (px per metre)
    python tools\feature_budget.py --crop-px 846 --vehicle-m 5.9
"""

from __future__ import annotations

import argparse

# name, real width (m), what the task needs, px required
FEATURES = [
    ("licence plate",      0.305, 60, "OCR, 7 characters"),
    ("A-pillar spotlight", 0.18,  14, "shape only"),
    ("roof light bar",     1.30,  20, "shape + colour"),
    ("push bumper",        1.60,  20, "shape only"),
    ("door livery panel",  1.80,  16, "large colour block"),
    ("agency door decal",  0.90,  45, "text, but big text"),
    ("amber DOT beacon",   0.60,  14, "shape + colour"),
    ("vehicle silhouette", 5.00,  24, "body style"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=0.0, help="pixels per metre")
    ap.add_argument("--crop-px", type=float, default=846.0)
    ap.add_argument("--vehicle-m", type=float, default=5.9)
    ap.add_argument("--skew", type=float, default=0.0,
                    help="degrees off the feature's normal (plates suffer, "
                         "light bars barely do)")
    a = ap.parse_args()

    scale = a.scale or (a.crop_px / a.vehicle_m)
    print(f"  scale: {scale:.0f} px per metre "
          f"(a {a.vehicle_m:.1f} m vehicle rendered {a.crop_px:.0f} px wide)\n")

    import math
    print(f"  {'feature':<20}{'size':>8}{'px here':>10}{'needed':>9}   headroom  task")
    print("  " + "-" * 78)
    for name, m, need, task in FEATURES:
        got = m * scale
        # Skew compresses a flat, front-facing feature like a plate or a decal.
        # A light bar is a 3D object seen against the sky - it keeps its width
        # from almost any angle, which is a second, separate advantage.
        flat = name in ("licence plate", "agency door decal")
        if flat and a.skew:
            got *= math.cos(math.radians(a.skew))
        ratio = got / need
        flag = "OK" if ratio >= 1.5 else ("marginal" if ratio >= 1.0 else "SHORT")
        print(f"  {name:<20}{m:>7.2f}m{got:>9.0f}{need:>9}   {ratio:>5.1f}x  {flag:<9}{task}")

    print("\n  The plate is the smallest feature AND the one needing the most")
    print("  pixels per millimetre. Everything else has large margin.")


if __name__ == "__main__":
    main()
