"""SparrowMap - is this a police/government vehicle, or somebody's Corolla?

This is the highest-stakes function in the project and it is worth being blunt
about why. Getting it wrong in one direction misses a patrol car. Getting it
wrong in the OTHER direction publishes a private citizen's plate and movements
on a public map. Those errors are not equally bad, so the whole module is built
to fail toward "civilian".

Three hard rules follow from that, enforced in code below:

  1. A vehicle enters the public tier only at high confidence.
  2. Plate text alone is NEVER sufficient. Text is easy to misread and easy to
     spoof, and a single OCR slip should not be able to make a stranger public.
     At least one independent signal must agree.
  3. Every classification carries its evidence. The map shows the reasons, not
     just the verdict, so a wrong call is visible and arguable instead of
     authoritative.

Signals come from three families:

  PLATE      what is written on the plate. Weak on its own, and deliberately
             so. Many agencies run ordinary-looking plates precisely to avoid
             being identified this way.

  VISUAL     what the vehicle looks like. The strong family. A roof light bar
             is close to decisive; an A-pillar spotlight is the classic tell
             for an unmarked unit, because civilians essentially never fit one.

  BEHAVIOUR  what the vehicle DOES across the whole network. This family only
             exists because SparrowMap is distributed, and it is the one that can
             surface unmarked cars no image classifier would catch: a vehicle
             that repeatedly traverses the same segment, at all hours, across
             many cameras, is patrolling. Nobody's commute looks like that.
"""

from __future__ import annotations

import re
from typing import Optional

from core import CONFIG

# Confidence a vehicle must reach before its PLATE is published.
PUBLIC_THRESHOLD = 0.85

# Confidence needed to record a public SIGHTING with no plate attached.
# Lower than PUBLIC_THRESHOLD on purpose: the two decisions carry different
# risk. Getting a sighting wrong puts a spurious dot on a map. Getting a plate
# publication wrong exposes a private person. Only the second deserves the
# stricter bar, and holding both to it silences every camera that cannot read
# a plate - which is most of them.
SIGHTING_THRESHOLD = 0.60

# Signals that are not "text written on a plate". At least one must fire.
_CORROBORATING = {"light_bar", "push_bumper", "pillar_spotlight", "livery",
                  "agency_decal", "antenna_array", "police_body", "steelies",
                  "patrol_pattern", "human_confirmed", "visual_police"}

# Hand-written pixel heuristics: no training, no measured error rate, and a
# documented history of firing on ordinary traffic. They are real evidence, but
# they are not evidence enough on their own - see the `sightable` rule below.
# `visual_police` (a trained classifier), `human_confirmed` (a person) and the
# plate-legend signals are deliberately NOT in this set.
_CRUDE_SIGNALS = {"light_bar", "police_body", "steelies", "antenna_array"}


# --------------------------------------------------------------------------
# Signal weights.
#
# These are LOG-ODDS style weights, summed then squashed. They are starting
# values, not measurements. Calibrate them against locally labelled footage
# before trusting the numbers; see ROADMAP "calibration".
# --------------------------------------------------------------------------

SIGNALS: dict[str, tuple[float, str, str]] = {
    # key                weight  family      human-readable reason
    "human_confirmed":   (4.0, "confirm",  "confirmed by a human reviewer"),
    "light_bar":         (3.2, "visual",   "roof light bar"),
    "agency_decal":      (3.0, "visual",   "agency markings on the door"),
    "livery":            (2.2, "visual",   "police-style two-tone livery"),
    "pillar_spotlight":  (2.4, "visual",   "A-pillar spotlight (unmarked tell)"),
    "push_bumper":       (2.0, "visual",   "push bumper / ram bar"),
    "antenna_array":     (1.2, "visual",   "multiple whip antennas"),
    "police_body":       (0.7, "visual",   "common patrol body style"),
    "steelies":          (0.8, "visual",   "steel wheels with small caps"),
    "patrol_pattern":    (1.6, "behaviour", "repeated patrol-shaped passes"),
    "gov_plate_word":    (2.6, "plate",    "plate carries a government legend"),
    "gov_plate_series":  (1.8, "plate",    "plate matches a known agency series"),
    "fleet_decal":       (2.0, "visual",   "commercial fleet livery"),
    # Weight 0.0: this does not lower a score, it OVERRIDES the class outright
    # (see the security block in classify()). It is listed here so the reason
    # string tells a reader why a marked vehicle was not published.
    "security_livery":   (0.0, "visual",   "marked PRIVATE SECURITY, not "
                                           "government - held private"),
    # Graded, not boolean - see the handling in classify(). Listed here only
    # so the reason string renders.
    "visual_police":     (0.0, "visual",   "identified as a police vehicle by sight"),
}

