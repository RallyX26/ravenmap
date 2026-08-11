"""SparrowMap - a simulated town, so the whole system is real before any hardware is.

This is the same discipline that caught the phase bug in the sonar build: put
known ground truth into the pipeline and the maths has nowhere to hide. Here it
buys three things at once.

  1. The hub, the API, the map and the privacy layer can be finished and tested
     end to end tonight, with no camera plugged in anywhere.
  2. The classifier has labelled data. The simulator knows which vehicles are
     police because it made them, so we can measure how often the two-tier gate
     publishes a civilian by mistake - which is the error that matters.
  3. The geometry gets exercised. Vehicles are only seen when they are actually
     inside a camera's range and cone, so `nodes.in_view` is under test from
     the first minute rather than being assumed correct.

The town is invented - plausible road geometry over open ground, no real
place - so the map looks like somewhere instead of like a grid, without
shipping anyone's actual streets in the source.
"""

from __future__ import annotations

import math
import random
import string
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classify
import db
import nodes as node_mod
import privacy
import snapshot
from core import CONFIG, bearing_deg, haversine_m, now

# ---------------------------------------------------------------------------
# Road network. Each route is a polyline of (lat, lon) with a name.
#
# INVENTED streets over open farmland, not a real town. This matters: the
# simulator's whole job is to exercise the map with traffic that looks real,
# and the shortest way to get that is to trace streets you know - which is
# how a developer's own neighbourhood ends up shipped in the source of a
# project about not publishing where people live. Generic names, rounded
# origin, nobody's address. The geometry is what the sim needs; the place
# is not.
# ---------------------------------------------------------------------------

ROUTES = [
    ("Main St", [
        (42.6915, -84.5215), (42.6961, -84.5122), (42.7008, -84.5029),
        (42.7050, -84.4941), (42.7092, -84.4848), (42.7138, -84.4754)]),
    ("W Second St", [
        (42.7028, -84.5090), (42.7021, -84.5021), (42.7016, -84.4959),
        (42.7012, -84.4898), (42.7007, -84.4829)]),
    ("Mill Rd", [
        (42.6860, -84.4932), (42.6925, -84.4936), (42.6991, -84.4940),
        (42.7058, -84.4944), (42.7124, -84.4949)]),
    ("Orchard Rd", [
        (42.6824, -84.5120), (42.6828, -84.5020), (42.6832, -84.4920),
        (42.6836, -84.4820)]),
    ("Old Route 9", [
        (42.7150, -84.5180), (42.7092, -84.5120), (42.7032, -84.5062),
        (42.6970, -84.5005), (42.6908, -84.4948)]),
]

CAMERA_SITES = [
    ("Porch - Main & Third",          42.7008, -84.5029, 250),
    ("Shopfront - W Second",          42.7016, -84.4959,  70),
    ("Garage - Mill Rd",              42.6991, -84.4940, 180),
    ("Driveway - Orchard Rd",         42.6832, -84.4920,  95),
    ("Second floor - Old Route 9",    42.7032, -84.5062, 205),
    ("Corner store - Main E",         42.7092, -84.4848, 240),
    ("Window - W Second W end",       42.7028, -84.5090,  85),
    ("Barn - Mill N",                 42.7124, -84.4949, 190),
]

COLORS = ["white", "black", "silver", "blue", "red", "grey", "green"]
BODIES = ["sedan", "suv", "pickup", "van", "hatchback"]


def _plate() -> str:
    """Michigan-shaped civilian plate: 3 letters, 4 digits."""
    return "".join(random.choices(string.ascii_uppercase, k=3)) + \
        "".join(random.choices(string.digits, k=4))


