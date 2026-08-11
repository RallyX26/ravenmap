"""How far can THIS camera actually read a plate?

The single most useful number in the project, and the one that decides whether
a node is worth installing. Everything else - model choice, lighting, tuning -
is downstream of whether the plate lands on enough pixels to have characters at
all.

THE RULE OF THUMB
A licence plate needs roughly 60-100 pixels across its WIDTH for reliable OCR.
Below about 40 px the characters are physically not resolved and no model
recovers them; that is optics, not software. A US plate is 12 in / 305 mm wide.

THE CONSEQUENCE PEOPLE GET WRONG
A wide-angle camera pointed at a whole intersection reads nothing. Spreading
2560 pixels across a 70 degree view puts about 550/d pixels on a plate at d
metres, so you are out of resolution by 15-20 m. This is exactly why commercial
ALPR cameras are TELEPHOTO and aimed at ONE LANE at ONE SPOT. A node covering
less ground and reading every plate beats a node covering the whole junction
and reading none.

    python tools\aim.py --width 2560 --fov 70
    python tools\aim.py --width 1920 --fov 25 --plate-mm 520   (EU plate)
"""

from __future__ import annotations

import argparse
import math

PLATE_MM_US = 305.0        # 12 inches
GOOD_PX = 70               # comfortable
MARGINAL_PX = 45           # sometimes
FLOOR_PX = 30              # essentially never


def plate_px(width_px: int, fov_deg: float, dist_m: float,
             plate_mm: float = PLATE_MM_US) -> float:
    """Pixels across the plate at a given distance."""
    view_m = 2.0 * dist_m * math.tan(math.radians(fov_deg) / 2.0)
    return width_px * (plate_mm / 1000.0) / view_m


def max_range(width_px: int, fov_deg: float, need_px: float,
              plate_mm: float = PLATE_MM_US) -> float:
    """Distance at which the plate falls to `need_px` wide."""
    return (width_px * (plate_mm / 1000.0)) / (
        2.0 * need_px * math.tan(math.radians(fov_deg) / 2.0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=2560, help="sensor width in px")
    ap.add_argument("--fov", type=float, default=70.0, help="horizontal FOV, degrees")
    ap.add_argument("--plate-mm", type=float, default=PLATE_MM_US)
    args = ap.parse_args()

    print(f"{args.width} px wide, {args.fov:.0f} deg horizontal FOV, "
          f"{args.plate_mm:.0f} mm plate\n")
    print(f"{'distance':>9}  {'plate px':>9}  verdict")
    print("-" * 44)
    for d in (5, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60):
        px = plate_px(args.width, args.fov, d, args.plate_mm)
        if px >= GOOD_PX:
            v = "good"
        elif px >= MARGINAL_PX:
            v = "marginal"
        elif px >= FLOOR_PX:
            v = "poor"
        else:
            v = "unreadable"
        print(f"{d:>7} m  {px:>9.0f}  {v}")

    print(f"\nusable to ~{max_range(args.width, args.fov, GOOD_PX, args.plate_mm):.0f} m "
          f"(good) / ~{max_range(args.width, args.fov, MARGINAL_PX, args.plate_mm):.0f} m "
          f"(marginal)")

    print("\nTo reach a given distance, this is the FOV you need:")
    print(f"{'target':>8}  {'max FOV':>9}  {'~zoom vs 70 deg':>16}")
    print("-" * 40)
    for d in (15, 20, 30, 40, 50):
        # invert: fov such that plate_px == GOOD_PX at distance d
        half = math.atan((args.width * (args.plate_mm / 1000.0)) / (2 * GOOD_PX * d))
        fov = math.degrees(2 * half)
        zoom = math.tan(math.radians(70) / 2) / math.tan(half)
        print(f"{d:>6} m  {fov:>8.1f} deg  {zoom:>13.1f}x")


if __name__ == "__main__":
    main()
