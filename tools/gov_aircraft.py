"""Which government aircraft are flying over a box, right now.

    python tools/gov_aircraft.py --box 41,-88,46.5,-82        # lat/lon bounds
    python tools/gov_aircraft.py --box 41,-88,46.5,-82 --le    # law enforcement only
    python tools/gov_aircraft.py --refresh                     # re-pull the FAA registry

RESEARCH PROTOTYPE. Nothing here is wired into the map or the hub - it answers
"could SparrowMap see police aircraft" and the answer is yes, with caveats that
matter more than the code.

HOW IT WORKS, in two public datasets and no hardware:
  1. OpenSky Network's /states/all gives live aircraft by bounding box, free,
     anonymous, no key. Each carries an ICAO24 hex.
  2. The FAA's Releasable Aircraft Database (public domain, refreshed daily)
     maps that hex to a registered owner in MASTER.txt.

🔑 THE FILTER THAT MATTERS IS THE FAA'S OWN, NOT A NAME PATTERN.
MASTER.txt has TYPE REGISTRANT, where 5 means Government - 5,719 aircraft.
That is authoritative. The first version of this matched owner NAMES for words
like MARSHAL and cheerfully flagged "MARSHALL XAVIER T", a private individual
who is not a US Marshal. Name matching now runs only INSIDE the government set,
where the words can only mean what they look like.

🚨 THREE LIMITS, AND THEY ARE THE WHOLE STORY:

  1. GOVERNMENT IS NOT POLICE. Most of the type-5 set is universities and
     agricultural agencies - Western Michigan's flight school shows up over
     this state constantly. Useful signal needs law-enforcement filtering on
     top, and even then:

  2. THE NAME OFTEN DOES NOT SAY. A live check found N632SD registered to
     "COUNTY OF OAKLAND" with no "SHERIFF" anywhere in it, which the --le
     filter therefore misses. Counties register their sheriff's aircraft under
     the county. A curated agency list beats a regex here, and a human beats
     both.

  3. FEDERAL SURVEILLANCE IS LARGELY INVISIBLE TO THIS. Those aircraft are
     widely documented as registered to front companies, which makes them
     TYPE REGISTRANT 3 (corporation), not 5. This finds the county helicopter;
     it does not find the FBI. Aircraft can also fly with ADS-B off or under
     an anonymised address, and the FAA's LADD programme lets owners ask feeds
     to hide them.

⏭️ The technique that survives all three is ORBIT PATTERN - sustained circling
at low altitude over one point - because it describes what the aircraft is
DOING rather than who signed its paperwork. That is a bigger build and it is
the honest next step, not this.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

FAA_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
OPENSKY = "https://opensky-network.org/api/states/all"
CACHE = Path(__file__).resolve().parent.parent / "data" / "faa_registry.json"
UA = {"User-Agent": "SparrowMap/1.0 (aircraft research; sparrowmap.com)"}

# Only ever applied to aircraft the FAA already marks as government-registered.
LAW_ENFORCEMENT = re.compile(
    r"(POLICE|SHERIFF|HIGHWAY PATROL|STATE PATROL|PUBLIC SAFETY|MARSHAL|"
    r"HOMELAND SECURITY|CUSTOMS|BORDER PROTECTION|DEPARTMENT OF JUSTICE|"
    r"DEPT OF JUSTICE|TROOPER|CONSTABLE|CORRECTIONS)")

# 🚨 MARSHALL UNIVERSITY IS NOT A US MARSHAL, AND IT FLIES A LOT.
# The word test caught a private individual named Marshall on the first pass;
# scoping it to government-registered aircraft was supposed to fix that, and
# then a state university with the same name walked straight through. Education
# is never law enforcement, so it is excluded outright rather than by hoping
# the next pattern is tighter.
NOT_LAW_ENFORCEMENT = re.compile(
    r"(UNIVERSITY|COLLEGE|SCHOOL|REGENTS|TRUSTEES|ACADEMY|INSTITUTE)")


def is_law_enforcement(owner: str) -> bool:
    o = owner.upper()
    return bool(LAW_ENFORCEMENT.search(o)) and not NOT_LAW_ENFORCEMENT.search(o)


def build_registry() -> dict:
    """{icao24 hex: [owner, n-number, state]} for GOVERNMENT aircraft only."""
    print(f"downloading the FAA registry ({FAA_URL}) …", file=sys.stderr)
    with urllib.request.urlopen(urllib.request.Request(FAA_URL, headers=UA),
                                timeout=180) as r:
        blob = r.read()
    z = zipfile.ZipFile(io.BytesIO(blob))
    raw = z.read("MASTER.txt").decode("latin-1", "replace").splitlines()
    hdr = [h.strip().lstrip("﻿") for h in raw[0].split(",")]
    ih, iname = hdr.index("MODE S CODE HEX"), hdr.index("NAME")
    ist, ityp = hdr.index("STATE"), hdr.index("TYPE REGISTRANT")
    out = {}
    for line in raw[1:]:
        f = line.split(",")
        if len(f) <= ih or f[ityp].strip() != "5":
            continue
        h = f[ih].strip().upper()
        if h:
            out[h] = [f[iname].strip(), f[0].strip(), f[ist].strip()]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"built": time.time(), "gov": out}),
                     encoding="utf-8")
    return out


def registry(refresh: bool = False) -> dict:
    if not refresh and CACHE.exists():
        try:
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            # The FAA refreshes daily; a week-old copy still answers the
            # question and avoids hammering a public dataset.
            if time.time() - d.get("built", 0) < 7 * 86400:
                return d["gov"]
        except Exception:
            pass
    return build_registry()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", default="41,-88,46.5,-82",
                    help="lamin,lomin,lamax,lomax")
    ap.add_argument("--le", action="store_true",
                    help="law enforcement only (see the caveats in the docstring)")
    ap.add_argument("--refresh", action="store_true", help="re-pull the registry")
    a = ap.parse_args()

    gov = registry(a.refresh)
    print(f"government-registered aircraft in the FAA registry: {len(gov)}")

    try:
        lamin, lomin, lamax, lomax = [float(x) for x in a.box.split(",")]
    except ValueError:
        sys.exit("--box wants lamin,lomin,lamax,lomax")
    url = (f"{OPENSKY}?lamin={lamin}&lomin={lomin}&lamax={lamax}&lomax={lomax}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=40) as r:
            states = (json.loads(r.read()) or {}).get("states") or []
    except Exception as exc:
        sys.exit(f"OpenSky unavailable: {exc}")

    print(f"live aircraft in the box: {len(states)}")
    hits = []
    for s in states:
        h = (s[0] or "").strip().upper()
        who = gov.get(h)
        if not who:
            continue
        is_le = is_law_enforcement(who[0])
        if a.le and not is_le:
            continue
        hits.append((h, who, s, is_le))

    print(f"{'law-enforcement' if a.le else 'government'}-registered among them: "
          f"{len(hits)}\n")
    for h, (owner, n, st), s, is_le in hits:
        alt = f"{round(s[7])}m" if s[7] else "-"
        vel = f"{round(s[9])}m/s" if s[9] else "-"
        print(f"  {h}  N{n:<7} {owner[:40]:<40} {st:<3} "
              f"{'LE ' if is_le else '   '} alt={alt:<7} spd={vel:<8} "
              f"{(s[5] or 0):.3f},{(s[6] or 0):.3f}")
    if hits and not a.le:
        print("\n⚠️  'government' includes universities and agricultural agencies. "
              "See the caveats at the top of this file before reading anything "
              "into a hit.")


if __name__ == "__main__":
    main()
