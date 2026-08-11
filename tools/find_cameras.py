"""Find IP cameras on the local network and work out whether SparrowMap can use them.

The question that decides everything is not the brand name, it is whether the
camera will hand a frame to something that is not its own app. Three tiers, best
to worst:

  RTSP (554/8554)  -> usable today, `cv2.VideoCapture(rtsp://...)` and done.
  ONVIF (80/8000)  -> usable, discover the stream URI then fall back to RTSP.
  cloud only       -> not usable without either a firmware swap or pointing a
                      second camera at the screen, which is silly.

Roku's smart-home cameras are Wyze hardware in a Roku shell, and both are
cloud-first, so they are the likely disappointments here. Generic pan/tilt
cameras are a coin flip: the ones built on common Chinese SoCs usually speak
RTSP, the Tuya-based ones usually do not.

    python tools\find_cameras.py 192.168.1.0/24
"""

from __future__ import annotations

import ipaddress
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

# port -> what it would mean
PORTS = {
    554:  "RTSP",
    8554: "RTSP (alt)",
    80:   "HTTP / ONVIF",
    8000: "ONVIF / Hik",
    8080: "HTTP (alt)",
    2020: "Tuya (cloud-locked)",
    6668: "Tuya local",
    88:   "ONVIF (alt)",
    443:  "HTTPS",
    9000: "app protocol",
}

TIMEOUT = 0.45


def probe(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def banner(host: str, port: int) -> str:
    """One RTSP OPTIONS request tells you the server software for free."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.2)
            s.connect((host, port))
            s.sendall(f"OPTIONS rtsp://{host}:{port}/ RTSP/1.0\r\n"
                      f"CSeq: 1\r\n\r\n".encode())
            return s.recv(512).decode("utf-8", "replace").strip().replace("\r\n", " | ")
    except OSError:
        return ""


def scan_host(host: str) -> tuple[str, dict]:
    open_ports = {}
    for p in PORTS:
        if probe(host, p):
            open_ports[p] = PORTS[p]
    return host, open_ports


def main() -> None:
    net = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.0/24"
    hosts = [str(h) for h in ipaddress.ip_network(net, strict=False).hosts()]
    print(f"scanning {net} ({len(hosts)} hosts) for camera ports...\n")

    found = []
    with ThreadPoolExecutor(max_workers=128) as ex:
        for host, ports in ex.map(scan_host, hosts):
            if ports:
                found.append((host, ports))

    if not found:
        print("nothing responded. Cameras may be powered off, on a different "
              "subnet, or on a guest network isolated from this one.")
        return

    for host, ports in sorted(found, key=lambda t: tuple(map(int, t[0].split(".")))):
        try:
            name = socket.gethostbyaddr(host)[0]
        except OSError:
            name = ""
        plist = ", ".join(f"{p} {n}" for p, n in sorted(ports.items()))
        print(f"{host:<16} {name:<28} {plist}")

        for rp in (554, 8554):
            if rp in ports:
                b = banner(host, rp)
                if b:
                    print(f"                 RTSP says: {b[:150]}")

    print("\nverdict:")
    for host, ports in found:
        if 554 in ports or 8554 in ports:
            print(f"  {host}: RTSP open -> USABLE as a SparrowMap node today")
        elif 2020 in ports or 6668 in ports:
            print(f"  {host}: Tuya ports -> almost certainly cloud-locked")
        elif 80 in ports or 8000 in ports or 88 in ports:
            print(f"  {host}: web/ONVIF only -> try ONVIF discovery before giving up")


if __name__ == "__main__":
    main()
