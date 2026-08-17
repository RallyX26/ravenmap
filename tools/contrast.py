"""Measure every text colour on the site against WCAG, instead of judging by eye.

🚨 WHY THIS EXISTS. A Hacker News reader said "Dark gray on black. Impossible to
see." I could not tell which element they meant, and I could not tell by looking
either - the palette reads fine on the monitor it was built on, which is exactly
how this kind of bug survives. So measure it.

The bar (WCAG 2.1):
  4.5:1  normal text
  3.0:1  large text (>=24px, or >=18.66px bold) and UI borders/icons
Anything under 3.0 is not "a bit dim", it is unreadable for a large number of
people and marginal for everyone on a phone in daylight.

⚠️ This parses the STYLESHEET, so it sees the colours as authored. An element
whose colour comes from JS is invisible here - grep those separately.

    python tools/contrast.py                 # the whole sheet
    python tools/contrast.py --fail-under 3  # exit 1 if anything is worse
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
CSS = HERE.parent / "public" / "style.css"

# The two things text actually sits on. --bg2 is the panel/card colour, and it
# is the LIGHTER of the two, so a colour that passes on --bg2 can still fail on
# --bg. Both are checked and the WORSE result is the one reported.
SURFACES = ("--bg", "--bg2")


def _srgb(c: int) -> float:
    x = c / 255
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def luminance(hexstr: str) -> float:
    h = hexstr.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def tokens(css: str) -> dict[str, str]:
    """The :root custom properties, resolved one level deep."""
    out = {}
    for name, val in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", css):
        val = val.strip()
        if val.startswith("#"):
            out[name] = val
    return out


def strip_comments(css: str) -> str:
    """🚨 A COMMENT MENTIONING A COLOUR IS NOT A USE OF IT.

    Writing `/* --civilian-ink, not color:var(--civilian) */` above the fixed
    rule made this tool report the fixed rule as still broken, and print the
    comment as the evidence. A checker that reports phantom failures is worse
    than no checker: the next real one gets skimmed past."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def uses(css: str, token: str) -> list[str]:
    """Selectors that set `color:` to this token. Best-effort, good enough to
    point at the offending rule rather than to be a parser."""
    found = []
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", strip_comments(css)):
        sel, body = block.group(1).strip(), block.group(2)
        # ⚠️ THE TOKEN NAME NEEDS A BOUNDARY AT BOTH ENDS.
        # Without the trailing one, `--civilian` matched `var(--civilian-ink)`
        # as a prefix, so the tool reported the two rules I had just FIXED as
        # still failing, and named the fixed colour as the culprit. A checker
        # that cannot tell a token from its longer namesake will send you to
        # re-fix working code.
        if re.search(r"(?<!-)color\s*:\s*var\(\s*" + re.escape(token)
                     + r"\s*[,)]", body):
            found.append(" ".join(sel.split())[:70])
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail-under", type=float, default=0.0,
                    help="exit 1 if any text colour scores below this")
    a = ap.parse_args()

    css = CSS.read_text(encoding="utf-8")
    tok = tokens(css)
    missing = [s for s in SURFACES if s not in tok]
    if missing:
        print(f"cannot find {missing} in {CSS}")
        return 2

    print(f"{CSS}\n")
    print(f"{'token':14} {'hex':9} {'on --bg':>8} {'on --bg2':>9}  verdict")
    print("-" * 74)

    worst = 99.0
    rows = []
    for name, hexv in tok.items():
        if name in SURFACES or name == "--mono":
            continue
        r1 = ratio(hexv, tok["--bg"])
        r2 = ratio(hexv, tok["--bg2"])
        low = min(r1, r2)
        where = uses(css, name)
        # A colour used only for a marker or a border is held to the 3.0 bar,
        # not the 4.5 one. Being honest about which bar applies matters: calling
        # a map dot a failure at 4.4 would bury the real problems.
        istext = bool(where)
        bar = 4.5 if istext else 3.0
        verdict = "ok" if low >= bar else ("LOW" if low >= 3.0 else "FAIL")
        rows.append((name, hexv, r1, r2, low, verdict, where, istext))
        if istext:
            worst = min(worst, low)
        print(f"{name:14} {hexv:9} {r1:8.2f} {r2:9.2f}  {verdict}"
              + ("" if istext else "   (not used as text)"))

    print()
    bad = [r for r in rows if r[5] != "ok" and r[7]]
    if bad:
        print("TEXT COLOURS BELOW THE BAR, and what they paint:")
        for name, hexv, r1, r2, low, verdict, where, _ in bad:
            print(f"  {name} {hexv}  worst {low:.2f}:1  ({verdict})")
            for w in where:
                print(f"      {w}")
    else:
        print("every text colour clears 4.5:1")

    if a.fail_under and worst < a.fail_under:
        print(f"\nworst text contrast {worst:.2f} < {a.fail_under}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
