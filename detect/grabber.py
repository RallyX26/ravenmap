"""Always hand the detector the NEWEST frame, never a queued one.

🚨 MEASURED, 2026-08-08: THE DETECTOR WAS ANALYSING THE PAST.

`run_live.py` claimed in its own docstring that "frames are dropped rather than
queued when the pipeline falls behind", and that a node which queues frames
"drifts further behind reality every second". The claim was right about the
consequence and wrong about the behaviour: nothing was dropping anything.

    tools/probe_latency.py
      stream delivers            20.0 fps
      detector consumes           4.3 fps
      time spent waiting in read() with 220ms of work per frame:  13 ms

Thirteen milliseconds. A frame is ALWAYS already sitting there, which means it
was captured while the previous one was still being processed - so every
`cap.read()` returns something from the backlog rather than from now. The
overlay lag was therefore never just processing time; it was queue depth, which
is why it felt like a second rather than the ~220ms the frame rate suggests.
`CAP_PROP_BUFFERSIZE = 1` does nothing here: the FFMPEG backend ignores it for
an HTTP MJPEG source, and it was measured doing nothing rather than assumed to
work.

This is the same shape as the rest of this codebase's recurring bug: a property
asserted in a comment, never verified, quietly false.

## What this does instead

One thread does nothing but read the stream as fast as it arrives and keep the
last frame it got, throwing away everything in between. The detector asks for
"the current frame" and gets exactly that, with the timestamp of when it was
captured. Latency becomes processing time alone, and it stops depending on how
far behind the pipeline has fallen.

Dropping frames is the correct behaviour for a live node, not a compromise. A
vehicle that was missed is a vehicle; a vehicle reported four seconds late at
the wrong point on the road is a false record.

## The timestamp matters as much as the frame

Every frame carries when it was CAPTURED, so a sighting can be stamped with
when the vehicle actually passed rather than when the pipeline got round to it,
and the overlay can tell the browser how old its boxes truly are instead of
being predicted forward by a guess.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import cv2
import numpy as np


class FrameGrabber:
    """Latest-frame-wins reader for a live stream.

    Usage:
        with FrameGrabber(src) as g:
            frame, captured_at = g.latest()
    """

    def __init__(self, src: str, reopen_after: float = 5.0) -> None:
        self.src = src
        self.reopen_after = reopen_after
        self._frame: Optional[np.ndarray] = None
        self._ts: float = 0.0
        self._seq: int = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Counters, so a node can report honestly how much it is skipping
        # rather than leaving it to be discovered by someone with a stopwatch.
        self.grabbed = 0
        self.dropped = 0
        self.reopens = 0
        self.last_error = ""
        # Sequence number of the last frame handed to the consumer. Per
        # instance, not per class - a shared one would make two grabbers on the
        # same box silently starve each other.
        self.grabbed_seen = 0

    # -- lifecycle ------------------------------------------------------
    def start(self) -> "FrameGrabber":
        cap = cv2.VideoCapture(self.src)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open {self.src}")
        ok, frame = cap.read()
        if not ok:
            cap.release()
            raise RuntimeError(f"opened but no frame from {self.src}")
        with self._lock:
            self._frame, self._ts, self._seq = frame, time.time(), 1
        self.grabbed = 1
        self._thread = threading.Thread(target=self._run, args=(cap,), daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # -- the reader thread ----------------------------------------------
    def _run(self, cap: cv2.VideoCapture) -> None:
        last_ok = time.time()
        while not self._stop.is_set():
            ok, frame = cap.read()
            now = time.time()
            if not ok or frame is None:
                # A stream that has stopped delivering must be reopened, not
                # spun on. A node reporting healthy while seeing nothing is the
                # failure this project keeps finding in itself.
                if now - last_ok > self.reopen_after:
                    self.reopens += 1
                    try:
                        cap.release()
                    except Exception:
                        pass
                    time.sleep(1.0)
                    cap = cv2.VideoCapture(self.src)
                    last_ok = now
                else:
                    time.sleep(0.02)
                continue
            last_ok = now
            with self._lock:
                # Whatever was here is discarded unread. That is the point.
                self._frame, self._ts = frame, now
                self._seq += 1
            self.grabbed += 1
        try:
            cap.release()
        except Exception:
            pass

    # -- consumer -------------------------------------------------------
    def latest(self, wait_for_new: bool = True,
               timeout: float = 2.0) -> tuple[Optional[np.ndarray], float]:
        """The newest frame and when it was captured.

        With `wait_for_new`, blocks briefly until a frame the caller has not
        already processed is available - otherwise a fast consumer would spin
        re-processing the same image.
        """
        deadline = time.time() + timeout
        while True:
            with self._lock:
                if self._frame is not None and (
                        not wait_for_new or self._seq != self.grabbed_seen):
                    skipped = self._seq - self.grabbed_seen - 1
                    if skipped > 0:
                        self.dropped += skipped
                    self.grabbed_seen = self._seq
                    return self._frame, self._ts
            if time.time() > deadline:
                return None, 0.0
            time.sleep(0.005)

    @property
    def age(self) -> float:
        """How old the current frame is, in seconds."""
        with self._lock:
            return time.time() - self._ts if self._ts else float("inf")

    def report(self) -> str:
        total = self.grabbed + self.dropped
        pct = (100.0 * self.dropped / total) if total else 0.0
        return (f"{self.grabbed} frames processed, {self.dropped} skipped "
                f"({pct:.0f}% dropped to stay live)"
                + (f", {self.reopens} reopens" if self.reopens else ""))
