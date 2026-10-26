"""enhance_keeper: OpenCV-native preview enhancement for keepers.

Ops, applied in this order when requested (``ops`` default = all three):
- ``wb``      cv2.xphoto GrayworldWB (saturation threshold 0.99)
- ``denoise`` cv2.fastNlMeansDenoisingColored(h=3, hColor=3, 7, 21), only when ISO >= 3200 (EXIF)
              or BRISQUE > 50; otherwise skipped and reported in ``ops_skipped``
- ``clahe``   cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) on the L channel of Lab

The work runs on a copy resized to long edge 1024 (the preview size), so NLMeans stays at the
~1024 px cost in the plan. Output is a JPEG q90 preview; originals are never touched.
``delta_brisque`` = BRISQUE(after) - BRISQUE(before); negative means the preview scores better.

BRISQUE uses the LIVE model files via cv2.quality (``_face_defaults.brisque``). When the core
``keepframe.tools.sharpness`` exposes a ``brisque`` helper, that one is used instead so the
whole pipeline shares one implementation.

Storage: the tool does not talk to S3 itself. The planned key is ``preview/<event_id>/<photo_id>.jpg``
(docs/ARCHITECTURE.md "S3 layout"). Pass ``writer(key, jpeg_bytes)`` to store it (the worker passes its S3 put), or
``out_path`` to write locally; otherwise the bytes are returned in ``jpeg`` for the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from keepframe.tools import _face_defaults as fd

ALL_OPS = ("wb", "denoise", "clahe")


def _brisque(img: np.ndarray) -> float:
    try:
        from keepframe.tools.sharpness import brisque as shared_brisque

        return float(shared_brisque(img))
    except (ImportError, AttributeError):
        return fd.brisque(img)


def clahe_l(img: np.ndarray, clip: float, tile: int) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2Lab)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_Lab2BGR)


def grayworld(img: np.ndarray, sat: float) -> np.ndarray:
    wb = cv2.xphoto.createGrayworldWB()
    wb.setSaturationThreshold(sat)
    out: np.ndarray = wb.balanceWhite(img)
    return out


def denoise(img: np.ndarray, t: dict[str, Any]) -> np.ndarray:
    out: np.ndarray = cv2.fastNlMeansDenoisingColored(
        img,
        None,
        h=t["nlmeans_h"],
        hColor=t["nlmeans_h_color"],
        templateWindowSize=t["nlmeans_template"],
        searchWindowSize=t["nlmeans_search"],
    )
    return out


def enhance_keeper(
    img: np.ndarray | None = None,
    ops: list[str] | None = None,
    *,
    event_id: str | None = None,
    photo_id: str | None = None,
    iso: int | float | None = None,
    writer: Callable[[str, bytes], Any] | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Make an enhanced JPEG preview of one keeper.

    Args:
        img: BGR image (``full`` proxy); loaded via proxies when omitted.
        ops: subset of ``["wb", "denoise", "clahe"]`` (default all; unknown names raise ValueError).
        iso: EXIF ISO, gates ``denoise`` together with BRISQUE.
        writer: callable ``(s3_key, jpeg_bytes)`` used to store the preview.
        out_path: write the preview here instead.

    Returns:
        ``{"s3_key", "ops_applied", "ops_skipped", "brisque_before", "brisque_after", "delta_brisque",
        "width", "height", "bytes", "written"[, "jpeg"]}`` (``jpeg`` only when nothing was written).
    """
    t = fd.thresholds()
    requested = list(ALL_OPS if ops is None else ops)
    bad = [o for o in requested if o not in ALL_OPS]
    if bad:
        raise ValueError(f"unknown ops {bad}; allowed {list(ALL_OPS)}")
    image = fd.resolve_image(img, event_id, photo_id)
    work, _ = fd.resize_long_edge(image, int(t["preview_long_edge"]))
    before = _brisque(work)

    applied: list[str] = []
    skipped: list[str] = []
    out = work
    for op in ALL_OPS:  # fixed order regardless of how ops was listed
        if op not in requested:
            continue
        if op == "wb":
            out = grayworld(out, float(t["wb_saturation_threshold"]))
        elif op == "denoise":
            noisy = (iso is not None and float(iso) >= t["denoise_iso_min"]) or before > t[
                "denoise_brisque_min"
            ]
            if not noisy:
                skipped.append("denoise")
                continue
            out = denoise(out, t)
        elif op == "clahe":
            out = clahe_l(out, float(t["clahe_clip_limit"]), int(t["clahe_tile"]))
        applied.append(op)

    after = _brisque(out)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, int(t["preview_jpeg_quality"])])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    data = buf.tobytes()
    key = f"preview/{event_id}/{photo_id}.jpg" if event_id and photo_id else None
    written = False
    if writer is not None:
        if key is None:
            raise ValueError("writer needs event_id and photo_id to build the s3 key")
        writer(key, data)
        written = True
    elif out_path is not None:
        Path(out_path).write_bytes(data)
        written = True

    res: dict[str, Any] = {
        "s3_key": key,
        "ops_applied": applied,
        "ops_skipped": skipped,
        "brisque_before": round(before, 2),
        "brisque_after": round(after, 2),
        "delta_brisque": round(after - before, 2),
        "width": int(out.shape[1]),
        "height": int(out.shape[0]),
        "bytes": len(data),
        "written": written,
    }
    if not written:
        res["jpeg"] = data
    return res
