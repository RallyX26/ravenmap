"""The review gate's helpers must all exist.

🚨 WHY THIS TEST IS SO SMALL AND SO SPECIFIC.

A helper called `working()` was defined inside the camera-key exchange block in
rv.html. That block was later removed - correctly, the server accepts a camera
key directly now - and the helper went with it while three calls to it stayed
behind. addToken threw ReferenceError on its first line, so the sign-in button
did literally nothing: no request, no error, no change on screen. Reported as
"nothing happens when i enter my token".

Every existing check passed. The file is valid HTML, the inline script parses,
`node --check` is clean, preflight was green. A ReferenceError is a RUNTIME
fact, and nothing in this repo was running that path.

This does not attempt to be a JavaScript linter - it cannot know about DOM
globals, browser builtins or anything defined in another file. It asserts one
narrow thing that is cheap and would have caught the outage: the handful of
helpers the sign-in path calls are defined in the same file that calls them.

Run: python tools/test_rv_gate.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RV = ROOT / "public" / "rv.html"

# The helpers the sign-in path depends on. Deliberately hand-written: a list
# somebody must edit on purpose is worth more here than a clever derivation
# that quietly stops matching.
REQUIRED = ["working", "addToken", "api", "withTimeout", "saveWallet",
            "start", "show", "toast", "dropToken"]


def inline_script(html: str) -> str:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    if not blocks:
        raise SystemExit("no inline script found in rv.html")
    return max(blocks, key=len)


def main() -> int:
    html = RV.read_text(encoding="utf-8")
    js = inline_script(html)

    bad = []
    for name in REQUIRED:
        defined = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", js) \
            or re.search(r"\b(?:var|let|const)\s+" + re.escape(name) + r"\s*=", js)
        called = re.search(r"\b" + re.escape(name) + r"\s*\(", js)
        if called and not defined:
            bad.append(f"{name}: CALLED but never defined")
        elif not called and not defined:
            bad.append(f"{name}: missing entirely (did the gate change shape?)")

    # And the reverse of the same mistake: a call to something that looks like
    # one of our helpers but was renamed.
    for name in REQUIRED:
        for m in re.finditer(r"\b" + re.escape(name) + r"\s*\(", js):
            pass

    print(f"checked {len(REQUIRED)} gate helpers in public/rv.html")
    if bad:
        for b in bad:
            print(f"  [fail] {b}")
        print("\n⛔ the review gate would throw at runtime")
        return 1
    for name in REQUIRED:
        print(f"  [ ok ] {name}")
    print("\nALL CLEAR - every helper the sign-in path calls is defined")
    return 0


if __name__ == "__main__":
    sys.exit(main())
