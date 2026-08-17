# Can a Raspberry Pi be a phone-style node? And can you gut a trail cam for it?

Asked 2026-08-17: *"I wanna use the lens and everything for a trail cam for my
own use. It's like a 3k camera it's gotta be good. I would remove the
motherboard and get a raspberry pi?"*

Short answers: **the Pi node, yes, today. Reusing the trail cam's sensor, no.
The lens, maybe but probably not worth it. And a trail cam's trigger model
fights the pipeline.**

---

## 1. A Pi as a phone-style node — YES, this already exists

`detect/relay.py` is the phone-style contract in one file: find a vehicle, send
a crop too small to carry a plate, let someone else's classifier decide what it
was. Three dependencies (`opencv-python`, `onnxruntime`, `numpy`), **no torch**,
and it fetches its own model and verifies it by SHA-256 on every run.

| Stage | Runs on a Pi? |
|---|---|
| Vehicle detection (`yolo11s.onnx`, onnxruntime CPU) | ✅ Pi 5 fine, Pi 4 marginal, Zero 2 W no |
| The government-vehicle **head** (CLIP + torch, ~2.5 GB) | ❌ seconds per crop — **and a phone-style node does not run it** |

That second row is the whole reason this works: the heavy model never touches the
device. It is also why a *fully independent* solar node is still a plan and not a
product — see `sparrow-solar-pi-plan`, where the open question is whether CLIP
converts to a Hailo-8L at a useful rate.

⚠️ **What a Pi relay node does NOT do today:** it never reads a plate. It posts
`plate_text: ""` and a crop capped at 200px on the long edge with the plate area
painted out. The full-resolution-on-confirmation path exists in the browser
camera and the drive popup, **not in relay.py**. So a Pi node contributes a dot
and a classifiable crop — not a readable government plate. If reading tags is
the goal, that is `run_live.py`, and `run_live.py` wants the head.

## 2. Reusing the trail cam's sensor — no, and this is the part to be blunt about

A Pi's camera port is CSI-2 and expects a **supported** sensor: one with a kernel
driver and a tuning file. A trail cam's sensor is bonded to the vendor's own
board and driven by their ISP firmware. There is no generic way to wire an
arbitrary sensor to a Pi.

And the value is in the thing you would be throwing away. What makes a good trail
camera good is the sensor, its ISP tuning, the IR cut filter and the night
illumination — all of which live on that motherboard. Removing it and keeping the
lens keeps the cheapest component.

## 3. The lens — maybe, but it is probably the wrong lens

If it is a standard **M12 board lens**, it can be adapted: the Pi HQ Camera is
C/CS mount and M12 adapters exist. Mechanically plausible.

But a trail cam lens is deliberately **wide** — it is trying to cover a clearing
at ten metres. Number plates need the opposite. What matters is **pixels across
the plate**, and the measured bar is about **120 px**; phones capturing at 640 px
caught essentially nothing. A wide lens spends its pixels on scenery.

For plates you want a **longer focal length on a narrower slice of road**, which
is the opposite of what a trail cam ships with. Buying the right lens costs less
than the adapter fiddling.

## 4. The real mismatch: trail cams take triggered stills

`relay.py` tracks a vehicle across frames before it believes it:

- `MIN_FRAMES = 3` — seen three times before it counts as a vehicle at all
- `GONE_S = 0.9` — unseen for 0.9 s and the pass is over

So it needs a **continuous stream at roughly 4 fps or better**. A PIR-triggered
camera that wakes, takes one or three stills, and sleeps cannot satisfy that, and
the design behind it is deliberate: single-frame detection on a moving car is a
coin flip, so the pipeline votes across the whole pass.

## 5. What to actually do

**Keep the trail cam intact.** Two better paths:

1. **Easiest, works now:** if the camera can produce an RTSP stream, point
   `relay.py` at it and change nothing about the camera —
   `map.sparrowmap.com/IPCamera`. The video never leaves the network; only the
   200px crop does.
2. **One purpose-built box:** Pi 5 + the HQ Camera module + a longer lens,
   running `relay.py`. Detection on the Pi, classification at the hub.

Either way the trail cam keeps doing what it is good at, and the node is built
from parts that are actually supported.

## ⚠️ Fleet caveats, if this ever becomes many devices

- **Ingest is one global 600/hr bucket** for all nodes (Caddy strips XFF, so
  every node looks like 127.0.0.1). One person's Pi is fine; a fleet is not.
- **Nodes have no outbox** — a 429 loses the pass, with no retry.

Both are in `sparrow-audit-fixes` as open items, and both want fixing before
hardware ships, not after.
