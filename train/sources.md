# Public data sources, and what each one is actually good for

Checked 2026-08-08. **Licences must be re-checked before use** — they change,
and "found on the internet" is not a licence.

## ⭐ The good news: object-detection sets solve the source-bias trap for free

The central risk (see README) is that positives from the internet and negatives
from his camera let the classifier learn *"stock photo vs webcam frame"* instead
of *"police vs not"*.

The Roboflow police sets below are **object-detection** datasets, and they
annotate ordinary vehicles in the same photos — `car`, `truck`, `van`, `bus`
alongside `Emergency Vehicle`. So cropping both classes out of the *same
images* gives positives and negatives that share photographer, camera, lighting,
country, era and compression exactly.

**That is worth more than a dataset ten times the size.** Style carries no
signal, so the only thing left to separate the classes is the vehicle itself.
Prefer these over any classification-only set of police photos.

## Candidates

| source | size | licence | verdict |
|---|---|---|---|
| [Roboflow · Police Cars (FYP TC)](https://universe.roboflow.com/fyp-tc-idn2o/police-cars-sumfm) | 314 imgs | CC BY 4.0 | ✅ **best starting point.** Has `Emergency Vehicle` + car/truck/van/bus in the same frames |
| [Roboflow · Priority-Vehicles-Police (PVold)](https://universe.roboflow.com/pvold/priority-vehicles-police) | 281 imgs | CC BY 4.0 | ✅ three police "styles" — useful livery variety |
| [Roboflow · Police (Security Training)](https://universe.roboflow.com/security-training/police-jq67t) | 119 imgs | CC BY 4.0 | ⚠️ mostly officers/equipment, not vehicles. Small vehicle yield |
| [Roboflow · Vehicle Detection (Leo Ueno)](https://universe.roboflow.com/leo-ueno/vehicle-detection-3mmwj) | 5,062 imgs | open | ✅ **negatives at volume.** Ordinary traffic, road-camera-ish framing |
| [GitHub · Emergency Vehicles (GAN-augmented)](https://github.com/Shatnawi-Moath/EMERGENCY-VEHICLES-ON-ROAD-NETWORKS-A-NOVEL-GENERATED-DATASET-USING-GANs) | 20,000 | check repo | ⚠️ **use the REAL portion only.** See below |
| [images.cv · police car](https://images.cv/dataset/police-car-image-classification-dataset) | 739 | check site | ⚠️ classification-only, so it brings no style-matched negatives |

### ⚠️ On the 20,000-image GAN set

It is by far the largest, and most of it is **generated**. A classifier is very
good at spotting GAN artifacts, so if the positives are synthetic and the
negatives are not, it will learn *"was this made by a GAN"* and score
brilliantly — the exact same failure as the stock-photo trap, wearing a
different hat. Use the real-photo portion, or nothing.

## The gap none of this closes

Every one of these is **front three-quarter, close, well lit, mostly American
municipal liveries**. His camera gets a **rear quarter through a window, small,
skewed 45–60°, in whatever light Michigan is having**. `prepare.py` degrades the
images toward that, which removes the trivial cue but does not manufacture the
viewpoint.

So public data can teach the model what a light bar, push bumper, two-tone
livery and a spotlight look like. Only real local positives can tell us whether
it recognises them **on the road it actually watches**. That is why promotion is gated on
`data/eval/labels.json` and not on any number produced from this table.

## Getting the data

Roboflow Universe needs a free account and an API key to export. That is a
signup, so it is the operator's to do — the key then goes in an env var, never in
the repo. Once exported as COCO or YOLO, crop by class into two folders and run:

    python train\prepare.py --positives raw\police --negatives raw\ordinary
    python train\embed.py --dir data\training_public
    python train\fit.py
