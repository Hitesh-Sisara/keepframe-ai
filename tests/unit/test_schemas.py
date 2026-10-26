"""schemas: manifest rows, tool result shapes, decisions, reason codes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from keepframe.config import reason_codes, thresholds, thresholds_sha
from keepframe.schemas import (
    ClusterResult,
    Decision,
    ExposureResult,
    PhotoRecord,
    ReasonCode,
    SharpnessResult,
    TraceLine,
    duplicate_of,
    to_epoch_s,
    validate_reason_code,
)
from keepframe.worker.synth import scene


def test_reason_codes_match_yaml_and_plan():
    plan = {"BLUR_MOTION", "BLUR_FOCUS", "EYES_CLOSED", "EXPOSURE_UNDER", "EXPOSURE_OVER", "LOW_CONTRAST",
            "DUPLICATE_OF", "BURST_NOT_BEST", "NO_SUBJECT", "KEEP_BEST_OF_GROUP", "KEEP_SINGLETON",
            "REVIEW_LOW_CONFIDENCE", "REVIEW_POLICY_GROUP_EYES", "REVIEW_ARTISTIC_INTENT"}
    assert {c.value for c in ReasonCode} == plan == set(reason_codes())


def test_reason_code_validation():
    assert validate_reason_code(duplicate_of("p_1")) == "DUPLICATE_OF:p_1"
    for bad in ("DUPLICATE_OF", "BLUR_FOCUS:x", "DELETE"):
        with pytest.raises(ValueError):
            validate_reason_code(bad)


def test_decision_rules():
    d = Decision(photo_id="p", verdict="reject", reason_codes=["BLUR_FOCUS"], confidence=0.9)
    assert d.decided_by == "worker"
    with pytest.raises(ValidationError):
        Decision(photo_id="p", verdict="delete", reason_codes=["BLUR_FOCUS"], confidence=0.9)  # never delete
    with pytest.raises(ValidationError):
        Decision(photo_id="p", verdict="keep", reason_codes=[], confidence=0.9)  # explain every verdict
    with pytest.raises(ValidationError):
        Decision(photo_id="p", verdict="keep", reason_codes=["KEEP_SINGLETON"], confidence=0.9, reason_text="x" * 141)


def test_photo_record_manifest_row_and_times():
    row = {"photo_id": "p1", "event_id": "e1", "clicked_at": "2026-10-05T13:00:00Z", "created_at": 1791205200000,
           "frs_faces": None, "rek_faces": [{"bbox": [0.1, 0.1, 0.2, 0.2], "confidence": 99.1}],
           "some_future_field": 1}
    r = PhotoRecord.model_validate(row)
    assert r.frs_faces == [] and r.rek_faces[0].confidence == 99.1
    assert r.clicked_ts() == to_epoch_s("2026-10-05T13:00:00+00:00")
    assert r.created_ts() == 1791205200.0
    assert to_epoch_s(None) is None and to_epoch_s("1791205200") == 1791205200.0


def test_tool_results_match_plan_shapes():
    from keepframe.tools.exposure import score_exposure_image
    from keepframe.tools.sharpness import score_sharpness_image

    img = scene(1, 683, 1024)
    s = score_sharpness_image(img, brisque_mode="never")
    for k in ("lap_var", "tenengrad", "fft_hf", "brisque", "label", "confidence"):
        assert k in s
    SharpnessResult.model_validate({k: v for k, v in s.items() if k != "roi_rect"})
    e = score_exposure_image(img)
    ExposureResult.model_validate(e)
    assert set(e) >= {"clip_lo_pct", "clip_hi_pct", "mean_L", "std_L", "entropy", "label"}
    ClusterResult.model_validate({"n_groups": 1, "groups": [{"group_id": "g_e_1", "photo_ids": ["a", "b"],
                                                              "size": 2, "span_s": 0.4}], "singletons": ["c"]})


def test_trace_line_plan_example():
    line = {"run_id": "run_01J9X", "seq": 18, "ts": "2026-10-12T09:14:03.201Z", "type": "tool_result",
            "tool": "score_sharpness", "result": {"lap_var": 74.2, "label": "borderline"}, "opencv": "5.0.0",
            "cv2_file": "/x/cv2.abi3.so", "latency_ms": 38}
    assert TraceLine.model_validate(line).latency_ms == 38


def test_thresholds_loaded_and_hashed():
    t = thresholds()
    assert t["sharpness"]["lap_blurry_max"] == 60.0 and t["hashes"]["phash_max"] == 10
    assert t["faces"]["yunet_score_threshold"] == 0.9  # face block
    assert len(thresholds_sha()) == 64
    t["sharpness"]["lap_blurry_max"] = 0  # callers get a copy
    assert thresholds()["sharpness"]["lap_blurry_max"] == 60.0


def test_opencv_info():
    import keepframe

    info = keepframe.opencv_info()
    assert info["opencv_version"].startswith("5.")
    assert set(info["contrib"]) == {"img_hash", "quality", "saliency", "xphoto"}
    assert len(keepframe.OPENCV_INFO["build_info_head"]) > 5
