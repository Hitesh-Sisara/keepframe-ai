"""C08 (docs/FAILURE_CASES.md): the group-eyes review fires for several closed non-main faces, not exactly one."""

from __future__ import annotations

import pytest

from keepframe.worker import policies as P


def _faces(n: int) -> list[dict]:
    return [{"face_id": f"f{i}", "bbox_px": [0, 0, 200 - 10 * i, 200 - 10 * i]} for i in range(n)]


def _eyes(n: int, closed: set[int], unknown: frozenset[int] = frozenset()) -> dict:
    def one(i: int) -> dict:
        state = "closed" if i in closed else ("unknown" if i in unknown else "open")
        p = None if state == "unknown" else (0.1 if state == "closed" else 0.9)
        return {"face_id": f"f{i}", "left_open_p": p, "right_open_p": p, "state": state}
    return {"per_face": [one(i) for i in range(n)], "main_subject_face_id": "f0"}


@pytest.mark.parametrize(("n_faces", "closed", "fires"), [
    (9, {1, 2, 3, 4, 5}, True),   # the E03 frame: 5 of 8 non-main closed (old rule: no code, kept)
    (5, {1, 2, 3}, True),         # E07 pattern: 3 of 4 closed
    (6, {1, 2}, True),            # >= 2 closed
    (3, {1}, True),               # 1 of 2 = 1/2
    (4, {1}, True),               # 1 of 3 = exactly 1/3 (integer comparison, no float edge)
    (5, {1}, False),              # 1 of 4 < 1/3 and < 2 closed (the old rule fired here)
    (9, set(), False),
    (2, {1}, False),              # not a group photo (< group_photo_min_faces)
])
def test_group_eyes_rule(n_faces: int, closed: set[int], fires: bool) -> None:
    d = P.eyes_defect(_eyes(n_faces, closed), _faces(n_faces))
    if fires:
        assert d is not None and d.code == "REVIEW_POLICY_GROUP_EYES" and d.verdict == "review"
    else:
        assert d is None


def test_unknown_eye_state_never_counts_as_closed() -> None:
    # 1 closed + 3 unknown of 4 non-main: share is 1/4 of DETECTED faces, unknowns are not closed
    assert P.eyes_defect(_eyes(5, {1}, frozenset({2, 3, 4})), _faces(5)) is None


def test_main_subject_closed_is_still_eyes_closed_reject() -> None:
    d = P.eyes_defect(_eyes(9, {0, 1, 2, 3}), _faces(9))
    assert d is not None and d.code == "EYES_CLOSED" and d.verdict == "reject"


def test_group_eyes_frame_is_review_not_reject_and_explains_itself() -> None:
    ev = P.PhotoEvidence("p", {"label": "sharp", "confidence": 0.9, "blur_kind": None, "lap_var": 500.0,
                               "roi_used": "full"},
                         {"label": "ok", "confidence": 0.9, "clip_lo_pct": 0.0, "mean_L": 120.0, "no_subject": False},
                         faces=_faces(9), faces_known=True, eyes=_eyes(9, {1, 2, 3, 4, 5}), eyes_known=True)
    d = P.decide_singleton(ev)
    assert d.verdict == "review" and "REVIEW_POLICY_GROUP_EYES" in d.reason_codes
    assert "eyes closed" in d.reason_text and len(d.reason_text) <= 140
