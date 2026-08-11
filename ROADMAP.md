# SparrowMap roadmap

Status as of **2026-08-08**. Phases 1–3 are done and the system is live on real data from one node.

---

## ✅ Phase 1 — the system, with no hardware
Two-tier privacy enforced at the storage choke point · keyed plate hashing with rotation · retention
janitor · plate redaction inside stored images · vehicle-crop-only snapshots · classifier with evidence
strings and a corroboration gate · behavioural patrol detector · ed25519 node identity · node position
jitter · stdlib hub (REST + TLS) · map, About, Transparency, contributor pages · simulated town
with ground truth.

## ✅ Phase 2 — real recognition
`detect/pipeline.py`: detect → velocity-predicted IoU track → plate localise → OCR → per-pass vote → one
sighting. **ONNX end to end, no torch** (rf-detr COCO, yolo-v9 plate, cct-s-v2 OCR) so a Raspberry Pi
node needs no torch install. Plate box returned in full-frame coordinates so redaction works.

**Measured:** plate detection 7/7 on real stills; OCR resolved every plate at ≥0.999 worst-character
probability (example plates omitted - this file ships publicly and a real civilian read has no place in it).

## ✅ Phase 3 — visual vehicle identification
`detect/vehicle_id.py`: CLIP zero-shot over police / emergency / gov_dot / fleet / civilian, multiple
prompts per class averaged, gated at confidence ≥ 0.80 **and margin ≥ 0.70**. Margin matters more than
confidence — CLIP is confidently wrong far more often than it is uncertain.

**Measured: police recall 5/5, civilians wrongly published 0/6**, using real street crops as negatives.

This exists because of the pixel budget. At a typical window scale a plate is 22 px against the 60 OCR
needs, while a roof light bar is 186 px against the 20 shape recognition needs. **Identifying a patrol
vehicle by sight has roughly ten times the headroom of reading its plate**, so an ordinary porch camera
can contribute police sightings on day one while never reading a plate.

## ✅ Phase 4 — live deployment, one node
Camera control app (`camctl/`) with software ROI pan/tilt/zoom, exposure and focus lock, named presets,
and a GPS placement view. It owns the camera and re-serves it as MJPEG, so the detector consumes it like
any network camera — one process on the hardware, unlimited consumers.

`detect/run_live.py` runs time-bounded against a live stream, drops frames rather than queueing, and
posts to the hub.

---

## ⏭️ NEXT, in order

1. **Long observation run.** Leave `run_live.py --post --visual` going and find out whether a real patrol
   unit trips the public tier. This is the outstanding empirical question and needs no code.

2. **Read the plate LEGEND, not just the number.** The recogniser returns only the alphanumeric slots, so
   a plate that literally says SHERIFF never fires `gov_plate_word`. A second generic OCR pass over the
   plate crop's border bands is cheap and high value. *Do this first of the code items.*

3. **`sources/rtsp.py` and `sources/webcam.py`.** The pipeline is written and tested; these are just
   frame sources.

4. **Calibrate `classify.py`.** The signal weights are educated guesses with a logistic squash on top, so
   the confidence numbers on the map are decorative. Label a few hundred local crops, fit the weights,
   and publish a confusion matrix on the About page. Publishing the error rate is the difference between
   a tool and a rumour mill.

5. **Distil CLIP into a small CNN** for Pi-class nodes. CLIP can auto-label the crops nodes already
   collect, so the labelled set builds itself. Zero-shot was the bootstrap, not the destination.

6. **Dual-stack sweep** of the other local apps on this machine — an IPv4-only bind costs 2–4 seconds per
   request from a phone. One line each.

## Phase 5 — the volunteer node
The product is not the map, it is the thing a stranger can put in their window.
- Raspberry Pi image, or an old Android in Termux, with QR-code enrollment
- Local buffer and retry so a node survives the hub being down
- **Clean shutdown path** — force-killing a process holding a USB capture graph wedges the device, and a
  volunteer's node must never brick its own camera on restart
- Node-side privacy mask over any part of the frame the volunteer does not want processed
- A one-page install guide written for someone who has never used a terminal

## Phase 6 — federation
A single hub is a single point of seizure, subpoena and shutdown.
- Publish the event format as a versioned spec with a conformance test
- Hub-to-hub sync of **public tier only**. Private-tier rows never federate
- Signed policy manifests so a hub cannot quietly loosen its own rules invisibly
- A public directory of hubs and their policies

## Phase 7 — accountability features only a network can do
- Patrol heatmaps by hour and neighbourhood. The question worth answering is not "where is this car" but
  "which streets get twenty passes a day and which get none"
- Unmarked-unit surfacing from behaviour alone (`patrol_score` already works)
- Deployment detection: a cluster of agency vehicles converging is newsworthy before any press release
- Compare against FOIA'd department vehicle inventories

---

## Open decisions

**Vehicle fingerprinting.** Re-identifying a vehicle by stickers, damage and wheels works, and unmarked
units do keep fleet-spec wheels. But **a fingerprint is a plate** — its whole function is cross-camera
linking, and it is worse than a plate hash in three ways: rotation cannot sever it because dents persist,
it follows the physical object through a sale, and it is self-contained so stealing the database is
enough. Rule: fingerprint hashes get the same lifecycle as plate hashes — peppered, rotated, short
retention for private tier, clear only once classified public. **Unresolved tension:** catching an
unmarked unit *by behaviour* requires cross-camera tracking of an unclassified vehicle. The peppered
lifecycle bounds it; it does not cure it. Strongest detector is fingerprint **and** patrol score, never
either alone.

**Ownership proof for the private tier.** "Let a driver see their own trail" answers *is someone
following me*, which is the one question this can answer *for* an ordinary person. It is switched off
because any self-service version is indistinguishable from looking up a stranger. Needs a mailed code, a
DMV integration, or an in-person check. Decide the channel before building.

**Borderline review queue.** A vehicle just under the gate loses its plate forever, so a human cannot
promote it later. A 48-hour quarantine holding plate text pending review would fix that, at the cost of a
table of readable plates. Deliberately not built — decide it on purpose, not by accident.

**Legal posture per state.** Private ALPR is regulated unevenly (California SB 34; New Hampshire,
Vermont, Maine). Michigan is the first deployment. Get a real read before promoting outside it, and let
`/api/policy` carry the per-deployment answer.

**Camera placement consent.** A volunteer consents; the neighbour whose driveway is in frame did not. The
node-side privacy mask is the technical half. The social half is a placement guideline: point at the
road, never at a window.

---

## Node siting — the two budgets

A node must pass **both**, and they are different kinds of constraint.

**1. Effective plate width = `observed_px × cos(θ)`, need 60–100 px.** Angle costs a cosine and
magnification is linear, so neither alone rescues a bad mount. `tools/aim.py` and
`tools/feature_budget.py` compute both.

**2. Within ~30° of the road axis, aimed at receding traffic** in rear-plate states like Michigan.

The placement instinct is backwards: people aim at a junction for volume. The correct target is the
**nearest and slowest** traffic — cars stopped at a light are the easiest shot ALPR ever gets. And buy
**focal length, not megapixels**: 1080p needs roughly 12 mm for 20 m and 20 mm for 30 m.

A node that fails these is still useful. `patrol_score` works on tracks, and visual identification works
with ten times the margin, so a plate-blind camera still contributes the public accountability layer.