def _make_vehicle(kind: str) -> dict:
    """Create a vehicle with its TRUE nature recorded, for scoring later."""
    v = {"truth": kind, "color": random.choice(COLORS),
         "body": random.choice(BODIES), "plate": _plate(),
         "speed_mph": random.uniform(22, 46)}

    if kind == "marked_police":
        v.update(plate="MUN" + "".join(random.choices(string.digits, k=4)),
                 color=random.choice(["white", "black"]), body="suv",
                 light_bar=True, livery=True, agency_decal=True,
                 push_bumper=True, antenna_array=True, police_body=True,
                 steelies=True, agency=random.choice(
                     ["POLICE", "SHERIFF", "STATE POLICE"]),
                 plate_legend="MUNICIPAL")
    elif kind == "unmarked_police":
        # The hard case. No livery, ordinary plate. Only a spotlight, the
        # antennas, the wheels - and the way it drives.
        v.update(color=random.choice(["black", "grey", "silver"]), body="suv",
                 pillar_spotlight=True, antenna_array=True,
                 police_body=True, steelies=True)
    elif kind == "gov":
        v.update(plate="STA" + "".join(random.choices(string.digits, k=4)),
                 plate_legend="STATE OWNED", body=random.choice(["sedan", "van"]),
                 color="white")
    elif kind == "fleet":
        v.update(fleet_decal=True, color="white",
                 body=random.choice(["van", "pickup"]))
    return v