# Legends printed on the plate itself. Strong text evidence, but still text.
_PLATE_WORDS = re.compile(
    r"\b(MUNICIPAL|OFFICIAL|EXEMPT|GOVERNMENT|GOVT|STATE OWNED|CITY OF|COUNTY|"
    r"SHERIFF|POLICE|US GOVERNMENT|FEDERAL)\b", re.I)

# 🚨 WORDS THAT MEAN "PRIVATE COMPANY", AND THEREFORE "NOT GOVERNMENT".
#
# HIS CATCH 2026-08-16, about a vehicle in his review queue: POLICE on the side
# with the word cropped out, SECURITY in the back window. His call on it was
# "too risky and could be a mall cop", and he is right on both counts.
#
# Private security is the one category built to LOOK like police, and the class
# decision below could never reach it: the `fleet` branch is skipped whenever
# any police signal fired, and `livery` and `agency_decal` are police signals.
# So a marked security vehicle lands in `police`, which is publishable with a
# readable plate. Publishing it attaches a legible plate to a private company -
# exactly the harm the two-tier split exists to prevent.
#
# ⚠️ NARROW ON PURPOSE. "PATROL" is absent because real forces use it (State
# Patrol, Highway Patrol) and it would retract real government vehicles. These
# are words that no police force paints on a car.
_SECURITY_WORDS = re.compile(
    r"\b(SECURITY|SECURITIES|PRIVATE SECURITY|LOSS PREVENTION|"
    r"COURTESY PATROL|SECURITAS|ALLIED UNIVERSAL|BRINKS|BRINK'?S)\b", re.I)


def _looks_private_security(evidence: dict) -> bool:
    """Did anything about this vehicle say 'private security company'?

    Reads the explicit signal a node may send, and also any TEXT we were given:
    detect/pipeline.py recognises SECURITY as bodywork rather than a plate, and
    a caller that passes the read through gets the benefit here.
    """
    if evidence.get("security_livery"):
        return True
    for key in ("livery_text", "plate_text", "plate_legend"):
        v = evidence.get(key)
        if v and _SECURITY_WORDS.search(str(v)):
            return True
    for w in (evidence.get("livery_words") or ()):
        if _SECURITY_WORDS.search(str(w)):
            return True
    return False


def _squash(score: float) -> float:
    """Logistic squash of summed evidence into 0..1."""
    import math
    return 1.0 / (1.0 + math.exp(-(score - 2.6)))


