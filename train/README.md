# Training a vehicle classifier that actually works on a porch camera

## Why this exists

SparrowMap's police call currently comes from CLIP used **zero-shot** plus one
hand-written colour rule. Measured on the reference camera's street:

| method | measured |
|---|---|
| `light_bar` colour rule | wrong **5 / 5** |
| CLIP zero-shot | calls POLICE on **40%** of ordinary traffic, confidently, up to 0.848 |

CLIP's prior is roughly *"dark sedan or SUV = police"*, which on a residential
street describes most of the traffic. Its precision here is zero. The public
tier is therefore **disabled in config** until something measurably better
exists. This directory is how that gets fixed.

## The approach: a head on CLIP embeddings

Not a CNN from scratch, and not fine-tuning CLIP end to end. Both need far more
data than we have and a bigger GPU than an RTX 2070 Super. Instead:

1. freeze CLIP, use it purely as a feature extractor;
2. train a small classifier **on top of its embeddings**;
3. keep everything that made the zero-shot version usable (robustness to
   lighting and viewpoint comes free from CLIP's pretraining) while replacing
   the part that is broken — the decision boundary.

This is cheap: embeddings for a few thousand crops take minutes on his card,
and the head trains in seconds. It can be retrained every time new labels
arrive, which is what makes "running it longer makes it better" true.

## 🚨 THE TWO TRAPS. Read before touching the data.

### 1. Source bias — the one that will silently ruin this

Public police-vehicle images are stock and press photography: close, sharp,
front three-quarter hero shots, good light. the reference camera sees rear and side
views through a window, small in frame, at 45–60° skew, in mixed light, with
JPEG artifacts and motion blur.

If the positives come from the internet and the negatives come from his camera,
**the easiest thing for the classifier to learn is "stock photo vs webcam
frame"**. It will score beautifully in validation and then call every sharp
photo a police car and every webcam frame civilian. It will have learned
photography, not policing.

Two defences, and both are required:

* **public negatives too.** Ordinary cars from the *same* source as the public
  positives, so image style carries no signal. This is the important one.
* **degrade the public images** toward what the camera actually produces:
  downscale, re-encode as low-quality JPEG, blur, jitter colour and exposure,
  and crop to rear/side aspect ratios. See `prepare.py`.

### 2. Class imbalance — the one that will flatter you

Roughly 1 vehicle in 500 on a residential street is police. A model that always
answers "not police" is **99.8% accurate** and completely worthless.

**Never report accuracy.** Report precision and recall, and set the operating
threshold on the precision-recall curve at the point where precision is high
enough to publish — because a false positive here is a public accusation, and
the whole project's value is that what it shows is true.

## The validation rule that cannot be bent

**A model is only ever validated on locally labelled crops from real cameras.**
Public data is for *training*. It says nothing about whether the model works on
a real window on a real street, which is the only question that matters. A
model that beats CLIP on public test data and not on `data/eval/labels.json`
has not earned the public tier.

Promotion is gated: `detect/calibrate.py` must report a real precision **and** a
real recall on held-out local data before `publish_public_tier` goes true in
`config.json`.

## Layout

    train/
      sources.md     where the public data came from, and its licence
      prepare.py     ingest + degrade public images into a training set
      embed.py       CLIP embeddings for a directory of crops (cached)
      fit.py         train the head, report precision/recall, save the model
      fit_local.py   train the head on LOCAL labelled crops; measure honestly

Local labelled data lives in `data/training/` (banked automatically by the
node) and `data/eval/labels.json` (the held-out truth set).
