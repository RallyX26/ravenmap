"""The basemap must be legible AND the markers must survive it being legible.

🚨 TWO BUGS, AND THE SECOND WAS CAUSED BY THE FIX FOR THE FIRST.
Reported: "Dark gray on black. Impossible to see" (Hacker News) and "everything
is dark gray and gray" (him). Measured: CARTO dark_all puts road casing at
1.10-1.37:1 against the land it sits on, so the streets were not dim, they were
absent.

Brightening the tile pane fixes that and immediately breaks something else: a
sighting sits ON a road, so lighting the roads washes out the dots. At the first
value tried, a fleet marker on a lit road measured 1.07:1. Brightness alone
cannot serve both - the best available balance left BOTH at about 2.3:1 - which
is why the markers carry a dark ring and the brightness is chosen for the roads.

This test pins all of it, so nobody re-tunes one number and silently undoes the
other half.

    python tools/test_map_contrast.py
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
CSS = HERE.parent / "public" / "style.css"
APP = HERE.parent / "public" / "app.js"

FAILED = []

# Sampled from real dark_all tiles over Brighton MI, Detroit and Manhattan.
# The LIGHTEST land and the LIGHTEST road are the worst case for separation.
LAND = "#090909"
ROAD_MAJOR = "#262626"
ROAD_MINOR = "#191919"

# Every colour that gets drawn as a filled marker over a road.
DOTS = {"police": "#ff3b47", "gov": "#ff3b47", "fleet": "#ffb547",
        "cam": "#3ddc97", "traffic": "#93a7c4", "hot": "#ff5b6e"}

BAR = 3.0          # WCAG 2.1: graphical objects and UI components


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def lum(hexv: str) -> float:
    h = hexv.lstrip("#")
    def f(c: str) -> float:
        x = int(c, 16) / 255
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(h[0:2]) + 0.7152 * f(h[2:4]) + 0.0722 * f(h[4:6])


def ratio(a: str, b: str) -> float:
    lo, hi = sorted((lum(a), lum(b)))
    return (hi + 0.05) / (lo + 0.05)


def bright(hexv: str, b: float) -> str:
    h = hexv.lstrip("#")
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, round(int(h[i:i + 2], 16) / 255 * b * 255)))
        for i in (0, 2, 4))


def main() -> int:
    css = CSS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    m = re.search(r"\.leaflet-tile-pane\{filter:brightness\(([\d.]+)\)", css)
    if not m:
        print("cannot find the tile-pane filter in style.css")
        return 2
    b = float(m.group(1))
    print(f"tile pane filter: brightness({b})\n")

    # ⚠️ THE FILTER MUST NOT REACH THE MARKERS. Every colour on this map means
    # something - red is a publicly owned vehicle - so a filter over the whole
    # map would turn a legibility fix into a data-integrity bug.
    check("🚨 the filter is on the TILE PANE only",
          not re.search(r"\.leaflet-container\{[^}]*filter:", css)
          and not re.search(r"#map\{[^}]*filter:", css),
          "a filter on the container would shift every marker colour too")

    land, rmaj, rmin = bright(LAND, b), bright(ROAD_MAJOR, b), bright(ROAD_MINOR, b)
    rl = ratio(land, rmaj)
    print(f"  land {LAND} -> {land}, road {ROAD_MAJOR} -> {rmaj}")
    check(f"roads clear {BAR}:1 against land", rl >= BAR,
          f"{rl:.2f}:1 - the reported bug is not fixed")
    print(f"  roads vs land: {rl:.2f}:1 (was {ratio(LAND, ROAD_MAJOR):.2f}:1)")

    # Nothing may clip: clipping deletes water, parks and buildings, which is
    # how a "contrast fix" turns a map into two colours.
    check("nothing clips to white", max(int(rmaj.lstrip('#')[i:i+2], 16)
                                        for i in (0, 2, 4)) < 255,
          f"road casing reached {rmaj}")

    # --- the regression guard ------------------------------------------------
    halo = re.search(r"const MARKER_HALO = '(#[0-9a-fA-F]{6})'", app)
    check("markers carry a halo constant", bool(halo))
    if halo:
        h = halo.group(1)
        print(f"\n  marker halo {h}")
        check("🚨 the halo separates a dot from a LIT ROAD",
              ratio(h, rmaj) >= BAR,
              f"{ratio(h, rmaj):.2f}:1 against {rmaj}")
        # The halo is meant to vanish on empty land, so the dot looks unchanged
        # where it always looked fine.
        check("and stays invisible on plain land", ratio(h, land) < 1.6,
              f"{ratio(h, land):.2f}:1 - it would read as a deliberate outline")

    check("the sighting dots use it",
          bool(re.search(r"color: MARKER_HALO,\s*\n\s*fillColor: COLOR", app)),
          "pingStyle still strokes with the class colour")
    check("the traffic dots use it too",
          bool(re.search(r"color: MARKER_HALO, fillColor: TRAFFIC", app)))

    print("\n  without the halo these would be the dot/road numbers:")
    unringed = []
    for name, hexv in DOTS.items():
        r = min(ratio(hexv, rmaj), ratio(hexv, rmin))
        if r < BAR:
            unringed.append(f"{name} {r:.2f}")
        print(f"    {name:8} {r:5.2f}:1")
    print(f"  {len(unringed)} of {len(DOTS)} would be under {BAR}:1 unringed"
          f" - which is why the ring is not optional")

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