def classify(evidence: dict) -> dict:
    """Turn a bag of observed signals into a class, a confidence and a reason.

    ``evidence`` keys are signal names from SIGNALS mapped to True, plus the
    optional extras ``plate_text``, ``plate_legend`` and ``agency_series``
    (a list of plate prefixes this deployment has learned locally).

    Returns ``{vclass, conf, why, tierable}``. ``tierable`` is the gate: it is
    True only when this vehicle is allowed to have its plate published.
    """
    fired: list[str] = []
    score = 0.0

    # 🚨 `visual_police` NEVER ENTERS HERE AS A BOOLEAN. It is the TRAINED HEAD's
    # verdict and it carries weight 0.0 in SIGNALS, so it contributes nothing to
    # the confidence score - its ONLY effect is to add a "visual" marker to the
    # count that the two-marker police rule keys on. Admitting it from raw
    # evidence let a submitting node fabricate the second visual marker for free
    # (evidence:{agency_decal:true, visual_police:true}) and store a stranger's
    # plate, bypassing the whole threshold apparatus below. It may ONLY be
    # appended by the gated head/zero-shot block, which enforces the measured
    # confidence and margin. hub._ingest also strips it from submitter evidence;
    # this is the belt that holds even for a direct classify() call.
    for key in evidence:
        if key == "visual_police":
            continue
        if evidence.get(key) and key in SIGNALS:
            w, _fam, _why = SIGNALS[key]
            score += w
            fired.append(key)

    # ---- graded visual signal --------------------------------------------
    # A visual classifier reports a CONFIDENCE, and collapsing that to a
    # boolean throws the useful part away. An earlier version mapped a
    # 0.99-certain police identification onto `police_body` - weight 0.7, the
    # weakest signal in the table - which squashed to 0.13 and published
    # nothing. The classifier was right and the plumbing discarded it.
    #
    # So this one is graded: weight scales with the reported confidence. At
    # 0.99 it contributes 3.96, at 0.85 it contributes 3.40, and at 0.65 -
    # the score of the one false positive in the calibration set - it
    # contributes 2.60, which lands exactly on the squash midpoint and does
    # not clear the sighting bar.
    # ⚠️ ...AND IT NEEDS A FLOOR, WHICH IT DID NOT HAVE.
    #
    # Any police call at all contributed, so CLIP at 0.67 scored 2.68, squashed
    # to 0.688, cleared the 0.60 bar and published. Measured on the operator's own
    # banked traffic: CLIP calls POLICE on 40% of ordinary vehicles, at
    # confidences from 0.557 to 0.712. On a residential street the true rate is
    # a fraction of a percent, so that number is the model's prior talking -
    # it is used ZERO-SHOT on a task it was never trained for, and its prior
    # appears to be close to "dark sedan or SUV = police", which describes most
    # of the street.
    #
    # ✅ Measured, and re-measured. See detect/vehicle_id.py: 0.90 came from
    # handheld photos and was exceeded within hours by a real false positive at
    # 0.938. 0.96 comes from the operator's own verdicts on published claims, which
    # separate 0.975+ (real) from 0.938 (not) - camera-view data, five points,
    # a narrow gap. The trained head is what replaces this, not another number.
    VISUAL_MIN_CONF, VISUAL_MIN_MARGIN = 0.96, 0.90
    vp = float(evidence.get("visual_police_conf") or 0.0)
    vm = float(evidence.get("visual_police_margin") or 0.0)

    if evidence.get("visual_source") == "head":
        # 🚨 THE TRAINED HEAD IS ON A DIFFERENT SCALE AND MUST NOT BE COMPARED
        # TO 0.96. Its operating point is a MEASURED threshold saved with the
        # weights (around 0.23, because the head is fitted with balanced class
        # weights against a 1-in-40 positive rate). Applying the zero-shot gate
        # to it would reject every prediction it ever makes, silently, and the
        # public tier would simply go quiet with nothing in the logs.
        #
        # vehicle_id.evidence() has already applied that measured threshold, so
        # arriving here means the head fired. What is left is how STRONGLY.
        #
        # Both gates are calibrated to the same place - 95% precision on
        # held-out camera-view crops - so a head firing at its threshold is
        # worth exactly what zero-shot firing at 0.96 is worth. That is the
        # anchor, not a number picked to feel right; above threshold it scales
        # the rest of the way to 1.0.
        thr = float(evidence.get("visual_head_threshold") or 0.0)
        span = max(1e-6, 1.0 - thr)
        strength = min(1.0, VISUAL_MIN_CONF
                       + (1.0 - VISUAL_MIN_CONF) * max(0.0, (vp - thr) / span))
        score += 4.0 * strength
        fired.append("visual_police")
    elif vp >= VISUAL_MIN_CONF and vm >= VISUAL_MIN_MARGIN:
        score += 4.0 * vp
        fired.append("visual_police")

    # Plate legend text, evaluated here so callers just pass the raw read.
    legend = (evidence.get("plate_legend") or "") + " " + (evidence.get("plate_text") or "")
    if _PLATE_WORDS.search(legend) and "gov_plate_word" not in fired:
        score += SIGNALS["gov_plate_word"][0]
        fired.append("gov_plate_word")

    series = evidence.get("agency_series") or []
    plate = (evidence.get("plate_text") or "").upper().replace(" ", "")
    if plate and any(plate.startswith(p.upper()) for p in series) \
            and "gov_plate_series" not in fired:
        score += SIGNALS["gov_plate_series"][0]
        fired.append("gov_plate_series")

    conf = _squash(score)

    # ---- decide the class -------------------------------------------------
    police_signals = {"light_bar", "push_bumper", "pillar_spotlight", "livery",
                      "agency_decal", "antenna_array", "police_body",
                      "steelies", "patrol_pattern", "visual_police"}
    if "fleet_decal" in fired and not (police_signals & set(fired)):
        vclass = "fleet"
    elif police_signals & set(fired):
        vclass = "police"
    elif {"gov_plate_word", "gov_plate_series"} & set(fired):
        vclass = "gov"
    else:
        vclass = "civilian"

    # 🚨 PRIVATE SECURITY OVERRIDES EVERY POLICE SIGNAL. See _SECURITY_WORDS.
    #
    # This sits AFTER the decision rather than inside it, and that is the whole
    # point: a security vehicle fires real police signals - it has the livery,
    # sometimes the light bar - so no amount of reordering the branches reaches
    # it. The signals are not wrong about what they saw; they are wrong about
    # what it means, and only the word on the glass settles that.
    #
    # It becomes `fleet`, which is honest (a commercial vehicle with company
    # livery) and, more importantly, publishes NOTHING: `sightable` allows only
    # police/emergency/gov and `tierable` only police/gov.
    #
    # ⚠️ The cost of a false positive here is one unpublished government
    # vehicle. The cost of a false negative is a private company's plate on a
    # public map under the word POLICE. Those are not the same price, which is
    # why this refuses rather than merely lowering the score.
    security = _looks_private_security(evidence)
    if security and vclass in ("police", "gov"):
        fired.append("security_livery")
        vclass = "fleet"

    # ---- rule 2: text alone can never publish somebody -------------------
    # `corroborated` feeds the reason string below; `tierable` (which authorises
    # publishing the PLATE TEXT) is computed further down, AFTER `enough`, so it
    # is held to the same on-vehicle corroboration as the identifier-free dot.
    # It used to sit here on a weaker one-corroborator rule, which let a plate
    # publish on a lower bar than a dot - backwards for the higher-harm path.
    corroborated = bool(_CORROBORATING & set(fired))

    if not fired:
        why = "no distinguishing signals; treated as private"
    else:
        why = "; ".join(SIGNALS[k][2] for k in fired if k in SIGNALS)
        if conf >= PUBLIC_THRESHOLD and not corroborated:
            why += " - held private: plate text alone is not enough"

    # ---- rule 4: a SIGHTING is not a PUBLICATION -------------------------
    #
    # the operator's insight, and it corrects a conflation in the original design.
    # "A marked patrol unit passed this corner at 14:32" contains NO
    # IDENTIFIER. Nobody's plate, nobody's movements, nothing that can be
    # traced to a person. It is exactly the accountability data the project
    # exists to produce, and publishing it carries none of the risk that
    # publishing a plate carries.
    #
    # So the two decisions are separated:
    #
    #   sightable  may this be recorded as a PUBLIC sighting with no plate?
    #             Needs a confident vehicle-class call and nothing else,
    #             because there is no identifier to protect.
    #
    #   tierable  may the PLATE TEXT be published? Unchanged: high confidence
    #             AND corroboration, because this one can expose a person.
    #
    # This matters enormously for the network. A volunteer's porch camera can
    # almost never read a plate (22 px at the reference camera against the 60
    # needed) but can identify a light bar with 9x margin. Under the old
    # single gate, every such camera contributed nothing. Under this one, it
    # contributes the public-accountability layer on day one.
    # 🚨 ...BUT NOT ON ONE CRUDE SIGNAL ALONE.
    #
    # `light_bar` carries weight 3.2, which squashes to 0.646 and clears the
    # 0.60 sighting bar by itself. So a single hand-written colour heuristic
    # was enough to put a police dot on the public map - and on 2026-08-08 it
    # put five there off the reference camera's street, none of which he confirmed as
    # police vehicles.
    #
    # Rule 3 of this file already says the same thing about plate text:
    # nothing publishes on text alone, because one OCR slip must never make a
    # stranger public. The identical argument applies to an unvalidated colour
    # rule, and it was missing only because `light_bar` happened to be the one
    # visual signal that was implemented. A wrong sighting exposes nobody -
    # that is why the bar is lower than for a plate - but this project's only
    # asset is that what it shows is true, and a map crying wolf about police
    # is worth less than an empty one.
    #
    # So a public sighting needs EITHER a signal that is not merely crude
    # (a trained classifier's call, a human's confirmation, a plate legend),
    # OR two independent crude signals agreeing.
    crude_fired = _CRUDE_SIGNALS & set(fired)
    solid_fired = (_CORROBORATING & set(fired)) - _CRUDE_SIGNALS

    # 🚨 2026-08-10: ONE SIGNAL WAS STILL ENOUGH TO CALL SOMETHING POLICE, AND
    # A PARODY BADGE PROVED IT.
    #
    # A contractor's truck carrying a joke "ROOFING PD" police-badge decal was
    # published as a police vehicle. `visual_police` - the trained head - sits
    # in the SOLID set, so it satisfied `solid_fired` on its own. The rule above
    # was written to stop ONE CRUDE signal publishing and it did exactly that,
    # while leaving one *confident* signal free to publish alone. A badge shape
    # is precisely what a head trained on door markings learns.
    #
    # 🚨 AND THE SECOND SIGNAL WAS NOT INDEPENDENT. The plate detector reads the
    # word POLICE off the door livery and returns it as plate text (see
    # detect/pipeline.py - the livery is a bigger patch of white-on-dark than
    # the plate is, and wins on area). `POLICE` is in `_PLATE_WORDS`, so
    # `gov_plate_word` fires. The livery therefore votes TWICE: once as paint,
    # once as text that is the same paint. Corroboration that comes from the
    # same pixels is not corroboration - it is one observation counted twice,
    # and it is exactly what defeats a "needs two markers" rule.
    #
    # So for POLICE the requirement is now TWO DISTINCT VISUAL MARKERS on the
    # vehicle - the operator's bar: "light bar + livery at least should be
    # visible". Plate text cannot supply the second, because plate text is not
    # a thing seen on the body and may well BE the first marker misread.
    #
    # `human_confirmed` waives the two-marker requirement, but only once
    # something has already made this a POLICE call - on its own it does not
    # set the class, so a confirmation with no other signal still lands as
    # civilian. Promotion by a reviewer runs through the review path, not here.
    #
    # gov/fleet keep the previous rule. The complaint and the evidence are both
    # about police calls, and a plate legend reading STATE OWNED is text that
    # genuinely is on a plate - tightening that would silence legitimate
    # government sightings to fix a fault they do not have.
    visual_fired = {k for k in fired
                    if k in SIGNALS and SIGNALS[k][1] == "visual"}
    if vclass == "police":
        enough = ("human_confirmed" in fired) or len(visual_fired) >= 2
    else:
        enough = bool(solid_fired or len(crude_fired) >= 2)

    sightable = (vclass in ("police", "emergency", "gov")
                 and conf >= SIGHTING_THRESHOLD
                 and enough)

    # ---- rule 2 (identifier): the PLATE needs the SAME corroboration as the
    # dot, and it can never corroborate itself.
    #
    # 🚨 THIS WAS THE HOLE. `tierable` - the gate that authorises STORING and
    # publishing the plate TEXT - used to require only `conf >= PUBLIC_THRESHOLD`
    # plus ONE corroborator. So the identifier published on a WEAKER bar than the
    # identifier-free dot, which is exactly backwards: a wrong dot exposes
    # nobody, a wrong plate exposes a stranger. And two plate-derived signals
    # (gov_plate_word + gov_plate_series read off the same door livery) could
    # satisfy it, which is one observation counted twice.
    #
    # Now it requires `enough` - the same rule the dot uses. For police that is
    # two DISTINCT visual markers or a human confirmation; plate signals are not
    # in the `visual` family, so plate text can never be the second marker. For
    # gov/fleet it is a solid (non-crude) on-vehicle signal or two crude ones -
    # so a bare plate legend, even a doubled one, no longer publishes an
    # identifier on its own. Note an agency decal is itself a police-family
    # signal, so a truck showing ONE decal is classed police and now waits for a
    # second visual marker or a human - the safe direction, since a single door
    # marking is exactly the parody-badge case. An unmarked sedan read as
    # "MUNICIPAL" likewise waits for a human. The identifier is never published
    # on less evidence than the identifier-free dot.
    tierable = (conf >= PUBLIC_THRESHOLD and enough
                and vclass in ("police", "gov"))

    if not sightable and vclass == "police" and len(visual_fired) < 2:
        why += (" - held private: only one visual marker; a police call needs "
                "two visible on the vehicle")
    elif not sightable and crude_fired and not solid_fired:
        why += " - held private: a single unverified visual cue is not enough"

    # 🚨 THE PUBLIC TIER IS OFF UNTIL SOMETHING IS ACTUALLY VALIDATED.
    #
    # Every police call this deployment can currently make comes from CLIP used
    # ZERO-SHOT plus one hand-written colour rule. Measured on the operator's own
    # traffic: the colour rule was wrong five times out of five, and CLIP calls
    # POLICE on 40% of ordinary vehicles - confidently, up to 0.848 with a
    # passing margin. Its measured precision on this camera is zero. There is
    # no confirmed police vehicle anywhere in the data, so there is no
    # threshold that can be chosen honestly: any number that excludes the known
    # negatives is a guess about a positive nobody has ever seen.
    #
    # So the tier is gated by config rather than by a hopeful constant. The
    # network keeps watching, every pass is still counted as traffic, and crops
    # keep banking for training - but nothing is asserted about a vehicle in
    # public until a classifier has been measured against locally labelled data
    # and beats what it replaces. Turn it on in config.json once
    # detect/calibrate.py reports a real precision AND a real recall.
    #
    # This is the difference between a map that is empty and a map that is
    # wrong. Empty is recoverable.
    # 🚨 THE KILL-SWITCH MUST HOLD THE HIGHER-HARM PATH TOO.
    # It used to clear only `sightable`, leaving `tierable` live - so with the
    # public tier "off" a classification could still force tier=public in ingest
    # and STORE a readable plate. The switch that means "empty, not wrong" now
    # covers the identifier as well as the dot: both are cleared together.
    gated = False
    if not CONFIG.get("publish_public_tier", False):
        if sightable or tierable:
            gated = True
            why += (" - HELD: public tier is disabled until the vehicle "
                    "classifier is validated on locally labelled data")
        sightable = False
        tierable = False

    return {"vclass": vclass if tierable else ("civilian" if vclass == "civilian" else vclass),
            "conf": round(conf, 3),
            "why": why,
            "fired": fired,
            "sightable": sightable,
            "tierable": tierable}


