from __future__ import annotations

import cv2
import numpy as np
import pytest
from _faces_fixtures import CLOSED, OPEN, group_photo, load, needs_models

from keepframe.tools._face_defaults import FACE_DEFAULTS
from keepframe.tools.faces import detect_faces

pytestmark = needs_models


def _iou(a: list[float], b: list[float]) -> float:
    iw = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return iw * ih / (a[2] * a[3] + b[2] * b[3] - iw * ih)


def _check_shape(res: dict) -> None:
    assert set(res) == {"faces", "n_faces"}
    assert res["n_faces"] == len(res["faces"])
    for i, f in enumerate(res["faces"]):
        assert set(f) == {"face_id", "bbox_px", "score", "landmarks5", "source"}
        assert f["face_id"] == f"f{i}"
        assert len(f["bbox_px"]) == 4 and all(isinstance(v, int) for v in f["bbox_px"])
        assert f["source"] in {"frsInfo", "rekognition", "yunet"}
        if f["landmarks5"] is not None:
            assert np.asarray(f["landmarks5"]).shape == (5, 2)


def test_yunet_finds_one_face_per_fixture() -> None:
    for name in OPEN + CLOSED:
        res = detect_faces(load(name))
        _check_shape(res)
        assert res["n_faces"] == 1, name
        f = res["faces"][0]
        assert f["source"] == "yunet" and f["score"] >= 0.9
        x, y, w, h = f["bbox_px"]
        for lx, ly in f["landmarks5"]:
            assert x - 0.1 * w <= lx <= x + 1.1 * w and y - 0.1 * h <= ly <= y + 1.1 * h


def test_coordinates_are_in_full_resolution_frame() -> None:
    img = load("open_a")
    big = cv2.resize(img, (2048, 2048), interpolation=cv2.INTER_CUBIC)  # forces the 1024 downscale path
    a, b = detect_faces(img)["faces"][0], detect_faces(big)["faces"][0]
    # b is in 2048-px coordinates even though YuNet ran at 1024
    assert _iou([v / 4 for v in b["bbox_px"]], a["bbox_px"]) > 0.8
    iod = float(np.linalg.norm(np.subtract(a["landmarks5"][0], a["landmarks5"][1])))
    diff = np.abs(np.array(b["landmarks5"]) / 4 - np.array(a["landmarks5"]))
    assert diff[:2].max() < 0.1 * iod and diff.max() < 0.15 * iod  # eyes tight, mouth jitters more


def test_sorted_largest_first_in_group() -> None:
    mid = np.full((512, 512, 3), 128, np.uint8)
    mid[128:384, 128:384] = cv2.resize(load("open_b"), (256, 256))
    canvas = cv2.hconcat(
        [cv2.resize(load("open_a"), (384, 384)), mid[64:448, 64:448], load("closed_a")[64:448, 64:448]]
    )
    res = detect_faces(canvas)
    _check_shape(res)
    assert res["n_faces"] == 3
    areas = [f["bbox_px"][2] * f["bbox_px"][3] for f in res["faces"]]
    assert areas == sorted(areas, reverse=True)


def test_no_faces_on_blank_and_mfaces_zero_skips() -> None:
    blank = np.full((768, 1024, 3), 128, np.uint8)
    assert detect_faces(blank)["n_faces"] == 0
    # mfaces == 0 means "no people": detection is skipped even though a face is present
    assert detect_faces(load("open_a"), record={"mfaces": 0})["n_faces"] == 0


def _tiny_face_canvas() -> np.ndarray:
    # A ~16 px face at 1024 is below YuNet's floor; at 1600 it is ~25 px.
    face = cv2.resize(load("open_a"), (64, 64), interpolation=cv2.INTER_AREA)
    canvas = np.full((2560, 2560, 3), 90, np.uint8)
    canvas[1200:1264, 1200:1264] = face
    return canvas


def test_retry_at_1600_when_mfaces_positive() -> None:
    canvas = _tiny_face_canvas()
    assert detect_faces(canvas, min_score=0.6)["n_faces"] == 0  # mfaces unknown -> no retry by default
    retried = detect_faces(canvas, record={"mfaces": 1}, min_score=0.6)
    assert retried["n_faces"] >= 1


def test_mfaces_none_is_unknown_not_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real manifests: mfaces is null everywhere. That must still run YuNet, never skip.
    rec = {"mfaces": None, "frs_status": None, "frs_faces": [], "rek_faces": [], "width": 512, "height": 512}
    res = detect_faces(load("open_a"), record=rec)
    assert res["n_faces"] == 1 and res["faces"][0]["source"] == "yunet"
    canvas = _tiny_face_canvas()
    rec2 = {"mfaces": None, "width": 2560, "height": 2560}
    assert detect_faces(canvas, record=rec2, min_score=0.6)["n_faces"] == 0
    monkeypatch.setitem(FACE_DEFAULTS, "detect_retry_on_unknown", True)
    assert detect_faces(canvas, record=rec2, min_score=0.6)["n_faces"] >= 1
    # frsInfo indexed with zero faces is evidence of "no face": no retry even when enabled
    rec3 = dict(rec2, frs_status="indexed", frs_faces=[])
    assert detect_faces(canvas, record=rec3, min_score=0.6)["n_faces"] == 0


