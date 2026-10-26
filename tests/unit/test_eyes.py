from __future__ import annotations

import cv2
import numpy as np
from _faces_fixtures import CLOSED, OPEN, group_photo, load, needs_models

from keepframe.tools.eyes import classify_eyes, eye_crops, score_eyes_open
from keepframe.tools.faces import detect_faces

pytestmark = needs_models


def _check_shape(res: dict) -> None:
    assert set(res) == {"per_face", "any_main_subject_closed", "main_subject_face_id", "confidence"}
    assert 0.0 <= res["confidence"] <= 1.0
    for r in res["per_face"]:
        assert set(r) == {
            "face_id",
            "left_open_p",
            "right_open_p",
            "state",
            "confidence",
            "min_px",
            "is_main",
        }
        assert r["state"] in {"open", "closed", "partial", "unknown"}


def test_open_fixtures_are_open() -> None:
    for name in OPEN:
        img = load(name)
        res = score_eyes_open(img, detect_faces(img))
        _check_shape(res)
        r = res["per_face"][0]
        assert r["state"] == "open", (name, r)
        assert r["is_main"] and not res["any_main_subject_closed"]


def test_closed_fixtures_are_closed_and_flag_main_subject() -> None:
    for name in CLOSED:
        img = load(name)
        res = score_eyes_open(img)  # detects faces itself
        _check_shape(res)
        r = res["per_face"][0]
        assert r["state"] == "closed", (name, r)
        assert res["any_main_subject_closed"] and res["main_subject_face_id"] == "f0"
        assert res["confidence"] >= 0.8


def test_group_secondary_closed_does_not_flag_main() -> None:
    # main subject (big, open) + two smaller faces, one closed
    canvas = cv2.hconcat(
        [
            cv2.resize(load("open_a"), (768, 768)),
            cv2.vconcat([cv2.resize(load("closed_a"), (384, 384)), cv2.resize(load("open_b"), (384, 384))]),
        ]
    )
    faces = detect_faces(canvas)
    assert faces["n_faces"] == 3
    res = score_eyes_open(canvas, faces)
    states = {r["face_id"]: r["state"] for r in res["per_face"]}
    assert states["f0"] == "open" and sorted(states.values()) == ["closed", "open", "open"]
    assert res["main_subject_face_id"] == "f0" and not res["any_main_subject_closed"]


def test_face_ids_filter_keeps_main_subject_logic() -> None:
    canvas = group_photo(OPEN)
    faces = detect_faces(canvas)
    res = score_eyes_open(canvas, faces, face_ids=["f2"])
    assert [r["face_id"] for r in res["per_face"]] == ["f2"]
    assert res["main_subject_face_id"] == "f0"


def test_small_face_is_unknown() -> None:
    small = cv2.resize(load("open_a"), (96, 96), interpolation=cv2.INTER_AREA)
    canvas = np.full((400, 400, 3), 120, np.uint8)
    canvas[150:246, 150:246] = small
    faces = detect_faces(canvas, min_score=0.6)
    assert faces["n_faces"] == 1 and faces["faces"][0]["bbox_px"][2] < 40
    res = score_eyes_open(canvas, faces)
    assert res["per_face"][0]["state"] == "unknown"
    assert res["main_subject_face_id"] is None and res["confidence"] == 0.0


def test_dark_small_face_is_unknown() -> None:
    img = cv2.resize(load("open_a"), (150, 150))
    dark = (img.astype(np.float32) * 0.12).astype(np.uint8)
    faces = detect_faces(img)  # boxes from the bright copy, scored on the dark one
    assert faces["n_faces"] == 1
    assert score_eyes_open(dark, faces)["per_face"][0]["state"] == "unknown"


def test_missing_landmarks_is_unknown() -> None:
    img = load("open_a")
    f = dict(detect_faces(img)["faces"][0], landmarks5=None)
    assert score_eyes_open(img, [f])["per_face"][0]["state"] == "unknown"


def test_crop_geometry_and_edge_padding() -> None:
    img = load("open_a")
    lms = detect_faces(img)["faces"][0]["landmarks5"]
    rc, lc, side = eye_crops(img, lms, 0.5)
    iod = float(np.linalg.norm(np.subtract(lms[0], lms[1])))
    assert abs(side - 0.5 * iod) < 1e-6
    assert abs(rc.shape[0] - side) <= 1 and rc.shape[:2] == lc.shape[:2]
    # landmark at the frame edge still yields a full square crop
    edge = [[2.0, 2.0], [60.0, 2.0], [30, 30], [10, 50], [50, 50]]
    rc2, _, side2 = eye_crops(img, edge, 0.5)
    assert rc2 is not None and abs(rc2.shape[0] - side2) <= 1


def test_classifier_batch_matches_single() -> None:
    img = load("closed_a")
    lms = detect_faces(img)["faces"][0]["landmarks5"]
    rc, lc, _ = eye_crops(img, lms, 0.5)
    both = classify_eyes([rc, lc])
    assert np.allclose(both, [classify_eyes([rc])[0], classify_eyes([lc])[0]], atol=1e-5)
