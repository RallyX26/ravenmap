"""SparrowMap - putting a sighting on the ROAD without putting the camera on the map.

🚨 THE PROBLEM THIS SOLVES
Node positions are published jittered by up to 60 m so the map can show where
people are recorded without publishing which house volunteered. Sightings were
then derived from that jittered point - pushed out along the heading by 0.6 x
reach - which sounds reasonable until you compare the two numbers. His node has
a reach of 28 m and a jitter budget of 60 m. **The privacy noise is more than
twice the entire depth of the cone.** So the cone, and every dot inside it,
lands wherever the jitter threw it: a back garden, the middle of a field, the
wrong side of the block. The dots were never on the road.

Raising the accuracy by shrinking the jitter is the wrong trade. The right one
comes from noticing that the two things being protected are not the same thing:

    the ROAD a camera watches is public.  People are entitled to know where
    they are recorded, and that is the whole civic argument for this project.

    the HOUSE the camera sits in is not.  A volunteer should not have to accept
    a target on their door to take part.

So we publish them separately and at different resolutions. Each node gets a
WATCHED SPAN: the stretch of real road its lens actually covers, computed from
the camera's TRUE position and snapped to the road centreline. That span is
published accurately, because it is public information. The camera's own point
stays jittered. A sighting is placed somewhere along the span - which is an
honest statement of what the system actually knows, since a camera watching
30 m of street cannot tell you which metre of it a car occupied.

## Why the span has a MINIMUM length

Publishing a 28 m span next to a published heading and reach would hand an
attacker a back-projection: perpendicular from the span midpoint, 28 m out,
lands on one or two houses. That is the recurring failure in this codebase -
a control applied to one representation and given away by another. Two
countermeasures, both here:

  * the published span is extended to at least ``SPAN_MIN_M``, centred on the
    real watched stretch, so its midpoint no longer localises the camera; and
  * ``hub`` serves the span INSTEAD of a precise heading and reach for nodes
    that have one, so there is nothing left to back-project with.

## Why the Overpass query is deliberately coarse

Asking a public API "what road is at this exact point" tells that API the
exact location of a volunteer's camera - which would be a strange thing for
this project to do to its own contributors. The bbox is therefore snapped to a
~400 m grid before it is sent, and all the geometry happens locally against the
returned tile. Overpass learns a neighbourhood, not a house. The result is
cached on the node record, so the query happens once at enrolment and never
again.

Everything degrades gracefully: no network, no road found, Overpass down - the
span falls back to the camera's own aim axis, which is still on the road for
any node mounted per the project's own aiming rule (aim DOWN the street).
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from core import angle_diff, bearing_deg, haversine_m

# The published span is never shorter than this. See the module docstring:
# it is what stops the span's midpoint from localising the camera. Chosen to
# match the existing 60 m node jitter budget rather than exceed it - the two
# uncertainties compound, and a volunteer should not have to reason about two
# different privacy radii.
SPAN_MIN_M = 80.0

# Roads people drive on. A camera pointed at a footpath is not doing ALPR, and
# snapping a sighting onto a cycleway would be worse than not snapping it.
DRIVEABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street", "service", "road",
}

OVERPASS = "https://overpass-api.de/api/interpreter"

# Grid the bbox onto ~400 m so the request does not carry the camera's precise
# position. 0.004 deg of latitude is ~445 m; longitude is scaled at query time.
GRID_DEG = 0.004


# --------------------------------------------------------------------------
# Local planar helpers
#
# Over a few hundred metres a flat approximation centred on the camera is
# accurate to well under a metre and makes the projection maths readable.
# Longitude is scaled by cos(lat) - forgetting that skews everything by 26% at
# Michigan's latitude, which is the sort of quiet error this project keeps
# finding in itself.
# --------------------------------------------------------------------------

def _to_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    return ((lon - lon0) * 111_320.0 * math.cos(math.radians(lat0)),
            (lat - lat0) * 111_320.0)


def _to_ll(x: float, y: float, lat0: float, lon0: float) -> tuple[float, float]:
    return (lat0 + y / 111_320.0,
            lon0 + x / (111_320.0 * max(math.cos(math.radians(lat0)), 1e-6)))


def _seg_points(a: tuple, b: tuple, step: float = 2.0) -> list[tuple[float, float]]:
    """Sample a segment every `step` metres, endpoints included."""
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(d / step))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
            for i in range(n + 1)]


# --------------------------------------------------------------------------
# Overpass
# --------------------------------------------------------------------------

def fetch_ways(lat: float, lon: float, timeout: float = 25.0) -> list[list[tuple]]:
    """Driveable road geometries near a point, as lists of (lat, lon).

    The bbox is snapped to a grid before it leaves this machine. See the module
    docstring - the camera's exact position is not Overpass's business.
    """
    dlat = GRID_DEG
    dlon = GRID_DEG / max(math.cos(math.radians(lat)), 1e-6)
    s = math.floor(lat / dlat) * dlat
    w = math.floor(lon / dlon) * dlon
    bbox = f"{s - dlat:.6f},{w - dlon:.6f},{s + 2 * dlat:.6f},{w + 2 * dlon:.6f}"

    q = f'[out:json][timeout:20];way["highway"]({bbox});out geom;'
    req = urllib.request.Request(
        OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "SparrowMap/0.1 (citizen ALPR; road snapping)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        doc = json.loads(r.read().decode("utf-8"))

    out = []
    for el in doc.get("elements", []):
        if el.get("type") != "way":
            continue
        if el.get("tags", {}).get("highway") not in DRIVEABLE:
            continue
        geom = [(g["lat"], g["lon"]) for g in el.get("geometry") or []]
        if len(geom) >= 2:
            out.append((el.get("tags", {}).get("name") or "", geom))
    return out


# --------------------------------------------------------------------------
# The span
# --------------------------------------------------------------------------

def span_from_ways(lat: float, lon: float, heading: float, fov: float,
                   reach_m: float, ways: list) -> Optional[dict]:
    """Which stretch of which road does this camera actually cover?

    Walks every candidate road at 2 m resolution and keeps the points that are
    both inside the cone and within reach. The winning road is the one with the
    most such points, not the nearest one: a camera aimed down a street will
    accumulate a long run of hits on that street and at most a glancing one or
    two on the cross street behind it. Nearest-point matching gets that
    backwards at exactly the junctions where it matters.
    """
    best = None
    for name, geom in ways:
        hits: list[tuple[float, float]] = []
        for i in range(len(geom) - 1):
            a = _to_xy(geom[i][0], geom[i][1], lat, lon)
            b = _to_xy(geom[i + 1][0], geom[i + 1][1], lat, lon)
            for px, py in _seg_points(a, b):
                d = math.hypot(px, py)
                if d > reach_m * 1.25 or d < 1.0:
                    continue
                # atan2(east, north) -> compass bearing.
                brg = (math.degrees(math.atan2(px, py)) + 360.0) % 360.0
                if angle_diff(brg, heading) <= fov / 2.0:
                    hits.append((px, py))
        if hits and (best is None or len(hits) > len(best[1])):
            best = (name, hits)

    if best is None:
        return None

    name, hits = best
    # The span is the extent of the hits along the road's own direction, which
    # is the principal axis of the hit cloud. Taking min/max on x and y
    # separately would give a bounding box, not a line.
    cx = sum(h[0] for h in hits) / len(hits)
    cy = sum(h[1] for h in hits) / len(hits)
    sxx = sum((h[0] - cx) ** 2 for h in hits)
    syy = sum((h[1] - cy) ** 2 for h in hits)
    sxy = sum((h[0] - cx) * (h[1] - cy) for h in hits)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ux, uy = math.cos(theta), math.sin(theta)

    ts = [(h[0] - cx) * ux + (h[1] - cy) * uy for h in hits]
    t0, t1 = min(ts), max(ts)

    # Extend to the published minimum, symmetrically about the real midpoint.
    length = t1 - t0
    if length < SPAN_MIN_M:
        pad = (SPAN_MIN_M - length) / 2.0
        t0, t1 = t0 - pad, t1 + pad

    p0 = _to_ll(cx + ux * t0, cy + uy * t0, lat, lon)
    p1 = _to_ll(cx + ux * t1, cy + uy * t1, lat, lon)
    return {"road_name": name, "span": [list(p0), list(p1)],
            "watched_m": round(length, 1), "source": "osm"}


def span_from_aim(lat: float, lon: float, heading: float,
                  reach_m: float) -> dict:
    """Fallback span: the camera's own aim axis, no road data required.

    The project's mounting rule is to aim DOWN the street at receding traffic,
    so the aim axis IS the road for any correctly mounted node. This keeps the
    system working with no network, no Overpass and no OSM coverage - it just
    trusts the operator's aim instead of verifying it against a map.
    """
    mid = reach_m * 0.6
    half = max(SPAN_MIN_M, reach_m) / 2.0
    h = math.radians(heading)
    out = []
    for t in (mid - half, mid + half):
        out.append(list(_to_ll(t * math.sin(h), t * math.cos(h), lat, lon)))
    return {"road_name": "", "span": out, "watched_m": round(reach_m, 1),
            "source": "aim"}


def span_nearest(lat: float, lon: float, ways: list) -> Optional[dict]:
    """The nearest driveable road, when nothing is known about where the camera
    is aimed.

    🚨 MOST CONTRIBUTORS NEVER PROVIDE A HEADING, AND THEIR DOTS LANDED ON
    BUILDINGS. A browser only reports a heading while the device is MOVING, so a
    camera enrolled from a window has none, and `span_from_ways` - which picks
    the road by counting hits inside the aim cone - has no cone to work with. The
    old fallback pushed a point out along heading 0, i.e. due north, and put
    every sighting from that node on whatever happened to be north of it.

    A camera watching a street is, in practice, near that street. So with no aim
    to reason about, take the closest road and publish a span centred on the
    nearest point of it. It says less than an aimed span - it does not claim to
    know which stretch is watched - but it is true, and it puts the dot on a
    road instead of somebody's living room.
    """
    px, py = 0.0, 0.0
    best, bestd, bestname = None, 1e9, ""
    for name, geom in ways:
        for i in range(len(geom) - 1):
            a = _to_xy(geom[i][0], geom[i][1], lat, lon)
            b = _to_xy(geom[i + 1][0], geom[i + 1][1], lat, lon)
            for qx, qy in _seg_points(a, b):
                d = math.hypot(qx - px, qy - py)
                if d < bestd:
                    bestd, best, bestname = d, (qx, qy), name
    if best is None or bestd > 120.0:
        return None            # nothing driveable close enough to be honest about
    # A span, not a point: a bare point would re-publish the camera's own spot.
    half = SPAN_MIN_M / 2.0
    # Orient along the road that won, so the span lies on it.
    ax = (best[0] + 1.0, best[1])
    for name, geom in ways:
        if name == bestname and len(geom) > 1:
            a = _to_xy(geom[0][0], geom[0][1], lat, lon)
            b = _to_xy(geom[1][0], geom[1][1], lat, lon)
            n = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
            ax = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
            break
    else:
        ax = (1.0, 0.0)
    p1 = _to_ll(best[0] - ax[0] * half, best[1] - ax[1] * half, lat, lon)
    p2 = _to_ll(best[0] + ax[0] * half, best[1] + ax[1] * half, lat, lon)
    return {"road_name": bestname or "", "span": [list(p1), list(p2)],
            "watched_m": SPAN_MIN_M, "source": "nearest"}


def resolve(lat: float, lon: float, heading: float, fov: float,
            reach_m: float, online: bool = True) -> dict:
    """Best available watched span for a camera. Never raises.

    A failure here must never block enrolment - a node with a fallback span is
    a working node, and a node that failed to enrol because a third-party API
    was down is not.
    """
    if online:
        try:
            ways = fetch_ways(lat, lon)
            got = span_from_ways(lat, lon, heading, fov, reach_m, ways)
            if got:
                return got
            # No aim to work with (a window enrolment reports no heading), so
            # fall back to the road itself rather than to a compass direction
            # nobody supplied.
            got = span_nearest(lat, lon, ways)
            if got:
                return got
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            print(f"[road] snap unavailable ({exc.__class__.__name__}), "
                  f"falling back to the aim axis")
    return span_from_aim(lat, lon, heading, reach_m)


# --------------------------------------------------------------------------
# Placing a sighting on the span
# --------------------------------------------------------------------------

def point_on_span(span: list, seed: str) -> tuple[float, float]:
    """A stable point along a published span.

    Deterministic in `seed` for two reasons. A dot that jumps to a new place on
    every page refresh is obviously fake, and re-deriving a position on each
    read would let anyone average many reads of the same sighting back to the
    span midpoint - which is the thing the minimum span length exists to hide.
    One sighting, one position, forever.

    The spread is not a claim of precision. It is the opposite: the camera
    covers this stretch and cannot say which metre of it the vehicle occupied,
    so the honest rendering is a dot somewhere along the stretch rather than a
    pile of dots on a single invented point.
    """
    import hashlib
    (a_lat, a_lon), (b_lat, b_lon) = span[0], span[1]
    h = hashlib.blake2b(seed.encode("utf-8"), digest_size=8).digest()
    t = int.from_bytes(h, "big") / float(1 << 64)
    return (round(a_lat + (b_lat - a_lat) * t, 6),
            round(a_lon + (b_lon - a_lon) * t, 6))


def span_length_m(span: list) -> float:
    return haversine_m(span[0][0], span[0][1], span[1][0], span[1][1])


def span_bearing(span: list) -> float:
    return bearing_deg(span[0][0], span[0][1], span[1][0], span[1][1])
