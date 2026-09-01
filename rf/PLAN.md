# SparrowMap RF scanner — plan

Open-source, free, government-only RF sensing that maps the **watchers' hardware**
(Flock/ALPR cameras, agency surveillance gear) by the wireless it emits. The RF
counterpart to the vehicle map, held to the exact same privacy line as the
plate rule: **only known surveillance/government devices ever leave the sensor;
every private device is discarded at the edge and never stored.**

## The honest split (why this scope)

- **Easy half:** a passive Wi-Fi/BLE scanner that logs what is around. Well-trodden
  (kismet, scapy, bettercap). A weekend.
- **Hard half:** deciding which signal is *government*. There is no way to do that
  for a random phone, and trying to is the mass-tracking we exist to oppose. So we
  do NOT fingerprint people. We match against a **curated allowlist of known
  surveillance/ALPR/agency vendors** (by OUI-resolved manufacturer and by known
  SSID/BLE patterns). Everything not on the list is hashed for de-dup within the
  run and then dropped. Nothing civilian is stored, ever.

This makes the FIRST useful target **fixed surveillance infrastructure** (Flock
cameras, ALPR poles, agency cameras) — identifiable, stationary, high value, and
the clean inverse of Flock. Mobile agency-vehicle RF ID is a later, harder phase.

## Privacy rules (non-negotiable, mirror the plate rule)

1. A device is kept ONLY if its resolved vendor is on the surveillance allowlist,
   or its SSID/BLE name matches a known surveillance pattern.
2. Everything else: the MAC is one-way hashed (salted, rotated) purely to de-dup
   inside a single scan, then discarded. Raw civilian MACs are never written to
   disk and never transmitted.
3. Position is the sensor's own GPS, snapped to a road/area the same way sightings
   are. A detection is a "a Flock camera was seen here", never "this person's
   phone was here".
4. Human review before anything is published, same as the pen.

## Architecture

```
[Pi + wifi adapter in monitor mode + BLE + GPS]
      -> capture (kismet/scapy)          # raw frames, on-device only
      -> resolve OUI -> vendor (IEEE db) # local file, no network
      -> allowlist filter                # keep surveillance vendors only
      -> edge discard everything else    # civilian never leaves
      -> candidate {vendor, signal, ssid, gps, ts}
      -> post to SparrowMap (new "rf" source)  # review + publish, gov-only
```

Reuses SparrowMap's spine: edge processing, human review, government-only publish.
It becomes a new **layer** on the map (surveillance cameras / RF), separate from
the vehicle sightings.

## Phases

- **P0 (now, no hardware):** skeleton + allowlist config + IEEE OUI resolver +
  dry-run against a sample capture. `rf/rf_scan.py`. Proves the filter and the
  privacy discard before any radio is touched.
- **P1 (hardware in hand):** wire real capture (kismet or scapy on the Alfa
  adapter), add BLE, add GPS. Log candidates locally, no upload. Walk/drive a
  known Flock camera and confirm it flags.
- **P2:** build the vendor allowlist for real — seed from the IEEE OUI registry
  filtered to surveillance vendors, plus SSID/BLE patterns observed in P1.
- **P3:** SparrowMap ingest — a new `source:"rf"` sighting + a map layer, behind
  the review pen, government-only, same as everything else.

## Open questions to settle before P3

- OEM Wi-Fi modules mean an OUI often resolves to the module maker, not "Flock".
  So allowlisting by OUI alone is weak; SSID/BLE-name patterns and behavior carry
  most of the weight. P1 is where we learn the real fingerprints.
- Legality is clean: passive listening to publicly broadcast Wi-Fi/BLE metadata,
  no interception, no connection — same footing as wardriving and as our cameras.
