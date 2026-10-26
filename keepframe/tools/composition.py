"""score_composition (optional): saliency-based subject centrality.

``subject_centrality`` in [0, 1]: 1 when the saliency centroid sits on a rule-of-thirds power point,
0 at the farthest possible distance (a frame corner relative to its nearest third point). The
centroid is the intensity-weighted centre of ``cv2.saliency.StaticSaliencyFineGrained`` thresholded
at 0.6 x max, computed on a 1024-px copy.

NIMA is NOT used: there is no NIMA ONNX with a clear licence and a verified numerical match to the
original Keras weights (the idealo weights need a tf2onnx conversion we have not validated), so
``saliency_bbox`` is the bounding box of the largest salient blob after a 15-px closing.

``nima_mean`` / ``nima_std`` are always None and ``nima_available`` is False. The design marks this tool
as a tiebreaker only (weight 0.10, default 0.5 when absent), so centrality alone is enough.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from keepframe.tools._face_defaults import resize_long_edge, resolve_image, thresholds

_THIRDS = [(x, y) for x in (1 / 3, 2 / 3) for y in (1 / 3, 2 / 3)]
_MAX_D = float(np.hypot(1 / 3, 1 / 3))  # corner to nearest power point, normalised coords


def score_composition(
    img: np.ndarray | None = None, *, event_id: str | None = None, photo_id: str | None = None
) -> dict[str, Any]:
    """Return ``{"nima_mean": None, "nima_std": None, "nima_available": False, "subject_centrality",
    "saliency_centroid": [x, y] (0-1), "saliency_bbox": [x, y, w, h] (0-1)}``."""
    t = thresholds()
    small, _ = resize_long_edge(resolve_image(img, event_id, photo_id), 1024)
    ok, sal = cv2.saliency.StaticSaliencyFineGrained_create().computeSaliency(small)  # type: ignore[attr-defined]
    h, w = small.shape[:2]
    res: dict[str, Any] = {
        "nima_mean": None,
        "nima_std": None,
        "nima_available": False,
        "subject_centrality": None,
        "saliency_centroid": None,
        "saliency_bbox": None,
    }
    if not ok or sal is None or float(sal.max()) <= 0:
        return res
    sal = sal.astype(np.float32)
    mask = (sal >= float(t["saliency_threshold_frac"]) * float(sal.max())).astype(np.uint8)
    m = cv2.moments(sal * mask)
    if m["m00"] <= 0:
        return res
    cx, cy = m["m10"] / m["m00"] / w, m["m01"] / m["m00"] / h
    d = min(float(np.hypot(cx - px, cy - py)) for px, py in _THIRDS)
    # bbox of the largest salient blob (the thresholded map is speckled; close it first)
    blobs = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
    if n > 1:
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        bx, by, bw, bh = (int(v) for v in stats[i, :4])
    else:
        bx, by, bw, bh = cv2.boundingRect(mask)
    res.update(
        subject_centrality=round(float(np.clip(1.0 - d / _MAX_D, 0.0, 1.0)), 4),
        saliency_centroid=[round(cx, 4), round(cy, 4)],
        saliency_bbox=[round(bx / w, 4), round(by / h, 4), round(bw / w, 4), round(bh / h, 4)],
    )
    return res
