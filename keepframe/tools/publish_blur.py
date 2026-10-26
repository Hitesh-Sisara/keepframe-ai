"""blur_faces_for_publication: anonymise faces before any public asset is exported (docs/RESPONSIBLE_USE.md).

Used for report figures, video frames and README screenshots; never for culling. Detection favours
recall: YuNet runs at a lower score threshold (0.6), at two scales (1024 and 1600 long edge, merged),
and every box is expanded 25 % before a strong cv2.GaussianBlur (kernel ~ box size, odd) is
applied inside an elliptical mask. Extra ``boxes`` (e.g. from frsInfo) can be passed and are
blurred too. Callers must still eyeball the output: a missed face is a privacy failure.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from keepframe.tools._face_defaults import resize_long_edge, thresholds, yunet


def _detect(img: np.ndarray, score: float) -> list[list[float]]:
    t = thresholds()
    boxes: list[list[float]] = []
    for le in (int(t["detect_long_edge"]), int(t["detect_retry_long_edge"])):
        small, s = resize_long_edge(img, le)
        det = yunet(score, float(t["yunet_nms_threshold"]), int(t["yunet_top_k"]))
        det.setInputSize((small.shape[1], small.shape[0]))
        _, rows = det.detect(small)
        if rows is not None:
            boxes += [[float(r[0]) / s, float(r[1]) / s, float(r[2]) / s, float(r[3]) / s] for r in rows]
        if s == 1.0:  # image already <= this long edge; the second scale would be identical
            break
    return boxes


def blur_faces_for_publication(
    img: np.ndarray, boxes: list[list[float]] | None = None, score_threshold: float | None = None
) -> dict[str, Any]:
    """Return ``{"image": blurred BGR copy, "n_blurred", "boxes_px": [[x, y, w, h], ...]}``."""
    t = thresholds()
    thr = float(score_threshold if score_threshold is not None else t["publish_blur_score_threshold"])
    allb = _detect(img, thr) + [list(map(float, b)) for b in (boxes or [])]
    out = img.copy()
    h, w = img.shape[:2]
    e = float(t["publish_blur_expand"])
    done: list[list[int]] = []
    for x, y, bw, bh in allb:
        x0, y0 = max(0, int(x - e * bw)), max(0, int(y - e * bh))
        x1, y1 = min(w, int(x + bw * (1 + e))), min(h, int(y + bh * (1 + e)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        roi = out[y0:y1, x0:x1]
        k = max(31, (max(x1 - x0, y1 - y0) // 2) | 1)
        blurred = cv2.GaussianBlur(roi, (k, k), 0)
        mask = np.zeros(roi.shape[:2], np.uint8)
        cv2.ellipse(mask, ((roi.shape[1] / 2, roi.shape[0] / 2), (roi.shape[1], roi.shape[0]), 0), 255, -1)
        roi[mask > 0] = blurred[mask > 0]
        done.append([x0, y0, x1 - x0, y1 - y0])
    return {"image": out, "n_blurred": len(done), "boxes_px": done}
