#!/usr/bin/env python3
"""SparrowMap RF scanner - P0 skeleton.

Passively lists nearby Wi-Fi/BLE devices and keeps ONLY the ones that resolve to
a known surveillance / ALPR / agency vendor (Flock and friends), or match a known
surveillance SSID/BLE-name pattern. Everything else - every private phone, watch,
car - is one-way hashed for in-run de-dup and then DROPPED. Civilian MACs are
never written to disk and never transmitted. This mirrors the plate rule: the
private side is destroyed at the edge.

P0 runs with a mock capture so the FILTER and the DISCARD can be proven before any
radio hardware exists. P1 replaces `capture()` with a real kismet/scapy source.

    python rf/rf_scan.py --demo         # dry run against built-in sample frames
    python rf/rf_scan.py --demo --json  # same, machine-readable candidates
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Allowlist: the ONLY devices we keep. Vendor names are matched against the
# manufacturer that an OUI resolves to (via the IEEE registry, oui.txt), and
# SSID/BLE names are matched as case-insensitive substrings/patterns. This list
# is the "database" and grows; nothing here is a guessed MAC prefix.
# ---------------------------------------------------------------------------
SURVEILLANCE_VENDORS = {
    # ALPR / camera / body-cam / fleet-surveillance makers. Matched by NAME
    # against the OUI-resolved manufacturer string, so OEM modules still need the
    # SSID/BLE patterns below to be caught reliably (see PLAN.md open questions).
    "flock safety", "motorola solutions", "axon", "genetec", "verkada",
    "avigilon", "elsag", "leonardo", "utility associates", "digital ally",
    "vigilant solutions", "watchguard",
}

SSID_PATTERNS = [
    # Known/expected broadcast names from surveillance gear. Seeded here; the
    # real values get confirmed by walking known devices in P1.
    "flock", "falcon", "vigilant", "alpr", "axon", "verkada",
]

# 🚔 POLICE-VEHICLE equipment, for CORROBORATING a visual police sighting.
# These ride IN a patrol car (body/dash cams, in-car MDT/router), so hearing one
# next to a camera's police sighting is a strong second signal. This is NOT a
# way to name a cop car from RF alone: the "weak" vendors below also live in
# utility and commercial trucks, so on their own they mean little. Confidence:
#   strong  -> police-dominant (Axon body/dash). A hit is real corroboration.
#   weak    -> in-car comms also found in commercial fleets. Corroborates only
#              WHEN it lines up with a visual police sighting; never alone.
POLICE_EQUIPMENT = {
    "axon": "strong",              # body/dash cameras + docks, police-dominant
    "digital ally": "strong",      # in-car/​body police video
    "watchguard": "strong",        # Motorola police in-car video
    "utility associates": "strong",  # BodyWorn / police in-car
    "sierra wireless": "weak",     # in-car routers - also commercial
    "cradlepoint": "weak",         # in-car routers - also commercial
    "panasonic": "weak",           # Toughbook MDTs - also field service
}
POLICE_SSID_PATTERNS = [
    ("axon", "strong"), ("bodyworn", "strong"), ("watchguard", "strong"),
    ("mdt", "weak"), ("patrol", "weak"), ("cradlepoint", "weak"),
]

# 🛸 DRONES. Since 2023 the FAA requires most drones to broadcast Remote ID over
# 2.4/5 GHz (WiFi Beacon/NAN or Bluetooth) - the exact band this scanner reads.
# A broadcast SSID scan catches the control link and the vendor; full Remote ID
# (the drone's id + operator position) needs monitor mode (an Alfa adapter or an
# ESP32 in promiscuous mode) and is the next hardware step. A drone alone is not
# proof of a POLICE drone - like the in-car gear, it corroborates when it lines
# up with a visual police presence, and agencies fly mostly DJI/Skydio too.
DRONE_VENDORS = {"dji", "szdji", "parrot", "autel", "skydio", "yuneec"}
DRONE_SSID_PATTERNS = ["dji", "mavic", "mini se", "anafi", "autel", "skydio",
                       "drone", "remoteid", "remote id"]


def drone_signal(dev, oui_db) -> str:
    """Return a reason if this looks like a drone, else ''. WiFi/BLE only."""
    vendor = (oui_db.get(oui_of(dev.get("mac", ""))) or "").lower()
    for v in DRONE_VENDORS:
        if v in vendor:
            return f"drone-vendor:{v}"
    name = (dev.get("ssid") or dev.get("name") or "").lower()
    for p in DRONE_SSID_PATTERNS:
        if p in name:
            return f"drone-ssid:{p}"
    return ""


SALT = secrets.token_bytes(16)  # per-process; civilian hashes never persist


def _norm_mac(mac: str) -> str:
    return mac.replace("-", ":").lower().strip()


def oui_of(mac: str) -> str:
    return ":".join(_norm_mac(mac).split(":")[:3])


def load_oui_db() -> dict:
    """OUI(prefix) -> manufacturer name, from a local IEEE oui.txt if present.

    We ship nothing here on purpose: download the public registry once
    (https://standards-oui.ieee.org/oui/oui.txt) to rf/oui.txt. Absent it, vendor
    matching falls back to SSID/BLE patterns only. No network calls at scan time.
    """
    f = HERE / "oui.txt"
    db: dict = {}
    if not f.exists():
        return db
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        # IEEE format: "AC-DE-48   (hex)   Vendor Name"
        if "(hex)" in line:
            parts = line.split("(hex)")
            pfx = parts[0].strip().replace("-", ":").lower()
            db[pfx] = parts[1].strip().lower()
    return db


def is_surveillance(dev: dict, oui_db: dict) -> tuple[bool, str]:
    """Return (keep, reason). Keep ONLY known surveillance devices."""
    vendor = (oui_db.get(oui_of(dev.get("mac", ""))) or "").lower()
    for v in SURVEILLANCE_VENDORS:
        if v in vendor:
            return True, f"vendor:{v}"
    name = (dev.get("ssid") or dev.get("name") or "").lower()
    for p in SSID_PATTERNS:
        if p in name:
            return True, f"ssid:{p}"
    d = drone_signal(dev, oui_db)   # drones are kept too (Remote ID broadcast)
    if d:
        return True, d
    return False, ""


def police_signal(dev: dict, oui_db: dict) -> tuple[str, str]:
    """Is this device POLICE-VEHICLE equipment? Return (confidence, reason).

    confidence is "strong", "weak", or "" (not police gear). Used to CORROBORATE
    a visual police sighting, never to name a cop car from RF alone - see the
    POLICE_EQUIPMENT note. A 'weak' hit is only meaningful once it lines up in
    space and time with a camera's police sighting.
    """
    vendor = (oui_db.get(oui_of(dev.get("mac", ""))) or "").lower()
    for v, conf in POLICE_EQUIPMENT.items():
        if v in vendor:
            return conf, f"police-vendor:{v}"
    name = (dev.get("ssid") or dev.get("name") or "").lower()
    for p, conf in POLICE_SSID_PATTERNS:
        if p in name:
            return conf, f"police-ssid:{p}"
    return "", ""


def to_candidate(dev: dict, reason: str, gps: tuple | None,
                 oui_db: dict | None = None) -> dict:
    """A publishable RF candidate. Note there is NO raw civilian data here."""
    pconf, preason = police_signal(dev, oui_db or {})
    dreason = drone_signal(dev, oui_db or {})
    return {
        "source": "rf",
        "vendor_reason": reason,
        "is_drone": bool(dreason),
        "drone_reason": dreason,
        "ssid": dev.get("ssid") or dev.get("name") or "",
        "band": dev.get("band", ""),
        "rssi": dev.get("rssi"),
        "lat": gps[0] if gps else None,
        "lon": gps[1] if gps else None,
        "ts": dev.get("ts") or time.time(),
        # Police-vehicle equipment tag, for corroborating a visual police
        # sighting. "" means not police gear; "strong"/"weak" per POLICE_EQUIPMENT.
        "police_conf": pconf,
        "police_reason": preason,
        # the surveillance device's own MAC is fair to keep (it is public
        # infrastructure), UNLIKE a civilian's. Kept for de-dup on the map.
        "dev_id": hashlib.sha256(_norm_mac(dev.get("mac", "")).encode()).hexdigest()[:16],
    }


DEMO_FRAMES = [
    {"mac": "AA:BB:CC:11:22:33", "ssid": "FlockSafety-Falcon-2831", "band": "2.4", "rssi": -58},
    {"mac": "DE:AD:BE:EF:00:01", "ssid": "MyHomeWiFi", "band": "5", "rssi": -71},
    {"mac": "12:34:56:78:9A:BC", "name": "iPhone", "band": "ble", "rssi": -47},
    {"mac": "00:1F:5B:AA:BB:CC", "ssid": "Axon-BodyCam-Dock", "band": "2.4", "rssi": -63},
    {"mac": "F0:99:BF:00:11:22", "ssid": "Tesla_Model3", "band": "2.4", "rssi": -66},
]


def host_wifi_scan() -> list[dict]:
    """A plain visible-AP scan on THIS machine. No monitor mode needed, so it
    runs on any Pi, laptop or Android phone - which is what makes a no-hardware
    beta possible: a Flock camera broadcasting an SSID shows up in an ordinary
    scan. Returns [] if the platform's scan tool is unavailable.

    Beta note: this catches BROADCAST SSIDs by pattern. The monitor-mode Alfa
    (P1) is what adds silent/hidden devices and BLE; the data shape is identical.
    """
    import platform
    import re
    import subprocess
    out = []
    sysname = platform.system().lower()
    try:
        if "windows" in sysname:
            raw = subprocess.run(["netsh", "wlan", "show", "networks", "mode=bssid"],
                                 capture_output=True, text=True, timeout=20).stdout
            ssid = None
            for ln in raw.splitlines():
                m = re.match(r"\s*SSID\s+\d+\s*:\s*(.*)", ln)
                if m:
                    ssid = m.group(1).strip(); continue
                mb = re.search(r"BSSID\s+\d+\s*:\s*([0-9A-Fa-f:]{17})", ln)
                if mb and ssid is not None:
                    out.append({"mac": mb.group(1), "ssid": ssid, "band": "wifi", "rssi": None})
        elif "linux" in sysname:
            # Works on a Pi (nmcli) or Android/Termux (termux-wifi-scaninfo).
            try:
                raw = subprocess.run(["nmcli", "-t", "-f", "BSSID,SSID,SIGNAL", "dev", "wifi"],
                                     capture_output=True, text=True, timeout=20).stdout
                for ln in raw.splitlines():
                    parts = ln.replace("\\:", "::").split(":")
                    # nmcli escapes the colons in BSSID; rejoin the first 6 octets
                    if len(parts) >= 7:
                        mac = ":".join(parts[0:6]).replace("::", ":")
                        rest = ":".join(parts[6:]).split(":")
                        out.append({"mac": mac, "ssid": rest[0], "band": "wifi",
                                    "rssi": (int(rest[-1]) - 100) if rest[-1].isdigit() else None})
            except FileNotFoundError:
                import json as _json
                raw = subprocess.run(["termux-wifi-scaninfo"], capture_output=True,
                                     text=True, timeout=20).stdout
                for ap in _json.loads(raw or "[]"):
                    out.append({"mac": ap.get("bssid"), "ssid": ap.get("ssid"),
                                "band": "wifi", "rssi": ap.get("rssi")})
    except Exception:
        return []
    return out


def capture(demo: bool) -> list[dict]:
    """Observed devices as {mac, ssid|name, band, rssi, ts}. Raw data stays on
    the device; the caller filters it immediately and discards the private side.

    demo -> built-in sample frames. Otherwise a real host wifi scan; if that
    yields nothing usable it falls back to the sample so the pipeline is still
    exercised. P1 swaps in the monitor-mode + BLE + GPS capture on the Alfa.
    """
    if demo:
        return list(DEMO_FRAMES)
    live = host_wifi_scan()
    return live if live else list(DEMO_FRAMES)


def read_gps() -> tuple | None:
    # TODO P1: read from gpsd (the u-blox USB dongle). None until then.
    return None


def scan_once(demo: bool) -> tuple[list, int]:
    """One scan -> (surveillance candidates, count of private devices dropped).

    The private side never leaves this function: a dropped device is counted and
    its hash forgotten when the process exits. This is the edge-discard rule.
    """
    oui_db = load_oui_db()
    gps = read_gps()
    kept, dropped = [], 0
    for dev in capture(demo):
        keep, reason = is_surveillance(dev, oui_db)
        if keep:
            kept.append(to_candidate(dev, reason, gps, oui_db))
        else:
            dropped += 1
    return kept, dropped


def post(hub: str, node: str, token: str, candidates: list) -> str:
    """Upload surveillance candidates to the hub's /api/rf. Parks for review;
    never publishes. Fire-and-forget-ish: a failed post just retries next scan."""
    import urllib.request
    body = json.dumps({"node_id": node, "candidates": candidates}).encode()
    req = urllib.request.Request(
        hub.rstrip("/") + "/api/rf", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "sparrowmap-rf-node"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", "replace")[:200]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="dry run on built-in sample frames")
    ap.add_argument("--json", action="store_true", help="print candidates as JSON")
    # Pi / uploading node mode:
    ap.add_argument("--post", action="store_true",
                    help="scan in a loop and upload surveillance candidates to the hub")
    ap.add_argument("--hub", default="https://map.sparrowmap.com", help="hub base URL")
    ap.add_argument("--node", default="", help="your node id from /api/enroll")
    ap.add_argument("--token", default="", help="your node token from /api/enroll")
    ap.add_argument("--interval", type=int, default=30, help="seconds between scans in --post mode")
    args = ap.parse_args()

    if args.post:
        # A Pi node: enroll once (see /rfbeta), then this loops, scans, keeps
        # only surveillance devices, and uploads them. The hub parks every one
        # for human review - nothing this posts reaches the public map on its own.
        if not args.node or not args.token:
            raise SystemExit("--post needs --node and --token (enroll first, see /rfbeta)")
        print(f"RF node {args.node} -> {args.hub}  (scan every {args.interval}s, Ctrl-C to stop)")
        try:
            while True:
                kept, dropped = scan_once(args.demo)
                if kept:
                    try:
                        res = post(args.hub, args.node, args.token, kept)
                        print(f"  uploaded {len(kept)} surveillance device(s), "
                              f"dropped {dropped} private at the edge  [{res}]")
                    except Exception as e:
                        print(f"  scan kept {len(kept)}, upload failed (will retry): {e}")
                else:
                    print(f"  no surveillance devices; {dropped} private ignored")
                time.sleep(max(5, args.interval))
        except KeyboardInterrupt:
            print("\nstopped.")
        return

    kept, dropped = scan_once(args.demo)
    if args.json:
        print(json.dumps({"candidates": kept, "dropped_private": dropped}, indent=2))
        return
    oui_db = load_oui_db()
    print(f"OUI db: {'loaded ' + str(len(oui_db)) + ' entries' if oui_db else 'MISSING (SSID/BLE match only) - see load_oui_db()'}")
    print(f"kept {len(kept)} surveillance, discarded {dropped} private at the edge")
    for c in kept:
        print(f"  + {c['vendor_reason']:22} rssi={c['rssi']} ssid={c['ssid']!r}")
    if not kept:
        print("  (no surveillance devices in this capture)")


if __name__ == "__main__":
    main()
