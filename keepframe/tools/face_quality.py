"""score_face_quality: aligned-face quality per face.

Alignment: ``cv2.FaceRecognizerSF.alignCrop`` (SFace 2021dec) on the YuNet 15-value face row ->
112x112 BGR. It matches the eDifFIQA demo's own 5-point similarity alignment to within 1 grey level
(checked on the test fixtures), so no custom warp is needed.

Quality: eDifFIQA(T) (``ediffiqa_tiny_jun2024.onnx``) via cv2.dnn; RGB, (x/255 - 0.5)/0.5, NCHW.
Higher is better; > 0.5 ~ good per the opencv_zoo README. Plus Laplacian variance of the aligned
crop's grey channel (``lap_var``), comparable across faces because every crop is 112x112.

Identity (``identity=True``): SFace 128-d features; faces are grouped greedily by cosine >= 0.363.
Embeddings are used transiently and never returned or stored (docs/RESPONSIBLE_USE.md). ``group_identities``
offers the same grouping across photos for the optional people filter.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from keepframe.tools._face_defaults import (
    ediffiqa_net,
    face_min_px,
    main_subject,
    resolve_image,
    select_faces,
    sface,
    thresholds,
)


def face_row(face: dict[str, Any]) -> np.ndarray:
    """detect_faces dict -> the 15-float YuNet row that FaceRecognizerSF expects."""
    x, y, w, h = face["bbox_px"]
    lm = np.asarray(face["landmarks5"], dtype=np.float32).reshape(-1)
    score = face.get("score")
    return np.array([x, y, w, h, *lm, 1.0 if score is None else score], dtype=np.float32).reshape(1, 15)


def align(img: np.ndarray, face: dict[str, Any]) -> np.ndarray:
    aligned: np.ndarray = sface().alignCrop(img, face_row(face))
    return aligned


def fiqa(aligned: list[np.ndarray]) -> np.ndarray:
    """eDifFIQA(T) scores for a batch of aligned 112x112 BGR crops."""
    blob = cv2.dnn.blobFromImages(aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
    net = ediffiqa_net()
    net.setInput(blob)
    return np.asarray(net.forward(), dtype=np.float64).reshape(-1)


def lap_var(aligned: np.ndarray) -> float:
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F, ksize=3).var())


def _features(aligned: list[np.ndarray]) -> list[np.ndarray]:
    rec = sface()
    return [rec.feature(a).copy() for a in aligned]


def _greedy_groups(feats: list[np.ndarray], thr: float) -> list[int]:
    """Assign each feature to the first earlier group whose representative matches (cosine >= thr)."""
    rec = sface()
    reps: list[np.ndarray] = []
    labels: list[int] = []
    for f in feats:
        for gi, r in enumerate(reps):
            if rec.match(f, r, cv2.FaceRecognizerSF_FR_COSINE) >= thr:
                labels.append(gi)
                break
        else:
            reps.append(f)
            labels.append(len(reps) - 1)
    return labels


def score_face_quality(
    img: np.ndarray | None = None,
    faces: list[dict[str, Any]] | dict[str, Any] | None = None,
    face_ids: list[str] | None = None,
    *,
    event_id: str | None = None,
    photo_id: str | None = None,
    identity: bool = False,
) -> dict[str, Any]:
    """Face image quality per face.

    Args:
        img: BGR image the face coordinates refer to; loaded via proxies when omitted.
        faces: ``detect_faces`` output (dict or list). Computed when omitted.
        face_ids: restrict to these faces.
        identity: also return an in-photo ``identity_group`` per face (SFace cosine >= 0.363).

    Returns:
        ``{"per_face": [{"face_id", "fiqa", "lap_var", "minPx", "good"[, "identity_group"]}],
        "main_subject_face_id"}``. Faces without landmarks get ``fiqa``/``lap_var`` = None.
    """
    t = thresholds()
    image = resolve_image(img, event_id, photo_id)
    if faces is None:
        from keepframe.tools.faces import detect_faces

        faces = detect_faces(image, event_id=event_id, photo_id=photo_id)
    all_faces = faces["faces"] if isinstance(faces, dict) else faces
    chosen = select_faces(all_faces, face_ids)
    main = main_subject(all_faces, int(t["main_subject_min_px"]))

    usable = [f for f in chosen if f.get("landmarks5") is not None]
    aligned = [align(image, f) for f in usable]
    scores = fiqa(aligned) if aligned else np.zeros(0)
    by_id: dict[str, dict[str, Any]] = {}
    for f, a, s in zip(usable, aligned, scores, strict=True):
        by_id[f["face_id"]] = {
            "fiqa": round(float(s), 4),
            "lap_var": round(lap_var(a), 2),
            "good": bool(s > t["fiqa_good"]),
        }
    if identity and aligned:
        labels = _greedy_groups(_features(aligned), float(t["sface_cosine_same"]))
        for f, g in zip(usable, labels, strict=True):
            by_id[f["face_id"]]["identity_group"] = g

    per_face = []
    for f in chosen:
        row = {"face_id": f["face_id"], "fiqa": None, "lap_var": None, "minPx": face_min_px(f), "good": False}
        row.update(by_id.get(f["face_id"], {}))
        per_face.append(row)
    return {"per_face": per_face, "main_subject_face_id": main["face_id"] if main else None}


def group_identities(
    items: list[tuple[np.ndarray, dict[str, Any]]], threshold: float | None = None
) -> list[int]:
    """Group faces across photos by identity. ``items`` = [(image, face_dict), ...] -> group label per item.

    Features are computed and discarded inside this call; only integer labels leave it.
    """
    thr = float(threshold if threshold is not None else thresholds()["sface_cosine_same"])
    aligned = [align(img, f) for img, f in items]
    return _greedy_groups(_features(aligned), thr) if aligned else []