def _frs_record(ref_box: list[int], pw: int, ph: int, frame_w: int, frame_h: int) -> dict:
    """frsInfo as stored in the source metadata: ratio bbox of the PROCESSED image, sourceImage* == processed dims."""
    sx, sy = pw / frame_w, ph / frame_h
    x, y, w, h = ref_box[0] * sx, ref_box[1] * sy, ref_box[2] * sx, ref_box[3] * sy
    r = [x / pw, y / ph, w / pw, h / ph]
    return {
        "mfaces": None,
        "width": pw,
        "height": ph,
        "frs_status": "indexed",
        "frs_src_w": pw,
        "frs_src_h": ph,
        "frs_faces": [{"bbox": r, "det_score": 0.99, "min_px": round(min(r[2] * pw, r[3] * ph))}],
    }


def test_reuse_frsinfo_on_full_proxy_is_ratio_times_processed_dims() -> None:
    img = cv2.resize(load("open_a"), (1200, 800))  # the full proxy == processed image (non-square)
    ref = detect_faces(img)["faces"][0]
    rec = _frs_record(ref["bbox_px"], 1200, 800, 1200, 800)
    f = detect_faces(img, record=rec)["faces"][0]
    assert f["source"] == "frsInfo" and f["score"] == 0.99
    r = rec["frs_faces"][0]["bbox"]
    assert np.allclose(f["bbox_px"], [r[0] * 1200, r[1] * 800, r[2] * 1200, r[3] * 800], atol=1)
    assert min(f["bbox_px"][2:]) == pytest.approx(rec["frs_faces"][0]["min_px"], abs=1)
    iod = float(np.linalg.norm(np.subtract(ref["landmarks5"][0], ref["landmarks5"][1])))
    assert f["landmarks5"] is not None
    diff = np.abs(np.array(f["landmarks5"]) - np.array(ref["landmarks5"]))
    assert diff[:2].max() < 0.1 * iod and diff.max() < 0.15 * iod  # eyes tight, mouth/nose jitter more


def test_reuse_frsinfo_on_downscaled_proxy_maps_into_analysed_frame() -> None:
    processed = cv2.resize(load("open_a"), (2560, 1707))
    ref = detect_faces(processed)["faces"][0]
    rec = _frs_record(ref["bbox_px"], 2560, 1707, 2560, 1707)
    p1024 = cv2.resize(processed, (1024, 683), interpolation=cv2.INTER_AREA)
    f = detect_faces(p1024, record=rec)["faces"][0]
    assert f["source"] == "frsInfo"
    s = 1024 / 2560
    assert np.allclose(f["bbox_px"], np.array(ref["bbox_px"]) * s, atol=2)
    assert f["landmarks5"] is not None


def test_reuse_existing_false_ignores_metadata() -> None:
    img = load("open_a")
    rec = _frs_record(detect_faces(img)["faces"][0]["bbox_px"], 512, 512, 512, 512)
    assert detect_faces(img, record=rec, reuse_existing=False)["faces"][0]["source"] == "yunet"


def test_reuse_rekognition_dict_bbox_and_percent_confidence() -> None:
    img = load("closed_a")
    x, y, w, h = detect_faces(img)["faces"][0]["bbox_px"]
    rec = {
        "mfaces": 1,
        "width": 512,
        "height": 512,
        "rek_faces": [
            {
                "BoundingBox": {"Left": x / 512, "Top": y / 512, "Width": w / 512, "Height": h / 512},
                "Confidence": 99.5,
            }
        ],
    }
    f = detect_faces(img, record=rec)["faces"][0]
    assert f["source"] == "rekognition" and abs(f["score"] - 0.995) < 1e-6
    assert f["landmarks5"] is not None


def test_reused_box_without_face_has_no_landmarks() -> None:
    img = load("open_a")
    rec = {
        "frs_status": "indexed",
        "frs_src_w": 512,
        "frs_src_h": 512,
        "frs_faces": [{"bbox": [0.0, 0.85, 0.12, 0.12], "det_score": 0.95}],
    }
    f = detect_faces(img, record=rec)["faces"][0]
    assert f["source"] == "frsInfo" and f["landmarks5"] is None


def test_aspect_mismatch_distrusts_reuse() -> None:
    img = load("open_a")  # square analysed image
    box = [0.1, 0.1, 0.2, 0.2]
    wrong_frame = {
        "frs_status": "indexed",
        "frs_src_w": 1600,
        "frs_src_h": 900,
        "width": 1600,
        "height": 900,
        "frs_faces": [{"bbox": box, "det_score": 0.95}],
    }
    assert detect_faces(img, record=wrong_frame)["faces"][0]["source"] == "yunet"
    # frsInfo computed on a different image than the processed one (src vs width/height disagree)
    src_vs_processed = {
        "frs_status": "indexed",
        "frs_src_w": 1600,
        "frs_src_h": 900,
        "width": 512,
        "height": 512,
        "frs_faces": [{"bbox": box, "det_score": 0.95}],
    }
    assert detect_faces(img, record=src_vs_processed)["faces"][0]["source"] == "yunet"
    # 1 % off (rounding in the processed resize) is still trusted
    near = {
        "frs_status": "indexed",
        "frs_src_w": 517,
        "frs_src_h": 512,
        "width": 517,
        "height": 512,
        "frs_faces": [{"bbox": box, "det_score": 0.95}],
    }
    assert any(f["source"] == "frsInfo" for f in detect_faces(img, record=near)["faces"])


def test_frs_without_dims_uses_record_width_height() -> None:
    img = load("open_a")
    ref = detect_faces(img)["faces"][0]["bbox_px"]
    rec = _frs_record(ref, 512, 512, 512, 512)
    del rec["frs_src_w"], rec["frs_src_h"]
    f = detect_faces(img, record=rec)["faces"][0]
    assert f["source"] == "frsInfo" and np.allclose(f["bbox_px"], ref, atol=1)


def test_group_photo_helper_detects_all() -> None:
    assert detect_faces(group_photo(OPEN))["n_faces"] == 3
