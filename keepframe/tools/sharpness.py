"""score_sharpness tool (docs/REPORT.md §3).

Metrics on the ROI of the 1024-px gray proxy:
  lap_var    cv2.Laplacian(roi, CV_64F, ksize=3).var()
  tenengrad  mean(gx^2 + gy^2) with 3x3 Sobel
  fft_hf     ROI resized to 500 px long edge; cv2.dft; zero a 60-px centre window of the shifted spectrum;
             inverse; mean(20*log|recon|)  (PyImageSearch method, done with cv2.dft instead of np.fft)
  brisque    cv2.quality.QualityBRISQUE on the full p1024 (LIVE model), only when votes disagree
  motion     max(Ex,Ey)/min(Ex,Ey) of directional Sobel energies > 2.5 -> motion blur, else focus blur

Label rule (documented choice; the plan says "majority of votes"):
  * lap vote is 3-state (blurry < 60 <= borderline <= 120 < sharp). A borderline lap vote always yields
    label=borderline: that is the trigger for policy 2 (re-score on faces/original).
  * otherwise lap and fft votes; if they agree that is the label. If they disagree, BRISQUE breaks
    the tie (majority of three). Without BRISQUE (model files absent) a disagreement is borderline.
  * confidence = w * clip(|lap_var - 90| / 90, 0.3, 0.95) + (1 - w) * vote_agreement.
"""

from __future__ import annotations

import hashlib
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from keepframe.config import model_path, section
from keepframe.schemas import SharpnessResult

# BRISQUE LIVE model. config/models.lock is authoritative and pins opencv_contrib@1275703c;
# these entries pin the same sha256 at tag 5.0.0 and are used only by ensure_brisque_models() (tests /
# dev boxes without scripts/fetch_models.py).
BRISQUE_FILES: dict[str, tuple[str, str]] = {
    "brisque_model_live.yml": (
        "https://raw.githubusercontent.com/opencv/opencv_contrib/5.0.0/modules/quality/samples/brisque_model_live.yml",
        "4c44c7eec9e5139830c0bb0e4a044c9cd351ba5bbc9e3798beca3243e2782ff4",
    ),
    "brisque_range_live.yml": (
        "https://raw.githubusercontent.com/opencv/opencv_contrib/5.0.0/modules/quality/samples/brisque_range_live.yml",
        "a18427f4f7ad087524bd0d63389649a3899b51ca6d44a4d5f8d8171f1f914a9c",
    ),
}

_brisque_lock = threading.Lock()
_brisque_obj: Any = None
_brisque_checked = False


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ensure_brisque_models(dest_dir: Path | None = None, timeout: float = 30.0) -> bool:
    """Download + verify the two BRISQUE yml files if missing (build time / tests). Returns availability."""
    ok = True
    for name, (url, sha) in BRISQUE_FILES.items():
        p = (dest_dir / name) if dest_dir else model_path(name)
        if p.is_file() and _sha256(p) == sha:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - pinned https URL
                data = r.read()
        except OSError:
            ok = False
            continue
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"sha256 mismatch for {name}")
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
    reset_brisque()
    return ok


def reset_brisque() -> None:
    global _brisque_obj, _brisque_checked
    with _brisque_lock:
        _brisque_obj, _brisque_checked = None, False


def _brisque() -> Any:
    """Per-process QualityBRISQUE (parsing the 0.5 MB model yml once), or None if files are absent."""
    global _brisque_obj, _brisque_checked
    with _brisque_lock:
        if not _brisque_checked:
            _brisque_checked = True
            m, r = model_path("brisque_model_live.yml"), model_path("brisque_range_live.yml")
            if m.is_file() and r.is_file():
                _brisque_obj = cv2.quality.QualityBRISQUE.create(str(m), str(r))
        return _brisque_obj


def brisque_available() -> bool:
    return _brisque() is not None


def brisque_score(img: np.ndarray) -> float | None:
    """BRISQUE of `img` as given (callers pass the 1024-px proxy), or None if the model is absent."""
    q = _brisque()
    if q is None:
        return None
    return round(float(q.compute(img)[0]), 3)


