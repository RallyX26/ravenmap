"""Test plate localisation + OCR on real still photographs.

Split out from the video test on purpose. The video exercises detection,
tracking and pass aggregation; this exercises the reader. Running them together
means a zero result has two possible causes and you cannot tell which, which is
exactly what happened the first time round: no plates were read off a night
clip where the plates were fifteen pixels wide, and that told us nothing about
whether the reader works.

    python detect\run_stills.py testvideo\stills
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detect.pipeline import PlateReader   # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    folder = Path(args.folder)
    out = Path(args.out) if args.out else folder / "_annotated"
    out.mkdir(parents=True, exist_ok=True)

    print("loading models...")
    reader = PlateReader()
    print(f"providers: {reader.providers}\n")

    imgs = sorted(p for p in folder.iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    hdr = f"{'file':<48} {'plates':>6} {'read':<12} {'conf':>6} {'px':>6}"
    print(hdr); print("-" * len(hdr))

    total, found = 0, 0
    for p in imgs:
        img = cv2.imread(str(p))
        if img is None:
            continue
        total += 1
        h, w = img.shape[:2]
        t0 = time.time()
        # Whole image as the "vehicle box": these are photographs of cars, not
        # frames with a detector upstream.
        reads = reader.read(img, (0, 0, w, h))
        dt = (time.time() - t0) * 1000

        if not reads:
            print(f"{p.name[:48]:<48} {0:>6} {'-':<12} {'-':>6} {'-':>6}")
            continue
        found += 1
        best = max(reads, key=lambda r: r.conf * (r.area ** 0.5))
        pw = best.box[2] - best.box[0]
        print(f"{p.name[:48]:<48} {len(reads):>6} {best.text or '-':<12} "
              f"{best.conf:>6.2f} {pw:>5}px   {best.region or '':<14} ({dt:.0f} ms)")

        vis = img.copy()
        for r in reads:
            cv2.rectangle(vis, (r.box[0], r.box[1]), (r.box[2], r.box[3]),
                          (60, 220, 150), max(2, w // 400))
            cv2.putText(vis, f"{r.text} {r.conf:.2f}", (r.box[0], max(20, r.box[1] - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, max(0.6, w / 1600),
                        (60, 220, 150), max(2, w // 700))
        cv2.imwrite(str(out / p.name), vis, [cv2.IMWRITE_JPEG_QUALITY, 88])

    print(f"\n{found}/{total} images produced a plate detection")
    print(f"annotated copies -> {out}")


if __name__ == "__main__":
    main()
