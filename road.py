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

    out: list = []
    fallback: list = []          # service roads and alleys, used only if out is empty
    for el in doc.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        if tags.get("highway") not in DRIVEABLE:
            continue
        # 🚨 A DRIVEWAY IS NOT A STREET, AND IT IS USUALLY THE NEAREST THING.
        # `service` covers driveways and parking aisles as well as alleys, and
        # a camera in a window is nearly always closer to its own driveway than
        # to the road it is pointed at. span_nearest takes the CLOSEST driveable
        # way, so the watched span was being drawn down a driveway - which the
        # basemap barely renders, so the span and its dots looked like they were
        # floating in the gap between the streets. Reported as "roads aren't
        # lining up"; the geometry was exact and the road was the wrong one.
        #
        # The sighting is a claim that a vehicle passed on a public road. A
        # private driveway or a supermarket parking aisle cannot carry that
        # claim, so they are excluded from the candidate set entirely rather
        # than merely ranked lower.
        if tags.get("service") in ("driveway", "parking_aisle", "drive-through"):
            continue
        geom = [(g["lat"], g["lon"]) for g in el.get("geometry") or []]
        if len(geom) < 2:
            continue
        entry = (tags.get("name") or "", geom)
        # An untagged `service` way is an alley or an access road. It is a real
        # place a vehicle can drive, so it is not discarded - but it should
        # never win against an actual street merely by being a few metres
        # nearer, which is exactly what "closest driveable way" would do to a
        # camera looking across its own back lane. Held back and used only if
        # nothing better exists in the tile.
        (fallback if tags.get("highway") == "service" else out).append(entry)
    return out or fallback


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


def span_from_travel(lat: float, lon: float, ways: list, axis_deg: float,
                     max_off_axis: float = 25.0,
                     max_dist_m: float = 120.0) -> Optional[dict]:
    """Pick the road the camera actually watches, from how its traffic MOVES.

    🚨 PROXIMITY IS THE WRONG QUESTION AND IT PICKS THE WRONG ROAD.
    span_nearest takes the closest driveable way. On a corner plot the closest
    way can be the cross street, or the alley behind, and "closest" then loses
    by half a metre to a road running at ninety degrees to the traffic. Measured
    on the operator's own camera: vehicles travel on a 93 degree axis with 0.97
    concentration over 49 passes, and the nearest road was North Bridge Street
    at 178 degrees - PERPENDICULAR - winning by 0.7 m over Hamrick Street at
    88.8 degrees, which matches the traffic to within 4 degrees.

    A camera pointed at a street sees vehicles travelling ALONG that street. So
    the road is identifiable from the sightings themselves, with no compass, no
    heading at enrolment, and nothing for a volunteer to get wrong - a browser
    in a window cannot report a bearing anyway, which is why 25 of 28 nodes
    have heading 0 and fall through to proximity in the first place.

    `axis_deg` is the dominant direction of travel folded to 0-180: a road
    carries traffic both ways, so a heading and its reverse are the same axis.
    Candidates are scored on angular agreement first and distance only as a
    tie-break, which is the inversion this function exists to make.

    Returns None when nothing agrees within `max_off_axis`. That matters: the
    caller must fall back rather than be handed a confident wrong answer, which
    is exactly what proximity did.
    """
    px, py = 0.0, 0.0
    best = None
    for name, geom in ways:
        for i in range(len(geom) - 1):
            a = _to_xy(geom[i][0], geom[i][1], lat, lon)
            b = _to_xy(geom[i + 1][0], geom[i + 1][1], lat, lon)
            dx, dy = b[0] - a[0], b[1] - a[1]
            seg = math.hypot(dx, dy)
            if seg < 1e-6:
                continue
            # Bearing of this segment, folded the same way as the travel axis.
            brg = math.degrees(math.atan2(dx, dy)) % 180.0
            off = abs(((brg - axis_deg + 90.0) % 180.0) - 90.0)
            # Closest point on the segment to the camera.
            t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / (seg * seg)))
            qx, qy = a[0] + dx * t, a[1] + dy * t
            d = math.hypot(qx - px, qy - py)
            if d > max_dist_m or off > max_off_axis:
                continue
            # 🚨 ANGLE IN BUCKETS, THEN DISTANCE - not raw angle.
            # Sorting on the exact angle first lets a road 101 m away beat one
            # at 31 m by being 0.9 degrees better aligned, which is what this
            # function did on its first run: it chose Mill Street over Hamrick
            # Street for a camera with a 30 m reach. That is the original bug
            # inverted - optimising one quantity so hard that the other stops
            # mattering.
            #
            # A 5 degree bucket is well inside the noise of a tracker heading
            # averaged over dozens of passes, so two roads in the same bucket
            # are not meaningfully different in alignment and the nearer one
            # wins. `max_dist_m` is the real guard: a camera cannot watch a
            # road beyond its reach however perfectly aligned it is.
            key = (round(off / 5.0), d)
            if best is None or key < best[0]:
                best = (key, (qx, qy), (dx / seg, dy / seg), name, d, off)
    if best is None:
        return None
    _, point, unit, name, dist, off = best
    half = SPAN_MIN_M / 2.0
    p1 = _to_ll(point[0] - unit[0] * half, point[1] - unit[1] * half, lat, lon)
    p2 = _to_ll(point[0] + unit[0] * half, point[1] + unit[1] * half, lat, lon)
    return {"road_name": name or "", "span": [list(p1), list(p2)],
            "watched_m": SPAN_MIN_M, "source": "traffic",
            "off_axis_deg": round(off, 1), "camera_dist_m": round(dist, 1)}


