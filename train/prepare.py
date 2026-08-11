"""Turn public police-vehicle photos into something a porch camera would produce.

🚨 THE PROBLEM THIS SOLVES IS THE WHOLE REASON THE CLASSIFIER MIGHT FAIL.

Public datasets are stock and press photography: close, sharp, front
three-quarter, good light. the reference camera sees a rear quarter through a
window, small in frame, at 45-60 degrees of skew, JPEG-crushed and often
motion-blurred.

Train on the first and validate on the second and the classifier learns
PHOTOGRAPHY, not policing - "is this a sharp studio-ish image" separates the
classes perfectly in training and means nothing on the street. It would then
call every crisp photo a police car and every webcam frame civilian, while
reporting excellent validation numbers.

So public images are degraded toward the camera's actual output before they are
used. This does not fully close the domain gap - nothing does except real local
positives - but it removes the trivially separable cue.

⚠️ AND IT IS ONLY HALF THE DEFENCE. The other half is that public NEGATIVES
must come from the same source as the public positives, so source style carries
no signal at all. Degrading only the positives would make degradation itself
the giveaway. `--negatives` is not optional; the script refuses without it.

    python train\\prepare.py --positives raw\\police --negatives raw\\civilian
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402

OUT = DATA / "training_public"
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Measured from his own banked crops: vehicles arrive between roughly 130 and
# 900 px on the long edge, mostly at the small end.
TARGET_EDGE = (140, 420)
JPEG_Q = (35, 78)


def hard_conditions(img: np.ndarray, rng: random.Random) -> np.ndarray:
    """The conditions a volunteer's camera will actually be installed in.

    the operator's point, and it reframes the whole dataset: people are going to run
    this pointed through a dirty window, under a streetlight, in a Michigan
    February. A classifier trained on fair weather is a classifier that works
    for whoever already had the best view.

    🚨 THE ONE THAT MATTERS MOST IS GREYSCALE. Cheap cameras cut to infrared at
    night and the frame goes monochrome - and every colour cue a livery-based
    model leans on vanishes at once. Michigan State Police blue and Michigan
    State Police grey are the same car to a classifier that has only ever seen
    daylight. 6% of his own banked crops are already effectively colourless,
    and his is a decent camera in a window; someone with a $30 outdoor unit
    will be monochrome for half of every day.

    Applied AFTER the resize and blur so the artefacts sit on top of the
    downscaled image, which is the order a real camera produces them in.
    """
    r = rng.random()

    if r < 0.22:
        # Night IR: monochrome, contrast crushed, noisier. Not a grey filter -
        # IR responds differently to materials, so lift the red channel first,
        # which is roughly what a near-IR sensor sees.
        f = img.astype(np.float32)
        mono = (f[:, :, 2] * 0.5 + f[:, :, 1] * 0.35 + f[:, :, 0] * 0.15)
        mono = np.clip(mono * rng.uniform(0.55, 0.95), 0, 255)
        img = cv2.merge([mono, mono, mono]).astype(np.uint8)
        img = np.clip(img.astype(np.int16) +
                      np.random.normal(0, rng.uniform(4, 12), img.shape).astype(np.int16),
                      0, 255).astype(np.uint8)

    elif r < 0.42:
        # Night in colour: blacks crushed nonlinearly, sodium/LED cast, and
        # headlight bloom. Linear exposure scaling does not look like night.
        f = img.astype(np.float32) / 255.0
        f = np.power(f, rng.uniform(1.5, 2.6)) * rng.uniform(0.5, 0.9)
        f[:, :, 0] *= rng.uniform(0.85, 1.0)          # cooler blue
        f[:, :, 2] *= rng.uniform(1.0, 1.2)           # warmer red
        img = np.clip(f * 255, 0, 255).astype(np.uint8)
        if rng.random() < 0.6:
            bloom = cv2.GaussianBlur(img, (0, 0), rng.uniform(6, 16))
            img = cv2.addWeighted(img, 1.0, bloom, rng.uniform(0.25, 0.6), 0)

    elif r < 0.54:
        # Sun glare or a backlit vehicle - the opposite failure, and just as
        # common on a west-facing window in the evening.
        h, w = img.shape[:2]
        gx, gy = rng.randint(0, w), rng.randint(0, max(1, h // 2))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt((xx - gx) ** 2 + (yy - gy) ** 2) / (max(h, w) * rng.uniform(0.3, 0.8))
        veil = np.clip(1.0 - d, 0, 1)[:, :, None] * rng.uniform(70, 170)
        img = np.clip(img.astype(np.float32) + veil, 0, 255).astype(np.uint8)

    elif r < 0.66:
        # Rain or snow on the glass: local defocus plus a bright haze. A wet
        # window does not blur evenly, so blur a random patch harder.
        h, w = img.shape[:2]
        blurred = cv2.GaussianBlur(img, (0, 0), rng.uniform(1.5, 4.0))
        mask = np.zeros((h, w), np.float32)
        for _ in range(rng.randint(2, 6)):
            cv2.circle(mask, (rng.randint(0, w), rng.randint(0, h)),
                       rng.randint(max(4, w // 10), max(8, w // 3)), 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), max(2, w // 20))[:, :, None]
        img = (img * (1 - mask) + blurred * mask).astype(np.uint8)
        img = np.clip(img.astype(np.float32) * rng.uniform(0.85, 1.0)
                      + rng.uniform(10, 40), 0, 255).astype(np.uint8)
        if rng.random() < 0.5:                        # falling snow
            h_, w_ = img.shape[:2]
            for _ in range(rng.randint(20, 120)):
                cv2.circle(img, (rng.randint(0, w_), rng.randint(0, h_)),
                           rng.randint(1, 2), (235, 235, 235), -1)

    if rng.random() < 0.25:
        # Vertical or diagonal shake - a pole-mounted camera in wind. The
        # existing blur is horizontal only, which assumes the vehicle moves and
        # the camera never does.
        k = rng.choice([3, 5, 7])
        kern = np.zeros((k, k), np.float32)
        if rng.random() < 0.5:
            kern[:, k // 2] = 1.0 / k
        else:
            np.fill_diagonal(kern, 1.0 / k)
        img = cv2.filter2D(img, -1, kern)

    return img


def degrade(img: np.ndarray, rng: random.Random) -> np.ndarray:
    """Push a clean photo toward what a window webcam actually produces."""
    h, w = img.shape[:2]

    # Rear/side framing. A stock photo is usually a front three-quarter; the
    # camera almost always gets a receding vehicle, so bias the crop toward one
    # end rather than keeping the photographer's composition.
    if rng.random() < 0.7:
        keep = rng.uniform(0.55, 0.9)
        x0 = int((w - w * keep) * (0 if rng.random() < 0.5 else 1))
        img = img[:, x0:x0 + int(w * keep)]
        h, w = img.shape[:2]

    # Small in frame, then back up to a working size - this is what destroys
    # the fine detail a stock photo has and a webcam never does.
    edge = rng.randint(*TARGET_EDGE)
    s = edge / max(h, w)
    img = cv2.resize(img, (max(8, int(w * s)), max(8, int(h * s))),
                     interpolation=cv2.INTER_AREA)

    if rng.random() < 0.55:                       # motion blur, horizontal
        k = rng.choice([3, 5, 7])
        kern = np.zeros((k, k), np.float32)
        kern[k // 2, :] = 1.0 / k
        img = cv2.filter2D(img, -1, kern)

    # Exposure and white balance drift. His camera runs manual exposure at -5
    # and the light changes all day.
    img = img.astype(np.float32)
    img *= rng.uniform(0.72, 1.3)
    for c in range(3):
        img[:, :, c] *= rng.uniform(0.9, 1.1)
    img = np.clip(img, 0, 255).astype(np.uint8)

    if rng.random() < 0.4:                        # sensor noise
        img = np.clip(img.astype(np.int16) +
                      np.random.normal(0, rng.uniform(2, 8), img.shape).astype(np.int16),
                      0, 255).astype(np.uint8)

    img = hard_conditions(img, rng)

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY,
                                         rng.randint(*JPEG_Q)])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else img


def ingest(src: Path, label: str, variants: int, rng: random.Random) -> int:
    dst = OUT / label
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.rglob("*")):
        if f.suffix.lower() not in EXTS:
            continue
        img = cv2.imread(str(f))
        if img is None or min(img.shape[:2]) < 40:
            continue
        # Name from the content hash so re-running is idempotent and the same
        # source image cannot sneak into both train and test under two names.
        stem = hashlib.blake2b(f.read_bytes(), digest_size=8).hexdigest()
        for v in range(variants):
            cv2.imwrite(str(dst / f"{stem}_{v}.jpg"), degrade(img, rng),
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positives", required=True, help="folder of police vehicles")
    ap.add_argument("--negatives", required=True,
                    help="ORDINARY vehicles FROM THE SAME SOURCE - see the module docstring")
    ap.add_argument("--variants", type=int, default=4,
                    help="degraded copies per source image")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    pos, neg = Path(args.positives), Path(args.negatives)
    for p in (pos, neg):
        if not p.is_dir():
            raise SystemExit(f"not a directory: {p}")

    rng = random.Random(args.seed)
    np.random.seed(args.seed)
    n_pos = ingest(pos, "police", args.variants, rng)
    n_neg = ingest(neg, "civilian", args.variants, rng)

    print(f"wrote {n_pos} police and {n_neg} civilian variants to {OUT}")
    if n_neg < n_pos * 0.5:
        print("\n⚠️  Far fewer negatives than positives. The head will learn to"
              "\n    say 'police' because saying it is usually right IN TRAINING,"
              "\n    which is the opposite of the real street. Get more ordinary"
              "\n    vehicles from the same source before fitting.")
    print("\nNext: train\\embed.py, then train\\fit.py")
    print("Remember: public data trains, LOCAL data validates. Nothing is")
    print("promoted on public test numbers - see train/README.md.")


if __name__ == "__main__":
    main()
