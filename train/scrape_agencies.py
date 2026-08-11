"""Collect patrol-vehicle photographs for every police agency in Michigan.

the operator's plan, and it is a good one: Michigan first, before anything else.
Michigan State Police run one of the most distinctive liveries in the country,
most municipal departments here converge on a handful of looks (blue-on-white
Explorer, black-on-white Charger/Tahoe), and 568 agencies in one state is a
tractable, checkable target in a way that "police cars" as a category is not.

Deliberately NOT scraped: non-US liveries. His call, and the right one - a
Brazilian or Indian patrol car teaches the head a decision boundary that does
not exist on his street, and the head has only ~8 real local passes to argue
against it.

# The rule this file exists to obey

**A scrape result is a weak label for the PHOTO, never for a vehicle in it.**
Searching "Ann Arbor Police Department patrol car" returns pictures that
*contain* a patrol car. They also contain parked civilian cars, traffic behind
the cruiser, the officer's own vehicle, and a fair number of results that are
not police vehicles at all. Nothing here writes a label. Crops go into the
label bank unlabelled and a human decides, in the UI that already exists.

The tempting shortcut - run CLIP over each crop and keep what it calls police -
would produce a head trained to imitate CLIP. CLIP is the thing being replaced;
its errors are exactly what the head must not inherit. See [[project-sparrow]]
for what its zero-shot precision measured at.

# Why the background of each photo is worth as much as the subject

`prepare.py` refuses to run without negatives, because public positives against
his-camera negatives teaches "stock photo vs webcam frame" and nothing else.
The cheapest genuine negatives in existence are already inside these photos: the
ordinary cars parked beside the cruiser. Same photographer, same lens, same
compression, same weather, same year. `ingest_scraped.py` mines them.

    python train\\scrape_agencies.py --tier state          # MSP first, ~4 agencies
    python train\\scrape_agencies.py --tier county         # 83 sheriffs
    python train\\scrape_agencies.py --tier municipal      # the long tail
    python train\\scrape_agencies.py --agency "Ann Arbor"  # one, by substring
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402

HERE = Path(__file__).resolve().parent
AGENCIES = HERE / "mi_agencies.json"
OUT = DATA / "scrape"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Long edge on disk. Everything downstream degrades to 140-420px anyway
# (prepare.py TARGET_EDGE), so storing 4000px originals would cost gigabytes to
# throw away. 1024 leaves room to crop a vehicle out of a wide shot first.
STORE_EDGE = 1024

TIERS = {
    "state": ["State agencies"],
    "county": ["County agencies"],
    "municipal": ["Municipal agencies", "Township agencies"],
    "other": ["College and university agencies", "Tribal agencies",
              "Airport police agencies", "Transit agencies",
              "Railroad police", "Park and school police departments"],
}


def agencies(tier: str, match: str | None) -> list[dict]:
    rows = json.loads(AGENCIES.read_text(encoding="utf-8"))
    if match:
        m = match.lower()
        return [r for r in rows if m in r["name"].lower()]
    if tier == "all":
        return rows
    cats = TIERS[tier]
    return [r for r in rows if r["category"] in cats]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60]


def _fetch(url: str, headers: dict, timeout: int = 20) -> bytes:
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=timeout).read()


def fetch_image(url: str) -> bytes:
    """Download one image, identifying honestly to hosts that ask.

    Wikimedia 403s a generic browser user-agent on upload.wikimedia.org - their
    policy wants a descriptive one that says who is calling. Sending Chrome's
    string there fails every single Commons image, which is how the first run
    of this scraper returned zero photographs while reporting no errors.
    """
    if "wikimedia.org" in url or "wikipedia.org" in url:
        return _fetch(url, {"User-Agent": "SparrowMap/0.1 (research; "
                                          "https://sparrowmap.com)",
                            "Accept": "image/*"}, 20)
    return _fetch(url, {"User-Agent": UA, "Accept": "image/*",
                        "Accept-Language": "en-US,en;q=0.9"}, 15)


def commons_categories(query: str) -> list[str]:
    """Curated Commons categories that might hold this agency's vehicles."""
    agency = re.sub(r"\s+(patrol car|police vehicle|patrol vehicle Michigan)$", "", query)
    return [f"Category:{agency} automobiles",
            f"Category:{agency} vehicles",
            f"Category:{agency}"]


