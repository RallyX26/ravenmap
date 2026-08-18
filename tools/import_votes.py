"""Pull community votes home, score the voters, and apply what they agree on.

    python tools/import_votes.py --box root@HOST --key PATH            # dry run
    python tools/import_votes.py --box ... --key ... --apply

🚨 CONSENSUS IS NOT A HEADCOUNT.

Three people agreeing means nothing if all three are guessing, so every voter is
scored first against the GOLD crops seeded invisibly into the task - crops whose
answer he already gave in review mode. A voter below the accuracy floor has
every one of their votes discarded before consensus is computed, including their
votes on ordinary crops. Without that step, consensus measures popularity.

⚠️ AND IT RUNS HERE, NOT ON THE BOX. The box collects votes and nothing else. The
decision - who counts, what agreement means, which label is written - happens on
this machine, so a compromised public server can produce noise but cannot write
a label or move anything on the map.

Applied labels carry `sampling='community'`, which keeps them out of `measurable`
exactly like the machine ones: they can TRAIN the head and can never MEASURE it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import labelbank                                       # noqa: E402
from core import DATA                                  # noqa: E402

MAP = DATA / "label_task_map.json"
REMOTE_DB = "/opt/sparrowmap/data/label_votes.db"


def fetch(box: str, key: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "label_votes.db"
    subprocess.run(["scp", "-i", key, "-o", "BatchMode=yes", "-q",
                    "%s:%s" % (box, REMOTE_DB), str(tmp)], check=True)
    return tmp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--agree", type=int, default=3,
                    help="votes needed on one crop before it counts")
    ap.add_argument("--margin", type=int, default=2,
                    help="how far ahead the winning answer must be")
    ap.add_argument("--gold-floor", type=float, default=0.75,
                    help="a voter below this accuracy on gold is discarded")
    ap.add_argument("--min-gold", type=int, default=4,
                    help="gold answers needed before a voter can be judged")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if not MAP.exists():
        raise SystemExit("no %s - export a task first" % MAP)
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    gold = {k: v["gold"] for k, v in mapping.items() if v.get("gold")}

    db = sqlite3.connect(fetch(a.box, a.key))
    db.row_factory = sqlite3.Row
    votes = list(db.execute("SELECT item, label, voter FROM votes"))
    db.close()
    print("votes pulled       : %d" % len(votes))
    print("distinct voters    : %d" % len({v["voter"] for v in votes}))
    print("gold crops in task : %d" % len(gold))
    print()

    # --- score every voter on the hidden gold ---------------------------
    right, asked = Counter(), Counter()
    for v in votes:
        want = gold.get(v["item"])
        if not want:
            continue
        asked[v["voter"]] += 1
        if v["label"] == want:
            right[v["voter"]] += 1

    trusted, rejected, unproven = set(), set(), set()
    for voter in {v["voter"] for v in votes}:
        n = asked[voter]
        if n < a.min_gold:
            unproven.add(voter)
        elif right[voter] / n >= a.gold_floor:
            trusted.add(voter)
        else:
            rejected.add(voter)

    print("--- voters, judged on crops they did not know were tests ---")
    print("  trusted   : %d" % len(trusted))
    print("  rejected  : %d  (below %.0f%% on gold)" % (len(rejected), a.gold_floor * 100))
    print("  unproven  : %d  (fewer than %d gold answers, not counted)"
          % (len(unproven), a.min_gold))
    for voter in sorted(asked, key=lambda x: -asked[x])[:10]:
        mark = "trusted" if voter in trusted else (
            "REJECTED" if voter in rejected else "unproven")
        print("    %-22s %2d/%2d gold  %s" % (voter[:22], right[voter], asked[voter], mark))
    print()

    # --- consensus, trusted voters only, ordinary crops only -------------
    tally = defaultdict(Counter)
    for v in votes:
        if v["voter"] not in trusted:
            continue
        if v["item"] in gold:
            continue                      # gold measures voters, never trains
        tally[v["item"]][v["label"]] += 1

    settled, split, thin = [], 0, 0
    for item, c in tally.items():
        if sum(c.values()) < a.agree:
            thin += 1
            continue
        (top, n), = c.most_common(1)
        second = c.most_common(2)[1][1] if len(c) > 1 else 0
        if n - second < a.margin:
            split += 1
            continue
        if top == "unsure":
            continue
        settled.append((item, top, n, second))

    print("--- crops the trusted voters agreed on ---")
    print("  settled         : %d" % len(settled))
    print("  not enough votes: %d" % thin)
    print("  too split       : %d" % split)
    by = Counter(t for _, t, _, _ in settled)
    for k, v in by.most_common():
        print("    %-10s %d" % (k, v))

    # --- the stratified estimate, weighted by band population ----------
    # 🚨 THE WEIGHTS EXIST TO BE USED. A stratified sample read as if it were
    # simple random is worse than no sample at all: it looks rigorous and
    # reports a rate inflated by however hard the dense band was oversampled.
    # Band A is 60 crops out of ~5,700 while band C is 80 out of ~500,000, so
    # an unweighted count would put the government rate roughly two orders of
    # magnitude too high.
    rand = [(i, l) for i, l, _, _ in settled if mapping.get(i, {}).get("pool") == "random"]
    if rand:
        from collections import defaultdict
        seen = defaultdict(int)
        pos = defaultdict(int)
        pop = {}
        for item, lab in rand:
            m = mapping[item]
            b = m.get("band") or "?"
            seen[b] += 1
            pop[b] = m.get("band_pop") or 0
            if lab in ("police", "gov"):
                pos[b] += 1
        print()
        print("--- the RANDOM slice: an unbiased estimate for the whole bank ---")
        total_pop = sum(pop.values())
        est = 0.0
        for b in sorted(seen):
            rate = pos[b] / seen[b]
            est += rate * pop[b]
            print("    band %-3s %3d labelled, %3d government -> %5.1f%% of a "
                  "%d-crop band" % (b, seen[b], pos[b], rate * 100, pop[b]))
        print("    weighted estimate: %.2f%% of the bank is a government vehicle"
              % (100.0 * est / max(total_pop, 1)))
        print("    (%d crops behind that estimate, %d labelled)"
              % (total_pop, sum(seen.values())))
        print("    ⚠️ band C is the thin one and it is where MISSED positives")
        print("       live, so widen it before trusting a recall figure.")

    if not a.apply:
        print()
        print("DRY RUN. add --apply to write these into the bank.")
        return

    ok = 0
    for item, label, _, _ in settled:
        m = mapping.get(item)
        if not m:
            continue
        # 🚦 TWO DIFFERENT TAGS, BECAUSE THEY HAVE DIFFERENT RIGHTS.
        # community_random came from a stratified random draw and is therefore
        # allowed to MEASURE (his call: the project is open and the statistic is
        # checkable by anyone). community came from the patrol queue, which is
        # biased toward the model's mistakes by construction, so it may only
        # train - and neither trains until he approves it on /proof, because
        # APPROVED_FOR_TRAINING in fit_local contains neither tag.
        tag = "community_random" if m.get("pool") == "random" else "community"
        try:
            labelbank.set_label(m["day"], m["stem"], label, tag)
            ok += 1
        except Exception as exc:                        # noqa: BLE001
            print("  %s failed: %s" % (item, exc))
    print()
    print("applied %d community label(s), tagged sampling='community'" % ok)


if __name__ == "__main__":
    main()
