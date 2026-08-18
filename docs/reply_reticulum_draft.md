# DRAFT reply to @jebidiahhambone (Instagram, Reticulum / mesh nodes)

**NOT POSTED.** Instagram comments cannot be sent from the inbox tool without
Meta App Review, so this one goes by hand anyway. Read it, change what sounds
wrong, post it yourself.

His comment, for reference:

> I think that's definitely true over something GMRS or LongFast Lora. But it is
> possible to create a node with anything like an android phone. In that case,
> one can pass it's data over wifi/4g using the Reticulum TCP client to server
> interface. But than wherever Sparrow map is hosted would need to allow for it.

---

## Short reply (comment length)

Reticulum is the right tool for the case I actually care about, which is a
camera with no internet at all. For a phone that already has wifi or 4G it would
sit on top of a connection that already works, so it would add a layer without
removing a problem.

The number that decides it is the payload. A crop is about 6.4 KB, which is
roughly 52 seconds of airtime on LongFast, before duty cycle. A claim on its own
is a few hundred bytes, about 3 seconds. So over LoRa you can send the verdict
but not the evidence.

That is the real blocker, and it is upstream of the transport: the map does not
publish on a claim alone, it wants the picture a human can check. A radio node
would have to decide for itself what it is looking at and send only the answer,
and whether the classifier fits on a small board is exactly the thing nobody has
confirmed yet. Settle that and the mesh question gets a lot easier.

Genuinely useful comment though, thank you. If you have run Reticulum over
LongFast with real traffic I would like to hear what throughput you actually got.

---

## The longer read, for you not for him

**He is technically literate and the suggestion is in good faith.** Worth
answering properly rather than politely.

**Where he is right:** Reticulum is a good fit for infrastructure-less nodes. It
gives every node a cryptographic identity and routes over whatever medium
exists, including LoRa and packet radio. That is the Gridbase case exactly.

**Where the proposal misses:** he suggests Reticulum over wifi/4G. If a node has
wifi or 4G it already has IP, and the phone already posts to the hub over HTTPS
with a signed payload. Reticulum there is a second addressing and encryption
layer over a working one. It also duplicates something you already have, because
nodes already sign their sightings with their own keys.

**The measurement that decides the whole thread:**

| | size | LongFast airtime (~1 kbps effective) |
|---|---|---|
| 200px crop | 6,438 bytes (median of 300) | **~52 s** |
| claim only | a few hundred bytes | ~3.2 s |

52 seconds per crop is not viable at any volume, and duty-cycle limits make it
worse. A claim-only node is viable.

**So the conclusion is the same one three separate conversations reached today.**
The GitHub issue about the Seeed reCamera, your question about getting a camera
online, and this comment all reduce to one unknown: **can the classifier run on
the node?** If it can, a radio node sends a verdict and the mesh works. If it
cannot, every node needs enough bandwidth to ship a picture home, and that rules
out radio entirely.

That is also the case for the $70 AI HAT+ on the support page. It is not one
experiment among several; it is the hinge three separate plans are waiting on.

**My recommendation: do not build a Reticulum bridge yet.** It is a real but
bounded piece of work, and it is only worth doing once a node exists that cannot
reach the internet any other way. Build it when the node exists, not before.
