"""Public-mirror mode: what the internet-facing box is allowed to hold.

the operator's requirement, and it is the right one to design to: *"even if it was
[hacked], the data retrieved is public government data anyway."*

Nothing is unhackable. What is achievable is a machine where a total
compromise hands the attacker the public record - which is already public -
and nothing else. That is a property of what the box STORES, not of how well
it is defended, so it belongs here rather than in a firewall rule.

## Why this is a mode of the existing hub, not a second program

A separate mirror server would duplicate the redaction rules, the tier logic
and the ingest path. This codebase has been bitten repeatedly by exactly that:
a rule living in two files gets fixed in one. `is_operator_addr` and the
government-vehicle call both became single implementations for the same
reason. So the mirror is one flag, and the differences are enforced in one
place each.

## What a mirror refuses to hold

| | home hub | public mirror |
|---|---|---|
| public-tier sighting + image | yes | **yes** - this is the point |
| private-tier image | 14 days | **never stored** |
| private-tier plate hash | yes | **never stored** |
| node TRUE position | yes | **never received** |
| training crops (the bank) | yes | **never written** |
| operator routes | yes | **absent entirely** |

The private tier still EXISTS on a mirror, reduced to `(ts, position, class)`
so the live traffic view still works. A dot with a time and "civilian" is not
personal data in any useful sense: no identifier, no image, nothing to
correlate.

## The true position never arrives

The strongest control here is not a filter, it is an absence. Position jitter
used to be applied by the server, which means the server had to be told the
real coordinates first. On a mirror the NODE jitters its own position before
enrolling and sends only the jittered point and the road span - so a full
database dump reveals what the map already shows.

Same principle as the phone node destroying plates at the edge rather than
asking the server to redact them: **the safest data is the data that was never
transmitted.**
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import core
from core import CONFIG, DATA

# Where a mirror parks a phone contributor's crop for the home classifier to
# pull. A mirror has no GPU and no trained head, so it cannot decide whether a
# public phone catch is a patrol car - only the home node can. The crop waits
# here, already destroyed below plate legibility, until the home puller scores
# it and deletes it. NOT the training bank (a mirror never banks); a short-lived
# outbox, pruned on every write.
INBOX = DATA / "inbox"
INBOX_TTL = 12 * 3600     # a crop nobody pulled in 12h is stale; drop it


def enabled() -> bool:
    """Is this deployment a public mirror?"""
    return bool(CONFIG.get("public_mirror", False))


def strip_sighting(rec: dict) -> dict:
    """Reduce a sighting to what a mirror may keep.

    Applied on the way IN, before the row is written - not on the way out.
    A redaction that happens at read time leaves the data on the disk, and the
    disk is exactly what an attacker copies.
    """
    if not enabled() or rec.get("tier") == "public":
        return rec
    out = dict(rec)
    # Everything that could identify the vehicle or link two sightings of it.
    for k in ("plate_text", "plate_state", "plate_hash", "plate_conf",
              "snap", "bank_ref", "make", "model", "color", "body",
              "heading", "speed_mph"):
        out[k] = None
    return out


def may_store_image(tier: str) -> bool:
    """A mirror stores an image only for a published government vehicle."""
    return (not enabled()) or tier == "public"


def may_bank() -> bool:
    """A mirror never accumulates training crops.

    Labelling happens where the camera is. A volunteer's own device keeps its
    own crops; the mirror carries claims, not photographs of the street.
    """
    return not enabled()


def node_fields(rec: dict) -> dict:
    """Drop a node's true position and contact details before storing it.

    🚨 A MIRROR NEVER LEARNS WHERE A CAMERA ACTUALLY IS.
    `nodes.lat/lon` are the real coordinates - the ones jitter exists to hide.
    Keeping them on an internet-facing box means a single breach hands over
    the home address of every volunteer photographing police, which is the
    worst outcome this project has.

    The node sends its jittered position; that is what gets stored, in both
    columns, so nothing downstream has to remember which one is safe.
    """
    if not enabled():
        return rec
    out = dict(rec)
    out["lat"] = out.get("pub_lat")
    out["lon"] = out.get("pub_lon")
    out["contact"] = None
    return out


def relay_enabled() -> bool:
    """Does this mirror quarantine phone crops for the home classifier?

    On by default for a mirror, because the whole point of a public map is that
    volunteers' phones contribute to it - and a phone cannot make the
    government call itself. Off (`relay_inbox: false`) reverts to the old
    behaviour: a phone crop on the mirror is dropped, never scored.
    """
    return enabled() and bool(CONFIG.get("relay_inbox", True))


# 🚨 THE PRUNE RAN ON EVERY SINGLE WRITE, AND IT READS THE WHOLE DIRECTORY.
#
# quarantine_write is called once per phone-crop sighting - the hottest path on
# this box - and it called _prune_inbox() first, unconditionally. _prune_inbox
# globs the inbox and then OPENS AND JSON-PARSES EVERY FILE IN IT to read one
# timestamp. With 4,772 files parked that is ~2,386 file reads PER POST, and at
# 40 concurrent posts roughly 95,000 file operations at once.
#
# Measured 2026-08-19 by py-spy during a publish burst: 18 threads in open(),
# 12 in read_text(), 10 in glob(), every one of them under
# quarantine_write -> _prune_inbox -> _ingest. POSTs were held 18-26s and the
# camera fleet had 87.7% of its posts refused.
#
# It also got worse over time rather than staying constant, because the cost is
# proportional to how many crops are parked - so the fuller the inbox, the
# slower every new write, which is the wrong way round.
#
# Pruning is housekeeping. It does not need to happen before each write; it
# needs to happen. Once a minute turns ~2,386 reads per POST into ~2,386 reads
# per MINUTE, and a crop lingering an extra sixty seconds costs nothing.
_PRUNE_EVERY_S = 60.0
_last_prune = 0.0

# 🚨 THE QUARANTINE MUST BE BOUNDED, AND IT WAS NOT.
#
# box_puller streams the WHOLE inbox as one tar over ssh, a design sized for
# "hundreds of tiny files". Once ingest started delivering 100% of the fleet
# instead of 12%, ~24,000 crops an hour were parked here against a home node
# that can score a fraction of that, and the pull cadence collapsed:
#
#     under 1 min -> 5 -> 12 -> 15 -> 25 -> 93 MINUTES between completed pulls
#
# That is a runaway, not a slowdown: a bigger inbox makes the pull slower, which
# leaves the inbox bigger. Measured at the time - 48,640 files/hr arriving
# against 60 files/hr drained, with 93,833 files and 572 MB parked.
#
# Crops beyond what the home node can score are wasted whatever we do; they age
# out at INBOX_TTL having never been looked at. The choice is only whether they
# are discarded cheaply at write time or expensively after slowing every pull
# and filling the disk. So: park up to a bound, then stop.
#
# ⚠️ THE SIGHTING IS NEVER FAILED FOR THIS. Only the crop is not quarantined;
# the row is stored and the map is unaffected. That is already how this function
# behaves on any error.
_INBOX_MAX_FILES = 12000
_inbox_count = None          # cached; refreshed by the prune, not per write


def _scandir_json(d: Path):
    """Entries ending .json, without building a Path per file.

    ⚠️ os.scandir rather than Path.glob because glob constructs a Path object
    for every entry and stats it through pathlib; at 55,000 files that overhead
    is the bulk of the work. scandir carries the stat data with the entry.
    """
    import os
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.name.endswith(".json"):
                    yield e
    except FileNotFoundError:
        return


def _prune_inbox(force: bool = False) -> None:
    """Drop crops the home puller never came for. Best-effort, never raises.

    ⚠️ RATE LIMITED. See the note above - this is O(files in the inbox) and it
    used to run on every write.
    """
    global _last_prune
    now_ = time.time()
    if not force and now_ - _last_prune < _PRUNE_EVERY_S:
        return
    _last_prune = now_
    global _inbox_count
    try:
        cutoff = time.time() - INBOX_TTL
        seen = 0
        for j in _scandir_json(INBOX):
            try:
                # 🚨 stat(), NOT read_text()+json.loads().
                # The only field this needs is when the crop was written, and
                # the filesystem already records that. Parsing the file to
                # recover a timestamp it is sitting next to costs an open, a
                # read and a JSON parse per entry.
                #
                # Measured 2026-08-19: the inbox reached 55,802 files, so the
                # once-a-minute prune was opening and parsing ~27,900 files
                # every minute. As one stat() each it is roughly fifty times
                # cheaper and, more importantly, it stops the housekeeping cost
                # scaling with how much data is flowing.
                #
                # These files are written once and never touched, so mtime and
                # the "written" field it used to parse are the same number.
                seen += 1
                if j.stat().st_mtime < cutoff:
                    Path(j.path).with_suffix(".jpg").unlink(missing_ok=True)
                    Path(j.path).unlink(missing_ok=True)
                    seen -= 1
            except Exception:
                continue
        # The count is a by-product of the walk we already did, so the bound
        # below costs nothing per write. Counting 90,000 files on every POST is
        # the exact mistake this function was just fixed for.
        _inbox_count = seen
    except Exception:
        pass


def quarantine_write(sighting_id: int, crop_bytes: bytes,
                     meta: dict) -> Optional[str]:
    """Park one plate-less phone crop for the home classifier to pull.

    The crop is ALREADY below plate legibility when it reaches here (the phone
    destroyed it; snapshot.subresolution_bytes re-verified the size). The mirror
    keeps it only long enough for the home node to read it over an out-of-band
    channel and delete it. Nothing here is served by any HTTP route - a mirror
    grows no operator surface for this. Best-effort: a sighting is never failed
    because its crop could not be parked.
    """
    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        _prune_inbox()
        # Bounded. See _INBOX_MAX_FILES: past this the home node is not going to
        # get to these crops anyway, and parking them makes every pull slower.
        if _inbox_count is not None and _inbox_count >= _INBOX_MAX_FILES:
            return None
        stem = str(int(sighting_id))
        (INBOX / f"{stem}.jpg").write_bytes(crop_bytes)
        (INBOX / f"{stem}.json").write_text(json.dumps(
            {**meta, "sighting_id": int(sighting_id), "written": time.time()},
            indent=1), encoding="utf-8")
        return stem
    except Exception:
        return None


REVIEW = INBOX.parent / "review"
RF_PEN = INBOX.parent / "rf_pen"


def rf_park(node_id: str, candidate: dict) -> Optional[str]:
    """Park ONE RF surveillance-device candidate for human review.

    RF candidates carry no image and no civilian data - the client already
    dropped every private device at the edge, so this is only ever a claim that
    a KNOWN surveillance device (a Flock/ALPR camera, etc.) was heard at a
    position. It NEVER publishes: it lands in the RF pen and waits for a person,
    exactly like the government-vehicle review pen. A false RF guess must cost a
    review click, never a wrong dot on the public map.

    Stored as one json per (node, device), so re-hearing the same camera updates
    rather than piling up.
    """
    try:
        RF_PEN.mkdir(parents=True, exist_ok=True)
        dev = str(candidate.get("dev_id") or "")[:32] or "unknown"
        stem = f"{str(node_id)[:24]}_{dev}"
        # keep only fields that describe the surveillance device + where/when.
        safe = {k: candidate.get(k) for k in
                ("dev_id", "ssid", "vendor_reason", "band", "rssi",
                 "lat", "lon", "ts")}
        (RF_PEN / f"{stem}.json").write_text(json.dumps(
            {**safe, "node_id": str(node_id)[:24], "reviewed": None,
             "written": time.time()}, indent=1), encoding="utf-8")
        return stem
    except Exception:
        return None


def review_write(sighting_id: int, crop_bytes: bytes, meta: dict) -> Optional[str]:
    """Park a plate-less crop in the review pen, keyed by its sighting id.

    Unlike quarantine_write (the inbox, which the home classifier pulls, scores
    and empties), the review pen holds government CANDIDATES that already have a
    score and are waiting for a HUMAN to confirm or reject them via the reviewer
    UI. A camera node has already scored its own crop, so its ambiguous calls
    land here directly rather than round-tripping through the home puller. The
    crop is sub-resolution and plate-less, the same guarantee the inbox carries.
    """
    try:
        REVIEW.mkdir(parents=True, exist_ok=True)
        stem = str(int(sighting_id))
        (REVIEW / f"{stem}.jpg").write_bytes(crop_bytes)
        (REVIEW / f"{stem}.json").write_text(json.dumps(
            {**meta, "sighting_id": int(sighting_id), "written": time.time()},
            indent=1), encoding="utf-8")
        return stem
    except Exception:
        return None


def evidence_write(sighting_id: int, full_bytes: bytes) -> Optional[str]:
    """Keep a government candidate's full-resolution original until reviewed.

    🚨 HOME ONLY, AND THE REFUSAL IS THE POINT. A mirror keeps claims and
    photographs of PUBLISHED government vehicles - never un-degraded imagery of
    whatever drove past, which is precisely what an unreviewed candidate is
    until somebody looks. `may_store_image` already drops private-tier images on
    a mirror for that reason; this is the same rule for the same reason, stated
    where the second writer lives rather than assumed from the first.

    Everything else about this file is described in core.EVIDENCE, including
    why holding it is justified and what it costs.
    """
    if enabled():
        return None
    try:
        core.EVIDENCE.mkdir(parents=True, exist_ok=True)
        stem = str(int(sighting_id))
        (core.EVIDENCE / f"{stem}.jpg").write_bytes(full_bytes)
        return stem
    except Exception:
        return None


def evidence_sweep(now_ts: Optional[float] = None) -> int:
    """Destroy candidate originals nobody answered in time.

    The retention sweep elsewhere runs on ROWS. This runs on FILES, because the
    thing being bounded is un-degraded imagery on disk and a file that outlived
    its row is exactly the case that needs catching - the same lesson as the
    `/snap` file that outlived its sighting.
    """
    if not core.EVIDENCE.exists():
        return 0
    cut = (now_ts if now_ts is not None else time.time()) - core.EVIDENCE_TTL_S
    gone = 0
    for p in core.EVIDENCE.glob("*.jpg"):
        try:
            if p.stat().st_mtime < cut:
                p.unlink(missing_ok=True)
                gone += 1
        except Exception:
            continue
    return gone


def route_allowed(path: str) -> bool:
    """Operator routes do not exist on a mirror.

    Not "are refused" - are absent. Review, promotion, retraction and purging
    all happen at home, on the hub that has the evidence to judge with. A
    mirror with a disabled review page is still a mirror with a review page to
    find a bug in.
    """
    if not enabled():
        return True
    return not path.startswith(("/review", "/api/review", "/api/operator",
                                "/api/purge", "/api/audit"))
