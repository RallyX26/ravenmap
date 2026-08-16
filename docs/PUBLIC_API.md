# SparrowMap public API

Everything below is **already public**. No key, no account, no arrangement, no
rate-limit deal. If you can open the map in a browser you can read the same data
from a script.

Base: `https://map.sparrowmap.com`

All responses are JSON. Every timestamp is **unix seconds** (float).

---

## `GET /api/sightings`

Vehicle passes. This is the main feed.

| parameter | default | meaning |
|---|---|---|
| `since` | `now - 3600` | unix seconds; only rows newer than this |
| `limit` | `400` | max rows; **capped at 2000** however high you ask |
| `vclass` | `all` | `all`, `public`, `police`, `gov`, `fleet`, `civilian` |
| `bbox` | none | `S,W,N,E` in degrees |

```bash
# published government vehicles in the last 24 hours
curl "https://map.sparrowmap.com/api/sightings?since=$(($(date +%s)-86400))&vclass=public"

# everything in a map viewport
curl "https://map.sparrowmap.com/api/sightings?since=0&bbox=42,-84,43,-83&limit=500"
```

A row:

```json
{
  "id": 376686,
  "node_id": "n:3ad261ad",
  "ts": 1786919635.26,
  "lat": 30.258555, "lon": -97.750741,
  "tier": "private",
  "plate_hash": null,
  "vclass": "civilian",
  "vclass_why": "no distinguishing signals; treated as private",
  "color": null, "body": null, "make": null, "model": null,
  "heading": null, "speed_mph": null,
  "source": "camera", "reviewed": null, "decided_by": null,
  "snap": null
}
```

### 🚨 The two tiers are the whole design, and the feed shows it

- **`tier: "public"`** — a government vehicle. Carries a photo, and the plate is
  kept **legible in that photo on purpose**, because a publicly owned vehicle
  doing public work on a public road is a public record.

  ⚠️ **`plate_text` is empty in practice.** The field exists and is returned once
  a human has confirmed a row, but **measured 2026-08-16: 0 of 159 published
  rows carried any plate text** — the reader has not produced one yet. Do not
  build anything that depends on plate strings from this API.
- **`tier: "private"`** — everybody else. **No plate text, ever.** The plate is
  destroyed on the camera before anything is uploaded, and the preview crop has
  the plate area painted out. `plate_hash` is a keyed hash on a **rotating**
  pepper, so it cannot be joined to anything after a rotation.

**The default feed is mostly private rows** — they are the live road, and they
are deliberately anonymous. Use **`vclass=public`** if you want the published
government vehicles. In a recent 24 hours that was **65 rows** out of thousands.

---

## `GET /api/nodes`

Cameras.

| parameter | default | meaning |
|---|---|---|
| `public_cams` | `1` | `0` = volunteer cameras only, omit the public traffic-camera fleet |
| `box` | none | `S,W,N,E`; snapped outward, so the answer is always a superset |

```bash
curl "https://map.sparrowmap.com/api/nodes?public_cams=0"          # ~455 volunteer cameras
curl "https://map.sparrowmap.com/api/nodes?box=42,-84,43,-83"      # one viewport
```

⚠️ **Unfiltered this is ~13,400 rows and 4 MB.** Ask for a `box`, or
`public_cams=0`, unless you genuinely want the whole fleet.

⚠️ A camera's published position is **coarse**. Exact positions are not
published, deliberately, and watched-road spans are **opt-in** per owner.

---

## Smaller endpoints

| endpoint | what |
|---|---|
| `GET /api/sighting/<id>` | one sighting |
| `GET /api/stats` | cameras online, sightings in 24h, hours watched |
| `GET /api/health` | uptime, db state, in-flight counters |
| `GET /api/places` | town badges |

---

## What to expect in practice

🔬 Measured, not estimated (2026-08-16, polite sequential calls):

| call | rows | size | time |
|---|---|---|---|
| `/api/sightings` default | 400 | 184 KB | 0.7 s |
| `/api/sightings?vclass=public` 24h | 65 | 39 KB | 1.0 s |
| `/api/sightings?limit=5` | 5 | 2 KB | 0.25 s |
| `/api/nodes?public_cams=0` | 455 | 104 KB | 0.9 s |
| `/api/nodes` everything | 13,432 | 4.0 MB | 0.4 s (cached) |

### 🚨 Please poll politely

Responses carry `Cache-Control: public, max-age=3` or `4`, and the edge honours
it. **Polling faster than that gains you nothing** and only costs the origin,
which is a single small box that also serves the public map.

Firing many *different* queries back to back is the thing that hurts: each unique
query string is a cache miss, and a cold `/api/nodes` has taken **57 seconds**
under that treatment. A test run doing exactly that got `503`s from the edge —
the hub itself never refused anything.

- reuse the same query string so you hit cache
- one request at a time
- `since=<last ts you saw>` rather than re-downloading a window
- send a `User-Agent` that says who you are and how to reach you

---

## 🚨 Latency: this is a record, not a live feed

There is **no streaming endpoint**. One existed and was removed: it pinned a
server thread per connected viewer, which does not survive a crowd.

A sighting appears **seconds to minutes** after the vehicle passed, and longer
when it waits on a human to confirm it.

⇒ Good for **where to place a sensor**. No good for **pointing one at a vehicle
while it is still there**.

---

## Licence

Code is **AGPL-3.0** — run your own instance, please do. The **SparrowMap** name
and mark are not part of that licence: a fork is welcome under its own name.