# --------------------------------------------------------------------------
# Behavioural signal, computed across the network rather than from one frame.
# --------------------------------------------------------------------------

def patrol_score(track: list[dict], window_h: float = 24.0) -> float:
    """How patrol-shaped is this vehicle's recent trail? 0..1.

    A commuter makes a small number of passes at consistent times along one
    direction. A patrol makes many passes, spread across the clock, often
    reversing direction over the same stretch. This looks for that shape.

    Returns a score, not a verdict. It feeds `patrol_pattern` as one signal
    among several; on its own it should never publish anyone, which is exactly
    what the corroboration rule in `classify` enforces.
    """
    if len(track) < 6:
        return 0.0

    from core import now
    recent = [s for s in track if s.get("ts", 0) > now() - window_h * 3600]
    if len(recent) < 6:
        return 0.0

    # 1. Breadth: how many distinct cameras.
    cams = len({s.get("node_id") for s in recent})

    # 2. Spread across the clock: how many distinct hours are represented.
    import time as _t
    hours = len({_t.localtime(s["ts"]).tm_hour for s in recent})

    # 3. Reversal: does it traverse the same camera in both directions.
    by_node: dict[str, set] = {}
    for s in recent:
        h = s.get("heading")
        if h is None:
            continue
        by_node.setdefault(s["node_id"], set()).add(round(h / 180.0))
    reversals = sum(1 for v in by_node.values() if len(v) > 1)

    score = min(cams / 6.0, 1.0) * 0.4 \
        + min(hours / 10.0, 1.0) * 0.35 \
        + min(reversals / 3.0, 1.0) * 0.25
    return round(min(score, 1.0), 3)
