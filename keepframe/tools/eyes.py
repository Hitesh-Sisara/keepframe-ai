"""score_eyes_open: per-face eye state from YuNet landmarks + an ONNX eye-state classifier via
cv2.dnn (policy P3, docs/REPORT.md §4).

Classifier: OCEC-M (PINTO0309/OCEC, MIT; see models/README.md). Input 1x3x24x40 RGB in [0, 1],
output ``prob_open`` (sigmoid, already a probability). One batched forward per face (2 crops).

Crop: a square of side ``eye_crop_iod_frac`` (0.5) x inter-ocular distance, centred on each eye
landmark, taken from the full-resolution image the landmarks refer to. Picked by an end-to-end
sweep of crop geometries (see docs/CV_FACES.md); the square is then resized to 40x24 like OCEC's
own pipeline.

States: open if both p_open >= 0.6; closed if both <= 0.3; partial otherwise; unknown when the face
has no landmarks, its min side < ``main_subject_min_px`` (40), an eye crop < 16 px, or the face is
dark *and* small (policy 3: don't guess in the dark, fall back to sharpness).

Main subject = largest face with min side >= 40 px (computed over all faces, not just the
requested ``face_ids``). ``any_main_subject_closed`` is True only when that face's state is closed
and its mean closed probability (1 - p_open) is >= 0.8, which is the reject bar in policy 3.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from keepframe.tools._face_defaults import (
    eye_state_net,
    face_min_px,
    main_subject,
    resolve_image,
    select_faces,
    thresholds,
)

EYE_INPUT_WH = (40, 24)  # OCEC input (w, h)


def _square(img: np.ndarray, cx: float, cy: float, side: float) -> np.ndarray | None:
    h, w = img.shape[:2]
    half = side / 2.0
    x0, y0 = int(round(cx - half)), int(round(cy - half))
    x1, y1 = int(round(cx + half)), int(round(cy + half))
    if x1 <= 0 or y1 <= 0 or x0 >= w or y0 >= h:
        return None
    pad = max(0, -x0, -y0, x1 - w, y1 - h)
    if pad:  # eye at the frame edge: replicate border instead of returning a lopsided crop
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        x0, y0, x1, y1 = x0 + pad, y0 + pad, x1 + pad, y1 + pad
    return img[y0:y1, x0:x1]


def eye_crops(img: np.ndarray, landmarks5: list[list[float]], iod_frac: float) -> tuple[Any, Any, float]:
    """(right_eye_crop, left_eye_crop, crop_side_px) — subject's right/left, YuNet order."""
    re, le = np.asarray(landmarks5[0], float), np.asarray(landmarks5[1], float)
    side = float(np.linalg.norm(re - le)) * iod_frac
    return _square(img, re[0], re[1], side), _square(img, le[0], le[1], side), side


def classify_eyes(crops: list[np.ndarray]) -> np.ndarray:
    """p_open for each BGR eye crop (any size), batched through the eye-state net."""
    blob = cv2.dnn.blobFromImages(crops, 1.0 / 255.0, EYE_INPUT_WH, (0, 0, 0), swapRB=True, crop=False)
    net = eye_state_net()
    net.setInput(blob)
    return np.clip(np.asarray(net.forward(), dtype=np.float64).ravel(), 0.0, 1.0)


def _face_dark(img: np.ndarray, box: list[int], dark_l: float) -> bool:
    x, y, w, h = box
    roi = img[y : y + h, x : x + w]
    if roi.size == 0:
        return True
    lum = cv2.cvtColor(roi, cv2.COLOR_BGR2Lab)[:, :, 0]
    return float(lum.mean()) < dark_l


def _state(lp: float, rp: float, t: dict[str, Any]) -> str:
    if lp >= t["eye_open_min_p"] and rp >= t["eye_open_min_p"]:
        return "open"
    if lp <= t["eye_closed_max_p"] and rp <= t["eye_closed_max_p"]:
        return "closed"
    return "partial"


