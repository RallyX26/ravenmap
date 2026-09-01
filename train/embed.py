"""CLIP embeddings for a directory of crops. Cached, because they never change.

CLIP is frozen and used purely as a feature extractor here. Everything SparrowMap
gets for free from its pretraining - tolerance of lighting, viewpoint, weather,
partial occlusion - is retained; the only part being replaced is the decision
boundary, which is the part measured to be broken (it calls POLICE on 40% of
ordinary traffic).

Embeddings are cached to a .npz keyed by the file's content hash, so re-running
after adding a few hundred new labels only embeds the new ones. On a 2070 Super
this is the difference between seconds and a coffee break, and it is what makes
retraining-on-every-new-label practical rather than a chore nobody does.

    python train\\embed.py --dir data\\training_public
    python train\\embed.py --dir data\\training          # the node's own bank
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import DATA   # noqa: E402

CACHE = DATA / "embeddings"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def file_key(p: Path) -> str:
    return hashlib.blake2b(p.read_bytes(), digest_size=12).hexdigest()


def embed_dir(root: Path, batch: int = 32, only=None) -> dict:
    """{keys, vecs, paths} for images under root.

    Normally walks the whole tree. If `only` is given (an iterable of image
    Paths), embeds JUST those. The measurement needs the ~1,900 LABELLED crops,
    not the 655k-crop bank - walking and embedding the whole bank held the 8 GB
    GPU for hours and starved box_puller's CLIP. In `only` mode the cache is
    MERGED, never shrunk, so the walk-everything path and this one share one
    growing npz.
    """
    from detect.vehicle_id import VehicleIdentifier
    vid = VehicleIdentifier()
    torch = vid.torch

    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / (root.name + ".npz")
    keys: list[str] = []
    vecs: list[np.ndarray] = []
    paths: list[str] = []
    known: dict[str, np.ndarray] = {}
    known_path: dict[str, str] = {}
    if cache_file.exists():
        z = np.load(cache_file, allow_pickle=True)
        for k, v, p in zip(z["keys"], z["vecs"], z["paths"]):
            known[str(k)] = v; known_path[str(k)] = str(p)
        print(f"  {len(known)} embeddings cached")

    if only is not None:
        files = [Path(f) for f in only]
    else:
        files = [f for f in sorted(root.rglob("*")) if f.suffix.lower() in EXTS]

    new: dict[str, tuple] = {}       # key -> (vec, rel), to merge into the cache
    todo, batch_imgs = [], []
    from PIL import Image

    for f in files:
        if not f.exists():           # a labelled crop may have been pruned
            continue
        k = file_key(f)
        rel = str(f.relative_to(root))
        if k in known:
            keys.append(k); vecs.append(known[k]); paths.append(rel)
            continue
        img = cv2.imread(str(f))
        if img is None:
            continue
        batch_imgs.append(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        todo.append((k, rel))
        if len(batch_imgs) >= batch:
            _flush(vid, torch, batch_imgs, todo, keys, vecs, paths, new)
            batch_imgs, todo = [], []
    if batch_imgs:
        _flush(vid, torch, batch_imgs, todo, keys, vecs, paths, new)

    if only is None:
        # Walk mode: the processed set IS the whole tree, so it is the cache.
        np.savez_compressed(cache_file, keys=np.array(keys),
                            vecs=np.array(vecs), paths=np.array(paths))
    elif new:
        # Labelled-only mode: fold the few new embeddings into the FULL cache so
        # nothing already cached is dropped. Only rewrite when we added some.
        for k, (v, rel) in new.items():
            known[k] = v; known_path[k] = rel
        mk = list(known.keys())
        np.savez_compressed(cache_file, keys=np.array(mk),
                            vecs=np.array([known[k] for k in mk]),
                            paths=np.array([known_path.get(k, "") for k in mk]))
    print(f"  {len(keys)} embeddings -> {cache_file}")
    return {"keys": keys, "vecs": np.array(vecs), "paths": paths}


def _flush(vid, torch, imgs, todo, keys, vecs, paths, new=None):
    with torch.no_grad():
        inp = vid.proc(images=imgs, return_tensors="pt").to(vid.device)
        f = vid.model.get_image_features(**inp)
        f = (f / f.norm(dim=-1, keepdim=True)).float().cpu().numpy()
    for (k, rel), v in zip(todo, f):
        keys.append(k); vecs.append(v); paths.append(rel)
        if new is not None:
            new[k] = (v, rel)
    print(f"    embedded {len(keys)}", end="\r")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    root = Path(args.dir)
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    print(f"embedding {root}")
    embed_dir(root)


if __name__ == "__main__":
    main()
