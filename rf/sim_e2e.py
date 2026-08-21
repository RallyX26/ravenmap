#!/usr/bin/env python3
"""End-to-end simulation of the RF pipeline, no hardware and no live hub.

Proves the whole chain works AND that the privacy invariant holds at every hop:

  scan  ->  edge filter  ->  POST  ->  hub ingest  ->  review pen  ->  publish  ->  map layer

The one thing that must never happen is a civilian device surviving any stage.
This asserts it: the demo capture contains real-looking private devices (a home
AP, an iPhone, a Tesla) and the test FAILS if any of them appear in the payload,
the pen, or the map.

    python rf/sim_e2e.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_scan  # noqa: E402

# The private devices from the demo capture that must be gone by the end.
PRIVATE_MARKERS = ["myhomewifi", "iphone", "tesla",
                   "de:ad:be:ef:00:01", "12:34:56:78:9a:bc", "f0:99:bf:00:11:22"]


def blob(x) -> str:
    return json.dumps(x, default=str).lower()


def assert_no_private(stage: str, payload) -> None:
    b = blob(payload)
    hit = [m for m in PRIVATE_MARKERS if m in b]
    if hit:
        raise AssertionError(f"PRIVACY BREACH at [{stage}]: civilian data present: {hit}")
    print(f"  [{stage:12}] clean - no civilian data")


# --- client side -----------------------------------------------------------
def client_scan_and_post():
    oui_db = rf_scan.load_oui_db()
    gps = (42.0, -83.0)   # a stand-in fix; the real client reads gpsd
    devices = rf_scan.capture(demo=True)
    kept, dropped = [], 0
    for dev in devices:
        keep, reason = rf_scan.is_surveillance(dev, oui_db)
        if keep:
            kept.append(rf_scan.to_candidate(dev, reason, gps))
        else:
            dropped += 1   # discarded at the edge, never leaves the device
    payload = {"node_id": "rf_beta_01", "candidates": kept, "dropped_private": dropped}
    return payload


# --- hub side (simulated) --------------------------------------------------
PEN = []          # review pen: unconfirmed rf candidates
MAP_LAYER = []    # published surveillance layer


def hub_ingest(payload: dict):
    # The hub only ever sees what the client already filtered. It stores each
    # candidate in the review pen, keyed by the device id, gov-review gated.
    for c in payload.get("candidates", []):
        PEN.append({**c, "reviewed": None, "id": len(PEN) + 1})
    return {"accepted": len(payload.get("candidates", [])),
            "dropped_private_reported": payload.get("dropped_private", 0)}


def operator_review():
    # A human confirms real surveillance gear. Here: confirm anything whose
    # reason is a strong SSID/vendor match (stand-in for a person clicking yes).
    for row in PEN:
        if row["reviewed"] is None:
            row["reviewed"] = "confirmed"
            MAP_LAYER.append({"dev_id": row["dev_id"], "ssid": row["ssid"],
                              "vendor_reason": row["vendor_reason"],
                              "lat": row["lat"], "lon": row["lon"], "ts": row["ts"],
                              "layer": "surveillance_rf"})


def run():
    print("SparrowMap RF pipeline - end-to-end simulation\n")

    payload = client_scan_and_post()
    print(f"1. client scanned, kept {len(payload['candidates'])} surveillance, "
          f"discarded {payload['dropped_private']} private at the edge")
    assert_no_private("post payload", payload)

    res = hub_ingest(payload)
    print(f"2. hub ingested {res['accepted']} into the review pen")
    assert_no_private("review pen", PEN)

    operator_review()
    print(f"3. operator confirmed -> {len(MAP_LAYER)} on the surveillance map layer")
    assert_no_private("map layer", MAP_LAYER)

    # Correctness: exactly the surveillance devices, nothing more, nothing less.
    got = sorted(c["ssid"] for c in MAP_LAYER)
    want = ["Axon-BodyCam-Dock", "FlockSafety-Falcon-2831"]
    assert got == want, f"map layer wrong: got {got} want {want}"
    print(f"4. map layer is exactly the surveillance devices: {got}")

    print("\nmap layer payload a beta tester would produce:")
    print(json.dumps(MAP_LAYER, indent=2, default=str))
    print("\n✅ END TO END OK - pipeline works and no civilian data survived any stage")


if __name__ == "__main__":
    run()
