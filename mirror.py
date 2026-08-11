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

from core import CONFIG


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
