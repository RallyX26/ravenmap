"""A marked PRIVATE SECURITY vehicle must never publish as police.

🚨 HIS CATCH 2026-08-16, on a sighting in his review queue: POLICE on the side
with the word cropped out, SECURITY in the back window. His ruling: "too risky
and could be a mall cop."

He was right about the danger and about the mechanism. Private security is the
one category built to LOOK like police, and classify() could never reach the
honest bucket for it: the `fleet` branch is skipped whenever any police signal
fired, and `livery` and `agency_decal` ARE police signals. So a marked security
truck landed in `police` - publishable, with a readable plate, attached to a
private company.

⚠️ TESTS THE DECISION, NOT THE REGEX. It is not enough that _SECURITY_WORDS
matches; what matters is what classify() returns for a vehicle that fires real
police signals at high confidence, because that is the case that publishes.

    python tools/test_security_not_police.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import classify  # noqa: E402

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# The shape that publishes: two distinct visual markers, which is exactly the
# bar `enough` sets for police. Anything less would be held for a human anyway,
# so testing that would prove nothing.
MARKED = {"light_bar": True, "livery": True, "agency_decal": True}


def main() -> int:
    # --- the baseline: without the word, this really does publish ------------
    base = classify.classify(dict(MARKED))
    print(f"  marked vehicle, no security text -> {base['vclass']} "
          f"(tierable={base['tierable']})")
    check("a genuinely marked vehicle still publishes",
          base["vclass"] == "police" and base["tierable"],
          "the guard must not have broken the normal case")

    # --- the fix -------------------------------------------------------------
    for label, ev in (
        ("explicit signal",      {**MARKED, "security_livery": True}),
        ("word off the bodywork", {**MARKED, "livery_words": ["SECURITY"]}),
        ("word in a plate read",  {**MARKED, "plate_text": "SECURITY"}),
        ("a named firm",          {**MARKED, "livery_words": ["SECURITAS"]}),
    ):
        r = classify.classify(ev)
        check(f"🚨 {label}: NOT police", r["vclass"] != "police",
              f"got {r['vclass']}")
        check(f"   {label}: publishes nothing", not r["tierable"],
              f"tierable={r['tierable']} - this would publish a plate")
        check(f"   {label}: not a public dot either", not r["sightable"],
              f"sightable={r['sightable']}")

    # --- and it must not eat real government vehicles ------------------------
    # 🚨 THE FALSE-POSITIVE SIDE. "PATROL" is deliberately NOT a security word:
    # State Patrol and Highway Patrol are real forces, and matching them would
    # silently retract exactly the vehicles this project exists to publish.
    for word in ("STATE PATROL", "HIGHWAY PATROL", "SHERIFF", "POLICE",
                 "STATE TROOPER"):
        r = classify.classify({**MARKED, "livery_words": [word]})
        check(f"'{word}' is still police", r["vclass"] == "police",
              f"got {r['vclass']} - a real force was suppressed")

    # A security vehicle with NO police markings was always fine; check the
    # guard did not change it into something odd.
    r = classify.classify({"fleet_decal": True, "livery_words": ["SECURITY"]})
    check("an unmarked security van stays fleet", r["vclass"] == "fleet",
          f"got {r['vclass']}")

    # The reason string has to SAY why, or a reviewer sees a vehicle quietly
    # demoted with no explanation and re-publishes it by hand.
    r = classify.classify({**MARKED, "livery_words": ["SECURITY"]})
    check("the reason names private security",
          "security" in (r.get("why") or "").lower(),
          f"why={r.get('why')!r}")

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
