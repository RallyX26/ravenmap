"""SparrowMap - one frame source for any camera.

The honest state of consumer cameras: there is no single way in. So rather than
support "IP cameras", SparrowMap supports five ACCESS METHODS behind one interface,
and the setup wizard's whole job is working out which one a given device
answers to.

    usb       a webcam plugged into the machine. Always works. No network.
    rtsp      a stream URL. The best case: one line, full frame rate.
    onvif     a standard that TELLS you the RTSP URL. Best case for setup,
              because nothing has to be guessed.
    mjpeg     an HTTP multipart stream. Old cameras and many doorbells.
    snapshot  poll a still-image URL at a few frames a second. Ugly, universal,
              and completely sufficient for reading plates on a street where a
              car is in view for several seconds.
    file      a video file. For testing and for replaying evidence.

WHY SNAPSHOT MATTERS MORE THAN IT LOOKS
Plate reading does not need 30 fps. A car crossing a 40 m field of view at
30 mph is visible for about three seconds, and the pipeline votes across every
read it gets. Two frames a second off a JPEG URL is ten chances at the plate.
That single fallback drags a large tail of otherwise-unusable cameras into the
network, which matters more for adoption than any amount of stream quality.

WHAT IS DELIBERATELY NOT SUPPORTED
Cloud-only cameras: Ring, Nest, Wyze, Roku (Wyze hardware), most Tuya devices.
Their video never exists on your network at all, it goes to the vendor and
comes back. Bridging them means impersonating the phone app against the
vendor's API, which breaks on their schedule, not ours. The honest answer to a
volunteer holding one is "that camera will not work, here is a $30 one that
will" - not a fragile bridge that dies silently three weeks in.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional
from urllib.parse import quote, urlparse, urlunparse

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# URL patterns. This table is the difference between "supports cameras" and
# "supports YOUR camera". Ordered roughly by how common the device is.
# {u} and {p} are substituted with url-encoded credentials.
# ---------------------------------------------------------------------------

RTSP_PATHS = [
    # generic / unbranded Chinese SoCs - by far the biggest population
    "/stream1", "/stream2", "/live", "/live/ch0", "/live/ch00_0",
    "/11", "/12", "/onvif1", "/onvif2", "/video1", "/video.h264",
    "/ch0_0.264", "/ch01.264", "/0", "/1",
    # Hikvision and its many clones
    "/Streaming/Channels/101", "/Streaming/Channels/102",
    "/h264/ch1/main/av_stream", "/h264/ch1/sub/av_stream",
    # Dahua / Amcrest / Lorex
    "/cam/realmonitor?channel=1&subtype=0",
    "/cam/realmonitor?channel=1&subtype=1",
    # Reolink
    "/h264Preview_01_main", "/h264Preview_01_sub",
    # TP-Link Tapo / Kasa
    "/stream1", "/stream2",
    # Foscam
    "/videoMain", "/videoSub",
    # Axis
    "/axis-media/media.amp",
    # Ubiquiti
    "/s0", "/s1",
    # Wyze with the old RTSP firmware
    "/live",
]

SNAPSHOT_PATHS = [
    "/snapshot.cgi", "/cgi-bin/snapshot.cgi", "/tmpfs/auto.jpg",
    "/tmpfs/snap.jpg", "/image/jpeg.cgi", "/snap.jpeg", "/snapshot.jpg",
    "/onvif-http/snapshot?Profile_1", "/cgi-bin/api.cgi?cmd=Snap&channel=0",
    "/ISAPI/Streaming/channels/101/picture",
    "/cgi-bin/hi3510/snap.cgi?&-getstream",
    "/axis-cgi/jpg/image.cgi",
]

MJPEG_PATHS = [
    "/videostream.cgi", "/video.mjpg", "/mjpg/video.mjpg",
    "/cgi-bin/mjpg/video.cgi?channel=0&subtype=1", "/video",
]

RTSP_PORTS = [554, 8554, 10554, 5543, 555]


def with_creds(url: str, user: str = "", password: str = "") -> str:
    """Put credentials into a URL's authority, url-encoding them.

    Camera passwords routinely contain '@' and ':' and people paste them in
    raw, which silently produces a URL that parses into the wrong host.
    """
    if not user:
        return url
    p = urlparse(url)
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    auth = quote(user, safe="") + (":" + quote(password, safe="") if password else "")
    return urlunparse((p.scheme, f"{auth}@{host}", p.path, p.params, p.query,
                       p.fragment))


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------

@dataclass
class CameraSource:
    """A camera, however it is reached. Iterate it for frames.

    Reconnection is built in rather than left to the caller. A porch camera on
    household wifi will drop out, and a node that needs a human to restart it
    is a node that is offline the first time it matters.
    """

    kind: str                       # usb | rtsp | onvif | mjpeg | snapshot | file
    target: str = ""                # url, device index, or path
    user: str = ""
    password: str = ""
    fps: float = 0.0                # 0 = as fast as the source gives
    snapshot_interval: float = 0.5  # seconds, snapshot mode only
    name: str = "camera"
    # USB only. 0 x 0 means "negotiate the highest the device will give".
    #
    # ⚠️ THIS DEFAULT IS NOT COSMETIC. OpenCV opens a webcam at 640x480
    # regardless of what the hardware can do. the reference camera silently opened
    # at VGA and actually supports 2560x1472 - a 4x linear gain, which is the
    # entire difference between a plate being 12 px wide and unreadable, and
    # 48 px wide and readable. A node that quietly runs at VGA looks like it is
    # working and never reads a single plate.
    width: int = 0
    height: int = 0
    # USB only. UVC zoom, 100 = none. Many webcams accept this and change
    # framing, but that does NOT prove it helps: a crop-and-upscale zoom adds
    # pixels and no information, and is strictly worse than cropping in
    # software afterwards because it throws away the surrounding view for
    # nothing. Only worth setting if a daylight A/B shows real extra detail
    # (tools/zoom_check.py). Leave at 0 to not touch the control.
    zoom: int = 0

    _cap: Optional[cv2.VideoCapture] = field(default=None, repr=False)
    _fail: int = field(default=0, repr=False)

    # -- opening --------------------------------------------------------
    def stream_url(self) -> str:
        return with_creds(self.target, self.user, self.password)

    def open(self) -> bool:
        self.close()
        if self.kind == "usb":
            # CAP_DSHOW on Windows: the default MSMF backend takes several
            # seconds to open and often fails outright on cheap webcams.
            idx = int(self.target or 0)
            # MSMF first: measured 29.8 fps at 1080p against DSHOW's 5.0 on the
            # same camera. DSHOW ignores FOURCC and stays in an uncompressed
            # mode. See camctl for the full measurement.
            for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW):
                self._cap = cv2.VideoCapture(idx, backend)
                if self._cap.isOpened():
                    ok, _ = self._cap.read()
                    if ok:
                        break
                self._cap.release()
            if self._cap.isOpened():
                self._negotiate_resolution()
                if self.zoom:
                    self._cap.set(cv2.CAP_PROP_ZOOM, float(self.zoom))
        elif self.kind == "file":
            self._cap = cv2.VideoCapture(self.target)
        elif self.kind in ("rtsp", "onvif", "mjpeg"):
            self._cap = cv2.VideoCapture(self.stream_url(), cv2.CAP_FFMPEG)
            # Keep the buffer at one frame. The default queues, so a slow
            # consumer ends up reading minutes-old video and the map lies
            # about when a car went past.
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
        elif self.kind == "snapshot":
            return True                      # nothing to hold open
        else:
            raise ValueError(f"unknown camera kind: {self.kind}")
        return bool(self._cap and self._cap.isOpened())

    # Descending, so the first one that sticks is the best the device offers.
    _LADDER = [(3840, 2160), (2592, 1944), (2560, 1440), (1920, 1080),
               (1600, 1200), (1280, 960), (1280, 720), (1024, 576), (640, 480)]

    def _negotiate_resolution(self) -> None:
        """Ask for the largest frame the device will actually deliver.

        The camera does not have to honour the request and does not report what
        it supports, so the only way to find out is to set a size, read back
        what you got, and pull a real frame to confirm it works. Several cheap
        webcams accept a set() and then hand back garbage or nothing.
        """
        cap = self._cap
        # MJPG before anything else. OpenCV's default YUY2 is uncompressed, so
        # 1080p needs 4.15 MB per frame and USB 2.0 throttles it to ~5 fps.
        # MJPG has the camera compress in hardware and the same resolution runs
        # at 30. DSHOW ignores a FOURCC set AFTER the resolution.
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        wants = ([(self.width, self.height)] if self.width and self.height
                 else self._LADDER)
        for w, h in wants:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            ok, frame = cap.read()
            if ok and frame is not None and frame.size:
                gw, gh = int(cap.get(3)), int(cap.get(4))
                if gw >= 640 and gh >= 480:
                    if (gw, gh) != (w, h):
                        print(f"[{self.name}] asked {w}x{h}, got {gw}x{gh}")
                    return

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    # -- reading --------------------------------------------------------
    def _read_snapshot(self) -> Optional[np.ndarray]:
        import urllib.request
        req = urllib.request.Request(self.stream_url(),
                                     headers={"User-Agent": "SparrowMap/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                buf = np.frombuffer(r.read(), dtype=np.uint8)
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            return None

    def read(self) -> Optional[np.ndarray]:
        if self.kind == "snapshot":
            return self._read_snapshot()
        if self._cap is None and not self.open():
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def frames(self, max_frames: int = 0) -> Iterator[np.ndarray]:
        """Yield frames forever, reconnecting on failure with backoff."""
        n = 0
        min_dt = 1.0 / self.fps if self.fps > 0 else 0.0
        last = 0.0
        while max_frames == 0 or n < max_frames:
            if self.kind == "snapshot":
                time.sleep(max(0.0, self.snapshot_interval - (time.time() - last)))
            frame = self.read()
            last = time.time()

            if frame is None:
                self._fail += 1
                if self._fail > 3:
                    wait = min(30.0, 2.0 ** min(self._fail - 3, 4))
                    print(f"[{self.name}] no frame, reconnecting in {wait:.0f}s")
                    time.sleep(wait)
                    self.open()
                continue

            self._fail = 0
            if min_dt:
                sleep = min_dt - (time.time() - last)
                if sleep > 0:
                    time.sleep(sleep)
            n += 1
            yield frame

    # -- proof ----------------------------------------------------------
    def test(self, timeout: float = 8.0) -> tuple[bool, str, Optional[np.ndarray]]:
        """Try to get one real frame. This is the ONLY definition of 'works'.

        Setup tools must validate by decoding a frame, never by whether a URL
        looked plausible or a port was open. Cameras answer on 554 and then
        refuse to stream; they accept an RTSP URL and return black; they hand
        back a 200 with an HTML error page instead of a JPEG. A decoded frame
        with non-degenerate content is the only thing that settles it.
        """
        t0 = time.time()
        try:
            if self.kind != "snapshot" and not self.open():
                return False, "could not open", None
            while time.time() - t0 < timeout:
                f = self.read()
                if f is not None and f.size and float(f.std()) > 1.0:
                    return True, f"{f.shape[1]}x{f.shape[0]}", f
                time.sleep(0.2)
            return False, "opened but no usable frame", None
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}", None
        finally:
            if self.kind != "snapshot":
                self.close()


# ---------------------------------------------------------------------------
# Local webcams
# ---------------------------------------------------------------------------

def list_usb(max_index: int = 6) -> list[dict]:
    """Enumerate attached webcams by opening each index and grabbing a frame."""
    out = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, f = cap.read()
            if ok and f is not None:
                out.append({"index": i, "width": int(cap.get(3)),
                            "height": int(cap.get(4)),
                            "fps": round(cap.get(cv2.CAP_PROP_FPS), 1)})
        cap.release()
    return out
