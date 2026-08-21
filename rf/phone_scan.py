#!/usr/bin/env python3
"""SparrowMap RF phone scanner - single file, no install, for beta testers.

Runs on an Android phone in Termux. Uses only the standard library plus the
termux-api scan command, so a tester copies ONE file and runs it. It scans the
Wi-Fi your phone can already see, keeps only names that look like surveillance
cameras (Flock, ALPR, Axon, etc.), and throws EVERY other network away before
anything is shown or saved. Your neighbours' wifi and everyone's phones never
leave your device - same rule SparrowMap uses for licence plates.

Setup on the phone (one time):
    pkg install termux-api python
    # install the Termux:API app from F-Droid too (it provides the scan)
Run:
    python phone_scan.py            # scan once, print what it found
    python phone_scan.py --watch    # scan every 30s (walk/drive around)
    python phone_scan.py --report   # copy/paste block to send us your finds

It does NOT upload anything on its own. During beta you review what it flags and
tell us, so a wrong guess never touches the public map.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# The only names we keep. Matched case-insensitively as substrings of the SSID.
# This is the whole "is it surveillance" test on a phone (broadcast names only);
# the Pi build adds silent + Bluetooth devices later. Grows as testers confirm
# real hardware names in the field.
SURVEILLANCE_HINTS = [
    "flock", "flocksafety", "falcon",        # Flock Safety ALPR
    "vigilant", "elsag",                     # Motorola/Vigilant ALPR
    "axon",                                  # Axon body/dash
    "verkada", "avigilon", "genetec",        # camera platforms
    "alpr", "lpr", "leonardo",
]


def scan() -> list[dict]:
    """Visible Wi-Fi via termux-api. Returns [] if the tool is missing."""
    try:
        raw = subprocess.run(["termux-wifi-scaninfo"], capture_output=True,
                             text=True, timeout=20).stdout
        return json.loads(raw or "[]")
    except FileNotFoundError:
        print("termux-wifi-scaninfo not found. Install the Termux:API app "
              "(F-Droid) and 'pkg install termux-api'.", file=sys.stderr)
        return []
    except Exception as e:
        print(f"scan error: {e}", file=sys.stderr)
        return []


def is_surveillance(ssid: str) -> str:
    s = (ssid or "").lower()
    for h in SURVEILLANCE_HINTS:
        if h in s:
            return h
    return ""


def one_pass(found: dict) -> tuple[int, int]:
    """Return (kept, discarded). Only kept devices are recorded; the rest are
    counted and forgotten immediately - nothing about them is stored."""
    aps = scan()
    kept = 0
    for ap in aps:
        ssid = ap.get("ssid") or ""
        hint = is_surveillance(ssid)
        if hint:
            kept += 1
            key = ap.get("bssid") or ssid
            if key not in found:
                found[key] = {"ssid": ssid, "match": hint,
                              "rssi": ap.get("rssi"), "ts": int(time.time())}
                print(f"  \U0001F6A8 SURVEILLANCE: {ssid!r}  (matched '{hint}', "
                      f"signal {ap.get('rssi')})")
    discarded = len(aps) - kept
    return kept, discarded


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watch", action="store_true", help="scan every 30s until you stop it")
    ap.add_argument("--report", action="store_true", help="print a block to send us")
    a = ap.parse_args()

    found: dict = {}
    if a.watch:
        print("watching... walk or drive around. Ctrl-C to stop.\n")
        try:
            while True:
                k, d = one_pass(found)
                print(f"  ...{len(found)} surveillance device(s) so far, "
                      f"{d} private networks ignored this pass")
                time.sleep(30)
        except KeyboardInterrupt:
            print("\nstopped.")
    else:
        k, d = one_pass(found)
        print(f"\nscan done: {len(found)} surveillance device(s), "
              f"{d} private networks ignored (and forgotten).")

    if a.report or found:
        print("\n--- copy everything below and send it to SparrowMap ---")
        print(json.dumps({"source": "rf_phone_beta",
                          "devices": list(found.values())}, indent=2))
        print("--- end ---")
        print("\nyou can add roughly where you were (a cross street is fine). "
              "we review before anything goes on the map.")


if __name__ == "__main__":
    main()