def _confidence(lp: float, rp: float, state: str, t: dict[str, Any]) -> float:
    """How far both eyes are from the state boundary, 0.5 at the boundary, up to 0.99."""
    if state == "open":
        m = min(lp, rp) - t["eye_open_min_p"]
        span = 1.0 - t["eye_open_min_p"]
    elif state == "closed":
        m = t["eye_closed_max_p"] - max(lp, rp)
        span = t["eye_closed_max_p"]
    else:  # partial: confident only when the two eyes disagree strongly (a wink) or both mid
        return 0.5
    return float(np.clip(0.5 + 0.5 * m / max(span, 1e-6), 0.5, 0.99))


def score_eyes_open(
    img: np.ndarray | None = None,
    faces: list[dict[str, Any]] | dict[str, Any] | None = None,
    face_ids: list[str] | None = None,
    *,
    event_id: str | None = None,
    photo_id: str | None = None,
) -> dict[str, Any]:
    """Eye-open probabilities and state per face.

    Args:
        img: BGR image the face coordinates refer to (the ``full`` proxy); loaded via
            ``keepframe.io.proxies.load_full`` when omitted.
        faces: ``detect_faces`` output (dict with ``faces`` or the list). Computed when omitted.
        face_ids: restrict scoring to these faces (main-subject logic still sees every face).

    Returns:
        ``{"per_face": [{"face_id", "left_open_p", "right_open_p", "state": open|closed|partial|unknown,
        "confidence", "min_px", "is_main"}], "any_main_subject_closed", "main_subject_face_id",
        "confidence"}``. Probabilities are ``None`` for ``unknown`` faces.
    """
    t = thresholds()
    image = resolve_image(img, event_id, photo_id)
    if faces is None:
        from keepframe.tools.faces import detect_faces

        faces = detect_faces(image, event_id=event_id, photo_id=photo_id)
    all_faces = faces["faces"] if isinstance(faces, dict) else faces
    main = main_subject(all_faces, int(t["main_subject_min_px"]))
    main_id = main["face_id"] if main else None

    per_face: list[dict[str, Any]] = []
    any_main_closed = False
    for f in select_faces(all_faces, face_ids):
        min_px = face_min_px(f)
        row: dict[str, Any] = {
            "face_id": f["face_id"],
            "left_open_p": None,
            "right_open_p": None,
            "state": "unknown",
            "confidence": 0.0,
            "min_px": min_px,
            "is_main": f["face_id"] == main_id,
        }
        lms = f.get("landmarks5")
        dark_small = min_px < t["dark_face_max_px"] and _face_dark(image, f["bbox_px"], t["dark_face_mean_l"])
        if lms is not None and min_px >= t["main_subject_min_px"] and not dark_small:
            rc, lc, side = eye_crops(image, lms, float(t["eye_crop_iod_frac"]))
            if rc is not None and lc is not None and side >= t["eye_min_crop_px"]:
                rp, lp = (float(p) for p in classify_eyes([rc, lc]))
                state = _state(lp, rp, t)
                row.update(
                    left_open_p=round(lp, 4),
                    right_open_p=round(rp, 4),
                    state=state,
                    confidence=round(_confidence(lp, rp, state, t), 3),
                )
                if row["is_main"] and state == "closed" and (1.0 - (lp + rp) / 2.0) >= 0.8:
                    any_main_closed = True
        per_face.append(row)

    known = [r["confidence"] for r in per_face if r["state"] != "unknown"]
    main_row = next((r for r in per_face if r["is_main"]), None)
    if main_row is not None and main_row["state"] != "unknown":
        conf = main_row["confidence"]
    elif known:
        conf = float(np.mean(known))
    else:
        conf = 0.0
    return {
        "per_face": per_face,
        "any_main_subject_closed": any_main_closed,
        "main_subject_face_id": main_id,
        "confidence": round(float(conf), 3),
    }
