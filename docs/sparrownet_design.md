# SparrowNet  -  organising the idea

His idea: an encrypted network of our own that also talks to the real internet,
to keep SparrowMap from going offline and to give nodes somewhere to connect.

This note does three things: separates the goals that are bundled inside it,
names the one he did not say which is probably the strongest, and argues hard
against the part that would sink it.

---

## The three goals hiding inside one name

**A. Keep the map from going offline.**
**B. Give nodes a connection they otherwise would not have.**
**C. Stop anyone learning WHO is running a camera.**

They sound like one problem and they are three, with three different answers.
Bundling them is what makes "build a network" feel like the solution, because it
is the only thing that appears to touch all three.

---

## A. Resilience: an overlay does not fix this

Worth being blunt, because it is the goal he led with. What would actually take
SparrowMap offline?

| threat | does an encrypted overlay help? |
|---|---|
| the domain being seized or a registrar acting | **no** - nothing to point people at |
| Cloudflare dropping the project | **no** |
| Hetzner dropping the box | **no** - nothing to route to |
| the single box dying | **no** |
| legal pressure on him personally | **no** |

An overlay is a **transport**, and transport is not the failure mode. The failure
mode is *naming* and *hosting*. `project_pages_hub` already records the same
conclusion from a different direction: **the domain is the real single point of
failure.**

**What does fix it**, roughly in order of value per hour:

1. **A `.onion` address.** No domain, no registrar, no CDN, no port forwarding,
   nothing anybody can be leaned on to withdraw. It is the single highest-value
   resilience step and it is close to a weekend of work.
2. **Mirrors.** `mirror.py` already exists and already refuses to hold evidence,
   so the shape is there.
3. **Signed data.** Nodes already sign sightings, so a mirror can be trusted
   without trusting whoever runs it. That is the property that makes copies
   worth having.

---

## C. The argument he did not make, and the strongest one

Right now the privacy posture is genuinely good, and better than most projects:

* the hub suppresses `log_message`, so there is no Python access log;
* Caddy's own config says *"Access log OFF (privacy: no record of who)"*, and
  `/var/log/caddy` is empty;
* the source IP is used only for the operator gate and is never stored.

So a database seizure yields nothing about who runs a camera. **But the traffic
still exists**, and three parties see it that we do not control:

* **Cloudflare terminates TLS.** `Server: cloudflare`, `CF-RAY` present. Every
  node's IP and every upload passes through a company that can be compelled.
* **Hetzner** carries the packets.
* **The volunteer's ISP** can see they talk to map.sparrowmap.com regularly.

A volunteer in a small town, running a camera that photographs the local police,
is identifiable to anyone who can compel one of those three. Not from anything
SparrowMap stores - from the shape of the traffic.

**That is the gap an overlay genuinely closes**, and it protects exactly the
people the project depends on. It is a better reason to build SparrowNet than
uptime, and it is the reason worth leading with.

---

## The part that would sink it: do not build a new network

Rolling your own encrypted transport is the classic way a good project dies. The
failure is silent - it looks like it works, and it keeps looking like it works
until somebody who attacks protocols for a living looks at it. Every serious
option here already exists and has already been attacked for years.

| need | use | why |
|---|---|---|
| resilience + anonymity | **Tor hidden service** | no domain, no CDN, no host to lean on |
| node connectivity | **Tailscale / Headscale** | already in use here (`100.72.222.76`) |
| radio / no-internet mesh | **Reticulum or Yggdrasil** | see the Reticulum reply draft |

"SparrowNet" is then a **name for the arrangement**, not a protocol to write. That
is not a smaller idea. It is the same idea with the part most likely to fail
taken out of it.

---

## What I would do first

1. **Stand up a `.onion` mirror of the public map.** Cheap, reversible, and it
   answers the domain SPOF and volunteer anonymity at the same time.
2. **Let a node post over Tor or over the tailnet**, so a volunteer who wants to
   can contribute without their home IP crossing Cloudflare.
3. **Publish the threat model honestly**, including what an overlay does NOT fix.
   `/transparency` already sets that standard.
4. Only then think about mesh, and only for nodes that genuinely have no
   internet - which needs on-device classification first, because a crop is
   6,438 bytes and about 52 seconds of LoRa airtime.

⚠️ One thing to decide early, because it is a policy question rather than a
technical one: **a node posting over Tor is a node whose location cannot be
checked.** Sightings carry coordinates, and the project's defence against faked
sightings partly rests on a node being a real thing in a real place. Anonymity
for the volunteer and accountability for the claim pull against each other, and
that tension should be resolved deliberately rather than discovered later.
