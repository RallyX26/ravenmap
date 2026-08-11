"""What the label bank ACTUALLY contains, counted in distinct vehicle passes.

Run before fitting anything that adds a class. The question a 3-way
police/gov/fleet head lives or dies on is not "how many crops" - it is "how
many distinct vehicles has this camera really seen in each class", because the
tracker fragments one car into a dozen crops seconds apart and augmentation
multiplies that again.

Rules this enforces (all previously violated, all measured):
  * count DISTINCT PASSES, not crops (same node, within 10 s = one pass)
  * report LOCAL and SCRAPED separately - public data trains, local validates
  * never print accuracy

Usage:  python tools\\class_census.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BANK = ROOT / "data" / "training"
PASS_GAP = 10.0


def load() -> list[dict]:
    out = []
    for j in sorted(BANK.rglob("*.json")):
        if not j.with_suffix(".jpg").exists():
            continue
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        d["_day"] = j.parent.name
        d["_stem"] = j.stem
        out.append(d)
    return out


def passes(rows: list[dict]) -> int:
    """Distinct vehicle passes. Same rule as train/fit_local.pass_groups."""
    n, last = 0, None
    for r in sorted(rows, key=lambda r: (r.get("node_id") or "", r.get("ts") or 0)):
        node, ts = r.get("node_id") or "", r.get("ts") or 0.0
        if last is None or node != last[0] or ts - last[1] > PASS_GAP:
            n += 1
        last = (node, ts)
    return n


def origin(r: dict) -> str:
    """Which DISTRIBUTION a crop came from, not which folder.

    `handheld` is separated because train/fit_local excludes it from both
    training and testing - it was measured to hurt (AP 0.890 -> 0.844). Folding
    it into "local" inflates every count that is supposed to say how much
    camera-view evidence exists.
    """
    s = r.get("source")
    if s in ("scraped", "remote_node"):
        return s
    if (r.get("sampling") or "") == "handheld":
        return "handheld"
    return "local"


def main() -> int:
    rows = load()
    done = [r for r in rows if r.get("label")]
    print(f"crops banked      {len(rows)}")
    print(f"crops labelled    {len(done)}   ({len(rows) - len(done)} unlabelled)")
    print()

    print("LABEL VOCABULARY IN USE (crops / distinct passes, by origin)")
    print(f"{'label':<10} {'origin':<12} {'crops':>7} {'passes':>7}")
    grid: dict[tuple[str, str], list] = defaultdict(list)
    for r in done:
        grid[(r["label"], origin(r))].append(r)
    for (lab, org) in sorted(grid):
        rs = grid[(lab, org)]
        # scraped photos have no node/ts timeline; one photo = one pass
        p = len(rs) if org == "scraped" else passes(rs)
        print(f"{lab:<10} {org:<12} {len(rs):>7} {p:>7}")
    print()

    # Everything the labeller called 'police' (the UI says "Government"), split
    # by what the live system called it. This is the only signal in the bank
    # that hints whether police and gov are separable populations at all.
    print("INSIDE label='police'  (UI reads 'Government') - live system's own call")
    pol = [r for r in done if r["label"] == "police"]
    c = Counter((r.get("clip") or {}).get("vclass") for r in pol)
    for k, v in c.most_common():
        print(f"  clip.vclass={k or '-':<12} {v:>5}")
    c = Counter((r.get("auto_verdict") or {}).get("vclass") for r in pol)
    for k, v in c.most_common():
        print(f"  auto_verdict={k or '-':<12} {v:>5}")
    print()

    print("EVIDENCE SIGNALS PRESENT ON LABELLED CROPS")
    ev = Counter()
    for r in done:
        for k, val in (r.get("evidence") or {}).items():
            if val:
                ev[f"{r['label']}:{k}"] += 1
    for k, v in ev.most_common(20):
        print(f"  {k:<34} {v:>5}")
    if not ev:
        print("  (none)")
    print()

    print("CAN A POLICE-vs-GOV HEAD BE FITTED YET?")
    import labelbank
    # Camera-view only: handheld and scraped can train, they can never measure.
    local = [r for r in done if origin(r) in ("local", "remote_node")]
    unsplit = [r for r in local
               if r["label"] == "police"
               and int(r.get("label_vocab") or 1) < labelbank.LABEL_VOCAB]
    split = [r for r in local if int(r.get("label_vocab") or 1) >= labelbank.LABEL_VOCAB]
    p_police = passes([r for r in split if r["label"] == "police"])
    p_gov = passes([r for r in split if r["label"] == "gov"])
    print(f"  merged 'government' crops still unsplit : {len(unsplit)}"
          f"  ({passes(unsplit)} passes)  -> :8160/label, Split mode")
    print(f"  distinct passes labelled POLICE         : {p_police}")
    print(f"  distinct passes labelled GOV, not police: {p_gov}")
    floor = 30
    if p_police >= floor and p_gov >= floor:
        print(f"  -> both classes clear {floor} passes. Fit it.")
    else:
        short = [n for n, v in (("police", p_police), ("gov", p_gov)) if v < floor]
        print(f"  -> NO. {', '.join(short)} under {floor} distinct passes.")
        print("     Fitting anyway measures the labeller's memory of one week of")
        print("     traffic, not a class boundary. Split what exists, then keep")
        print("     labelling until both sides clear the floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
