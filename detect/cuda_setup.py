"""Let onnxruntime find a CUDA runtime, without dragging torch into the node.

🚨 THE DETECTOR HAD BEEN RUNNING ON CPU THE WHOLE TIME, AND SAID SO QUIETLY.

Every run printed this and carried on:

    Failed to create CUDAExecutionProvider. Require cuDNN 9.* and CUDA 13.*
    Error loading "onnxruntime_providers_cuda.dll" which depends on
    "cublasLt64_13.dll" which is missing.

...as a warning, in red, above a line that then reported
`providers: ['CUDAExecutionProvider', 'CPUExecutionProvider']` - because
`get_available_providers()` lists what was COMPILED IN, not what actually
LOADED. So the node looked GPU-accelerated in its own logs while running every
frame on an i9. That is this project's recurring failure once more: a component
reporting success while doing something else entirely, and the check that was
supposed to catch it measuring the wrong thing.

## What was actually wrong

    onnxruntime-gpu 1.27  ->  built for CUDA 13  (wants cublasLt64_13.dll)
    torch 2.5.1+cu121     ->  ships CUDA 12      (has  cublasLt64_12.dll)

A straight version mismatch. There is no CUDA toolkit installed on this box;
the only CUDA runtime present is the one **torch bundles**, and torch is
already used for CLIP, already works, and is pinned because the ACE-Step and
LLM-training work depends on it. So the fix is to move onnxruntime to a CUDA 12
build (1.22.0) and point it at the runtime that is already here:

    pip install onnxruntime-gpu==1.22.0

## Why this file exists rather than `import torch` at the top of pipeline.py

SparrowMap's design rule is that the DETECTION PATH CONTAINS NO TORCH - the ONNX
models are chosen so a Raspberry-Pi-class node can run them. Importing torch to
borrow its DLLs would quietly break that and add ~2GB to a node that does not
need it.

So this finds torch's library folder WITHOUT importing torch (importlib can
locate a package without executing it) and adds it to the DLL search path. On a
machine with no torch it does nothing at all and onnxruntime falls back to CPU,
which is the right answer on a Pi anyway.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_done = False


def enable_cuda() -> str:
    """Put a CUDA runtime on the DLL search path. Call BEFORE onnxruntime.

    Returns a short human-readable note about what happened, so a node can say
    plainly which processor it is about to use instead of leaving it to be
    discovered later with a stopwatch.
    """
    global _done
    if _done or sys.platform != "win32":
        return "already configured" if _done else "not windows; nothing to do"
    _done = True

    spec = importlib.util.find_spec("torch")
    if spec is None or not spec.origin:
        return "no torch present; onnxruntime will use whatever is on PATH"

    lib = Path(spec.origin).parent / "lib"
    if not lib.is_dir():
        return f"torch found but no lib dir at {lib}"

    have = {p.name.lower() for p in lib.glob("*.dll")}
    # The set onnxruntime's CUDA 12 provider actually needs. Checked rather
    # than assumed, because "the DLLs are probably there" is how this went
    # unnoticed in the first place.
    need = {"cublas64_12.dll", "cublaslt64_12.dll", "cudart64_12.dll",
            "cufft64_11.dll", "curand64_10.dll", "cudnn64_9.dll"}
    missing = need - have
    try:
        os.add_dll_directory(str(lib))
    except OSError as exc:
        return f"could not add {lib} to the DLL path: {exc}"

    if missing:
        return (f"added {lib.name}, but missing {sorted(missing)} - "
                f"CUDA will probably still fail")
    return f"CUDA 12 runtime from torch ({lib})"


def report(session_providers: list[str]) -> str:
    """Describe what a session is REALLY using.

    ⚠️ Use the providers of an actual InferenceSession, never
    `ort.get_available_providers()`. The latter lists compiled-in providers and
    will happily claim CUDA on a machine that cannot load it - which is exactly
    the sentence this node printed to itself for its entire life so far.
    """
    if "CUDAExecutionProvider" in session_providers:
        return "GPU (CUDA)"
    if "TensorrtExecutionProvider" in session_providers:
        return "GPU (TensorRT)"
    return "CPU"
