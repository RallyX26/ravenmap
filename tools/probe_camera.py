"""Deep-probe one camera: what is it, and will it give us a frame?

Runs when the quick subnet scan finds something on port 80 but nothing on 554,
which is the ambiguous case - it could be an ONVIF camera serving RTSP on a
non-standard port, or a cloud-locked device whose web page is just a setup
screen.

    python tools\probe_camera.py 192.168.1.100 [more ips...]
"""

from __future__ import annotations

import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

# Everything a consumer camera might plausibly answer on.
PORTS = [80, 81, 443, 554, 555, 8000, 8080, 8081, 8443, 8554, 8899, 8999,
         9000, 10554, 34567, 37777, 5000, 5001, 88, 2020, 6668, 6667, 1935,
         3702, 8090, 8181, 7001, 49152, 5543]


def open_ports(host, ports, timeout=0.6):
    def t(p):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return p if s.connect_ex((host, p)) == 0 else None
        except OSError:
            return None
    with ThreadPoolExecutor(max_workers=64) as ex:
        return [p for p in ex.map(t, ports) if p]


def http_head(host, port, tls=False):
    """Grab status line, Server header and <title> - the cheapest fingerprint."""
    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(3.0)
        raw.connect((host, port))
        s = raw
        if tls:
            import ssl
            ctx = ssl._create_unverified_context()
            s = ctx.wrap_socket(raw, server_hostname=host)
        s.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n"
                  f"User-Agent: SparrowProbe/0.1\r\nConnection: close\r\n\r\n".encode())
        buf = b""
        while len(buf) < 24000:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        text = buf.decode("utf-8", "replace")
        status = text.split("\r\n", 1)[0]
        server = ""
        m = re.search(r"^Server:\s*(.+)$", text, re.I | re.M)
        if m:
            server = m.group(1).strip()
        auth = ""
        m = re.search(r"^WWW-Authenticate:\s*(.+)$", text, re.I | re.M)
        if m:
            auth = m.group(1).strip()
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        if m:
            title = " ".join(m.group(1).split())[:90]
        return status, server, auth, title, len(buf)
    except OSError as e:
        return f"(error: {e})", "", "", "", 0


def rtsp_options(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.5)
            s.connect((host, port))
            s.sendall(f"OPTIONS rtsp://{host}:{port}/ RTSP/1.0\r\nCSeq: 1\r\n"
                      f"User-Agent: SparrowProbe\r\n\r\n".encode())
            return s.recv(1024).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def onvif_probe(host):
    """Ask the ONVIF device service who it is. No auth needed for GetCapabilities
    on many cameras, and even a fault reply proves ONVIF is present."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        '<s:Body><GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl">'
        '<Category>All</Category></GetCapabilities></s:Body></s:Envelope>')
    paths = ["/onvif/device_service", "/onvif/Device", "/onvif/services",
             "/onvif/device", "/Device"]
    import urllib.error
    import urllib.request
    for port in (80, 8000, 8080, 8899, 2020):
        for path in paths:
            url = f"http://{host}:{port}{path}"
            req = urllib.request.Request(
                url, data=body.encode(),
                headers={"Content-Type": 'application/soap+xml; charset=utf-8'})
            try:
                with urllib.request.urlopen(req, timeout=3) as r:
                    return url, r.status, r.read(3000).decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                payload = e.read(2000).decode("utf-8", "replace")
                if "onvif" in payload.lower() or e.code in (400, 401, 500):
                    return url, e.code, payload
            except (urllib.error.URLError, OSError, ValueError):
                continue
    return None, None, ""


def main():
    hosts = sys.argv[1:] or ["192.168.1.100", "192.168.1.101"]
    for host in hosts:
        print("=" * 72)
        print(host)
        print("=" * 72)

        ports = open_ports(host, PORTS)
        print(f"open ports: {ports}\n")

        for p in ports:
            if p in (80, 81, 8080, 8081, 8000, 8899, 5000, 8090, 8181, 7001):
                st, srv, auth, title, n = http_head(host, p)
                print(f"  :{p} HTTP  {st}")
                if srv:
                    print(f"        Server: {srv}")
                if auth:
                    print(f"        Auth:   {auth}")
                if title:
                    print(f"        Title:  {title}")
            if p in (443, 8443):
                st, srv, auth, title, n = http_head(host, p, tls=True)
                print(f"  :{p} HTTPS {st}  {srv} {title}")
            if p in (554, 8554, 10554, 5543, 555):
                r = rtsp_options(host, p)
                print(f"  :{p} RTSP  {r.splitlines()[0] if r else '(no reply)'}")
                for line in r.splitlines()[1:]:
                    if line.strip():
                        print(f"        {line.strip()}")

        url, code, payload = onvif_probe(host)
        if url:
            print(f"\n  ONVIF: {url} -> HTTP {code}")
            for tag in ("Manufacturer", "Model", "FirmwareVersion", "XAddr",
                        "SerialNumber", "HardwareId"):
                for m in re.finditer(rf"<[^>]*{tag}>([^<]+)<", payload):
                    print(f"        {tag}: {m.group(1)}")
                    break
            if not re.search(r"XAddr|Manufacturer", payload):
                print(f"        (reply body, first 300 chars)\n        {payload[:300]}")
        else:
            print("\n  ONVIF: no device service answered")
        print()


if __name__ == "__main__":
    main()