# A grid cell's road geometry, kept so a MOBILE contributor does not hit
# Overpass once per vehicle. The key is the same ~400 m cell the query is
# already snapped to, so the cache cannot be finer-grained than the request
# and a hit reveals nothing a miss would not have.
_WAYS_CACHE: dict[tuple[int, int], list] = {}
_WAYS_CACHE_MAX = 64


def _cell(lat: float, lon: float) -> tuple[int, int]:
    dlon = GRID_DEG / max(math.cos(math.radians(lat)), 1e-6)
    return (int(math.floor(lat / GRID_DEG)), int(math.floor(lon / dlon)))


def snap_point(lat: float, lon: float, seed: str,
               online: bool = True) -> Optional[tuple[float, float]]:
    """Put a loose point on the nearest road, or None if we cannot.

    🚨 FOR MOBILE CONTRIBUTORS, WHOSE DOTS LANDED IN PARKS AND BUILDINGS.
    A fixed camera gets a watched span at enrolment and its sightings are
    placed along it. A phone has no span - it moves - so its sightings were
    published as its own GPS with the 60 m jitter applied and nothing else, and
    60 m in a random direction routinely lands off the road: a park, a back
    garden, the middle of a block.

    Snapping AFTER the jitter matters and is not the same as snapping before.
    The caller jitters first, so what arrives here is already displaced; this
    finds the nearest road to THAT point and then places the dot at a stable
    position along an 80 m stretch of it. The privacy noise is not undone - it
    is redistributed along the road instead of across it, which is also the
    more honest shape, since what is actually known is "a vehicle passed on
    this street", not "a vehicle was in this garden".

    Returns None when the road lookup is unavailable, and the caller keeps the
    jittered point: a dot slightly off the road beats no dot at all, and a
    third-party API being down must never drop a sighting.
    """
    try:
        key = _cell(lat, lon)
        ways = _WAYS_CACHE.get(key)
        if ways is None:
            if not online:
                return None
            ways = fetch_ways(lat, lon)
            if len(_WAYS_CACHE) >= _WAYS_CACHE_MAX:
                _WAYS_CACHE.clear()      # bounded; a cold cell just refetches
            _WAYS_CACHE[key] = ways
        if not ways:
            return None
        got = span_nearest(lat, lon, ways)
        span = (got or {}).get("span")
        if not span or len(span) < 2:
            return None
        return point_on_span(span, seed)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return None


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
