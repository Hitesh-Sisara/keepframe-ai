"""score_exposure on dark / bright / flat / normal synthetic frames (docs/REPORT.md §3)."""

from __future__ import annotations

import numpy as np

from keepframe.tools.exposure import exposure_stats, score_exposure_image
from keepframe.worker.synth import exposure_shift, scene


def test_normal_scene_ok():
    r = score_exposure_image(scene(1, 683, 1024))
    assert r["label"] == "ok"
    assert 0.3 <= r["confidence"] <= 0.95
    assert not r["no_subject"]


def test_dark_frame_under():
    r = score_exposure_image(exposure_shift(scene(2, 683, 1024), 0.05))
    assert r["label"] == "under"
    assert r["clip_lo_pct"] > 0.40 and r["mean_L"] < 45


def test_bright_frame_over():
    r = score_exposure_image(exposure_shift(scene(3, 683, 1024), 1.0, 170))
    assert r["label"] == "over"
    assert r["clip_hi_pct"] > 0.05 and r["mean_L"] > 200


def test_flat_frame_low_contrast_and_no_subject():
    r = score_exposure_image(np.full((683, 1024, 3), 120, np.uint8))
    assert r["label"] == "low_contrast"
    assert r["std_L"] < 1 and r["entropy"] < 0.01
    assert r["no_subject"]


def test_stats_fractions_and_entropy_bounds():
    s = exposure_stats(scene(4, 683, 1024))
    for k in ("clip_lo_pct", "clip_hi_pct"):
        assert 0.0 <= s[k] <= 1.0
    assert 0.0 < s["entropy"] <= 8.0
    gray_ramp = np.tile(np.arange(256, dtype=np.uint8), (64, 1))
    ramp = np.dstack([gray_ramp] * 3)
    assert exposure_stats(ramp)["entropy"] > 7.0


def test_confidence_grows_with_distance():
    a = score_exposure_image(exposure_shift(scene(5, 683, 1024), 0.05))
    b = score_exposure_image(exposure_shift(scene(5, 683, 1024), 0.02))
    assert a["label"] == b["label"] == "under"
    assert b["confidence"] >= a["confidence"]


def _dim_party_frame():
    """Dark venue (most pixels near black) with a lit, sharp, well-contrasted subject in the middle."""
    img = np.full((683, 1024, 3), 3, np.uint8)
    subj = scene(42, 300, 260)  # textured, mean L ~ 130
    img[190:490, 380:640] = subj
    return img, [380 / 1024, 190 / 683, 260 / 1024, 300 / 683]


def test_dim_but_sharp_well_contrasted_frame_is_not_flagged():
    from keepframe.tools.sharpness import score_sharpness_image

    img, box = _dim_party_frame()
    r = score_exposure_image(img)  # no faces known: highlights decide
    assert r["label"] == "under"  # soft label stays for the UI
    assert r["mean_L"] < 45 and r["clip_lo_pct"] > 0.40
    assert r["subject_source"] == "highlights" and r["subject_L"] > 45
    assert r["under_flag"] is False
    rf = score_exposure_image(img, face_boxes=[box])
    assert rf["subject_source"] == "faces" and rf["under_flag"] is False
    assert score_sharpness_image(img, roi="saliency", brisque_mode="never")["label"] == "sharp"


def test_dim_frame_with_dark_subject_is_flagged():
    img = exposure_shift(scene(2, 683, 1024), 0.05)  # everything dark, nothing lit
    r = score_exposure_image(img)
    assert r["label"] == "under" and r["under_flag"] is True
    # dark face box -> flagged; a lit face -> not flagged
    box = [0.4, 0.3, 0.2, 0.3]
    assert score_exposure_image(img, face_boxes=[box])["under_flag"] is True
    lit, lbox = _dim_party_frame()
    assert score_exposure_image(lit, face_boxes=[lbox])["under_flag"] is False


def test_event_relative_baseline():
    from keepframe.tools.exposure import under_flag

    r = score_exposure_image(exposure_shift(scene(2, 683, 1024), 0.05))
    assert under_flag(r, None) is True
    assert under_flag(r, event_median_mean_l=r["mean_L"] * 1.5) is False  # whole event is this dark
    assert under_flag(r, event_median_mean_l=r["mean_L"] * 4.0) is True   # dark even for this event


def _studio_frame():
    """White studio backdrop (most pixels clipped) with a normally exposed face in the middle."""
    img = np.full((683, 1024, 3), 255, np.uint8)
    img[150:550, 380:640] = scene(9, 400, 260)
    return img, [380 / 1024, 150 / 683, 260 / 1024, 400 / 683]


def test_bright_studio_backdrop_with_exposed_face_not_flagged():
    from keepframe.tools.exposure import over_flag

    img, box = _studio_frame()
    r = score_exposure_image(img, face_boxes=[box])
    assert r["label"] == "over" and r["subject_clip_hi"] < 0.2 and r["over_flag"] is False
    assert over_flag({**r, "subject_clip_hi": 0.5}, event_median_mean_l=r["mean_L"] - 60) is True
    assert over_flag({**r, "subject_clip_hi": 0.5}, event_median_mean_l=r["mean_L"] - 10) is False  # venue-bright


def test_blown_frame_without_faces_flagged():
    r = score_exposure_image(exposure_shift(scene(3, 683, 1024), 1.0, 200))
    assert r["label"] == "over" and r["p5_L"] >= 180 and r["over_flag"] is True
