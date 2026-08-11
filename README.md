# SparrowMap

**sparrowmap.com** — a licence-plate camera network that the neighbourhood owns
instead of a police department renting.

> "And ye shall be brought before governors and kings for my sake,
> **for a testimony against them**." — Matthew 10:18

Volunteers point a camera at a public road from their own property. The camera
does all recognition locally and sends a detection event, never video. The
results land on a public map anyone can open with no account.

```
Start Sparrow.bat
  map          http://localhost:8150/
  transparency http://localhost:8150/transparency
  camera app   https://localhost:8151/app          <- note https
```

The hub ships with a **simulated town** (`sources/synthetic.py`) so the whole
system is testable against known ground truth with no hardware attached. It is
off by default; `python hub.py --sim` turns it on.

---

## The design decision everything else follows from

A network like this can be built two ways and only one is worth building. If
every plate is public and searchable, anyone can look up an ex-partner's week
with photographs. That is not accountability, that is a stalking service with a
civic-sounding name.

So there are two tiers, enforced in code rather than in a policy document:

| | Public tier | Private tier |
|---|---|---|
| who | government vehicles, local and federal | everyone else |
| plate text | stored readable, searchable | **never written to disk** |
| snapshot | plate legible | plate destroyed in the pixels |
| retention | indefinite (public record) | 14 days, then deleted |
| lookup by plate | yes, and searches are never logged | **no path exists** |

A publicly owned vehicle, doing public work, on a public road, is a public
record. A private person driving to work is not.

## Five things that make the tiering real rather than decorative

1. **The gate is conservative and corroborated.** A vehicle reaches the public
   tier only above 0.85 confidence *and* only when something other than the
   plate text agrees. A single OCR slip must never be able to publish a
   stranger. `classify.py`.

2. **The plate is destroyed in the image, not just in the database.** This is
   the hole that sinks most designs: every plate reader photographs the plate,
   so a private tier that keeps the photo keeps the plate. SparrowMap pixelates
   and then bars the plate region before the JPEG is written.
   `snapshot.redact_plate`.

3. **Snapshots are crops of the vehicle, not the frame.** The pedestrian, the
   house number, the kid in the yard are not in a tight crop of a car's rear
   end. Full-frame storage is off by default and requires face blurring when on.

4. **The hash key rotates.** Private plates are keyed hashes so a car still
   re-identifies across cameras. The key rotates every 30 days, so trails cannot
   be joined across the boundary. This caps how long anyone can follow a private
   citizen *including whoever runs the hub*. `privacy.rotate_pepper`.

5. **No video crosses the network boundary.** Recognition is on-device; what
   arrives is a few hundred bytes and one still. There is no stream to
   intercept, subpoena or leak.

## Layout

```
core.py         paths + the entire privacy posture as one config block
privacy.py      plate hashing, pepper rotation, retention, redaction policy
classify.py     police / gov / fleet / civilian, with evidence and a hard gate
snapshot.py     vehicle crop, plate redaction, provenance stamp
nodes.py        ed25519 node identity, enrollment, camera cone geometry
db.py           sqlite schema + queries
hub.py          stdlib HTTP, the API, the janitor, TLS listener
sources/
  synthetic.py  a simulated town with ground truth, for building with no hardware
public/         map, about, transparency, phone contributor
data/           sparrow.db, snaps/, pepper.json  <- pepper.json is the crown jewel
certs/          self-signed TLS (browsers refuse the camera on plain http)
```

## API

| route | what |
|---|---|
| `GET /api/sightings?since=&vclass=&bbox=&limit=` | recent detections, redacted |
| `GET /api/sighting/<id>` | one detection |
| `GET /api/track/<plate_hash>` | a vehicle's trail + patrol score |
| `GET /api/nodes` | cameras, at **jittered** positions |
| `GET /api/leaderboard?hours=` | most-seen public vehicles |
| `GET /api/policy` | this deployment's privacy posture, machine-readable |
| `GET /api/audit` | the operator's public-tier decisions (searches are never logged) |
| `POST /api/enroll` | register a camera |
| `POST /api/sightings` | a node submits a detection (signed, or bearer for phones) |

`/api/policy` exists so an outside auditor can diff a deployment's claims
against its behaviour without being trusted with access. A promise nobody can
check is not a promise.

## Where it is honest about being incomplete

- The signal weights in `classify.py` are **starting values, not measurements**.
  They need calibrating against locally labelled footage before the confidence
  numbers mean anything. See ROADMAP.
- Government vehicles whose only evidence is plate text stay private, on
  purpose. That is rule 2 doing its job, and it costs real coverage.
- Phone submissions are one person's eyes. They are allowed to publish, because
  a human looking at a marked patrol car beats any classifier, but they are
  marked unverified and attributed to the submitting node.
- The keyed hash is only as strong as the pepper. A plate has ~10^8 possible
  values, so anyone who steals `data/pepper.json` can reverse every live hash.
  Retention and rotation bound the damage; they do not eliminate it.

## Licence, and the name

The code is **AGPL-3.0** (see `LICENSE`). In plain terms:

- You can run it, read it, modify it, and share it, freely.
- **If you run a modified version as a network service, you must publish your
  source, including your changes.** That is the point: a fork used to watch the
  public must itself be open to the public. A surveillance vendor cannot take
  this, close it, and sell it back.
- Derivatives stay AGPL. Nobody can relicense it or take it proprietary.

The bundled vehicle detector (`public/vendor/yolo11n.onnx`, Ultralytics YOLO11)
is itself AGPL, so the choice is partly the dependency's and partly ours, and it
is the right one for this project.

**The licence covers the CODE, not the NAME.** *SparrowMap*, the sparrow mark,
and `sparrowmap.com` are the identity of this project, not part of the AGPL
grant. You are welcome, encouraged, to run your own instance from this code, but
give it your own name and logo rather than presenting it as SparrowMap. (Same
principle as Firefox: the source is open, the name is not.)

Running your own regional instance *is* the design. A network nobody owns is many
small servers, not one big one.