def search_images(query: str, limit: int) -> list[dict]:
    """Image results for one query, from two independent sources.

    Commons first: openly licensed, stable, and it will still work in a year.
    It is also thin - a few dozen Michigan police images in total - so the web
    index carries the volume. Failure of either source is not fatal; an agency
    with no usable photos is a normal outcome, not an error.
    """
    hits: list[dict] = []

    # 0. Commons CATEGORIES, when one exists for this agency. Full-text search
    # on Commons is mostly noise - searching "Michigan State Police patrol car"
    # returns "Compendium of..." and "History of..." - but the curated category
    # "Michigan State Police automobiles" is 77 files of actual patrol cars.
    # Precision over recall: these are the cleanest images we will ever get.
    for cat in commons_categories(query):
        try:
            u = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
                 "&generator=categorymembers&gcmtype=file&gcmlimit=200&gcmtitle=%s"
                 "&prop=imageinfo&iiprop=url|size&iiurlwidth=%d"
                 % (urllib.parse.quote(cat), STORE_EDGE))
            d = json.loads(_fetch(u, {"User-Agent": "SparrowMap/0.1 (research)"}, 25))
            for p_ in ((d.get("query") or {}).get("pages") or {}).values():
                ii = (p_.get("imageinfo") or [{}])[0]
                src = ii.get("thumburl") or ii.get("url")
                if src:
                    hits.append({"url": src, "w": ii.get("width", 0),
                                 "h": ii.get("height", 0), "via": "commons-cat",
                                 "page": p_.get("title", "")})
        except Exception:
            pass

    # 1. ~~Commons full-text search~~ REMOVED.
    #
    # It looked like coverage and was noise. Searching one small-town force's
    # "... Police Department patrol car" returned 25 results, every one a scanned
    # newspaper, a dictionary cover or a 1940s magazine - Commons' full-text
    # index matches the words anywhere in a file's description, and old print
    # scans mention police and Michigan constantly.
    #
    # It produced 222 files across nine agencies and not one police vehicle,
    # and it did so while REPORTING SUCCESS, which is worse than failing. The
    # curated categories above are precise and thin; the web index below has
    # the volume. A source that only ever adds noise is not a fallback.

    # 2. Web image index. Needs a per-session token before it answers, and it
    # rate-limits hard: a burst of queries earns a 403 that persists for a
    # while. That is not a bug to code around, it is a request to slow down -
    # so this backs off and gives up rather than hammering. An agency skipped
    # today is picked up on the next run; `.done` makes the whole thing
    # resumable.
    for attempt in range(3):
        try:
            html = _fetch("https://duckduckgo.com/?ia=images&iax=images&q="
                          + urllib.parse.quote(query),
                          {"User-Agent": UA,
                           "Accept": "text/html,application/xhtml+xml"}
                          ).decode("utf-8", "replace")
            vqd = re.search(r'vqd=["\']?([\d-]+)', html).group(1)
            d = json.loads(_fetch(
                "https://duckduckgo.com/i.js?l=us-en&o=json&f=,,,&p=1&q=%s&vqd=%s"
                % (urllib.parse.quote(query), vqd),
                {"User-Agent": UA, "Accept": "application/json",
                 "Referer": "https://duckduckgo.com/",
                 "X-Requested-With": "XMLHttpRequest"}).decode("utf-8", "replace"))
            for r in d.get("results", []):
                if r.get("image"):
                    hits.append({"url": r["image"], "w": r.get("width", 0),
                                 "h": r.get("height", 0), "via": "web",
                                 "page": r.get("url", "")})
            break
        except Exception as exc:
            if "403" not in str(exc) or attempt == 2:
                break
            time.sleep(20 * (attempt + 1))

    return hits[:limit * 3]          # over-fetch; most get dropped below


def save(raw: bytes, dst: Path, meta: dict) -> bool:
    """Write one image, downscaled, with its provenance beside it.

    Named by content hash so re-running never duplicates, and so the same photo
    reached through two agencies cannot end up in both train and test wearing
    different names. That is the same leakage that made recall read 87% when it
    was 69% - see [[project-sparrow]].
    """
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    h, w = img.shape[:2]
    if min(h, w) < 200:              # too small to survive being degraded again
        return False
    if max(h, w) > STORE_EDGE:
        s = STORE_EDGE / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)

    stem = hashlib.blake2b(raw, digest_size=8).hexdigest()
    f = dst / f"{stem}.jpg"
    if f.exists():
        return False
    cv2.imwrite(str(f), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    meta["sha"] = stem
    (dst / f"{stem}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return True


def scrape_one(agency: dict, per_agency: int, pause: float) -> int:
    d = OUT / slug(agency["name"])
    done = d / ".done"
    if done.exists():
        return 0
    d.mkdir(parents=True, exist_ok=True)

    name = agency["name"]
    # Two phrasings. "patrol car" pulls cruisers; "police vehicle" catches the
    # SUVs and the marked units that nobody calls a car.
    queries = [f"{name} patrol car", f"{name} police vehicle"]
    if agency["category"] == "County agencies":
        queries.append(f"{name} patrol vehicle Michigan")

    n = 0
    for q in queries:
        if n >= per_agency:
            break
        for hit in search_images(q, per_agency):
            if n >= per_agency:
                break
            try:
                raw = fetch_image(hit["url"])
            except Exception:
                continue
            if save(raw, d, {"agency": name, "category": agency["category"],
                             "query": q, "source": hit["url"],
                             "page": hit.get("page", ""), "via": hit["via"],
                             "label": None}):
                n += 1
            time.sleep(pause * random.uniform(0.5, 1.5))
        time.sleep(pause)

    done.write_text(str(n), encoding="utf-8")
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="state",
                    choices=list(TIERS) + ["all"],
                    help="which slice of the 568 agencies to run")
    ap.add_argument("--agency", help="substring match on one agency name")
    ap.add_argument("--per-agency", type=int, default=25)
    ap.add_argument("--pause", type=float, default=1.2,
                    help="seconds between requests - be a good citizen")
    ap.add_argument("--limit", type=int, default=0, help="stop after N agencies")
    args = ap.parse_args()

    rows = agencies(args.tier, args.agency)
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        raise SystemExit("no agencies matched")

    print(f"{len(rows)} agencies -> {OUT}")
    total = 0
    for i, a in enumerate(rows, 1):
        got = scrape_one(a, args.per_agency, args.pause)
        total += got
        flag = "" if got else "  (nothing new)"
        print(f"[{i:3d}/{len(rows)}] {got:3d}  {a['name'][:52]}{flag}", flush=True)

    print(f"\n{total} new photographs.")
    print("NOTHING IS LABELLED. Next: python train\\ingest_scraped.py")


if __name__ == "__main__":
    main()
