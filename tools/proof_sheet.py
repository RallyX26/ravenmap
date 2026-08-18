"""SUPERSEDED by the live /proof page. Kept only as a static export.

⚠️ /proof is now served from camctl/proof_app.html and reads /api/proof/list
directly, so it is always current and every label can be kept or corrected in
one click. This script writes a READ-ONLY snapshot, which is what proved
inadequate: three wrong labels were spotted on it and there was no way to fix
them except describing them in a message, and each description matched several
crops. Use it only if you want a frozen copy of a moment.

Every machine label on one page, grouped by call, so a human can skim them.

    python tools/proof_sheet.py          ->  camctl/proof.html, served at /proof

WHY IT EXISTS

A machine first pass is only acceptable if a person can check it cheaply. The
labelling UI shows one crop at a time, which is right for DECIDING and wrong for
AUDITING - spotting a mistake means noticing that one tile in a wall of tiles
does not belong, and that is a glance, not two hundred clicks.

So: every crop the machine labelled, grouped under the call it was given, newest
first, with the two model scores under each. A wrong one stands out because it
is surrounded by right ones.

⚠️ THE POINT IS TO FIND MISTAKES, so the groups are ordered worst-first by the
head's own score: within `police`, the crops the head was MOST confident about
rejecting are shown first, because those are simultaneously the most valuable if
the call is right and the most damaging if it is wrong.

Images come from camctl's existing /api/bank/img route rather than being
embedded, so the page stays a few KB instead of tens of megabytes.
"""

from __future__ import annotations

import html
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bank_index                                      # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "camctl" / "proof.html"

ORDER = ["police", "gov", "fleet", "civilian", "unsure"]
BLURB = {
    "police": "Law enforcement. These are the ones that matter and the ones "
              "worth checking hardest — a wrong one here teaches the head "
              "that a private car is a patrol car.",
    "gov": "Publicly owned, not police: fire engines, municipal trucks, "
           "ambulances where the operator was legible.",
    "fleet": "Commercially owned. Autonomous test cars, delivery and plant.",
    "civilian": "Private vehicles CLIP called government and the machine "
                "disagreed with it.",
    "unsure": "Deliberately not called: night glare, too small, ambiguous "
              "ownership, or not a street capture at all. Nothing is lost by "
              "leaving these — they simply do not train anything.",
}


def build() -> None:
    db = bank_index.read()
    rows = list(db.execute(
        "SELECT day, stem, label, clip_vclass, clip_conf, head_conf, labelled_at "
        "FROM crops WHERE sampling = 'machine' ORDER BY head_conf DESC"))
    db.close()

    by = {}
    for r in rows:
        by.setdefault(r["label"] or "?", []).append(r)

    p = []
    p.append("<!doctype html><html lang=en><head><meta charset=utf-8>")
    p.append("<meta name=viewport content='width=device-width,initial-scale=1'>")
    p.append("<title>Proof the machine labels</title><style>")
    p.append("""
:root{--bg:#0a0d12;--card:#111621;--line:#1e2634;--line2:#2a3547;--ink:#e6ecf5;
 --dim:#8794a8;--dim2:#5c6879;--blue:#3d8cff;--good:#3ddc97;--warn:#ffb547;--hot:#ff5b6e;
 --mono:ui-monospace,Consolas,monospace}
*{box-sizing:border-box}
html,body{height:auto;overflow:visible}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:22px 16px 80px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--dim2);margin:0 0 20px;font-size:13px}
.warnbox{border-left:2px solid var(--warn);background:#15130c;padding:12px 14px;
 border-radius:0 8px 8px 0;margin:0 0 22px;font-size:13px;color:#d8cdb4}
.warnbox b{color:var(--ink)}
h2{font:600 12px var(--mono);letter-spacing:.14em;text-transform:uppercase;
 margin:30px 0 4px;padding-top:16px;border-top:1px solid var(--line)}
h2 .n{color:var(--dim2);margin-left:8px}
.blurb{color:var(--dim);font-size:12.5px;margin:0 0 12px;max-width:760px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:10px}
.c{background:var(--card);border:1px solid var(--line2);border-radius:8px;
 overflow:hidden}
.c img{width:100%;height:120px;object-fit:contain;background:#000;display:block}
.m{font:10.5px var(--mono);color:var(--dim2);padding:5px 7px;line-height:1.45}
.m b{color:var(--dim)}
.police h2{color:var(--hot)} .gov h2{color:var(--blue)}
.fleet h2{color:var(--warn)} .civilian h2{color:var(--dim)}
.unsure h2{color:var(--dim2)}
.hi{color:var(--warn)}
""")
    p.append("</style></head><body><div class=wrap>")
    p.append("<h1>Proof the machine labels</h1>")
    p.append("<p class=sub>%d crop%s labelled by machine, grouped by the call. "
             "Built %s.</p>" % (len(rows), "" if len(rows) == 1 else "s",
                                time.strftime("%d %b %Y, %H:%M")))
    p.append(
        "<div class=warnbox><b>These train the head. They can never measure it.</b> "
        "Every crop here carries <code>sampling='machine'</code>, so it is excluded "
        "from <code>measurable</code> automatically — precision and recall still "
        "come only from crops you labelled in <b>review</b> mode.<br><br>"
        "Within each group the crops the head was <b>most confident about</b> come "
        "first, because those are the most valuable if the call is right and the "
        "most damaging if it is wrong. To undo the whole lot: "
        "<code>python tools/label_sheet.py --wipe-machine</code></div>")

    for lab in ORDER:
        rs = by.get(lab) or []
        if not rs:
            continue
        p.append("<section class=%s>" % lab)
        p.append("<h2>%s<span class=n>%d</span></h2>" % (lab, len(rs)))
        p.append("<p class=blurb>%s</p>" % BLURB.get(lab, ""))
        p.append("<div class=grid>")
        for r in rs:
            src = "/api/bank/img/%s/%s" % (html.escape(r["day"]),
                                           html.escape(r["stem"]))
            hc = r["head_conf"]
            cls = " class=hi" if (hc is not None and hc > 0.30) else ""
            p.append(
                "<div class=c><img loading=lazy src='%s' alt=''>"
                "<div class=m><b>%s</b> %.2f<br><span%s>head %.3f</span></div></div>"
                % (src, html.escape(str(r["clip_vclass"])), r["clip_conf"] or 0,
                   cls, hc if hc is not None else 0))
        p.append("</div></section>")

    p.append("</div></body></html>")
    OUT.write_text("\n".join(p), encoding="utf-8")
    print("wrote %s  (%d crops)" % (OUT, len(rows)))
    for lab in ORDER:
        if by.get(lab):
            print("   %-10s %d" % (lab, len(by[lab])))
    print()
    print("open: http://[::1]:8160/proof")


if __name__ == "__main__":
    build()