class Sim:
    """A little world that ticks."""

    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        random.seed(seed)
        self.nodes: list[dict] = []
        self.vehicles: list[dict] = []
        self.last_emit: dict[tuple, float] = {}
        self.stats = {"emitted": 0, "published": 0, "leaks": 0, "missed": 0}

    # -- setup ----------------------------------------------------------
    def build_town(self) -> None:
        existing = {n["name"] for n in db.nodes(active_only=False)}
        for name, lat, lon, heading in CAMERA_SITES:
            if name in existing:
                continue
            _priv, pub = node_mod.new_keypair()
            node_mod.enroll(name=name, lat=lat, lon=lon, pubkey=pub,
                            heading=heading, fov=62, reach_m=55, kind="fixed")
        self.nodes = db.nodes()

    def spawn(self, n_civilian: int = 26, n_marked: int = 3,
              n_unmarked: int = 2, n_gov: int = 2, n_fleet: int = 3) -> None:
        plan = (["civilian"] * n_civilian + ["marked_police"] * n_marked +
                ["unmarked_police"] * n_unmarked + ["gov"] * n_gov +
                ["fleet"] * n_fleet)
        for kind in plan:
            v = _make_vehicle(kind)
            v["route"] = self.rng.randrange(len(ROUTES))
            v["t"] = self.rng.random()
            v["dir"] = self.rng.choice([1, -1])
            # Patrol vehicles are slower, reverse often, and stay out all day.
            v["patrol"] = kind in ("marked_police", "unmarked_police")
            self.vehicles.append(v)

    # -- motion ---------------------------------------------------------
    def _position(self, v: dict) -> tuple[float, float, float]:
        pts = ROUTES[v["route"]][1]
        span = len(pts) - 1
        t = max(0.0, min(1.0, v["t"])) * span
        i = min(int(t), span - 1)
        f = t - i
        lat = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f
        lon = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f
        hd = bearing_deg(*pts[i], *pts[i + 1])
        if v["dir"] < 0:
            hd = (hd + 180) % 360
        return lat, lon, hd

    def step(self, dt_s: float = 4.0) -> list[dict]:
        """Advance the world, return the sightings that happened."""
        out = []
        for v in self.vehicles:
            route_len_m = 0.0
            pts = ROUTES[v["route"]][1]
            for a, b in zip(pts, pts[1:]):
                route_len_m += haversine_m(*a, *b)
            mps = v["speed_mph"] * 0.44704
            v["t"] += v["dir"] * (mps * dt_s) / max(route_len_m, 1.0)

            if v["t"] > 1.0 or v["t"] < 0.0:
                v["t"] = max(0.0, min(1.0, v["t"]))
                if v["patrol"] or self.rng.random() < 0.5:
                    v["dir"] *= -1          # patrols turn around and come back
                else:
                    v["route"] = self.rng.randrange(len(ROUTES))
                    v["t"] = self.rng.random()

            lat, lon, hd = self._position(v)
            for nd in self.nodes:
                if not node_mod.in_view(nd, lat, lon):
                    continue
                key = (nd["id"], v["plate"])
                if now() - self.last_emit.get(key, 0) < 25:
                    continue           # one read per pass, not per frame
                self.last_emit[key] = now()
                out.append(self.emit(nd, v, lat, lon, hd))
        return [o for o in out if o]

    # -- the ingest path, exercised for real ----------------------------
    def emit(self, nd: dict, v: dict, lat: float, lon: float,
             hd: float) -> Optional[dict]:
        ts = now()

        # Evidence the detector would have produced from this frame.
        ev = {k: v.get(k) for k in
              ("light_bar", "livery", "agency_decal", "push_bumper",
               "antenna_array", "police_body", "steelies", "pillar_spotlight",
               "fleet_decal")}
        ev["plate_text"] = v["plate"]
        ev["plate_legend"] = v.get("plate_legend", "")
        ev["agency_series"] = ["MUN", "STA", "CTY"]

        # Behavioural signal: ask the network what this car has been doing.
        ph_probe = privacy.plate_hash(v["plate"])
        trail = db.track_for(ph_probe, limit=200)
        if classify.patrol_score(trail) > 0.55:
            ev["patrol_pattern"] = True

        c = classify.classify(ev)
        tier = "public" if c["tierable"] else "private"

        # Detector confidence on the plate read itself, degraded by distance.
        dist = haversine_m(nd["lat"], nd["lon"], lat, lon)
        conf = max(0.0, 0.98 - (dist / (nd.get("reach_m") or 55)) * 0.45
                   - self.rng.random() * 0.08)
        if conf < float(CONFIG.get("min_plate_confidence", 0.55)):
            self.stats["missed"] += 1
            return None

        ph = privacy.plate_hash(v["plate"])
        img, vbox, pbox = snapshot.render_simulated(v, nd["name"], ts)
        meta = {"ts": ts, "node_id": nd["id"], "node_name": nd["name"],
                "tier": tier, "plate_text": v["plate"], "vclass": c["vclass"],
                "watermark": "SIMULATED"}
        # pbox is passed on every call, not only private ones, so the redaction
        # path is exercised by the same code that runs in production.
        snap_name, _sha = snapshot.store_crop(img, vbox, meta, plate_box=pbox)

        rec = {
            "node_id": nd["id"], "ts": ts, "lat": lat, "lon": lon,
            "tier": tier, "plate_hash": ph,
            "plate_text": v["plate"] if tier == "public" else None,
            "plate_state": "MI" if tier == "public" else None,
            "plate_conf": round(conf, 3),
            "vclass": c["vclass"], "vclass_conf": c["conf"], "vclass_why": c["why"],
            "color": v["color"], "body": v["body"], "make": None, "model": None,
            "heading": round(hd, 1), "speed_mph": round(v["speed_mph"], 1),
            "snap": snap_name, "source": "synthetic", "sig_ok": 1,
        }
        rec["id"] = db.insert_sighting(rec)

        # Score the gate against ground truth. This is the number that decides
        # whether the design is honest, so it is measured, not asserted.
        self.stats["emitted"] += 1
        if tier == "public":
            self.stats["published"] += 1
            if v["truth"] == "civilian":
                self.stats["leaks"] += 1     # a civilian was made public. Bad.
        return rec

    def report(self) -> str:
        s = self.stats
        return (f"emitted={s['emitted']} published={s['published']} "
                f"civilian_leaks={s['leaks']} low_conf_dropped={s['missed']}")


def run(seed: int = 7, ticks: int = 0, dt: float = 4.0, sleep: float = 1.0,
        on_sighting=None) -> Sim:
    """Run the town. ticks=0 runs forever (used by the hub's background thread)."""
    sim = Sim(seed)
    db.init()
    sim.build_town()
    sim.spawn()
    i = 0
    while ticks == 0 or i < ticks:
        for rec in sim.step(dt):
            if on_sighting:
                on_sighting(rec)
        i += 1
        if sleep:
            time.sleep(sleep)
    return sim


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    s = run(ticks=n, sleep=0)
    print(s.report())
