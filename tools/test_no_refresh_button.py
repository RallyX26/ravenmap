"""The refresh button is gone, and it must not come back by half-measures.

🚨 REMOVING A SHARED CONTROL IS NOT ONE DELETE. It was loaded by NINE pages
across TWO apps, four pages defined a `window.sparrowRefresh` hook that existed
only to serve it, sitenav.js positioned it and stacked other controls against
it, and the service worker precached its file. Any one of those left behind is
either a 404 on every page load, dead code that reads as live, or - the one that
actually bites - a layout rule written around a control that is no longer there.

⚠️ It was also the LAST `position:fixed` control on the site, which is what
seven overlap reports in one day were about ([[sparrow-no-floating-controls]]).
If a refresh control is ever wanted again it belongs IN THE HEADER, in flow.

    python tools/test_no_refresh_button.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    check("the file itself is gone", not (ROOT / "public" / "refresh.js").exists())

    html = list((ROOT / "public").glob("*.html")) + list((ROOT / "camctl").glob("*.html"))
    print(f"  ({len(html)} pages checked)")

    loads = [p.name for p in html if "refresh.js" in p.read_text(encoding="utf-8")]
    check("no page still loads it", not loads, f"still referenced by {loads}")

    # 🚨 A 404 IN THE SERVICE WORKER SHELL IS INVISIBLE. The install uses
    # allSettled precisely so one missing file cannot break it - which is also
    # why a stale entry can sit there failing forever without a symptom.
    sw = (ROOT / "public" / "sw.js").read_text(encoding="utf-8")
    shell = re.search(r"const SHELL = \[(.*?)\];", sw, re.S)
    body = shell.group(1) if shell else sw
    # ⚠️ STRIP COMMENTS FIRST. The note explaining why the entry was removed
    # naturally quotes the path it removed, so a plain substring search failed
    # on the very comment documenting the fix. Same trap as tools/contrast.py:
    # a checker that cannot tell code from prose about code sends you to undo
    # working changes.
    body = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", body, flags=re.S))
    check("the service worker does not precache it",
          "/static/refresh.js" not in body)

    # The hook existed ONLY for this button. Leaving it is dead code that reads
    # as live, and the next person wires a new button to it and wonders why.
    hooks = []
    for p in html + [ROOT / "public" / "app.js"]:
        t = p.read_text(encoding="utf-8")
        if re.search(r"^\s*window\.sparrowRefresh\s*=", t, re.M):
            hooks.append(p.name)
    check("no page still defines window.sparrowRefresh", not hooks, f"{hooks}")

    nav = (ROOT / "public" / "sitenav.js").read_text(encoding="utf-8")
    check("sitenav no longer queries .swrefresh", ".swrefresh" not in nav)
    # A variable left behind after its consumer is deleted is the tell that a
    # removal was done by search-and-delete rather than by reading.
    check("and has no orphaned refTop", "refTop" not in nav)

    css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
    check("no .swrefresh styles anywhere", ".swrefresh" not in css)

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
