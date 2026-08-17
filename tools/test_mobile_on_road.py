"""A dashcam sighting belongs on the road the car was ON.

🚨 THE REPORT, with a screenshot: eight dashcam sightings in one town landed
scattered across three different streets, in yards and parking lots, when the
car had been on one street the whole time. "they all should be on the road."

🔬 THE MEASUREMENT THAT MATTERED WAS NOT THE OBVIOUS ONE. Five of the eight
were already within 2 m of a road, so "snap to the road" was never the bug.
They were on the WRONG roads, for two independent reasons:

  1. sighting_position jittered by 60 m and snapped the DISPLACED point. 60 m
     is further than the gap between streets, so near a junction the nearest
     road to the jittered point is a different road.
  2. span_nearest centred the span on the correct nearest point but oriented it
     using the way's FIRST TWO geometry points instead of the segment that
     actually won - so on a long or curving street the 80 m span was drawn at
     the bearing of a segment hundreds of metres away, and dots placed along it
     landed 15-20 m off the centreline.

⚠️ AND THE FIRST VERSION OF THIS TEST WOULD HAVE PASSED ON THE BROKEN CODE.
Re-snapping a stored point moves it 30-60 m, which looks like proof it was
never snapped - but snap_point returns a SEEDED POSITION ALONG an 80 m stretch,
not the nearest point, so it moves an already-snapped point too. Distance moved
proves nothing. The question is which STREET the dot lands on.

⚠️ SYNTHETIC GEOMETRY, DELIBERATELY. An earlier version used the real
coordinates from the report and preflight refused it - correctly, because this
repo is public and those coordinates are a contributor's position. Fake roads
also make the test offline, deterministic and fast, so it can run in preflight
instead of depending on Overpass being up.

    python tools/test_mobile_on_road.py
"""
from __future__ import annotations

import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# A crossroads on open ocean south of Ghana - no town, nobody's position, and
# far from any real deployment. Two straight streets meeting at right angles,
# plus a LONG CURVING one to exercise the span-orientation bug: its first
# segment points a completely different way from its middle.
# ⚠️ Written without decimal places on purpose. preflight flags any pair of
# numbers carrying 4+ decimals, because that is the precision that identifies a
# PLACE - and it was right to flag the earlier version of this file, which used
# the real coordinates from the report. Half a degree is not a location.
BASE_LAT = 0.5
BASE_LON = 0.5
_M = 1.0 / 111_320.0          # metres -> degrees, near the equator


def _n(metres):
    return metres * _M


WAYS = [
    # "Main" runs north-south through the origin.
    ("Main Street", [(BASE_LAT + _n(d), BASE_LON) for d in range(-300, 301, 20)]),
    # "Broad" runs east-west, crossing it at the origin.
    ("Broad Street", [(BASE_LAT, BASE_LON + _n(d)) for d in range(-300, 301, 20)]),
    # A long way that STARTS heading east and then curves hard north. Its first
    # segment bears 90 degrees; near the crossroads it bears about 0.
    ("Curving Road", [(BASE_LAT - _n(200) + _n(200 * math.sin(t / 10.0)),
                       BASE_LON + _n(120) + _n(t * 4))
                      for t in range(0, 60)]),
]


def _seg_dist(p, a, b) -> float:
    kx = 111320.0 * math.cos(math.radians(p[0]))
    ky = 110540.0
    px, py = p[1] * kx, p[0] * ky
    ax, ay = a[1] * kx, a[0] * ky
    bx, by = b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def nearest_road(pt):
    best, name = 1e9, None
    for nm, pts in WAYS:
        for i in range(len(pts) - 1):
            d = _seg_dist(pt, pts[i], pts[i + 1])
            if d < best:
                best, name = d, nm
    return name, best


def main() -> int:
    import nodes as node_mod
    import road

    # Feed the synthetic network in place of Overpass. Everything below then
    # exercises the real span_nearest / point_on_span / sighting_position code.
    road.ways_for_cell = lambda lat, lon, online=True: WAYS

    # 🚨 40 m DOWN "MAIN" FROM THE CROSSROADS, NOT ON IT.
    # A point ON the junction cannot decide this question - both streets are
    # equally near, so the test would pass while proving nothing. It has to be
    # somewhere the right answer is unambiguous, which is also the situation
    # being reported: a car partway down one street, near a junction.
    TRUE = (BASE_LAT - _n(40), BASE_LON)
    want, d0 = nearest_road(TRUE)
    print(f"  car is on: {want} ({d0:.1f} m from its centreline)\n")
    check("the test point is unambiguously on one street", want == "Main Street",
          f"nearest is {want!r}")

    nd = {"id": "n_test", "lat": TRUE[0], "lon": TRUE[1], "kind": "phone",
          "heading": None, "reach_m": 30}

    # Many seeds: the failure was probabilistic. A jittered point crosses to the
    # wrong street only for some directions, so one sample proves nothing.
    N = 60
    on_right, wrong, worst_off = 0, {}, 0.0
    for i in range(N):
        la, lo = node_mod.sighting_position(nd, TRUE[0], TRUE[1], seed=f"t{i}")
        got, dist = nearest_road((la, lo))
        worst_off = max(worst_off, dist)
        if got == want:
            on_right += 1
        else:
            wrong[got] = wrong.get(got, 0) + 1

    print(f"  {on_right}/{N} landed on {want}")
    if wrong:
        print(f"  wrong streets: {wrong}")
    print(f"  worst distance from a centreline: {worst_off:.1f} m")

    # 🚨 THE ONE THAT MATTERS. Not "on a road" - on the RIGHT road.
    check("every dot lands on the street the car was on", on_right == N,
          f"{N - on_right} of {N} landed elsewhere: {wrong}")
    # And ON it, which is the span-orientation half of the fix.
    check("every dot is within 8 m of a centreline", worst_off <= 8.0,
          f"worst {worst_off:.1f} m - the span is drawn at the wrong angle")

    # The privacy budget must survive: dots spread ALONG the road rather than
    # stacking on the contributor's exact position.
    pts = [node_mod.sighting_position(nd, TRUE[0], TRUE[1], seed=f"s{i}")
           for i in range(16)]
    spread = 0.0
    for a in pts:
        for b in pts:
            kx = 111320.0 * math.cos(math.radians(a[0]))
            spread = max(spread, math.hypot((a[1] - b[1]) * kx,
                                            (a[0] - b[0]) * 110540.0))
    check("dots still spread along the road (privacy kept)", spread > 15.0,
          f"max spread {spread:.0f} m - they are stacking on one point")
    print(f"  spread along the road: {spread:.0f} m")

    # 🚨 THE CURVE. A point near the curving road's MIDDLE must be placed using
    # that segment's bearing, not the bearing of its first segment.
    CURVE = (BASE_LAT - _n(200) + _n(200 * math.sin(3.0)), BASE_LON + _n(120) + _n(120))
    cname, _ = nearest_road(CURVE)
    nd2 = {"id": "n_curve", "lat": CURVE[0], "lon": CURVE[1], "kind": "phone",
           "heading": None, "reach_m": 30}
    off = 0.0
    for i in range(20):
        la, lo = node_mod.sighting_position(nd2, CURVE[0], CURVE[1], seed=f"c{i}")
        off = max(off, nearest_road((la, lo))[1])
    check(f"on a curving road ({cname}) dots stay on it", off <= 10.0,
          f"worst {off:.1f} m from any centreline")
    print(f"  curving road: worst {off:.1f} m off")

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
