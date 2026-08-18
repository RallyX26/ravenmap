"""Build a numbered contact sheet from a labelling queue, and apply calls to it.

    python tools/label_sheet.py --mode patrol --n 25
    python tools/label_sheet.py --apply "1=p 2=c 3=u ..."
    python tools/label_sheet.py --wipe-machine        # undo every machine label

WHY A SHEET

The queue is one crop per request, which is right for a person: big image, big
buttons, one decision. It is the wrong shape for a machine first pass, because
looking at 2,300 crops one at a time is 2,300 round trips. A 5x5 sheet is one
look for twenty-five decisions, and the crops are small enough (100-450px) that
twenty-five of them fit at native size with room to spare.

🚨 EVERY LABEL THIS WRITES IS MARKED `sampling='machine'`.

That is not decoration. `measurable` in labelbank counts only labels sampled in
`review` mode, so machine labels are excluded from precision and recall with no
extra code - they can TRAIN the head and can never MEASURE it. The project's own
warning is the reason: a model evaluated against labels it partly wrote scores
well and the exercise measures nothing.

⚠️ AND THEY MUST STAY REVERSIBLE. `--wipe-machine` clears every one of them, so
if a retrain gets worse the first thing to try is removing this entire class of
label rather than guessing which one was wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw, ImageFont          # noqa: E402

import bank_index                                     # noqa: E402
import labelbank                                      # noqa: E402
from core import DATA                                 # noqa: E402

SHEET = DATA / "label_sheet.jpg"
MANIFEST = DATA / "label_sheet.json"

# One letter per class, because the whole point is a short reply.
KEYS = {"p": "police", "g": "gov", "f": "fleet", "c": "civilian", "u": "unsure"}

CELL = 300          # px per cell; crops are 100-450 wide so this is near native
COLS = 5
PAD = 8
HDR = 34            # strip above each cell for its number


def _font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def build(mode: str, n: int) -> dict:
    thr = labelbank._head_threshold()
    rows = bank_index.pick(mode, thr=thr, n=n * 3)     # over-fetch, some 404
    picked = []
    for r in rows:
        p = labelbank.image_path(r["day"], r["stem"])
        if p and p.exists():
            picked.append((r, p))
        if len(picked) >= n:
            break
    if not picked:
        raise SystemExit("queue '%s' is empty" % mode)

    cols = min(COLS, len(picked))
    rows_n = (len(picked) + cols - 1) // cols
    W = cols * (CELL + PAD) + PAD
    H = rows_n * (CELL + HDR + PAD) + PAD
    sheet = Image.new("RGB", (W, H), (14, 18, 26))
    d = ImageDraw.Draw(sheet)
    f_num = _font(26)
    f_sub = _font(15)

    man = {"mode": mode, "thr": thr, "items": []}
    for i, (r, p) in enumerate(picked):
        cx = i % cols
        cy = i // cols
        x = PAD + cx * (CELL + PAD)
        y = PAD + cy * (CELL + HDR + PAD)
        # number strip
        d.rectangle([x, y, x + CELL, y + HDR], fill=(30, 40, 58))
        d.text((x + 8, y + 4), "%d" % (i + 1), font=f_num, fill=(255, 220, 120))
        d.text((x + 52, y + 9),
               "clip %.2f  head %.3f" % (r["clip_conf"] or 0, r["head_conf"] or 0),
               font=f_sub, fill=(150, 165, 185))
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        im.thumbnail((CELL, CELL), Image.LANCZOS)
        ox = x + (CELL - im.width) // 2
        oy = y + HDR + (CELL - im.height) // 2
        sheet.paste(im, (ox, oy))
        d.rectangle([x, y + HDR, x + CELL, y + HDR + CELL], outline=(42, 53, 71))
        man["items"].append({"i": i + 1, "day": r["day"], "stem": r["stem"],
                             "clip": r["clip_vclass"], "clip_conf": r["clip_conf"],
                             "head": r["head_conf"]})

    sheet.save(SHEET, quality=88)
    MANIFEST.write_text(json.dumps(man, indent=1), encoding="utf-8")
    print("sheet   : %s  (%dx%d, %d crops)" % (SHEET, W, H, len(man["items"])))
    print("manifest: %s" % MANIFEST)
    print()
    print("reply with:  python tools/label_sheet.py --apply \"1=p 2=c 3=u ...\"")
    print("classes   :  p police   g gov(not police)   f fleet   c civilian   u unsure")
    return man


def apply(calls: str) -> None:
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_i = {it["i"]: it for it in man["items"]}
    done = {}
    for tok in calls.replace(",", " ").split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        try:
            idx = int(k)
        except ValueError:
            continue
        lab = KEYS.get(v.strip().lower()[:1])
        if not lab or idx not in by_i:
            print("  skipped %r" % tok)
            continue
        done[idx] = lab

    ok = bad = 0
    counts = {}
    for idx, lab in sorted(done.items()):
        it = by_i[idx]
        try:
            # 🚨 sampling='machine' - trains, never measures, wipeable.
            labelbank.set_label(it["day"], it["stem"], lab, "machine")
            ok += 1
            counts[lab] = counts.get(lab, 0) + 1
        except Exception as exc:                       # noqa: BLE001
            bad += 1
            print("  #%d failed: %s" % (idx, exc))
    print("applied %d label(s)%s" % (ok, ", %d failed" % bad if bad else ""))
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print("   %-10s %d" % (k, v))


def wipe_machine() -> None:
    """Remove every label this tool has ever written."""
    db = bank_index.read()
    rows = list(db.execute(
        "SELECT day, stem FROM crops WHERE sampling = 'machine'"))
    db.close()
    print("clearing %d machine label(s)" % len(rows))
    n = 0
    for r in rows:
        try:
            labelbank.clear_label(r["day"], r["stem"])
            n += 1
        except Exception:
            pass
    print("cleared %d" % n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="patrol")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--apply")
    ap.add_argument("--wipe-machine", action="store_true")
    a = ap.parse_args()
    if a.wipe_machine:
        return wipe_machine()
    if a.apply:
        return apply(a.apply)
    build(a.mode, a.n)


if __name__ == "__main__":
    main()
