# Contributing to SparrowMap

Thanks for helping. SparrowMap is a citizen-run camera network that watches
government vehicles on public roads and destroys everything else on the device.
The project only works if the privacy line is defended, so most of what follows
is about *that*, not about code style.

## The one rule that is not up for debate

**A private vehicle must never become public.** The whole design fails toward
"civilian". If a change makes it easier for an ordinary person's plate or
movements to reach the public map, it will not be merged, however clever.

Concretely, do not weaken any of these without a very good, discussed reason:

- The two-tier split in `classify.py` and `core.py` (`public_tiers`).
- Plate destruction in the image itself (`snapshot.redact_plate`) — not just in
  the database.
- The keyed-hash + pepper rotation in `privacy.py`.
- Node-position jitter, retention windows, and the "searches are never logged"
  behaviour.

If you think one of these is wrong, open an issue and argue it in the open
first. That is the project working as intended.

## Getting it running

No hardware needed — the hub ships with a simulated town.

```
python hub.py --sim
  map          http://localhost:8150/
  transparency http://localhost:8150/transparency
  camera app   https://localhost:8151/app     (note https; accept the self-signed cert)
```

`python hub.py` (no `--sim`) runs it for real. The detector runs in the browser
on the contributor page — nothing to install.

## Pull requests

- Keep changes focused; one concern per PR.
- Explain *why*, not just *what* — this codebase leans on its comments.
- If you touch the privacy path, say so in the PR description and show that a
  private vehicle still cannot leak.
- Run what you changed. The tests under `detect/` and a `--sim` run are the
  quickest ways to prove you did not break the pipeline.

## Reporting a bug or a privacy concern

- Bugs: <https://github.com/SparrowMap/sparrowmap/issues>
- Something that could expose a private person: email **sparrowmap@icloud.com**
  before filing a public issue, so it can be fixed before it is advertised.

## Licence and the name

Code is **AGPL-3.0**. By contributing you agree your contribution is licensed the
same way. The **name "SparrowMap", the mark, and `sparrowmap.com` are not part of
the licence** — run your own instance freely, but give it your own name and logo
rather than presenting it as SparrowMap. (Same idea as Firefox: open source, name
reserved.)
