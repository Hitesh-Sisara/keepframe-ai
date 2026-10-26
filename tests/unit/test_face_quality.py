from __future__ import annotations

import cv2
import numpy as np
from _faces_fixtures import CLOSED, OPEN, group_photo, load, needs_models

from keepframe.tools.face_quality import align, group_identities, score_face_quality
from keepframe.tools.faces import detect_faces

pytestmark = needs_models


def test_shape_and_good_on_clean_faces() -> None:
    for name in OPEN + CLOSED:
        img = load(name)
        res = score_face_quality(img, detect_faces(img))
        assert set(res) == {"per_face", "main_subject_face_id"}
        r = res["per_face"][0]
        assert set(r) == {"face_id", "fiqa", "lap_var", "minPx", "good"}
        assert r["fiqa"] > 0.5 and r["good"] and r["lap_var"] > 0
        assert res["main_subject_face_id"] == "f0"


def test_alignment_is_112() -> None:
    img = load("open_a")
    assert align(img, detect_faces(img)["faces"][0]).shape == (112, 112, 3)


def test_blur_lowers_fiqa_and_lap_var() -> None:
    img = load("open_b")
    faces = detect_faces(img)  # same landmarks for both so only pixels differ
    sharp = score_face_quality(img, faces)["per_face"][0]
    soft = score_face_quality(cv2.GaussianBlur(img, (0, 0), 4), faces)["per_face"][0]
    assert soft["fiqa"] < sharp["fiqa"] - 0.1
    assert soft["lap_var"] < 0.2 * sharp["lap_var"]


def test_face_without_landmarks_reports_none() -> None:
    img = load("open_a")
    f = dict(detect_faces(img)["faces"][0], landmarks5=None)
    r = score_face_quality(img, [f])["per_face"][0]
    assert r["fiqa"] is None and r["lap_var"] is None and not r["good"]


def test_identity_same_person_across_transforms() -> None:
    # The synthetic (machine-generated) fixtures are not identity-diverse (cross-person SFace cosine 0.26-0.67, see
    # docs/CV_FACES.md), so we test grouping mechanics: a transformed copy always groups with its
    # source, and a strict threshold separates people the default one merges.
    img = load("open_c")
    flipped_bright = cv2.convertScaleAbs(cv2.flip(img, 1), alpha=1.1, beta=10)
    items = [(im, detect_faces(im)["faces"][0]) for im in (img, flipped_bright, load("closed_b"))]
    labels = group_identities(items)
    assert labels[0] == labels[1] and labels[2] != labels[0]  # closed_b vs open_c cosine ~0.26
    strict = group_identities(items, threshold=0.99)
    assert len(set(strict)) == 3


def test_identity_flag_in_photo_and_no_embeddings_returned() -> None:
    canvas = group_photo(OPEN)
    res = score_face_quality(canvas, identity=True)
    assert len(res["per_face"]) == 3
    for r in res["per_face"]:
        assert isinstance(r["identity_group"], int)
        assert not any(isinstance(v, (list, np.ndarray)) for v in r.values())
