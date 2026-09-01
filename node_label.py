"""What a camera operator's label does to the sighting on the map.

This is the box-side half of the camera's labelling popup. The camera writes the
training label into its own crop sidecar - that half always worked - and then
calls /api/node/label so the same judgement reaches the map. The state machine
lives here rather than inside the route so the route stays about authentication
and this stays about policy, and so it can be exercised without HTTP.

🚨 WHY IT IS HERE AND NOT ON THE CAMERA. `labelbank._sync_sighting` used to do
this locally against `data/sparrow.db`. That file stopped being the map when the
box became the single source of truth: the sighting ids camctl's crops carry are
issued by the BOX, and none of them exist in the local file, so the lookup
returned None and the function did nothing at all. It did not error. The popup
kept saying it had moved the sighting. Every "Yes - government" answered at the
camera between the cutover and this fix moved nothing.

⚠️ ONE DATABASE. Do not "fix" any of this by giving the camera a second local
copy to write to. Two databases is what broke it.
"""

from __future__ import annotations

from typing import Optional

import db
import privacy

# What the camera may say. `unsure` is a real answer and deliberately does
# nothing to the map: it is a training label about a crop nobody could call.
VALID = {"police", "gov", "civilian", "fleet", "unsure", "screen"}
# `screen` = a vehicle photographed off a screen/phone (a faked sighting). It is
# never a publishable government vehicle, so it falls through to the demote
# branch below and RETRACTS a fake that the classifier had already published,
# exactly like `civilian`/`fleet`. Without it here the box would reject the
# label and leave the spoof on the map.


def _publishes(vclass: str) -> bool:
    """Would this class reach the public tier on THIS deployment?

    🚨 ASKED, NOT ASSUMED, AND THIS IS THE POINT OF THE FUNCTION.
    The old local code promoted both `police` and `gov` on the argument that a
    human calling something a government vehicle is the strongest signal the
    project has. That is still true about the SIGNAL and no longer true about
    the MAP: `public_tiers` is police-only (see core.py), because gov_dot
    publishing bin lorries is recorded history and every one of them dilutes the
    single claim this map exists to make. review_api.verdict already stopped
    publishing `gov` for exactly that reason; this path had been dead since the
    cutover, so porting it to the box verbatim would have quietly REVIVED a
    route to the public tier that the review page had deliberately closed.

    Reading the config rather than hard-coding "police" means a deployment that
    does widen `public_tiers` gets the wider behaviour here too, without anyone
    having to remember this file exists.
    """
    return privacy.tier_for(vclass) == "public"


def apply(sid: int, row: dict, label: str, undo: str = "") -> dict:
    """Make the map agree with the human. Returns what changed, or nothing.

    `undo` carries what a previous call reported doing, so clearing a label
    reverses EXACTLY that rather than inferring it. A sighting that was already
    public before anyone labelled it must not be retracted just because a label
    is being cleared - and a half-undo that leaves the change in place is worse
    than no undo, because it tells you the mistake is fixed. #27421 sat on the
    public map for fourteen hours behind precisely that.
    """
    if undo:
        return _undo(sid, undo)
    if label not in VALID:
        return {"error": f"label must be one of {sorted(VALID)}"}
    if label == "unsure":
        return {"did": None}

    tier = row.get("tier")
    vclass = row.get("vclass")

    if label in ("police", "gov"):
        if not _publishes(label):
            # Recorded, not published. The label is the thing the classifier
            # needs - "a human looked and said government but not police" is
            # the distinction it is worst at - and the row stays private, which
            # is what this deployment says a council pickup is. Matches
            # review_api.verdict('gov') exactly, so the two pages are once again
            # two views of one decision.
            #
            # ⚠️ AND IT HAS TO COME DOWN IF IT IS ALREADY UP. review_api only
            # ever sees pen items, which are private by definition, so it never
            # had to think about this. Here the operator can be looking at a row
            # the classifier ALREADY published as police and saying "no, that is
            # a council truck" - and the one thing that must not happen is the
            # map going on asserting police after a human said otherwise.
            if tier == "public":
                db.review_sighting(sid, "retracted")
                return {"did": "retracted", "published": False,
                        "note": "taken off the map - the map shows police "
                                "vehicles only"}
            db.review_sighting(sid, "confirmed_gov")
            return {"did": "recorded_gov", "published": False,
                    "note": "recorded as government but not published - the "
                            "map shows police vehicles only"}
        if tier != "public":
            db.promote_sighting(sid, label, None,
                                "confirmed as a government vehicle by the "
                                "camera operator while labelling")
            return {"did": "promoted", "published": True}
        if vclass != label:
            # Re-splitting a public row keeps it public and corrects the class,
            # rather than leaving the map asserting a police vehicle the
            # operator has just said was not one. The OLD class rides back in
            # the answer so the undo is an exact reversal and not a guess.
            db.promote_sighting(sid, label, None,
                                "class corrected by the camera operator while "
                                "splitting police from government")
            return {"did": f"reclassified:{vclass}", "published": True}
        return {"did": None, "published": True}

    # civilian / fleet / screen - anything a human says is not a publishable
    # government vehicle. A `screen` fake retracts here just like an ordinary car.
    if tier == "public":
        db.review_sighting(sid, "retracted")
        return {"did": "retracted", "published": False}
    # Already private, but a human has now looked at it. Recording that keeps it
    # out of the possibly-missed queue, which is what backfill_pen sweeps for -
    # without it the operator's work comes back to be judged again next sweep
    # and a reviewer's work quietly undoes itself. 'retracted' is the verdict
    # review_api.verdict('not') already uses for this same "never published,
    # human says ordinary vehicle" case, so it stays the one spelling.
    db.review_sighting(sid, "retracted")
    return {"did": "recorded_civilian", "published": False}


def _undo(sid: int, was: str) -> dict:
    """Reverse exactly what a previous apply() reported doing."""
    if was == "promoted":
        db.review_sighting(sid, "retracted")
        done = "taken off the map"
    elif was.startswith("reclassified:"):
        db.promote_sighting(sid, was.split(":", 1)[1], None,
                            "label cleared; the class correction it caused was "
                            "undone with it")
        done = "class put back"
    elif was == "retracted":
        db.promote_sighting(sid, "police", None,
                            "label cleared; the retraction it caused was undone "
                            "with it")
        done = "put back on the map"
    elif was in ("recorded_gov", "recorded_civilian"):
        done = "verdict withdrawn"
    else:
        return {"did": None}
    # Back to "no decision has been made". Both calls above stamp `reviewed`,
    # and leaving that behind hides the sighting from the possibly-missed queue
    # for ever - so the accident would still cost the vehicle its second look.
    db.unreview_sighting(sid)
    return {"did": "undone", "undone": done}
