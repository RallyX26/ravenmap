"""Run the real pipeline over a video file and report honestly.

    python detect\run_video.py testvideo\sjc_rental_exit.webm
    python detect\run_video.py <video> --post --node n_xxxx --token T   (send to a hub)

Writes a contact sheet of every vehicle pass to data/eval/, with the plate
redaction applied exactly as the private tier would apply it, so the output can
be looked at rather than only summarised.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classify              # noqa: E402
import privacy               # noqa: E402
import snapshot              # noqa: E402
from core import CONFIG, DATA  # noqa: E402
from detect import visual     # noqa: E402
from detect.pipeline import run_video  # noqa: E402

EVAL = DATA / "eval"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--plate-every", type=int, default=2)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--post", action="store_true", help="POST sightings to a hub")
    ap.add_argument("--hub", default="http://localhost:8150")
    ap.add_argument("--node", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--lat", type=float, default=None)
    ap.add_argument("--lon", type=float, default=None)
    args = ap.parse_args()

    EVAL.mkdir(parents=True, exist_ok=True)
    for old in EVAL.glob("*.jpg"):
        old.unlink()

    t0 = time.time()
    print(f"reading {args.video}")

    def prog(i, live, fin):
        print(f"  frame {i:5d}   tracking {live}   completed {fin}", flush=True)

    passes = run_video(args.video, plate_every=args.plate_every,
                       max_frames=args.max_frames, progress=prog)
    dt = time.time() - t0

    cap = cv2.VideoCapture(args.video)
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_src = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    print(f"\n{n_frames} frames in {dt:.1f}s  "
          f"({n_frames / dt:.1f} fps processed, source is {fps_src:.0f} fps)")
    print(f"{len(passes)} vehicle passes\n")

    rows, published, with_plate = [], 0, 0
    for vp in sorted(passes, key=lambda v: v.first_frame):
        plate, conf, agree = vp.vote()
        ev = {}
        if vp.best_frame is not None and vp.best_vehicle_box:
            ev = visual.signals(vp.best_frame, vp.best_vehicle_box)
        ev["plate_text"] = plate
        c = classify.classify(ev)
        tier = "public" if c["tierable"] else "private"
        if plate:
            with_plate += 1
        if tier == "public":
            published += 1

        rows.append({
            "track": vp.track_id, "type": vp.cls_name,
            "frames": vp.frames_seen, "reads": len(vp.reads),
            "plate": plate, "ocr_conf": round(conf, 3),
            "agreement": round(agree, 3),
            "vclass": c["vclass"], "vclass_conf": c["conf"],
            "tier": tier, "why": c["why"],
            "heading_img": vp.heading(),
        })

        # Write the crop exactly as the hub would store it, redaction and all.
        if vp.best_frame is not None:
            meta = {"ts": time.time(), "node_id": args.node or "eval",
                    "node_name": Path(args.video).stem, "tier": tier,
                    "plate_text": plate, "vclass": c["vclass"],
                    "watermark": "" if tier == "public" else ""}
            img = snapshot.Image.fromarray(
                cv2.cvtColor(vp.best_frame, cv2.COLOR_BGR2RGB))
            try:
                name, _ = snapshot.store_crop(img, vp.best_vehicle_box, meta,
                                              plate_box=vp.best_plate_box)
                (EVAL / f"track{vp.track_id:03d}_{tier}.jpg").write_bytes(
                    (snapshot.SNAPS / name).read_bytes())
                (snapshot.SNAPS / name).unlink()
            except Exception as exc:
                print(f"  ! track {vp.track_id}: {exc}")

    hdr = f"{'trk':>4} {'type':<10} {'frm':>4} {'rds':>4} {'plate':<10} " \
          f"{'ocr':>5} {'agree':>6} {'tier':<8} class"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        print(f"{r['track']:>4} {r['type']:<10} {r['frames']:>4} {r['reads']:>4} "
              f"{r['plate'] or '-':<10} {r['ocr_conf']:>5.2f} {r['agreement']:>6.2f} "
              f"{r['tier']:<8} {r['vclass']}")

    print(f"\npasses={len(rows)}  with a plate read={with_plate}  "
          f"published to public tier={published}")
    print(f"crops written to {EVAL}")

    (EVAL / "report.json").write_text(
        json.dumps({"video": args.video, "frames": n_frames,
                    "seconds": round(dt, 2), "passes": rows}, indent=2),
        encoding="utf-8")

    if args.post:
        post(rows, passes, args)


def post(rows, passes, args) -> None:
    import base64
    import urllib.request

    by_track = {vp.track_id: vp for vp in passes}
    sent = 0
    for r in rows:
        vp = by_track[r["track"]]
        if vp.best_frame is None:
            continue
        ok, buf = cv2.imencode(".jpg", vp.best_frame,
                               [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            continue
        body = {
            "node_id": args.node, "ts": time.time(),
            "lat": args.lat, "lon": args.lon,
            "plate_text": r["plate"], "plate_state": "MI",
            # The hub's confidence gate gets the AGREEMENT number, not the raw
            # OCR score. Agreement is what actually predicts a correct read.
            "plate_conf": r["agreement"],
            "body": r["type"], "heading": r["heading_img"],
            "evidence": {}, "source": "camera",
            "snap_b64": "data:image/jpeg;base64," +
                        base64.b64encode(buf.tobytes()).decode(),
            "plate_box": list(vp.best_plate_box) if vp.best_plate_box else None,
        }
        req = urllib.request.Request(
            f"{args.hub}/api/sightings", method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {args.token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                print("  posted:", json.loads(resp.read()))
                sent += 1
        except Exception as exc:
            print("  post failed:", exc)
    print(f"posted {sent} sightings to {args.hub}")


if __name__ == "__main__":
    main()
