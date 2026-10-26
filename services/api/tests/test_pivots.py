from __future__ import annotations

from app.pivots import find_pivots


def _chain(eye_states: list[str | None], margin: float = 0.2) -> list[dict]:
    faces = [
        {"face_id": f"f{i}", "left_open_p": None, "right_open_p": None, "state": s}
        for i, s in enumerate(eye_states)
    ]
    return [
        {"seq": 1, "type": "tool_call", "tool": "score_eyes_open", "args": {"photo_id": "p"}},
        {"seq": 2, "type": "tool_result", "tool": "score_eyes_open", "result": {"per_face": faces}},
        {"seq": 3, "type": "tool_call", "tool": "pick_best_in_cluster", "args": {"group_id": "g"}},
        {
            "seq": 4,
            "type": "tool_result",
            "tool": "pick_best_in_cluster",
            "result": {"margin": margin, "tie": False},
        },
        {"seq": 5, "type": "tool_call", "tool": "request_human_approval", "args": {"items": []}},
    ]


def test_unknown_eyes_are_not_a_pivot() -> None:
    kinds = [p["kind"] for p in find_pivots(_chain(["unknown", "unknown"]))]
    assert "eyes_compare_in_group" not in kinds


def test_closed_secondary_face_is_a_pivot() -> None:
    kinds = [p["kind"] for p in find_pivots(_chain(["open", "closed", "unknown"]))]
    assert kinds == ["eyes_compare_in_group"]


def test_margin_pivot_only_below_threshold() -> None:
    assert [p["kind"] for p in find_pivots(_chain(["open"], margin=0.03))] == ["small_margin_to_human"]
    assert find_pivots(_chain(["open"], margin=0.05)) == []


def test_borderline_needs_a_changed_roi() -> None:
    lines = [
        {"seq": 1, "type": "tool_call", "tool": "score_sharpness", "args": {"photo_id": "p", "roi": "full"}},
        {
            "seq": 2,
            "type": "tool_result",
            "tool": "score_sharpness",
            "result": {"label": "borderline", "lap_var": 70},
        },
        {"seq": 3, "type": "tool_call", "tool": "score_sharpness", "args": {"photo_id": "p", "roi": "full"}},
    ]
    assert find_pivots(lines) == []
    lines[2]["args"] = {"photo_id": "p", "roi": "full", "variant": "original"}
    (p,) = find_pivots(lines)
    assert p["kind"] == "sharpness_rescore" and "original" in p["summary"]


# ---------------------------------------------------------------- real agent trace shape (synthetic ids)
# Excerpt of the 2026-10-05 agent run: list-taking tools return {"per_photo": [...]}, and the agent makes a
# contact sheet between pick_best_in_cluster and request_human_approval.

G = "g_00000000-0000-4000-8000-00000000e009_5"
A, B = "00000000-0000-4000-8000-0000000000a1", "00000000-0000-4000-8000-0000000000b2"


def _real_excerpt() -> list[dict]:
    def faces(main: str) -> list[dict]:
        return [
            {"face_id": "f0", "is_main": True, "state": main, "left_open_p": 0.06, "right_open_p": 0.04},
            {"face_id": "f1", "is_main": False, "state": "open", "left_open_p": 1.0, "right_open_p": 0.97},
            {
                "face_id": "f2",
                "is_main": False,
                "state": "partial",
                "left_open_p": 0.99,
                "right_open_p": 0.17,
            },
        ]

    per_photo = [
        {
            "photo_id": p,
            "any_main_subject_closed": True,
            "main_subject_face_id": "f0",
            "confidence": 0.898,
            "per_face": faces("closed"),
        }
        for p in (A, B)
    ]
    return [
        {"seq": 10, "type": "tool_call", "tool": "score_eyes_open", "args": {"photo_ids": [A, B]}},
        {"seq": 11, "type": "tool_result", "tool": "score_eyes_open", "result": {"per_photo": per_photo}},
        {"seq": 12, "type": "tool_call", "tool": "score_face_quality", "args": {"photo_ids": [A, B]}},
        {"seq": 13, "type": "tool_result", "tool": "score_face_quality", "result": {"per_photo": []}},
        {"seq": 14, "type": "model_reasoning", "text": "Both frames identical, main subject eyes closed."},
        {"seq": 15, "type": "tool_call", "tool": "pick_best_in_cluster", "args": {"group_id": G}},
        {
            "seq": 16,
            "type": "tool_result",
            "tool": "pick_best_in_cluster",
            "result": {
                "best_photo_id": A,
                "tie": True,
                "margin": 0.0,
                "ranking": [{"photo_id": A, "score": 0.8037}, {"photo_id": B, "score": 0.8037}],
            },
        },
        {"seq": 17, "type": "model_reasoning", "text": "Tie=true, margin=0: P5 triggers human review."},
        {"seq": 18, "type": "tool_call", "tool": "make_contact_sheet", "args": {"group_id": G}},
        {
            "seq": 19,
            "type": "tool_result",
            "tool": "make_contact_sheet",
            "result": {"n_tiles": 2, "key": "sheets/x.jpg"},
        },
        {
            "seq": 20,
            "type": "tool_call",
            "tool": "request_human_approval",
            "args": {"items": [{"group_id": G, "reason": "tie"}]},
        },
        {"seq": 21, "type": "interrupt", "text": "run paused for 1 approval item(s)"},
    ]


def test_real_shape_finds_policy_3_and_5_through_intervening_calls() -> None:
    pivots = find_pivots(_real_excerpt())
    got = [(p["policy"], p["result_seq"], p["call_seq"], p["via_seqs"]) for p in pivots]
    assert got == [("Policy 3", 11, 15, [12]), ("Policy 5", 16, 20, [18])]
    p5 = pivots[1]
    assert p5["reasoning_seqs"] == [17]
    assert p5["summary"] == "Best-of-group tie (margin 0.00) → asked a human"
    assert pivots[0]["summary"] == "Main subject's eyes closed → compared within the group"


def test_a_non_helper_call_breaks_the_chain() -> None:
    lines = _real_excerpt()
    lines[8] = {"seq": 18, "type": "tool_call", "tool": "write_decision", "args": {"decisions": []}}
    assert [p["policy"] for p in find_pivots(lines)] == ["Policy 3"]


def test_per_photo_sharpness_borderline_then_new_roi() -> None:
    lines = [
        {
            "seq": 1,
            "type": "tool_call",
            "tool": "score_sharpness",
            "args": {"photo_ids": [A, B], "roi": "full"},
        },
        {
            "seq": 2,
            "type": "tool_result",
            "tool": "score_sharpness",
            "result": {
                "per_photo": [
                    {"photo_id": A, "label": "sharp", "lap_var": 9000.0},
                    {"photo_id": B, "label": "borderline", "lap_var": 71.4},
                ]
            },
        },
        {"seq": 3, "type": "model_reasoning", "text": "B is borderline; re-score on the face."},
        {"seq": 4, "type": "tool_call", "tool": "score_sharpness", "args": {"photo_ids": [B], "roi": "face"}},
    ]
    (p,) = find_pivots(lines)
    assert p["policy"] == "Policy 2" and p["call_seq"] == 4
    assert p["summary"] == "Borderline sharpness (Laplacian variance 71.4) → re-scored on the face ROI"
