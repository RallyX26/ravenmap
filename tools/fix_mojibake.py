"""Repair UTF-8-that-was-read-as-cp1252 mojibake, and strip UTF-8 BOMs.

Kept as a real tool rather than a scratch script because PowerShell 5.1 causes
this every time it rewrites a file: `Get-Content` decodes as the ANSI codepage
and `Set-Content -Encoding utf8` re-encodes as UTF-8 *with a BOM*, so one
innocent `-replace` double-encodes every non-ASCII character in the file and
adds a BOM on the way out.

    python tools\fix_mojibake.py <file-or-dir> [...]

TWO THINGS THAT ARE EASY TO GET WRONG, both of which bit me:

1. DO NOT DETECT WITH A MARKER LIST. An em dash and a curly quote mojibake into
   different byte leads, so a list tuned on one silently misses the other and
   reports the file clean. Detect by attempting the repair instead.

2. THE PLAIN cp1252 ROUND TRIP IS NOT ENOUGH. cp1252 leaves five slots
   undefined - 0x81, 0x8D, 0x8F, 0x90, 0x9D - so Python's codec raises on those
   characters and the repair aborts on exactly the files that need it. A right
   curly quote (U+201D, UTF-8 E2 80 9D) mojibakes to a string containing
   U+009D, which is one of them. Windows passes those C1 bytes through
   unchanged, so the repair has to as well.
"""

from __future__ import annotations

import sys
from pathlib import Path

# cp1252 slots with no defined character. Windows treats the byte as itself.
C1_PASSTHROUGH = {0x81, 0x8D, 0x8F, 0x90, 0x9D}

TEXT_SUFFIXES = {".html", ".htm", ".css", ".js", ".json", ".md", ".txt",
                 ".py", ".csv", ".svg", ".xml"}


def _to_bytes(s: str) -> bytes:
    """Encode as cp1252, passing the five undefined slots through as raw bytes."""
    out = bytearray()
    for ch in s:
        o = ord(ch)
        if o in C1_PASSTHROUGH:
            out.append(o)
        else:
            out.extend(ch.encode("cp1252"))    # raises for genuinely clean text
    return bytes(out)


def unmojibake_once(s: str) -> str | None:
    """Repaired string, or None if `s` was not mojibake.

    Both steps succeeding is the signal. Mojibake round-trips by construction.
    Clean text does not: a real em dash encodes to the single byte 0x97, which
    is a UTF-8 continuation byte with no lead in front of it, so the decode
    raises.
    """
    try:
        return _to_bytes(s).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def fix_file(p: Path, apply: bool = True) -> tuple[bool, int, bool]:
    """Returns (changed, repair_rounds, bom_removed)."""
    raw = p.read_bytes()
    had_bom = raw[:3] == b"\xef\xbb\xbf"
    if had_bom:
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, 0, False

    rounds = 0
    while rounds < 4:
        fixed = unmojibake_once(text)
        if fixed is None or fixed == text:
            break
        text, rounds = fixed, rounds + 1

    changed = bool(rounds or had_bom)
    if changed and apply:
        p.write_bytes(text.encode("utf-8"))     # UTF-8, no BOM, ever
    return changed, rounds, had_bom


def main(argv: list[str]) -> int:
    targets: list[Path] = []
    for a in argv or ["."]:
        p = Path(a)
        if p.is_dir():
            targets += [f for f in sorted(p.rglob("*"))
                        if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES]
        elif p.is_file():
            targets.append(p)

    n = 0
    for f in targets:
        changed, rounds, bom = fix_file(f)
        if changed:
            n += 1
            print(f"{f}  repaired x{rounds}  bom_removed={bom}")
    print(f"\n{n} file(s) changed of {len(targets)} scanned")

    # Verify only files that are genuinely valid UTF-8. Decoding a non-UTF-8
    # file with errors='replace' invents a string that can round-trip by
    # accident - which flagged the minified Leaflet bundle as mojibake.
    def strict(f: Path) -> str | None:
        try:
            return f.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return None

    # A file is only mojibake if the repair would actually CHANGE it. Pure-ASCII
    # text round-trips to itself - cp1252 encodes it byte-for-byte and ASCII is
    # valid UTF-8 - so testing `is not None` alone flags every ASCII source file
    # in the tree as damaged.
    def is_damaged(f: Path) -> bool:
        t = strict(f)
        if t is None:
            return False
        fixed = unmojibake_once(t)
        return fixed is not None and fixed != t

    bad = [f for f in targets if is_damaged(f)]
    if bad:
        print("STILL MOJIBAKE:")
        for f in bad:
            print("  ", f)
        return 1
    print("all clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
