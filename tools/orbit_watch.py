"""Find aircraft that are CIRCLING, not passing through.

    python tools/orbit_watch.py --box 42.3,-84.4,43.3,-83.2 --minutes 30
    python tools/orbit_watch.py --box ... --once      # analyse stored tracks, no polling
    python tools/orbit_watch.py --box ... --gov       # only government-registered

STAGED / LOCAL ONLY. Nothing here is wired into the map or the hub.

WHY PATTERN AND NOT PAPERWORK
tools/gov_aircraft.py answers "who owns this aircraft" from the FAA registry,
and it finds county sheriffs' helicopters well. It cannot find the aircraft
that matter most: federal surveillance is widely documented as registered to
front companies, so it is a CORPORATION in the registry, not a government
entity. Registration is a claim someone filed once; an orbit is a thing the
aircraft is doing right now, over your house, and it cannot be filed away.

This is the technique journalists used to identify surveillance aircraft that
were deliberately registered to look like nothing.

WHAT AN ORBIT LOOKS LIKE, NUMERICALLY
A plane going somewhere flies roughly straight: the distance it covers is about
the distance between where it started and where it ended. A plane watching
something flies a long way and ends up where it began.

  path length      how far it actually flew
  net displacement how far it got
  turning          total heading change; one full circle is 360 degrees
  radius           how far it strayed from the centre of its own track

So the test is: flew a long way, got nowhere, turned through several circles,
stayed inside a small radius, and did it low. All five, because each on its own
has an innocent explanation - a holding pattern near an airport is a legitimate
orbit, and this will flag it.

🚨 THIS FINDS ORBITS, NOT POLICE. A crop duster, a traffic helicopter, a news
crew, a survey flight and a training circuit all orbit. The output is a list of
aircraft worth LOOKING AT, never a claim about who they are - the same posture
as the camera network, where a classifier proposes and a human decides.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKS = ROOT / "data" / "orbit_tracks.json"
OPENSKY = "https://opensky-network.org/api/states/all"
UA = {"User-Agent": "SparrowMap/1.0 (orbit research; sparrowmap.com)"}

# --- what counts as an orbit -------------------------------------------------
# Deliberately conservative. A missed orbit costs nothing; a false one sends
# someone looking at a crop duster and teaches them to ignore the next alert.
WINDOW_S      = 30 * 60     # how much history to judge on
MIN_POINTS    = 8           # fewer than this is not a track, it is a rumour
MIN_PATH_KM   = 8.0         # it has to have actually flown
MAX_NET_KM    = 5.0         # ...and got nowhere
MIN_TURN_DEG  = 540.0       # one and a half circles, so a single turn is not enough
MAX_RADIUS_KM = 8.0         # stayed over one place
MAX_ALT_M     = 4000.0      # airliners hold high; surveillance orbits low


def hav(a: tuple, b: tuple) -> float:
    """Kilometres between two (lat, lon)."""
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def bearing(a: tuple, b: tuple) -> float:
    y = math.sin(math.radians(b[1] - a[1])) * math.cos(math.radians(b[0]))
    x = (math.cos(math.radians(a[0])) * math.sin(math.radians(b[0]))
         - math.sin(math.radians(a[0])) * math.cos(math.radians(b[0]))
         * math.cos(math.radians(b[1] - a[1])))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def turn_total(pts: list) -> float:
    """Sum of absolute heading changes along the track, in degrees.

    Signed deltas would cancel: a figure-of-eight turns 360 left and 360 right
    and would score zero, while being exactly the behaviour we are looking for.
    """
    total = 0.0
    for i in range(2, len(pts)):
        b1 = bearing(pts[i - 2][:2], pts[i - 1][:2])
        b2 = bearing(pts[i - 1][:2], pts[i][:2])
        d = abs((b2 - b1 + 180) % 360 - 180)
        # A single sample jumping ~180 degrees is usually a GPS glitch or a
        # gap, not an aircraft reversing in place.
        if d < 170:
            total += d
    return total


def analyse(pts: list) -> dict:
    """pts = [(lat, lon, alt, ts), ...] oldest first."""
    if len(pts) < MIN_POINTS:
        return {"orbit": False, "why": f"only {len(pts)} points"}
    path = sum(hav(pts[i - 1][:2], pts[i][:2]) for i in range(1, len(pts)))
    net = hav(pts[0][:2], pts[-1][:2])
    clat = sum(p[0] for p in pts) / len(pts)
    clon = sum(p[1] for p in pts) / len(pts)
    radius = max(hav((clat, clon), p[:2]) for p in pts)
    turn = turn_total(pts)
    alts = [p[2] for p in pts if p[2] is not None]
    alt = sum(alts) / len(alts) if alts else None
    span = (pts[-1][3] - pts[0][3]) / 60.0

    checks = {
        "flew far enough":  path >= MIN_PATH_KM,
        "got nowhere":      net <= MAX_NET_KM,
        "turned enough":    turn >= MIN_TURN_DEG,
        "stayed local":     radius <= MAX_RADIUS_KM,
        "low enough":       alt is None or alt <= MAX_ALT_M,
    }
    return {"orbit": all(checks.values()), "checks": checks,
            "path_km": path, "net_km": net, "turn_deg": turn,
            "radius_km": radius, "alt_m": alt, "span_min": span,
            "points": len(pts), "centre": (clat, clon)}


def poll(box: str) -> list:
    lamin, lomin, lamax, lomax = [float(x) for x in box.split(",")]
    url = f"{OPENSKY}?lamin={lamin}&lomin={lomin}&lamax={lamax}&lomax={lomax}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=30) as r:
        return (json.loads(r.read()) or {}).get("states") or []


def load() -> dict:
    try:
        return json.loads(TRACKS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(d: dict) -> None:
    TRACKS.parent.mkdir(parents=True, exist_ok=True)
    TRACKS.write_text(json.dumps(d), encoding="utf-8")


def gov_registry() -> dict:
    """Owner lookup, reusing whatever gov_aircraft.py already cached."""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import gov_aircraft
        return gov_aircraft.registry()
    except Exception:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", required=True, help="lamin,lomin,lamax,lomax")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--every", type=float, default=20.0, help="seconds between polls")
    ap.add_argument("--once", action="store_true", help="analyse stored tracks only")
    ap.add_argument("--gov", action="store_true", help="government-registered only")
    a = ap.parse_args()

    tracks = load()
    gov = gov_registry() if a.gov else {}

    if not a.once:
        end = time.time() + a.minutes * 60
        polls = 0
        while time.time() < end:
            try:
                states = poll(a.box)
            except Exception as exc:
                print(f"  poll failed: {exc}", file=sys.stderr)
                time.sleep(a.every)
                continue
            polls += 1
            now = time.time()
            for s in states:
                icao = (s[0] or "").strip().lower()
                lat, lon, alt = s[6], s[5], s[7]
                if not icao or lat is None or lon is None:
                    continue
                t = tracks.setdefault(icao, {"call": (s[1] or "").strip(), "pts": []})
                if (s[1] or "").strip():
                    t["call"] = (s[1] or "").strip()
                # OpenSky repeats a position when nothing new arrived; a
                # duplicated point inflates nothing but wastes the window.
                if t["pts"] and abs(t["pts"][-1][3] - now) < 5:
                    continue
                t["pts"].append([lat, lon, alt, now])
                t["pts"] = [p for p in t["pts"] if now - p[3] <= WINDOW_S]
            save(tracks)
            left = int(end - time.time())
            print(f"\r  poll {polls}: {len(states)} aircraft, "
                  f"{len(tracks)} tracked, {left}s left   ", end="", flush=True)
            time.sleep(a.every)
        print()

    now = time.time()
    results = []
    for icao, t in tracks.items():
        pts = [p for p in t["pts"] if now - p[3] <= WINDOW_S]
        if len(pts) < MIN_POINTS:
            continue
        r = analyse(pts)
        r["icao"] = icao
        r["call"] = t.get("call") or ""
        r["owner"] = (gov.get(icao.upper()) or [None])[0] if gov else None
        if a.gov and not r["owner"]:
            continue
        results.append(r)

    orbits = [r for r in results if r["orbit"]]
    print(f"\n{len(results)} aircraft with enough history; {len(orbits)} circling\n")

    for r in sorted(orbits, key=lambda x: -x["turn_deg"]):
        who = f" [{r['owner']}]" if r.get("owner") else ""
        print(f"  🔄 {r['icao']} {r['call']:<8}{who}")
        print(f"      flew {r['path_km']:.1f} km, ended {r['net_km']:.1f} km from "
              f"start, turned {r['turn_deg']:.0f}°")
        print(f"      within {r['radius_km']:.1f} km of "
              f"{r['centre'][0]:.3f},{r['centre'][1]:.3f} at "
              f"{r['alt_m']:.0f} m over {r['span_min']:.0f} min")

    if not orbits and results:
        # Say what nearly qualified: a detector that only ever prints nothing is
        # indistinguishable from one that is broken.
        near = sorted(results, key=lambda x: -sum(x["checks"].values()))[:3]
        print("  nothing circling. Closest:")
        for r in near:
            failed = [k for k, v in r["checks"].items() if not v]
            print(f"    {r['icao']} {r['call']:<8} passed "
                  f"{sum(r['checks'].values())}/5, failed: {', '.join(failed)}")
            print(f"      path {r['path_km']:.1f} km, net {r['net_km']:.1f} km, "
                  f"turn {r['turn_deg']:.0f}°, radius {r['radius_km']:.1f} km")


if __name__ == "__main__":
    main()
