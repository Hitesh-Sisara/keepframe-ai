"""KeepFrame (keepframe): agentic photo culling on OpenCV 5.

Importing this package verifies the OpenCV build and logs it as one JSON line on stderr
(CloudWatch picks it up). This is the run-time proof that every process uses OpenCV 5
with the contrib modules we depend on.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

import cv2

__version__ = "0.1.0"

REQUIRED_CONTRIB = ("img_hash", "quality", "saliency", "xphoto")
BUILD_INFO_LINES = 40


def log_json(event: str, **fields: Any) -> None:
    """Write one structured JSON log line to stderr."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z", "event": event, **fields}
    sys.stderr.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
    sys.stderr.flush()


# Build-information lines that say which CPU kernels this cv2 runs (docs/CV_CORE.md "Arm acceleration").
HAL_LINE_KEYS = ("Custom HAL:", "Baseline:", "Dispatched code generation:")
_HAL_ENTRY = re.compile(r"([A-Za-z][\w.+-]*) \(ver ([^)]*)\)")


def hal_lines(build_info: str) -> dict[str, str]:
    """`{"Custom HAL": "YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))", "Baseline": ..., ...}` (first match each)."""
    out: dict[str, str] = {}
    for ln in build_info.splitlines():
        key, sep, val = ln.strip().partition(":")
        if sep and f"{key}:" in HAL_LINE_KEYS and key not in out:
            out[key] = " ".join(val.split())
    return out


def hal_short(custom_hal: str | None) -> str:
    """Short HAL label for RUN# `opencv_hal`: "kleidicv-26.03+carotene", "carotene", "ipp", "none".

    KleidiCV first (it is the one that changes on Arm), then the rest in build order, lower-case; carotene's
    placeholder version 0.0.1 is dropped."""
    if not custom_hal or not custom_hal.upper().startswith("YES"):
        return "none"
    entries = _HAL_ENTRY.findall(custom_hal)
    if not entries:
        return "unknown"
    parts = [n.lower() if v in ("", "0.0.1") else f"{n.lower()}-{v}" for n, v in entries]
    parts.sort(key=lambda x: not x.startswith("kleidicv"))
    return "+".join(parts)


def _verify_opencv() -> dict[str, Any]:
    version = cv2.__version__
    if not version.startswith("5."):
        raise ImportError(f"keepframe requires OpenCV 5.x, found {version} at {cv2.__file__}")
    missing = [m for m in REQUIRED_CONTRIB if not hasattr(cv2, m)]
    if missing:
        raise ImportError(f"OpenCV contrib modules missing: {missing} (install opencv-contrib-python-headless)")
    build_info = cv2.getBuildInformation()
    head = [ln.rstrip() for ln in build_info.splitlines() if ln.strip()][:BUILD_INFO_LINES]
    hal = hal_lines(build_info)
    return {
        "opencv_version": version,
        "cv2_file": cv2.__file__,
        "opencv_hal": hal_short(hal.get("Custom HAL")),
        "cv2_custom_hal": hal.get("Custom HAL"),
        "cv2_cpu_baseline": hal.get("Baseline"),
        "cv2_cpu_dispatch": hal.get("Dispatched code generation"),
        "contrib": list(REQUIRED_CONTRIB),
        "font_face": hasattr(cv2, "FontFace"),
        "dnn_engine": os.environ.get("OPENCV_FORCE_DNN_ENGINE"),
        "build_info_head": head,
    }


OPENCV_INFO: dict[str, Any] = _verify_opencv()

if os.environ.get("KEEPFRAME_IMPORT_LOG", "1") != "0":
    log_json("opencv_build", pid=os.getpid(), **OPENCV_INFO)


def opencv_info() -> dict[str, Any]:
    """Version facts for trace/summary headers (without the long build-info block)."""
    return {k: v for k, v in OPENCV_INFO.items() if k != "build_info_head"}