def brisque(img: np.ndarray) -> float:
    """BRISQUE (0-100, lower is better) with the LIVE model files from config/models.lock.

    Shared implementation for enhance_keeper. The image is capped at proxy.long_edge (1024)
    with INTER_AREA first, so scores are comparable with score_sharpness. Raises FileNotFoundError when
    the model files are missing (run scripts/fetch_models.py or set CULL_MODELS_DIR).
    """
    from keepframe.io.images import resize_long_edge

    small = resize_long_edge(img, int(section("proxy")["long_edge"]))
    v = brisque_score(small)
    if v is None:
        raise FileNotFoundError(f"BRISQUE model files missing under {model_path('brisque_model_live.yml').parent}")
    return float(v)


# --------------------------------------------------------------------------- metrics


def fft_hf(gray: np.ndarray, size: int = 500, window: int = 60) -> float:
    """Mean log-magnitude of the image after removing the low-frequency centre of the spectrum."""
    h, w = gray.shape[:2]
    s = size / max(h, w)
    g = cv2.resize(gray, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    f = cv2.dft(g.astype(np.float32), flags=cv2.DFT_COMPLEX_OUTPUT)
    hh, ww = f.shape[:2]
    r = window // 2
    # unshifted spectrum: the centre window of the shifted spectrum maps to the four corners
    rows = np.r_[0 : min(r, hh), max(hh - r, 0) : hh]
    cols = np.r_[0 : min(r, ww), max(ww - r, 0) : ww]
    f[np.ix_(rows, cols)] = 0
    recon = cv2.idft(f, flags=cv2.DFT_SCALE | cv2.DFT_COMPLEX_OUTPUT)
    mag = cv2.magnitude(recon[:, :, 0], recon[:, :, 1])
    return float(np.mean(20.0 * np.log(mag + 1e-8)))


def gradient_metrics(gray: np.ndarray) -> dict[str, float]:
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    ex, ey = float(np.mean(gx * gx)), float(np.mean(gy * gy))
    return {"tenengrad": ex + ey, "ex": ex, "ey": ey, "motion_ratio": max(ex, ey) / max(min(ex, ey), 1e-9)}


def sharpness_metrics(gray: np.ndarray, cfg: dict[str, Any] | None = None) -> dict[str, float]:
    cfg = cfg or section("sharpness")
    g = gradient_metrics(gray)
    return {
        "lap_var": float(cv2.Laplacian(gray, cv2.CV_64F, ksize=3).var()),
        "tenengrad": g["tenengrad"],
        "fft_hf": fft_hf(gray, int(cfg["fft_resize"]), int(cfg["fft_window"])),
        "motion_ratio": g["motion_ratio"],
    }


# --------------------------------------------------------------------------- ROI


def _clip_rect(x: float, y: float, w: float, h: float, W: int, H: int) -> tuple[int, int, int, int]:
    x0, y0 = max(0, int(np.floor(x))), max(0, int(np.floor(y)))
    x1, y1 = min(W, int(np.ceil(x + w))), min(H, int(np.ceil(y + h)))
    return x0, y0, max(0, x1 - x0), max(0, y1 - y0)


def faces_roi(face_boxes: list[list[float]], shape: tuple[int, ...], expand: float) -> tuple[int, int, int, int] | None:
    """Union of face boxes ([l,t,w,h] ratios of the image) expanded by `expand`, in pixels of `shape`."""
    if not face_boxes:
        return None
    H, W = shape[:2]
    xs0, ys0, xs1, ys1 = [], [], [], []
    for bl, t, w, h in (b[:4] for b in face_boxes):
        ex, ey = w * expand / 2, h * expand / 2
        xs0.append((bl - ex) * W)
        ys0.append((t - ey) * H)
        xs1.append((bl + w + ex) * W)
        ys1.append((t + h + ey) * H)
    return _clip_rect(min(xs0), min(ys0), max(xs1) - min(xs0), max(ys1) - min(ys0), W, H)


_sal_local = threading.local()


def saliency_roi(bgr: np.ndarray, frac: float, long_edge: int) -> tuple[int, int, int, int] | None:
    """StaticSaliencyFineGrained at `long_edge`; bounding rect of map >= frac*max, scaled back."""
    sal = getattr(_sal_local, "obj", None)
    if sal is None:
        sal = _sal_local.obj = cv2.saliency.StaticSaliencyFineGrained.create()
    H, W = bgr.shape[:2]
    s = min(1.0, long_edge / max(H, W))
    small = cv2.resize(bgr, (max(1, round(W * s)), max(1, round(H * s))), interpolation=cv2.INTER_AREA) if s < 1 else bgr
    ok, m = sal.computeSaliency(small)
    if not ok or m is None:
        return None
    smap = np.asarray(m, dtype=np.float32)
    mx = float(smap.max())
    if mx <= 0:
        return None
    mask = cv2.morphologyEx((smap >= frac * mx).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    pts = cv2.findNonZero(mask)
    if pts is None:
        return None
    x, y, w, h = cv2.boundingRect(pts)
    return _clip_rect(x / s, y / s, w / s, h / s, W, H)


# --------------------------------------------------------------------------- scoring


def _label(m: dict[str, float], brisque: float | None, cfg: dict[str, Any]) -> tuple[str, float, dict[str, str]]:
    lap = m["lap_var"]
    lap_vote = "blurry" if lap < cfg["lap_blurry_max"] else "sharp" if lap > cfg["lap_sharp_min"] else "borderline"
    votes = {"lap": lap_vote, "fft": "blurry" if m["fft_hf"] < cfg["fft_hf_blurry_max"] else "sharp"}
    if brisque is not None:
        votes["brisque"] = "blurry" if brisque > cfg["brisque_poor_min"] else "sharp"
    if lap_vote == "borderline":
        label = "borderline"
    elif votes["fft"] == lap_vote:
        label = lap_vote
    elif "brisque" in votes:
        label = votes["brisque"]  # lap and fft disagree, so BRISQUE is the majority-maker
    else:
        label = "borderline"
    if label == "borderline":
        agreement = 0.5
    else:
        agreement = sum(v == label for v in votes.values()) / len(votes)
    lap_conf = float(np.clip(abs(lap - cfg["lap_conf_center"]) / cfg["lap_conf_center"], cfg["lap_conf_min"],
                             cfg["lap_conf_max"]))
    w = float(cfg["conf_weight_lap"])
    return label, round(float(np.clip(w * lap_conf + (1 - w) * agreement, 0.0, 1.0)), 4), votes


def score_sharpness_image(
    bgr: np.ndarray,
    roi: str = "full",
    face_boxes: list[list[float]] | None = None,
    cfg: dict[str, Any] | None = None,
    brisque_mode: str | None = None,
    brisque_value: float | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Pure scoring of one 1024-px BGR image. face_boxes are [l,t,w,h] ratios. Returns the tool-result dict.

    brisque_value lets callers reuse a BRISQUE already computed for this photo; `timings` (optional)
    receives saliency/sharpness/brisque milliseconds and is kept out of the result for determinism.
    """
    clock = time.perf_counter
    tm: dict[str, float] = {}
    cfg = cfg or section("sharpness")
    mode = brisque_mode or cfg.get("brisque_mode", "on_disagree")
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    rect = None
    roi_used = "full"
    if roi == "faces":
        rect = faces_roi(face_boxes or [], gray.shape, float(cfg["face_roi_expand"]))
    elif roi == "saliency" and bgr.ndim == 3:
        t0 = clock()
        rect = saliency_roi(bgr, float(cfg["saliency_thresh_frac"]), int(cfg["saliency_long_edge"]))
        tm["saliency"] = (clock() - t0) * 1e3
    if rect is not None and min(rect[2], rect[3]) >= int(cfg["roi_min_px"]):
        x, y, w, h = rect
        roi_gray = gray[y : y + h, x : x + w]
        roi_used = roi
    else:
        rect, roi_gray = None, gray
    t0 = clock()
    m = sharpness_metrics(roi_gray, cfg)
    tm["sharpness_metrics"] = (clock() - t0) * 1e3

    brisque = brisque_value
    if brisque is None and roi_used != "faces" and mode != "never" and bgr.ndim == 3:
        need = mode == "always"
        if mode == "on_disagree":
            prelim = _label(m, None, cfg)[2]
            need = prelim["lap"] != prelim["fft"]
        if need:
            t0 = clock()
            brisque = brisque_score(bgr)
            tm["brisque"] = (clock() - t0) * 1e3
    label, conf, votes = _label(m, brisque if roi_used != "faces" else None, cfg)
    blur_kind = None
    if label in ("blurry", "borderline"):
        blur_kind = "motion" if m["motion_ratio"] > float(cfg["motion_ratio_min"]) else "focus"
    res = SharpnessResult(
        lap_var=round(m["lap_var"], 3),
        tenengrad=round(m["tenengrad"], 3),
        fft_hf=round(m["fft_hf"], 3),
        brisque=brisque if roi_used != "faces" else None,
        label=label,  # type: ignore[arg-type]
        confidence=conf,
        blur_kind=blur_kind,  # type: ignore[arg-type]
        motion_ratio=round(m["motion_ratio"], 3),
        roi=roi,  # type: ignore[arg-type]
        roi_used=roi_used,  # type: ignore[arg-type]
        votes=votes,
    ).model_dump()
    res["roi_rect"] = list(rect) if rect else None
    if timings is not None:
        for k, v in tm.items():
            timings[k] = timings.get(k, 0.0) + v
    return res


def _face_boxes_from_context(ctx: Any, photo_id: str, full_wh: tuple[int, int] | None) -> list[list[float]]:
    """Face boxes as ratios from a prior detect_faces result (bbox_px in processed-image px)."""
    faces = (ctx.results.get(photo_id, {}).get("detect_faces") or {}).get("faces") or []
    if not faces:
        return []
    if full_wh is None:
        return []
    W, H = full_wh
    return [[f["bbox_px"][0] / W, f["bbox_px"][1] / H, f["bbox_px"][2] / W, f["bbox_px"][3] / H] for f in faces]


def score_sharpness(
    photo_id: str,
    roi: str = "full",
    variant: str = "processed",
    event_id: str | None = None,
    face_boxes: list[list[float]] | None = None,
) -> dict[str, Any]:
    """Score sharpness of a photo on roi "full" | "faces" | "saliency", variant "processed" | "original".

    faces ROI uses face_boxes ([l,t,w,h] ratios) or the detect_faces result recorded for this photo.
    variant="original" decodes the original and resizes it to 1024 px so thresholds stay valid; HEIC or
    missing originals fall back to the processed proxy with variant_fallback=true.
    Returns {lap_var, tenengrad, fft_hf, brisque, label, confidence, blur_kind, motion_ratio, ...}.
    """
    from keepframe.io import images, proxies
    from keepframe.tools.ingest import get_context, resolve_event

    ev = resolve_event(photo_id, event_id)
    ctx = get_context(ev)
    t0 = time.perf_counter()
    fallback = False
    img = None
    if variant == "original":
        orig = proxies.load_original(ev, photo_id)
        if orig is None:
            fallback = True
        else:
            img = images.resize_long_edge(orig, int(section("proxy")["long_edge"]))
    if img is None:
        img = proxies.load_p1024(ev, photo_id)
    if roi == "faces" and face_boxes is None:
        rec = ctx.photos[photo_id].record if photo_id in ctx.photos else None
        wh = (rec.width, rec.height) if rec and rec.width and rec.height else None
        if wh is None and (ctx.results.get(photo_id, {}).get("detect_faces")):
            full = proxies.load_full(ev, photo_id)
            wh = (full.shape[1], full.shape[0])
        face_boxes = _face_boxes_from_context(ctx, photo_id, wh)
    prev = ctx.results.get(photo_id, {}).get("brisque") if variant == "processed" else None
    res = score_sharpness_image(img, roi=roi, face_boxes=face_boxes, brisque_value=prev)
    res["variant"] = "processed" if fallback else variant
    res["variant_fallback"] = fallback
    if res.get("brisque") is not None and variant == "processed" and not fallback:
        ctx.record(photo_id, "brisque", res["brisque"])
    ctx.record(photo_id, f"sharpness_{roi}_{res['variant']}", res)
    res["latency_ms"] = round((time.perf_counter() - t0) * 1e3, 2)
    return res
