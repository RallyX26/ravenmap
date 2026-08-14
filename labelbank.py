"""The labelling queue: turning banked crops into ground truth.

The node banks every vehicle it sees with CLIP's guess and an empty `label`.
This module is what a human fills in, and it is the only source of truth the
project has. the operator's five "none of them are police vehicles" was worth more
than every heuristic in the codebase; this is the machinery for getting more of
that, faster.

## 🚨 CLIP'S GUESS IS HIDDEN UNTIL AFTER YOU DECIDE

Showing a model's prediction next to the image you are asking a human to label
is the fastest way to destroy a dataset. The human anchors on it, agrees with
it more often than they should, and the labels drift toward the model. Then the
model is evaluated against labels it partly wrote, scores well, and the whole
exercise measures nothing - which is exactly the trap this project is climbing
out of. So the guess is withheld until a call has been made, and only then
revealed (it is genuinely useful afterwards - a disagreement is interesting).

## Two sampling modes, because they answer different questions

    review   RANDOM order. Produces an unbiased, representative sample, which
             is the only kind that can MEASURE precision and recall. Default.

    hunt     Most CLIP-uncertain first. Finds the decision boundary fastest and
             is the right way to spend labelling effort on IMPROVING a model.
             ⚠️ The labels it produces are a biased sample by construction and
             must never be used to report performance.

Keeping these apart matters. Label the hard cases, measure on the random ones,
and never quote a number computed over the hard ones - it will look far worse
than reality and lead to the wrong decision.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Optional

from core import DATA, NODE_UA

BANK = DATA / "training"
# ⚠️ THE KEYS ARE STABLE, THE DISPLAY NAMES ARE NOT.
# 'police' is what 16 existing labels already say and what classify.py calls
# the class, so it stays the key even though the UI now reads "Government" -
# renaming it would silently orphan every label gathered so far.
#
# 'fleet' was missing entirely, which meant a delivery van, a utility truck or
# a school bus could only be recorded as 'civilian' (wrong - it is a
# commercially owned vehicle the map has a category for) or 'unsure' (a wasted
# judgement). A label set narrower than the class set forces the labeller to
# lie.
#
# 🚨 'gov' ADDED 2026-08-10, AND THE OLD LABELS DID NOT CHANGE MEANING.
# classify.py has emitted FOUR classes (police / gov / fleet / civilian) since
# it was written, but this set only ever offered three, so the labeller had no
# way to say "government, not police". A municipal pickup and a marked patrol
# car both had to be clicked `police`. That is not a labelling inconvenience,
# it is why there is no police-vs-gov classifier: the routing rule in
# classify.py is doing the job because no ground truth exists to train against.
#
# The trap when adding it is REDEFINING `police` in place. 73 local crops
# already say `police` MEANING "government vehicle" - the button read
# "Government". Narrowing the key would silently convert every one of them into
# a claim nobody made. So the old labels keep their old meaning and are marked
# as such: `label_vocab` 1 = the merged government bucket, 2 = police and gov
# are distinct. `split` mode re-opens the vocab-1 crops so a human resolves
# them, one pass at a time. Nothing is inferred.
VALID = {"police", "gov", "fleet", "civilian", "unsure"}

# Bumped when a key's MEANING changes, never when a key is merely added.
LABEL_VOCAB = 2


def _sidecar(day: str, stem: str) -> Path:
    return BANK / day / f"{stem}.json"


def items() -> list[dict]:
    """Every banked crop with its metadata. Cheap enough at this scale."""
    out = []
    for j in sorted(BANK.rglob("*.json")):
        img = j.with_suffix(".jpg")
        if not img.exists():
            continue
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        clip = d.get("clip") or {}
        out.append({
            "day": j.parent.name,
            "stem": j.stem,
            "ts": d.get("ts"),
            "cls_name": d.get("cls_name"),
            "label": d.get("label"),
            "labelled_at": d.get("labelled_at"),
            "sampling": d.get("sampling"),
            # ⚠️ PROVENANCE, AND IT MUST COME FROM `source`, NOT `sampling`.
            # Labelling a crop through this UI overwrites `sampling` with
            # whatever mode the page was in, which silently destroys the only
            # field saying where a crop came from. `source` is written once by
            # whatever ingested it and never touched again.
            "source": d.get("source"),
            "node_id": d.get("node_id"),
            # 1 (or absent) = labelled when `police` meant "government vehicle",
            # so it says nothing about whether it was a POLICE vehicle.
            "vocab": int(d.get("label_vocab") or 1),
            # kept server-side; only sent to the page AFTER a call is made
            "_clip": {"vclass": clip.get("vclass"), "conf": clip.get("conf"),
                      "margin": clip.get("margin"), "scores": clip.get("scores")},
            "_uncertainty": 1.0 - float(clip.get("margin") or 0.0),
        })
    return out


def stats() -> dict:
    it = items()
    done = [i for i in it if i["label"]]
    by = {}
    for i in done:
        by[i["label"]] = by.get(i["label"], 0) + 1
    # Only labels gathered in review mode can be used to report performance.
    # See the module docstring.
    measurable = [i for i in done if (i.get("sampling") or "review") == "review"]
    likely_left = sum(1 for i in it if not i["label"] and _gov_score(i) >= 0.50)
    recheck = [i for i in done
               if i["label"] == "civilian"
               and i["_clip"].get("vclass") in ("fleet", "gov_dot", "police", "emergency")]
    # Crops still carrying the merged government bucket. Until this reaches 0
    # the police-vs-gov question has no ground truth to answer it with.
    unsplit = [i for i in done
               if i["label"] == "police" and i["vocab"] < LABEL_VOCAB
               and i.get("source") != "scraped"]
    return {
        "fleet": by.get("fleet", 0),
        "gov": by.get("gov", 0),
        "unsplit": len(unsplit),
        "recheck": len(recheck),
        "likely_left": likely_left,
        "total": len(it),
        "labelled": len(done),
        "remaining": len(it) - len(done),
        "by_label": by,
        "measurable": len(measurable),
        "positives": by.get("police", 0),
        **_positive_breakdown(done),
    }


def _pass_count(rows: list) -> int:
    """Distinct vehicle passes: same node, within ten seconds.

    Same rule as train/fit_local.pass_groups, which exists because random folds
    over fragmented tracks reported 87% recall against a true 69%.
    """
    n, last = 0, None
    for i in sorted(rows, key=lambda r: (r.get("node_id") or "", r.get("ts") or 0)):
        node, ts = i.get("node_id") or "", i.get("ts") or 0
        if last is None or node != last[0] or ts - last[1] > 10.0:
            n += 1
        last = (node, ts)
    return n


def _positive_breakdown(done: list) -> dict:
    """What the government-vehicle count is ACTUALLY made of.

    🚨 THE HEADLINE NUMBER WAS OFF BY FIFTEEN TIMES.
    The page read "242 gov", which sounds like 242 government vehicles. The
    truth was 16 distinct vehicle passes. The gap came from three multipliers
    stacked on each other, none of them visible in a single total:

      * 201 of the 242 were SCRAPED Michigan State Police photographs - which
        were measured to make the classifier WORSE, not better, because the
        local force runs black Tahoes and MSP run blue Chargers;
      * those 201 are 3 degraded variants each of only 67 source photographs;
      * and his own 41 crops are 16 vehicle passes, because the tracker
        fragments one car into several crops seconds apart.

    A count that grows when you augment your data is measuring augmentation,
    not evidence. The number that has actually capped this classifier from the
    beginning is `positive_passes` - distinct government vehicles this camera
    has really seen - so that is what gets shown.
    """
    # 'gov' counts as a positive here for the same reason it always did: this
    # number is "government vehicles this camera has really seen", and splitting
    # the bucket must not make the headline figure appear to collapse.
    pol = [i for i in done if i["label"] in ("police", "gov")]
    local = [i for i in pol if i.get("source") not in ("scraped", "remote_node")]
    remote = [i for i in pol if i.get("source") == "remote_node"]

    passes = _pass_count(local + remote)
    # The two numbers that decide whether a 3-way head can be fitted at all.
    # Counted in PASSES and only on vocab-2 labels, because a vocab-1 `police`
    # crop is not a claim about police.
    split = [i for i in local + remote if i["vocab"] >= LABEL_VOCAB]
    return {
        "police_passes": _pass_count([i for i in split if i["label"] == "police"]),
        "gov_passes": _pass_count([i for i in split if i["label"] == "gov"]),
        "positive_passes": passes,
        "positive_local": len(local),
        "positive_remote": len(remote),
        "positive_public": len(pol) - len(local) - len(remote),
    }


def next_item(mode: str = "review", seed: Optional[int] = None) -> Optional[dict]:
    """The next crop to show.

    `recheck` is the exception to "never show a labelled crop": it deliberately
    re-opens ones already marked `civilian` where the model saw a fleet,
    government or emergency vehicle.

    🚨 IT EXISTS BECAUSE A MISSING BUTTON CORRUPTED THE DATASET.
    Until the Fleet option was added, the only choices were police, civilian
    and unsure - so a delivery van, a box truck and a MedStar AMBULANCE were
    all recorded as `civilian`, because there was nothing truer to click. Those
    labels then became the ceiling on the classifier: the highest-scoring
    "false positives" holding recall down turned out to be an ambulance and a
    box truck that were never civilian at all.
    """
    if mode == "marked":
        # 🚨 EVERY CROP YOU CALLED GOVERNMENT, NEWEST FIRST, SO A MISTAKE CAN BE
        # TAKEN BACK.
        #
        # This was missing and it has now cost twice. Labelling a crop
        # Government PROMOTES its sighting onto the public map; the queue only
        # ever served UNLABELLED crops, so the moment you answered there was no
        # route back to it. Both times the fix was me editing the database by
        # hand, which is not a fix that scales past one operator.
        #
        # `recheck` did not cover this: it re-opens crops marked CIVILIAN where
        # the model disagreed. The opposite direction - a false POSITIVE the
        # human created - had no queue at all, and it is the direction that
        # puts a claim about a real vehicle on a public map.
        #
        # Newest first because an accidental keypress is remembered for about a
        # minute. Local crops only: the scraped ones are press photographs, not
        # claims about anybody's street.
        cand = [i for i in items()
                if i["label"] in ("police", "gov")
                and i.get("source") not in ("scraped",)]
        cand.sort(key=lambda i: -(i.get("labelled_at") or i.get("ts") or 0))
        return cand[0] if cand else None

    if mode == "split":
        # 🚨 THE ONLY QUEUE THAT CAN CREATE A POLICE-VS-GOV DATASET.
        # Every crop labelled back when one button said "Government" and meant
        # both. Re-asked as two questions instead of one.
        #
        # Ordered by NODE then TIME, not by newest or by confidence, so the
        # several crops of one vehicle pass arrive together. A pass split across
        # a session - patrol car now, the same patrol car in twenty minutes -
        # is how one vehicle ends up in both classes, and 39 passes cannot
        # absorb that kind of noise.
        #
        # Scraped press photographs are excluded: they are agency publicity
        # shots, they are already police by construction, and re-asking about
        # them would bury the 73 crops that came off the street.
        cand = [i for i in items()
                if i["label"] == "police" and i["vocab"] < LABEL_VOCAB
                and i.get("source") != "scraped"]
        # CAMERA-VIEW CROPS FIRST. The handheld police-station photographs sort
        # to the front by node id ("handheld"), and they are the crops whose
        # answer is already known - they were photographed AT a police station,
        # and train/fit_local excludes that distribution from both training and
        # testing anyway. Answering fifteen foregone questions before reaching
        # the street is how a queue gets abandoned half done.
        # ⚠️ KEYED ON node_id AS WELL AS sampling, AND node_id IS THE ONE THAT
        # HOLDS. Labelling a crop through :8160/label overwrites `sampling` with
        # whatever mode the page was in, so it cannot be trusted to say where a
        # crop came from. `node_id` is written once by the ingest ("handheld")
        # and never touched again - the same lesson `source` exists for.
        cand.sort(key=lambda i: ((i.get("node_id") or "") == "handheld"
                                 or (i.get("sampling") or "") == "handheld",
                                 i.get("node_id") or "", i.get("ts") or 0))
        return cand[0] if cand else None

    if mode == "recheck":
        cand = [i for i in items()
                if i["label"] == "civilian"
                and (i["_clip"].get("vclass") in
                     ("fleet", "gov_dot", "police", "emergency"))]
        cand.sort(key=lambda i: -(i["_clip"].get("conf") or 0))
        return cand[0] if cand else None

    todo = [i for i in items() if not i["label"]]
    if not todo:
        return None
    if mode == "remote":
        # Crops from OTHER PEOPLE'S nodes - a phone in someone's window rather
        # than the one camera this project has always been measured on.
        #
        # A separate mode rather than a filter on the default queue, for two
        # reasons. They arrive in bursts, so mixed into a random draw they
        # would swamp it the day a node joins and vanish the day it leaves.
        # And they are the only crops here whose conditions nobody chose - a
        # different window, angle, lens and street - which is exactly what
        # makes them worth labelling deliberately rather than incidentally.
        #
        # ⚠️ They carry NO CLIP block: nothing has guessed about them, so the
        # ordering below cannot be by model confidence. Oldest first, which is
        # simply fair to whoever contributed them.
        cand = [i for i in todo if i.get("source") == "remote_node"]
        cand.sort(key=lambda i: i.get("ts") or 0)
        return cand[0] if cand else None
    if mode == "hunt":
        # Closest to CLIP's decision boundary first.
        todo.sort(key=lambda i: -i["_uncertainty"])
        return todo[0]
    if mode == "likely":
        # HIGHEST GOVERNMENT CONFIDENCE FIRST.
        #
        # Aimed squarely at the one thing capping the classifier: TEN
        # camera-view positives. Recall moves in ten-point steps and no amount
        # of labelling ordinary cars changes that - only more confirmed
        # government vehicles do, and they sit at the top of the model's own
        # ranking. Random order spends most of its clicks on Corollas.
        #
        # WARNING: these labels are biased TWICE, so they are marked
        # sampling='likely' and drop out of `measurable` automatically.
        #   * selection - a deliberately non-random slice of the traffic;
        #   * anchoring - knowing you are in the "likely government" queue
        #     primes you to see government vehicles, and the hidden-guess rule
        #     cannot undo that because the ORDER itself carries the hint.
        # They can train a model. They must never measure one.
        todo.sort(key=lambda i: -_gov_score(i))
        return todo[0]
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(todo)


def same_pass(day: str, stem: str, gap: float = 2.0,
              mode: str = "review") -> list[dict]:
    """The other unlabelled crops that are probably the SAME vehicle.

    the operator asked whether one car driving past means five separate labelling
    decisions. It does: the tracker loses and re-acquires a vehicle, each track
    banks its own crop, and every one of them arrives in the queue on its own.
    Measured across his bank, roughly one click in three is a car he labelled
    seconds earlier.

    🚨 THIS DOES NOT AUTO-APPLY A LABEL, AND THE MEASUREMENTS ARE WHY.
    Grouping by time alone at the 10s window the evaluator uses merges 22
    groups that a human had already given CONFLICTING labels - provably
    different cars. Tightening until the merges are all correct (2s plus 0.95
    embedding similarity) leaves almost nothing grouped, so it saves no work.
    There is no threshold that is both safe and useful.

    So the group is returned to be SHOWN, not to be trusted. Two different cars
    side by side is obvious to a person in a way it is not to a cosine
    distance, and the human stays the one deciding what is one vehicle.

    Deliberately generous: same node, within `gap` seconds, unlabelled. Being
    wrong here costs a glance, not a bad label.
    """
    me = _sidecar(day, stem)
    if not me.exists():
        return []
    d = json.loads(me.read_text(encoding="utf-8"))
    ts, node = float(d.get("ts") or 0), d.get("node_id") or ""
    if not ts:
        return []
    # In `split` the siblings worth showing are the ones that are ALREADY
    # labelled - the rest of the same government call, waiting to be split the
    # same way. Everywhere else an already-answered crop must never reappear.
    def eligible(i: dict) -> bool:
        if mode == "split":
            return (i["label"] == "police" and i["vocab"] < LABEL_VOCAB
                    and i.get("source") != "scraped")
        return not i["label"]

    out = []
    for i in items():
        if (i["day"] == day and i["stem"] == stem) or not eligible(i):
            continue
        if (i.get("node_id") or "") != node:
            continue
        if abs(float(i.get("ts") or 0) - ts) <= gap:
            out.append({"day": i["day"], "stem": i["stem"],
                        "ts": i.get("ts"), "cls_name": i.get("cls_name")})
    out.sort(key=lambda r: r.get("ts") or 0)
    return out[:7]           # a filmstrip, not a contact sheet


def _gov_score(item: dict) -> float:
    """How strongly the model called this a government vehicle, 0 if it did not."""
    c = item.get("_clip") or {}
    if c.get("vclass") not in ("police", "gov_dot", "emergency"):
        return 0.0
    return float(c.get("conf") or 0.0)


def set_label(day: str, stem: str, label: str, sampling: str = "review") -> dict:
    """Write a human's call into the sidecar. Returns what CLIP had thought.

    The model's guess comes back in the RESPONSE rather than being available
    before the call, so the page can reveal it afterwards without ever having
    been able to show it first. The ordering is enforced here rather than
    trusted to the front end.
    """
    if label not in VALID:
        raise ValueError(f"label must be one of {sorted(VALID)}")
    p = _sidecar(day, stem)
    if not p.exists():
        raise FileNotFoundError(f"no such crop: {day}/{stem}")
    import time
    d = json.loads(p.read_text(encoding="utf-8"))
    d["label"] = label
    d["labelled_at"] = time.time()
    d["label_vocab"] = LABEL_VOCAB
    # ⚠️ SPLIT MODE MUST NOT REWRITE `sampling`, and that is not a detail.
    # `measurable` counts only labels gathered in review mode, so re-asking a
    # review-sampled crop "police or gov?" and stamping it `split` would quietly
    # delete it from the set the project measures precision and recall on. The
    # crop entered the queue exactly once; how it entered is what sampling
    # records. Splitting refines the answer, it does not resample.
    if not (sampling == "split" and d.get("sampling")):
        d["sampling"] = sampling      # which mode produced this label
    p.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")

    # 🚨 A HUMAN VERDICT HAS TO REACH THE MAP, NOT JUST THE TRAINING SET.
    #
    # Labelling wrote a training label and stopped there, so marking a crop
    # "Government" left its sighting sitting in the private tier for ever. Four
    # were labelled that way and two never appeared on the map - the operator
    # had told the system exactly what the vehicle was and the system carried on
    # disagreeing with him in public.
    #
    # This is the strongest signal the project has. classify.py already ranks
    # `human_confirmed` at weight 4.0, above every visual cue, and the review
    # page already promotes and retracts on exactly this judgement. The two
    # pages are two views of ONE decision, so they now agree.
    #
    # Best effort on purpose: the label is the thing that must not be lost, so a
    # database problem may never fail the labelling call.
    # 🚨 A CONFIRMATION WITH NOTHING TO CONFIRM IS THE COMMON CASE, NOT THE EDGE.
    # 22 of 63 live-popup answers had no sighting_id, because the posting gate
    # had already discarded the pass. Promoting a row that does not exist is a
    # no-op, so every one of those was lost in silence. Report it instead - see
    # _report_new_sighting for why only the live popup is allowed to.
    if not d.get("sighting_id") and label in ("police", "gov"):
        new_id = _report_new_sighting(day, stem, d, label)
        if new_id:
            d["sighting_id"] = new_id
            d["reported_by_operator"] = True
            p.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")

    synced = _sync_sighting(d.get("sighting_id"), label)
    if synced:
        # Recorded so `clear_label` can reverse EXACTLY what this label did,
        # rather than guessing. See the note there.
        #
        # ⚠️ A RECLASSIFICATION MUST NOT OVERWRITE A PROMOTION. If this label is
        # what put the row on the map, splitting it police->gov later changes
        # the class but not the fact; the undo is still "take it off the map".
        # Overwriting would leave the row public with no route back - the exact
        # bug that cost sighting #27421 fourteen hours.
        if not (synced.startswith("reclassified") and d.get("synced") == "promoted"):
            d["synced"] = synced
        p.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")

    clip = d.get("clip") or {}
    return {"clip": {"vclass": clip.get("vclass"), "conf": clip.get("conf"),
                     "margin": clip.get("margin")},
            "agreed": _same_category(clip.get("vclass"), label),
            "synced": synced}


# Where this camera's sightings actually live, and who this camera is. Read
# from camctl's placement file rather than passed in, because labelbank is
# imported by camctl, by the labelling page and by tools, and threading node
# credentials through all three call sites is how one of them ends up without.
_PLACEMENT = Path(__file__).resolve().parent / "camctl" / "placement.json"


def _node_creds() -> Optional[tuple]:
    """(hub, node_id, token) for this camera, or None if it is not enrolled."""
    import os
    try:
        p = json.loads(_PLACEMENT.read_text(encoding="utf-8"))
    except Exception:
        return None
    nid, tok = p.get("node_id"), p.get("token")
    if not nid or not tok:
        return None
    hub = os.environ.get("SPARROW_HUB", "https://map.sparrowmap.com").rstrip("/")
    return hub, nid, tok


def _call_box(payload: dict, path: str = "/api/node/label") -> Optional[dict]:
    creds = _node_creds()
    if not creds:
        print("[labelbank] not enrolled: no node_id/token in placement.json, "
              "so this label cannot reach the map")
        return None
    hub, nid, tok = creds
    import urllib.request
    req = urllib.request.Request(
        f"{hub}{path}", method="POST",
        data=json.dumps({**payload, "node_id": nid}).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": NODE_UA,
                 "Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _report_new_sighting(day: str, stem: str, d: dict, label: str) -> Optional[int]:
    """The operator confirmed a pass the POSTING GATE THREW AWAY. Report it now.

    🚨 35% OF EVERY LIVE CONFIRMATION HE EVER MADE WAS SILENTLY LOST TO THIS.
    Measured over the whole bank: 63 crops answered through the live popup, 41
    had a sighting to move and **22 did not**. The map never heard about those
    22 patrol cars, and nothing anywhere said so.

    The cause is ordering, not policy. The posting gate runs the instant a
    vehicle leaves frame and drops any pass clearing fewer than two markers - so
    a marked patrol car with an unreadable plate is discarded (head 0.987, plate
    agreement 0.34 < 0.55) before a human ever sees it. The popup then asks the
    operator, they say yes, and there is no row to promote. classify.py has
    always weighed `human_confirmed` at 4.0 and lets it WAIVE the two-marker
    rule; it simply never got told, because the row was already gone.

    ⚠️ ONLY THE LIVE POPUP MAY DO THIS - `sampling == "likely"`.
    A label from the review or hunt queue is a TRAINING judgement about an old
    photograph, not a decision to report a vehicle. 201 crops carry a `hunt`
    label of police, and publishing those would be inventing sightings from a
    labelling session nobody meant as a report. The popup is different in kind:
    it fires while the vehicle is still in sight and asks a person to make a
    call about THIS pass.

    The box still decides. This sends the same claim /api/sightings takes, and
    `human_confirmed` is set server-side from the node token - never asserted
    here - so classify() and `public_tiers` still gate what becomes public.
    """
    if d.get("sampling") != "likely":
        return None
    img = _sidecar(day, stem).with_suffix(".jpg")
    if not img.exists():
        print(f"[labelbank] cannot report {stem}: the crop image is gone")
        return None
    import base64
    clip = d.get("clip") or {}
    body = {
        "ts": d.get("ts") or time.time(),
        "vclass_hint": label,
        "body": d.get("cls_name"),
        "evidence": {k: v for k, v in (d.get("evidence") or {}).items()
                     if not k.startswith("_")},
        "bank_ref": stem,
        # No plate. This path exists precisely BECAUSE the plate was unreadable,
        # and a public sighting is public because it carries no identifier.
        "plate_text": "", "plate_conf": 0.0,
        "snap_b64": "data:image/jpeg;base64,"
                    + base64.b64encode(img.read_bytes()).decode(),
    }
    if clip.get("head_conf") is not None:
        body["evidence"].setdefault("visual_police_conf", clip["head_conf"])
    try:
        out = _call_box(body, path="/api/node/confirm")
    except Exception as exc:
        print(f"[labelbank] REPORT FAILED - {stem} confirmed but not on the map: {exc}")
        return None
    sid = (out or {}).get("id")
    if sid:
        print(f"[labelbank] reported {stem} as sighting {sid} "
              f"(tier={out.get('tier')}, {out.get('vclass')})")
    return sid


def _sync_sighting(sighting_id, label: str) -> Optional[str]:
    """Make the map agree with the human. Returns what changed, or None.

    🚨 THIS ASKS THE BOX. IT USED TO ASK A LOCAL DATABASE, AND THAT IS THE BUG.

    It called `db.sighting(id)` against `data/sparrow.db` on this machine. That
    stopped being the map when the box became the single source of truth: the
    sighting ids these crops carry are issued by the BOX (45512-45543 in the
    last sample) and the local file tops out at 30,437, so the lookup returned
    None for every single one and the function returned None having done
    nothing. It did not raise. It did not log. The popup went on telling him
    "your answer labels the crop AND moves the sighting on the map", the
    training label really was written, and the map never changed - for every
    government vehicle he confirmed at the camera between the cutover and now.

    🍀 It could have been much worse: the two id ranges do not overlap. If they
    had, this would have promoted a completely unrelated vehicle to the public
    tier every time he pressed Y.

    ⚠️ Only ever moves a sighting the labeller is looking at. A crop with no
    `sighting_id` never became one - the pass did not clear the posting gate -
    and inventing a sighting here would publish a vehicle from a photograph
    nobody chose to report.

    The POLICY now lives on the box in node_label.py, next to the database it
    acts on, so this side does not get to hold an opinion that has drifted out
    of date - which is the second half of what went wrong here.
    """
    if not sighting_id or label == "unsure":
        return None
    try:
        out = _call_box({"sighting_id": int(sighting_id), "label": label})
    except Exception as exc:
        # 🚨 LOUD. The whole cost of this bug was that the failure was silent,
        # so a network error here says so plainly rather than returning None
        # like the success-with-nothing-to-do case does.
        print(f"[labelbank] SIGHTING NOT SYNCED - the map was not updated for "
              f"sighting {sighting_id}: {exc}")
        return None
    return (out or {}).get("did")


# CLIP's classes are finer than the label set, and comparing them directly
# would report a disagreement every time it said 'gov_dot' and the human said
# 'Government' - which is the same answer in different words. The reveal is
# there to surface REAL disagreements; a stream of false ones trains the
# labeller to ignore it.
_CATEGORY = {
    "police": "gov", "gov_dot": "gov", "emergency": "gov", "gov": "gov",
    "fleet": "fleet",
    "civilian": "civilian",
}


def _same_category(clip_class: Optional[str], label: str) -> bool:
    if not clip_class or label == "unsure":
        return False
    return _CATEGORY.get(clip_class) == _CATEGORY.get(label)


def clear_label(day: str, stem: str) -> Optional[str]:
    """Undo. A mislabelled crop is worse than an unlabelled one.

    🚨 AND IT HAS TO UNDO THE MAP TOO.
    Labelling a crop "Government" PROMOTES its sighting to the public tier -
    that is deliberate, it is how the operator's judgement reaches the map. But
    Undo only ever cleared the sidecar, so one accidental keypress published a
    vehicle and the undo button could not take it back. It happened: #27421, a
    night frame nobody can identify, sat on the public map for fourteen hours
    because a key was pressed by mistake and the obvious remedy did nothing.

    An undo that silently undoes half of what was done is worse than no undo,
    because it tells you the mistake is fixed.

    Reverses exactly what `set_label` recorded doing, rather than inferring it -
    a sighting that was ALREADY public before anyone labelled it must not be
    retracted just because a label is being cleared.
    """
    p = _sidecar(day, stem)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    undone = None
    sid, synced = d.get("sighting_id"), d.get("synced")
    if sid and synced:
        # 🚨 THE UNDO GOES TO THE BOX TOO, AND FOR A SHARPER REASON THAN THE
        # LABEL DID. While the forward path was dead the undo path was harmless
        # - it could not take anything off a map it had never put anything on.
        # The moment the forward path works, an undo that still writes to the
        # stale local file becomes an undo button that does nothing to a
        # sighting this camera just published. Both halves move together or
        # neither does.
        #
        # `was` is what the box itself reported doing, replayed back to it, so
        # the reversal is exact rather than inferred - see node_label._undo.
        try:
            out = _call_box({"sighting_id": int(sid), "undo": synced})
            undone = (out or {}).get("undone")
        except Exception as exc:
            print(f"[labelbank] UNDO NOT SYNCED - sighting {sid} is still "
                  f"'{synced}' on the map: {exc}")
            undone = None
    d["label"] = None
    d.pop("labelled_at", None)
    d.pop("sampling", None)
    d.pop("synced", None)
    p.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
    return undone


def image_path(day: str, stem: str) -> Optional[Path]:
    """Resolve a crop path, refusing anything that escapes the bank."""
    # Traversal guard: these components arrive from a URL. The bank sits beside
    # the sightings database and the pepper.
    #
    # 🚨 STRIP BOTH SEPARATORS FROM BOTH PARTS.
    # This stripped "-" from the day and "_" from the stem, which was right
    # when every day folder was a bare date. It silently stopped being right
    # the moment folders like `remote_2026-08-09` and
    # `2026-08-08_police_station` existed: they contain BOTH characters, so
    # isalnum() failed and every image in them 404'd. The labelling queue
    # happily served those crops and the page showed a blank frame - a
    # whitelist that quietly narrowed as the data grew.
    #
    # The real protection is the resolve()/startswith check below, which
    # compares the RESOLVED path against the bank root and cannot be talked
    # out of it by any spelling. This filter is the cheap first pass, so it
    # should reject traversal characters rather than ordinary names.
    clean = lambda s: s.replace("-", "").replace("_", "")
    if not clean(day).isalnum() or not clean(stem).isalnum():
        return None
    p = (BANK / day / f"{stem}.jpg").resolve()
    if not str(p).startswith(str(BANK.resolve())):
        return None
    return p if p.exists() else None
