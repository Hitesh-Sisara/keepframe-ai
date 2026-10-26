"""Shared defaults and helpers for the face tools (faces, eyes, face_quality, composition,
enhance, publish_blur).

Thresholds default here; the ``faces:`` section of ``config/thresholds.yaml`` overrides them. ``thresholds()`` merges,
in increasing priority: the defaults below, the ``faces:`` section returned by
``keepframe.config.thresholds()`` (the shared loader), else the ``faces:`` section of
``config/thresholds.yaml`` read directly. Tools never hardcode numbers; they read ``thresholds()``.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]

FACE_DEFAULTS: dict[str, Any] = {
    # detect_faces (docs/REPORT.md §3)
    "yunet_score_threshold": 0.9,
    "yunet_nms_threshold": 0.3,
    "yunet_top_k": 5000,
    "detect_long_edge": 1024,
    "detect_retry_long_edge": 1600,
    "detect_retry_on_unknown": False,  # retry at 1600 when mfaces is None (real data: always None)
    # landmark refinement on reused frsInfo / Rekognition boxes
    "reuse_crop_expand": 0.3,  # fraction of bbox w/h added on every side
    "reuse_crop_long_edge": 320,  # crop is resized to this long edge before YuNet
    "reuse_score_threshold": 0.5,  # a face is known to be there; accept weaker YuNet hits
    "reuse_min_iou": 0.3,
    "reuse_aspect_tol": 0.02,  # processed dims vs analysed image (and frs_src vs width/height)
    # score_eyes_open
    "eye_crop_iod_frac": 0.5,  # square side = frac * inter-ocular distance
    "eye_open_min_p": 0.6,
    "eye_closed_max_p": 0.3,
    "eye_min_crop_px": 16,
    "main_subject_min_px": 40,
    "dark_face_mean_l": 40.0,  # Lab L (0-255) of the face box; darker AND small -> unknown
    "dark_face_max_px": 80,
    # score_face_quality
    "fiqa_good": 0.5,
    "sface_cosine_same": 0.363,
    # enhance_keeper
    "clahe_clip_limit": 2.0,
    "clahe_tile": 8,
    "denoise_iso_min": 3200,
    "denoise_brisque_min": 50.0,
    "nlmeans_h": 3,
    "nlmeans_h_color": 3,
    "nlmeans_template": 7,
    "nlmeans_search": 21,
    "wb_saturation_threshold": 0.99,
    "preview_long_edge": 1024,
    "preview_jpeg_quality": 90,
    "brisque_long_edge": 1024,
    # publish_blur (privacy: favour recall, so a lower score threshold than culling)
    "publish_blur_score_threshold": 0.6,
    "publish_blur_expand": 0.25,
    # score_composition
    "saliency_threshold_frac": 0.6,
}

MODEL_FILES = {
    "yunet": "face_detection_yunet_2023mar.onnx",
    "sface": "face_recognition_sface_2021dec.onnx",
    "ediffiqa": "ediffiqa_tiny_jun2024.onnx",
    "eye_state": "ocec_m.onnx",
    "brisque_model": "brisque_model_live.yml",
    "brisque_range": "brisque_range_live.yml",
}


def thresholds() -> dict[str, Any]:
    """Return the merged ``faces`` threshold dict (defaults < config/thresholds.yaml ``faces:``)."""
    merged = dict(FACE_DEFAULTS)
    section: Any = None
    try:
        from keepframe.config import thresholds as _shared  # the shared loader

        section = (_shared() or {}).get("faces")
    except Exception:  # noqa: BLE001 - loader not landed yet or file missing
        path = REPO_ROOT / "config" / "thresholds.yaml"
        if path.exists():
            import yaml

            section = (yaml.safe_load(path.read_text()) or {}).get("faces")
    if isinstance(section, dict):
        merged.update(section)
    return merged


def models_dir() -> Path:
    """Model directory: ``$CULL_MODELS_DIR``, else ``/opt/models`` (container), else ``<repo>/models``."""
    env = os.environ.get("CULL_MODELS_DIR")
    if env:
        return Path(env)
    opt = Path("/opt/models")
    if (opt / MODEL_FILES["yunet"]).exists():
        return opt
    return REPO_ROOT / "models"


def model_path(key: str) -> str:
    p = models_dir() / MODEL_FILES[key]
    if not p.exists():
        raise FileNotFoundError(f"{p} missing; run `python scripts/fetch_models.py` (or set CULL_MODELS_DIR)")
    return str(p)


def models_available() -> bool:
    return all((models_dir() / f).exists() for f in MODEL_FILES.values())


# --- cached model objects (one per process; worker processes are single-threaded, by design) ---


@functools.lru_cache(maxsize=4)
def yunet(score_threshold: float, nms_threshold: float, top_k: int) -> cv2.FaceDetectorYN:
    return cv2.FaceDetectorYN.create(
        model_path("yunet"), "", (320, 320), score_threshold, nms_threshold, top_k
    )


@functools.lru_cache(maxsize=1)
def sface() -> cv2.FaceRecognizerSF:
    return cv2.FaceRecognizerSF.create(model_path("sface"), "")


@functools.lru_cache(maxsize=1)
def ediffiqa_net() -> cv2.dnn.Net:
    return cv2.dnn.readNet(model_path("ediffiqa"))


@functools.lru_cache(maxsize=1)
def eye_state_net() -> cv2.dnn.Net:
    return cv2.dnn.readNet(model_path("eye_state"))


# --- image helpers ---


def resolve_image(img: np.ndarray | None, event_id: str | None, photo_id: str | None) -> np.ndarray:
    """Return ``img`` or load the ``full`` proxy via ``keepframe.io.proxies``."""
    if img is not None:
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img
    if not (event_id and photo_id):
        raise ValueError("pass img (BGR ndarray) or both event_id and photo_id")
    from keepframe.io.proxies import load_full  # lazy import

    loaded: np.ndarray = load_full(event_id, photo_id)
    return loaded


def resize_long_edge(img: np.ndarray, long_edge: int, upscale: bool = False) -> tuple[np.ndarray, float]:
    """Resize so max(h, w) == long_edge. Returns (image, scale). Never upscales unless asked."""
    h, w = img.shape[:2]
    s = long_edge / max(h, w)
    if s >= 1.0 and not upscale:
        return img, 1.0
    interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=interp), s


def brisque(img: np.ndarray, long_edge: int | None = None) -> float:
    """BRISQUE (0-100, lower is better) with the LIVE model files, on a long-edge-capped copy."""
    le = int(long_edge or thresholds()["brisque_long_edge"])
    small, _ = resize_long_edge(img, le)
    brisque_fn = cv2.quality.QualityBRISQUE_compute  # type: ignore[attr-defined]  # contrib, not in stubs
    score = brisque_fn(small, model_path("brisque_model"), model_path("brisque_range"))
    return float(score[0])


def face_area(face: dict[str, Any]) -> float:
    b = face["bbox_px"]
    return float(b[2] * b[3])


def face_min_px(face: dict[str, Any]) -> int:
    b = face["bbox_px"]
    return int(min(b[2], b[3]))


def main_subject(faces: list[dict[str, Any]], min_px: int) -> dict[str, Any] | None:
    """Largest face whose min side is >= min_px (docs/REPORT.md §4 policy 3), else None."""
    big = [f for f in faces if face_min_px(f) >= min_px]
    return max(big, key=face_area) if big else None


def select_faces(faces: list[dict[str, Any]], face_ids: list[str] | None) -> list[dict[str, Any]]:
    if face_ids is None:
        return list(faces)
    wanted = set(face_ids)
    return [f for f in faces if f["face_id"] in wanted]
