"""Make the detector ask the OS not to throttle it.

    from detect import priority
    priority.claim("detector")

WHY THIS EXISTS

Reported from the desk: "my detector is stalling". It was not stalled - it was
beating, posting, and running - it was being given a fraction of a CPU.

Measured 2026-08-19: every run_live and camctl process was at **BelowNormal**
priority while ffmpeg, software-encoding the radio stream, sat at 266% of a core
at Normal. Nothing in the project asked for BelowNormal. Windows did it, because
the camera stack had been relaunched with a minimized/hidden window after a
power cut, and Windows 11 treats a hidden background process as a candidate for
EcoQoS "efficiency mode" - reduced clock, parked on efficiency cores.

🚨 SETTING IT FROM OUTSIDE DOES NOT HOLD. `Start Camera Node.bat` restarts the
detector in a loop, and each new process inherits the priority of the launcher
it was spawned from - so a priority set by hand is gone at the next restart, and
the stall comes back looking like a new bug. The process has to claim it for
itself, every time it starts.

⚠️ ABOVE NORMAL, NOT HIGH. HIGH on Windows outranks most of the desktop and can
starve input handling and audio - on the machine that is also his workstation
and is streaming a radio station. ABOVE_NORMAL is enough to beat ffmpeg and the
batch jobs without making the desktop feel broken.

Everything here is best-effort: a machine that refuses the call still runs the
detector, just slower. Never raise.
"""
from __future__ import annotations

import sys

ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000

# SetProcessInformation(ProcessPowerThrottling = 4). Setting ControlMask with a
# cleared StateMask means "explicitly opt OUT of throttling" rather than
# "leave it to the system", which is the difference that matters here.
_PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
_ProcessPowerThrottling = 4


def claim(label: str = "process", quiet: bool = False) -> bool:
    """Raise priority and opt out of efficiency mode. Windows only, best effort."""
    if not sys.platform.startswith("win"):
        return False
    ok = False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # ⚠️ DECLARE THE SIGNATURES. Without these ctypes defaults the return
        # type to C int, which truncates a 64-bit HANDLE - GetCurrentProcess
        # returns the pseudo-handle (HANDLE)-1, the truncated value is rejected,
        # and SetPriorityClass silently fails while the code looks correct.
        # Measured: claim() returned False and GetPriorityClass read 0x0.
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        h = k32.GetCurrentProcess()

        if k32.SetPriorityClass(h, ABOVE_NORMAL_PRIORITY_CLASS):
            ok = True

        # Opt out of EcoQoS. Absent on older Windows, hence the guard: a
        # missing export must not take the detector down.
        class _PPTS(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG),
                        ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]

        try:
            k32.SetProcessInformation.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.SetProcessInformation.restype = wintypes.BOOL
            st = _PPTS(1, _PROCESS_POWER_THROTTLING_EXECUTION_SPEED, 0)
            k32.SetProcessInformation(h, _ProcessPowerThrottling,
                                      ctypes.byref(st), ctypes.sizeof(st))
        except Exception:
            pass

        if ok and not quiet:
            print(f"  [priority] {label}: above-normal, throttling off",
                  flush=True)
    except Exception as exc:                       # pragma: no cover
        if not quiet:
            print(f"  [priority] {label}: could not raise ({exc})", flush=True)
    return ok
