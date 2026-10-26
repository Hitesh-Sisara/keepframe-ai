"""detect_faces: face boxes + 5 landmarks for one photo.

Coordinate frame: every ``bbox_px`` / ``landmarks5`` value is in pixels of the image the tool
analysed, i.e. the ``full`` proxy returned by ``keepframe.io.proxies.load_full`` (or the ``img``
argument). Downstream tools (eyes, face_quality, sharpness roi="faces") crop from that same image.

Order of sources:
1. ``reuse_existing`` and frsInfo.status == "indexed" with faces -> frsInfo boxes. Verified on 12
   real events (data agent, 2026-10-05): frsInfo ratio bboxes are relative to the PROCESSED image;
   sourceImageWidth/Height equal the processed width/height and minPx == min(bw*srcW, bh*srcH).
   So a box is ``ratio x processed dims`` (frs_src_w/h, else the record's width/height), then mapped
   into the analysed frame (``px * img_w / processed_w``) — identity for the full proxy, a scale-down
   for p1024. If the processed aspect ratio disagrees with the analysed image by > 2 %, or
   frs_src_w/h disagree with the record's width/height by > 2 %, the reused boxes are distrusted
   and YuNet runs instead.
2. ``reuse_existing`` and Rekognition faces[] -> ratio x processed width/height (same frame logic).
   Kept for completeness; faces[] was empty in every row of the 12 verified events.
3. mfaces == 0 -> no people; detection is skipped. mfaces is None (the norm in real data) means
   unknown, never "skip".
4. cv2.FaceDetectorYN (YuNet 2023mar) on the image resized to long edge 1024. When nothing is found,
   retry at long edge 1600 if mfaces > 0, or if mfaces is unknown and ``detect_retry_on_unknown`` is
   set (default False: the retry costs ~2x the first pass on every face-free frame). frsInfo indexed
   with zero faces is evidence of no face and never triggers a retry.

Reused boxes carry no landmarks, so YuNet always runs on an expanded crop around each reused box
(cheap: a 320-px crop, not the frame) and the best-overlapping detection supplies ``landmarks5``.
If YuNet cannot confirm the face, ``landmarks5`` is ``None`` and eye-state reports ``unknown``.

``landmarks5`` order is YuNet's: right eye, left eye, nose tip, right mouth corner, left mouth
corner (subject's right/left, so the "right eye" is on the image's left). Faces are sorted by area,
largest first, and named f0, f1, ...
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from keepframe.tools._face_defaults import resize_long_edge, resolve_image, thresholds, yunet

Face = dict[str, Any]


def _get(record: Any, key: str, default: Any = None) -> Any:
    if record is None:
        return default
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _ratio_bbox(b: Any) -> tuple[float, float, float, float] | None:
    """Accept [l, t, w, h] ratios or a Rekognition-style {Left, Top, Width, Height} dict."""
    if b is None:
        return None
    if isinstance(b, Mapping):
        keys = {k.lower(): v for k, v in b.items()}
        try:
            return float(keys["left"]), float(keys["top"]), float(keys["width"]), float(keys["height"])
        except KeyError:
            return None
    if len(b) == 4:
        return float(b[0]), float(b[1]), float(b[2]), float(b[3])
    return None


def _clip_box(x: float, y: float, w: float, h: float, img_w: int, img_h: int) -> list[int] | None:
    x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
    x1, y1 = min(img_w, int(round(x + w))), min(img_h, int(round(y + h)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return [x0, y0, x1 - x0, y1 - y0]


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax1, bx1) - max(a[0], b[0]))
    ih = max(0.0, min(ay1, by1) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _landmarks(row: np.ndarray, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> list[list[float]]:
    return [
        [round(float(row[4 + 2 * i]) / scale + dx, 1), round(float(row[5 + 2 * i]) / scale + dy, 1)]
        for i in range(5)
    ]


def _run_yunet(img: np.ndarray, long_edge: int, score: float, upscale: bool = False) -> list[Face]:
    t = thresholds()
    small, s = resize_long_edge(img, long_edge, upscale=upscale)
    det = yunet(float(score), float(t["yunet_nms_threshold"]), int(t["yunet_top_k"]))
    det.setInputSize((small.shape[1], small.shape[0]))
    _, rows = det.detect(small)
    faces: list[Face] = []
    if rows is None:
        return faces
    h, w = img.shape[:2]
    for r in rows:
        box = _clip_box(r[0] / s, r[1] / s, r[2] / s, r[3] / s, w, h)
        if box is None:
            continue
        faces.append(
            {
                "bbox_px": box,
                "score": round(float(r[14]), 4),
                "landmarks5": _landmarks(r, s),
                "source": "yunet",
            }
        )
    return faces


def _refine_landmarks(img: np.ndarray, box: list[int]) -> list[list[float]] | None:
    """Run YuNet on an expanded crop around a reused box; return landmarks of the best match."""
    t = thresholds()
    h, w = img.shape[:2]
    e = float(t["reuse_crop_expand"])
    x0 = max(0, int(box[0] - e * box[2]))
    y0 = max(0, int(box[1] - e * box[3]))
    x1 = min(w, int(box[0] + box[2] * (1 + e)))
    y1 = min(h, int(box[1] + box[3] * (1 + e)))
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    small, s = resize_long_edge(crop, int(t["reuse_crop_long_edge"]), upscale=True)
    det = yunet(float(t["reuse_score_threshold"]), float(t["yunet_nms_threshold"]), int(t["yunet_top_k"]))
    det.setInputSize((small.shape[1], small.shape[0]))
    _, rows = det.detect(small)
    if rows is None:
        return None
    target = [(box[0] - x0) * s, (box[1] - y0) * s, box[2] * s, box[3] * s]
    best = max(rows, key=lambda r: _iou(target, [float(v) for v in r[:4]]))
    if _iou(target, [float(v) for v in best[:4]]) < float(t["reuse_min_iou"]):
        return None
    return _landmarks(best, s, x0, y0)


def _pos(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _same_aspect(w1: float, h1: float, w2: float, h2: float, tol: float) -> bool:
    return abs((w1 / h1) / (w2 / h2) - 1.0) <= tol


def processed_dims(record: Any) -> tuple[float, float] | None:
    """Processed-image (w, h) that frsInfo/Rekognition ratios refer to: frs_src_w/h, else width/height."""
    sw, sh = _pos(_get(record, "frs_src_w")), _pos(_get(record, "frs_src_h"))
    if sw and sh:
        return sw, sh
    pw, ph = _pos(_get(record, "width")), _pos(_get(record, "height"))
    if pw and ph:
        return pw, ph
    return None


def _frame_ok(record: Any, img_w: int, img_h: int, tol: float) -> tuple[float, float] | None:
    """Processed dims if the metadata is consistent with the analysed image, else None (distrust)."""
    dims = processed_dims(record)
    if dims is None:  # no dims at all: ratios can only be taken relative to the analysed image
        return float(img_w), float(img_h)
    sw, sh = _pos(_get(record, "frs_src_w")), _pos(_get(record, "frs_src_h"))
    pw, ph = _pos(_get(record, "width")), _pos(_get(record, "height"))
    if sw and sh and pw and ph and not _same_aspect(sw, sh, pw, ph, tol):
        return None  # frsInfo was computed on a different image than the processed one we hold
    if not _same_aspect(dims[0], dims[1], img_w, img_h, tol):
        return None
    return dims


def _reused_faces(img: np.ndarray, record: Any) -> list[Face] | None:
    """Faces from frsInfo or Rekognition metadata, or None when there is nothing usable to reuse."""
    h, w = img.shape[:2]
    tol = float(thresholds()["reuse_aspect_tol"])
    frs = _get(record, "frs_faces") or []
    if str(_get(record, "frs_status") or "").lower() == "indexed" and frs:
        dims = _frame_ok(record, w, h, tol)
        if dims is not None:
            pw, ph = dims
            out = []
            for f in frs:
                rb = _ratio_bbox(_get(f, "bbox"))
                if rb is None:
                    continue
                # ratio -> processed px (x processed w/h) -> analysed-image px
                xp, yp, wp, hp = rb[0] * pw, rb[1] * ph, rb[2] * pw, rb[3] * ph
                box = _clip_box(xp * w / pw, yp * h / ph, wp * w / pw, hp * h / ph, w, h)
                if box is not None:
                    score = _get(f, "det_score")
                    out.append(
                        {
                            "bbox_px": box,
                            "score": None if score is None else round(float(score), 4),
                            "source": "frsInfo",
                        }
                    )
            if out:
                return out
    rek = _get(record, "rek_faces") or []
    if rek:
        dims = _frame_ok(record, w, h, tol)
        if dims is not None:
            out = []
            for f in rek:
                rb = _ratio_bbox(_get(f, "bbox") or _get(f, "BoundingBox"))
                if rb is None:
                    continue
                box = _clip_box(rb[0] * w, rb[1] * h, rb[2] * w, rb[3] * h, w, h)
                if box is not None:
                    c = _get(f, "confidence", _get(f, "Confidence"))
                    c = None if c is None else float(c) / (100.0 if float(c) > 1.0 else 1.0)
                    out.append(
                        {"bbox_px": box, "score": None if c is None else round(c, 4), "source": "rekognition"}
                    )
            if out:
                return out
    return None


def _retry_allowed(record: Any, mfaces: Any, retry_on_unknown: bool) -> bool:
    if mfaces is not None:
        return int(mfaces) > 0
    frs_says_none = str(_get(record, "frs_status") or "").lower() == "indexed" and not (
        _get(record, "frs_faces") or []
    )
    return retry_on_unknown and not frs_says_none


def _lookup_record(event_id: str, photo_id: str) -> Any:
    """Manifest row for a photo via keepframe.io.manifest (data agent); None if unavailable."""
    try:
        from keepframe.io.manifest import read_manifest
    except ImportError:
        return None
    for rec in read_manifest(event_id):
        if _get(rec, "photo_id") == photo_id:
            return rec
    return None


def detect_faces(
    img: np.ndarray | None = None,
    *,
    event_id: str | None = None,
    photo_id: str | None = None,
    record: Any = None,
    min_score: float | None = None,
    reuse_existing: bool = True,
) -> dict[str, Any]:
    """Detect faces with 5 landmarks in one photo.

    Args:
        img: BGR uint8 image (the ``full`` proxy). If omitted, loaded with
            ``keepframe.io.proxies.load_full(event_id, photo_id)``.
        event_id, photo_id: photo identity, used to load ``img`` and the manifest ``record``.
        record: manifest row (``PhotoRecord`` or dict) with mfaces, frs_status, frs_faces, frs_src_w,
            frs_src_h, rek_faces, width, height. Looked up from the manifest when omitted.
        min_score: YuNet score threshold (default ``faces.yunet_score_threshold`` = 0.9). Reused
            frsInfo/Rekognition faces are trusted as-is and keep their own score.
        reuse_existing: reuse frsInfo / Rekognition boxes when present.

    Returns:
        ``{"faces": [{"face_id", "bbox_px": [x, y, w, h], "score", "landmarks5": [[x, y] x 5] | None,
        "source": "frsInfo" | "rekognition" | "yunet"}], "n_faces"}``
    """
    t = thresholds()
    image = resolve_image(img, event_id, photo_id)
    if record is None and event_id and photo_id:
        record = _lookup_record(event_id, photo_id)
    score = float(min_score if min_score is not None else t["yunet_score_threshold"])
    mfaces = _get(record, "mfaces")

    faces: list[Face] | None = _reused_faces(image, record) if reuse_existing else None
    if faces is not None:
        for f in faces:
            f["landmarks5"] = _refine_landmarks(image, f["bbox_px"])
    elif mfaces is not None and int(mfaces) == 0:
        faces = []
    else:
        faces = _run_yunet(image, int(t["detect_long_edge"]), score)
        if not faces and _retry_allowed(record, mfaces, bool(t["detect_retry_on_unknown"])):
            faces = _run_yunet(image, int(t["detect_retry_long_edge"]), score, upscale=True)

    faces.sort(key=lambda f: f["bbox_px"][2] * f["bbox_px"][3], reverse=True)
    out = [
        {
            "face_id": f"f{i}",
            "bbox_px": f["bbox_px"],
            "score": f["score"],
            "landmarks5": f["landmarks5"],
            "source": f["source"],
        }
        for i, f in enumerate(faces)
    ]
    return {"faces": out, "n_faces": len(out)}
