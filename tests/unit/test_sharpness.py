"""score_sharpness on synthetic checkerboards / scenes (docs/REPORT.md §3)."""

from __future__ import annotations

import numpy as np
import pytest

from keepframe.tools import sharpness as sh
from keepframe.tools.sharpness import faces_roi, score_sharpness_image, sharpness_metrics
from keepframe.worker.synth import checkerboard, gaussian_blur, motion_blur, scene


def _gray(img):
    import cv2

    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def test_checkerboard_sharp_vs_blurred_metrics_monotonic():
    board = checkerboard()
    vals = [sharpness_metrics(_gray(b)) for b in (board, gaussian_blur(board, 2), gaussian_blur(board, 8))]
    for k in ("lap_var", "tenengrad", "fft_hf"):
        assert vals[0][k] > vals[1][k] > vals[2][k], k


def test_sharp_checkerboard_labelled_sharp():
    r = score_sharpness_image(checkerboard(), brisque_mode="never")
    assert r["label"] == "sharp"
    assert r["lap_var"] > 120 and r["fft_hf"] > 10
    assert 0.3 <= r["confidence"] <= 1.0
    assert r["blur_kind"] is None


def test_heavily_blurred_checkerboard_labelled_blurry_focus():
    r = score_sharpness_image(gaussian_blur(checkerboard(), 10), brisque_mode="never")
    assert r["label"] == "blurry"
    assert r["blur_kind"] == "focus"
    assert r["votes"] == {"lap": "blurry", "fft": "blurry"}


def test_borderline_band_is_borderline():
    cfg = sh.section("sharpness")
    img = scene(5, 683, 1024)
    # find a blur level inside [60, 120]
    for sigma in np.linspace(1.0, 4.0, 31):
        r = score_sharpness_image(gaussian_blur(img, float(sigma)), brisque_mode="never")
        if cfg["lap_blurry_max"] <= r["lap_var"] <= cfg["lap_sharp_min"]:
            assert r["label"] == "borderline"
            assert r["blur_kind"] in ("motion", "focus")
            return
    pytest.fail("no sigma produced a borderline lap_var")


def test_motion_blur_detected_as_motion():
    img = scene(3, 683, 1024)
    m = sharpness_metrics(_gray(motion_blur(img, 41)))
    assert m["motion_ratio"] > 2.5
    assert sharpness_metrics(_gray(gaussian_blur(img, 6)))["motion_ratio"] < 2.5


def test_motion_blurred_frame_blur_kind_motion():
    r = score_sharpness_image(motion_blur(gaussian_blur(checkerboard(), 1.5), 61), brisque_mode="never")
    if r["label"] != "sharp":
        assert r["blur_kind"] == "motion"
    assert r["motion_ratio"] > 2.5


def test_faces_roi_rescues_soft_background():
    """Bokeh portrait: sharp 'face' region on a blurred frame scores sharp on roi=faces (policy 2)."""
    img = gaussian_blur(scene(21, 683, 1024), 6)
    board = checkerboard(200, 160, 10)
    img[200:400, 400:560] = board
    box = [400 / 1024, 200 / 683, 160 / 1024, 200 / 683]
    full = score_sharpness_image(img, roi="full", brisque_mode="never")
    face = score_sharpness_image(img, roi="faces", face_boxes=[box], brisque_mode="never")
    assert face["roi_used"] == "faces"
    assert face["lap_var"] > full["lap_var"]
    assert face["label"] == "sharp"
    assert face["brisque"] is None  # BRISQUE is a full-frame metric


def test_faces_roi_expansion_and_clipping():
    x, y, w, h = faces_roi([[0.9, 0.9, 0.2, 0.2]], (100, 200), 0.2)
    assert x + w <= 200 and y + h <= 100 and x >= 0 and y >= 0
    assert faces_roi([], (100, 200), 0.2) is None


def test_saliency_roi_runs_and_falls_back():
    img = scene(4, 683, 1024)
    r = score_sharpness_image(img, roi="saliency", brisque_mode="never")
    assert r["roi_used"] in ("saliency", "full")
    flat = np.full((683, 1024, 3), 120, np.uint8)
    r2 = score_sharpness_image(flat, roi="saliency", brisque_mode="never")
    assert r2["roi_used"] == "full"
    assert r2["label"] == "blurry"


def test_deterministic():
    img = scene(8, 683, 1024)
    a = score_sharpness_image(img, roi="saliency", brisque_mode="never")
    b = score_sharpness_image(img, roi="saliency", brisque_mode="never")
    assert a == b


def test_brisque_live_model(tmp_path, monkeypatch):
    """BRISQUE via cv2.quality with the LIVE yml (downloaded + sha256-verified; skipped offline)."""
    monkeypatch.setenv("CULL_MODELS_DIR", str(tmp_path))
    if not sh.ensure_brisque_models(tmp_path):
        pytest.skip("BRISQUE model files not downloadable (offline)")
    try:
        assert sh.brisque_available()
        img = scene(30, 683, 1024)
        v_sharp = sh.brisque(img)
        v_blur = sh.brisque(gaussian_blur(img, 4))
        assert 0.0 <= v_sharp <= 100.0 and 0.0 <= v_blur <= 100.0
        assert v_blur > v_sharp
        # disagreement triggers BRISQUE in on_disagree mode
        r = score_sharpness_image(img, brisque_mode="always")
        assert r["brisque"] is not None and "brisque" in r["votes"]
    finally:
        sh.reset_brisque()


def test_brisque_missing_models(tmp_path, monkeypatch):
    monkeypatch.setenv("CULL_MODELS_DIR", str(tmp_path / "none"))
    sh.reset_brisque()
    try:
        assert sh.brisque_score(scene(1, 300, 400)) is None
        with pytest.raises(FileNotFoundError):
            sh.brisque(scene(1, 300, 400))
        r = score_sharpness_image(scene(1, 683, 1024), brisque_mode="always")
        assert r["brisque"] is None
    finally:
        sh.reset_brisque()
