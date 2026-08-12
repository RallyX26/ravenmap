"""Measure whether a given machine can run a SparrowMap camera node.

    python tools/bench_node.py                  # the standard run, ~3 minutes
    python tools/bench_node.py --seconds 600    # a proper thermal soak
    python tools/bench_node.py --image shot.jpg # time against a real photo
    python tools/bench_node.py --camera 0       # add a capture test

WHAT THIS IS FOR
"Will a Raspberry Pi run this?" cannot be answered from a spec sheet, because
the node is not one workload. It is three, with wildly different appetites:

    vehicle detection   ONNX, no torch, runs on anything      (rf-detr-small-512)
    plate read          ONNX, no torch, only on found vehicles (yolo-v9-s + cct-s-v2)
    government call     CLIP + trained head, needs torch, ~2.5 GB installed

A board can pass the first two and fail the third, and that is a perfectly
useful node - it reports vehicles and plates and lets a hub or a desktop head
make the government call. So this script times the three SEPARATELY and reports
which tier the machine reached, rather than printing pass or fail.

🚨 THIS MEASURES SPEED, NOT ACCURACY.
CNN inference cost is set by input size, not picture content, so the default
synthetic frame gives a comparable number on a machine with no test data.
Detection ACCURACY on that frame is meaningless and is not reported. Pass
--image with a real road photo if you want the plate stage timed against real
work; the numbers move a little because the plate and OCR stages only run on
vehicles that were actually found.

🌡️ WHY IT RUNS FOR MINUTES RATHER THAN TIMING ONE FRAME
On a passively cooled board the first frame is not the frame you care about.
A Pi 5 with no heatsink will hold full clocks for roughly a minute and then
throttle, so a burst benchmark reports a number the machine cannot sustain for
an afternoon - which is the only duration that matters for a camera in a
window. The soak reports FIRST versus LAST and reads the SoC's own throttle
flags, so thermal loss shows up as a number instead of as a mystery months
later.

⚠️ MEDIANS, NOT MEANS. One scheduler hiccup or one background apt job skews a
mean badly on a small board. The median is what the machine actually does.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The floor is not invented. Snapshot-mode cameras polling at 2 fps give a
# vehicle roughly ten chances at a plate during a three-second pass, and that
# has been enough to be useful in this project. Below it, a car crosses the
# watched span between two frames and the node misses passes entirely.
FPS_FLOOR = 2.0          # usable for one camera
FPS_COMFORTABLE = 5.0    # room for a second camera or a busier road

FRAME_W, FRAME_H = 1280, 720
WARMUP = 3               # ONNX allocates arenas on the first run; never time it


# ---------------------------------------------------------------- environment

def _mem_total_mb() -> int | None:
    """Total RAM. Reads /proc on Linux so a Pi needs no extra dependency."""
    try:
        if Path("/proc/meminfo").exists():
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    try:                                             # Windows, stdlib only
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _MS()
        st.dwLength = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return int(st.ullTotalPhys // (1024 * 1024))
    except Exception:
        return None


def _board_model() -> str | None:
    """The Pi's own name for itself, e.g. 'Raspberry Pi 5 Model B Rev 1.0'.

    platform.machine() only says aarch64, which does not distinguish a Pi 4
    from a Pi 5 from an Orange Pi - and those are exactly the machines this
    benchmark exists to tell apart.
    """
    for p in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            return Path(p).read_text().strip("\x00").strip() or None
        except Exception:
            continue
    return None


def _soc_temp_c() -> float | None:
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return int(raw) / 1000.0
    except Exception:
        return None


def _throttled_flags() -> str | None:
    """Ask the Pi firmware whether it throttled. It keeps sticky bits.

    Bit 0 = under-voltage NOW, bit 1 = arm frequency capped, bit 2 = throttled,
    bit 3 = soft temperature limit; bits 16-19 are the same four LATCHED since
    boot. Latched under-voltage is the single most common cause of a Pi that
    "runs slow" and it is a power supply, not the software.
    """
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return out.split("=", 1)[1] if "=" in out else out or None
    except Exception:
        return None


def describe_machine() -> dict:
    import onnxruntime as ort
    return {
        "board": _board_model(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_mb": _mem_total_mb(),
        "python": platform.python_version(),
        "onnxruntime": ort.__version__,
        "ort_providers": ort.get_available_providers(),
        "temp_start_c": _soc_temp_c(),
        "throttled_start": _throttled_flags(),
    }


# --------------------------------------------------------------- test material

def synthetic_frame(w: int = FRAME_W, h: int = FRAME_H) -> np.ndarray:
    """A deterministic, low-clutter scene.

    Pure noise would be a worse choice than it looks: a noisy frame produces
    spurious low-confidence boxes, and NMS cost scales with box count, so the
    benchmark would partly be measuring the noise. A smooth gradient with a few
    solid blocks keeps the detector's post-processing in the same regime as a
    real road scene while staying identical on every machine.
    """
    y = np.linspace(40, 150, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 30, w, dtype=np.float32)[None, :]
    base = np.clip(y + x, 0, 255)
    img = np.repeat(base[:, :, None], 3, axis=2)
    img[:, :, 0] *= 0.85                       # a mild colour cast, as a lens has
    rng = np.random.default_rng(1)             # seeded: same frame everywhere
    for cx, cy, bw, bh in ((int(w * .25), int(h * .60), 260, 150),
                           (int(w * .62), int(h * .55), 210, 120)):
        img[cy:cy + bh, cx:cx + bw] = rng.integers(30, 200, (bh, bw, 3))
    img += rng.normal(0, 2.0, img.shape)       # a plausible sensor noise floor
    return np.clip(img, 0, 255).astype(np.uint8)


def load_frame(path: str | None) -> tuple[np.ndarray, str]:
    if not path:
        return synthetic_frame(), "synthetic"
    import cv2
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"could not read --image {path}")
    if img.shape[1] != FRAME_W:                # compare like with like
        s = FRAME_W / img.shape[1]
        img = cv2.resize(img, (FRAME_W, int(img.shape[0] * s)))
    return img, Path(path).name


# ------------------------------------------------------------------- the stages

def _timed(fn, frame, repeats: int) -> list[float]:
    """Warm up, then return per-call milliseconds."""
    for _ in range(WARMUP):
        fn(frame)
    out = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(frame)
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def stage_vehicle(frame, repeats):
    from detect.pipeline import VehicleTracker
    t0 = time.perf_counter()
    trk = VehicleTracker()
    load_s = time.perf_counter() - t0
    ms = _timed(trk.track, frame, repeats)
    return {"load_s": round(load_s, 1), "ms": ms}, trk


def stage_plate(frame, repeats):
    from detect.pipeline import PlateReader
    t0 = time.perf_counter()
    pr = PlateReader()
    load_s = time.perf_counter() - t0
    h, w = frame.shape[:2]
    # A vehicle-sized box in the lower middle, where one sits on a real road
    # frame. The plate stage is only ever handed a vehicle box, so timing it on
    # a full frame would flatter it.
    vbox = (int(w * .30), int(h * .45), int(w * .70), int(h * .95))
    ms = _timed(lambda f: pr.read(f, vbox), frame, repeats)
    return {"load_s": round(load_s, 1), "ms": ms, "device": pr.device}


def stage_clip(frame, repeats):
    """The government-vehicle call. Absent torch, this is the tier a Pi skips.

    A missing torch is not an error here. It is the expected result on a small
    board and it is reported as a tier, not a failure - the node still runs.
    """
    try:
        import torch  # noqa: F401
    except Exception as e:
        return {"skipped": f"torch not installed ({type(e).__name__})"}
    try:
        from detect.vehicle_id import VehicleIdentifier
        t0 = time.perf_counter()
        vid = VehicleIdentifier()
        load_s = time.perf_counter() - t0
        h, w = frame.shape[:2]
        crop = frame[int(h * .45):int(h * .95), int(w * .30):int(w * .70)]
        ms = _timed(vid.classify, crop, max(3, repeats // 3))
        return {"load_s": round(load_s, 1), "ms": ms}
    except Exception as e:
        return {"skipped": f"{type(e).__name__}: {e}"}


def soak(trk, frame, seconds: float) -> dict:
    """Run the detector flat out and report what the machine LOSES over time."""
    ms, t_end = [], time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        t0 = time.perf_counter()
        trk.track(frame)
        ms.append((time.perf_counter() - t0) * 1000.0)
    if len(ms) < 20:
        return {"frames": len(ms), "note": "too few frames to judge drift"}
    k = max(5, len(ms) // 10)
    first, last = statistics.median(ms[:k]), statistics.median(ms[-k:])
    return {
        "frames": len(ms),
        "first_ms": round(first, 1),
        "last_ms": round(last, 1),
        "slowdown_pct": round((last - first) / first * 100.0, 1),
        "temp_end_c": _soc_temp_c(),
        "throttled_end": _throttled_flags(),
    }


def capture_test(index: int, seconds: float = 6.0) -> dict:
    """Optional: can this machine actually GRAB frames as fast as it thinks?

    Kept separate from the model timings on purpose. "The board is too slow"
    and "the USB camera is negotiating an uncompressed mode" are different
    faults with the same symptom, and merging them into one number is how a
    day gets lost. On Windows this is also the MSMF-versus-DSHOW question,
    which is worth a factor of six on the same camera.
    """
    import cv2
    backend = cv2.CAP_MSMF if os.name == "nt" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return {"error": f"could not open camera {index}"}
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    for _ in range(10):
        cap.read()
    n, black, t0 = 0, 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        ok, f = cap.read()
        if ok and f is not None:
            n += 1
            # Identically zero mean AND std means no sensor data, which is what
            # a second opener gets on Windows. A genuinely dark room still has
            # noise, so this distinguishes "dark" from "someone else has it".
            if n <= 3 and float(f.mean()) == 0.0 and float(f.std()) == 0.0:
                black += 1
    dt = time.perf_counter() - t0
    w, h = int(cap.get(3)), int(cap.get(4))
    cap.release()
    time.sleep(1.0)                            # never churn a USB capture graph
    r = {"fps": round(n / dt, 1), "resolution": f"{w}x{h}"}
    if black:
        r["warning"] = "black frames - another program is holding this camera"
    return r


# ------------------------------------------------------------------- reporting

def _med(d: dict) -> float | None:
    ms = d.get("ms")
    return round(statistics.median(ms), 1) if ms else None


def verdict(veh_ms: float | None, plate_ms: float | None, clip: dict) -> tuple[str, str]:
    if veh_ms is None:
        return "UNKNOWN", "the vehicle stage did not run"
    fps = 1000.0 / veh_ms
    has_clip = "ms" in clip
    if fps < 1.0:
        return "NOT USABLE", (f"{fps:.1f} detections/sec is below one per second; "
                              "vehicles will cross the frame unseen")
    if fps < FPS_FLOOR:
        return "MARGINAL", (f"{fps:.1f} detections/sec is under the {FPS_FLOOR:g}/sec floor. "
                            "Workable only on a quiet road, and only for one camera")
    tier = ("FULL NODE" if has_clip else "DETECT-AND-SHIP NODE")
    why = (f"{fps:.1f} detections/sec"
           + ("" if fps >= FPS_COMFORTABLE else ", enough for one camera on a quiet road")
           + ("; classifies government vehicles locally" if has_clip else
              "; no local government-vehicle call, so crops go to a hub or desktop head"))
    return tier, why


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=15,
                    help="timed calls per stage after warm-up")
    ap.add_argument("--seconds", type=float, default=120.0,
                    help="thermal soak length; 0 skips it. 600 for a real answer")
    ap.add_argument("--image", help="a real road photo to time against")
    ap.add_argument("--camera", type=int, help="also test capture on this index")
    ap.add_argument("--json", help="write the full result to this file")
    ap.add_argument("--label", default="", help="a name for this machine in the output")
    a = ap.parse_args()

    print("SparrowMap node benchmark")
    print("=" * 58)
    env = describe_machine()
    env["label"] = a.label
    for k in ("board", "machine", "cpu_count", "ram_mb", "python", "onnxruntime"):
        if env.get(k) is not None:
            print(f"  {k:<14} {env[k]}")
    if env.get("throttled_start") and env["throttled_start"] not in ("0x0",):
        print(f"  ⚠️ throttled     {env['throttled_start']}  (see notes: check the PSU)")
    print()

    frame, src = load_frame(a.image)
    print(f"  frame          {frame.shape[1]}x{frame.shape[0]} ({src})")
    if src == "synthetic":
        print("                 timing only - accuracy on this frame means nothing")
    print()

    print("  loading models (first run downloads them; that is not timed)")
    veh, trk = stage_vehicle(frame, a.repeats)
    print(f"  vehicle detect   {_med(veh):>7.1f} ms   (load {veh['load_s']}s)")
    plate = stage_plate(frame, a.repeats)
    print(f"  plate + OCR      {_med(plate):>7.1f} ms   (load {plate['load_s']}s, {plate['device']})")
    clip = stage_clip(frame, a.repeats)
    if "ms" in clip:
        print(f"  CLIP + head      {_med(clip):>7.1f} ms   (load {clip['load_s']}s)")
    else:
        print(f"  CLIP + head      SKIPPED  - {clip['skipped']}")
    print()

    soaked = {}
    if a.seconds > 0:
        print(f"  thermal soak: running the detector for {a.seconds:.0f}s ...")
        soaked = soak(trk, frame, a.seconds)
        if "slowdown_pct" in soaked:
            t = soaked.get("temp_end_c")
            print(f"  first {soaked['first_ms']} ms  ->  last {soaked['last_ms']} ms"
                  f"   ({soaked['slowdown_pct']:+.1f}%)"
                  + (f"   {t:.0f}°C" if t else ""))
            if soaked["slowdown_pct"] > 15:
                print("  ⚠️ it slowed under sustained load: add a heatsink or a fan,"
                      " and use a supply that meets the board's rating")
        print()

    cap = {}
    if a.camera is not None:
        print(f"  capture test on camera {a.camera} ...")
        cap = capture_test(a.camera)
        print(f"  {json.dumps(cap)}")
        print()

    # The sustained number is the honest one wherever we have it.
    veh_ms = soaked.get("last_ms") or _med(veh)
    tier, why = verdict(veh_ms, _med(plate), clip)
    print("=" * 58)
    print(f"  VERDICT: {tier}")
    print(f"  {why}")
    print()

    result = {"env": env, "frame_source": src,
              "vehicle_ms": _med(veh), "plate_ms": _med(plate),
              "clip_ms": _med(clip), "clip_skipped": clip.get("skipped"),
              "soak": soaked, "capture": cap,
              "sustained_fps": round(1000.0 / veh_ms, 2) if veh_ms else None,
              "tier": tier}
    line = json.dumps(result, default=str)
    if a.json:
        Path(a.json).write_text(line, encoding="utf-8")
        print(f"  written to {a.json}")
    print("  paste this line when reporting your result:")
    print(f"  {line}")


if __name__ == "__main__":
    main()
