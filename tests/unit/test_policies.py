"""Agent policy enforcement (keepframe.agent.policies): the checks the hooks apply to every call."""

from __future__ import annotations

import pytest

from keepframe.agent import policies as ap
from keepframe.agent._tool_stubs import demo_scenario
from keepframe.agent.context import RunContext
from keepframe.agent.strands_agent import load_work


@pytest.fixture()
def ctx() -> RunContext:
    return load_work("ev_demo", "run_t", scenario=demo_scenario())


def put(ctx: RunContext, pid: str, key: str, res: dict, order: int) -> None:
    ctx.photos[pid]["ev"][key] = {**res, "_order": order}


def sharp(label: str, lap: float, roi: str = "full", variant: str = "processed", conf: float = 0.9,
          blur_kind: str | None = None) -> dict:
    return {"lap_var": lap, "label": label, "confidence": conf, "roi_used": roi, "roi_requested": roi,
            "variant": variant, "variant_requested": variant, "blur_kind": blur_kind}


FACES2 = {"faces": [{"face_id": "f0", "bbox_px": [0, 0, 200, 240], "source": "frsInfo"},
                    {"face_id": "f1", "bbox_px": [300, 0, 90, 110], "source": "yunet"}], "n_faces": 2}


def test_work_set_from_worker_flags(ctx: RunContext) -> None:
    ids = [i.item_id for i in ctx.items]
    assert ids == ["g_412", "p_9002"]  # g_413 and p_9001 were settled by the worker
    assert ctx.photos["p_8831"]["known_faces"] == 2 and ctx.photos["p_9002"]["known_faces"] == 0


def test_p2_borderline_needs_faces_rescore_before_labelling(ctx: RunContext) -> None:
    put(ctx, "p_8831", "sharpness_full_processed", sharp("borderline", 74.2), 1)
    w = ap.check_write(ctx, [{"photo_id": "p_8831", "verdict": "keep", "reason_codes": ["KEEP_BEST_OF_GROUP"],
                              "confidence": 0.9}])
    assert w.blocks and w.blocks[0].policy == "P2"
    assert "roi='faces'" in w.blocks[0].message
    put(ctx, "p_8831", "detect_faces", FACES2, 2)
    put(ctx, "p_8831", "sharpness_faces_processed", sharp("sharp", 188.9, "faces"), 3)
    assert ap.pending_sharpness_step(ctx, "p_8831") is None


def test_p2_still_borderline_asks_for_original(ctx: RunContext) -> None:
    put(ctx, "p_8831", "sharpness_full_processed", sharp("borderline", 80), 1)
    put(ctx, "p_8831", "detect_faces", FACES2, 2)
    put(ctx, "p_8831", "sharpness_faces_processed", sharp("borderline", 95, "faces"), 3)
    assert ap.pending_sharpness_step(ctx, "p_8831") == {"roi": "faces", "variant": "original"}
    put(ctx, "p_8831", "sharpness_faces_original", sharp("sharp", 160, "faces", "original"), 4)
    assert ap.pending_sharpness_step(ctx, "p_8831") is None
    assert ap.final_sharpness(ctx.photos["p_8831"]["ev"])["variant"] == "original"


def test_blur_reject_needs_blurry_evidence(ctx: RunContext) -> None:
    put(ctx, "p_8832", "sharpness_full_processed", sharp("sharp", 300), 1)
    w = ap.check_write(ctx, [{"photo_id": "p_8832", "verdict": "reject", "reason_codes": ["BLUR_FOCUS"],
                              "confidence": 0.95}])
    assert w.blocks and "not blurry" in w.blocks[0].message


def _eyes(main_state: str, p_main: tuple, other_states: list[str]) -> dict:
    per = [{"face_id": "f0", "left_open_p": p_main[0], "right_open_p": p_main[1], "state": main_state,
            "is_main": True}]
    for i, st in enumerate(other_states, start=1):
        per.append({"face_id": f"f{i}", "left_open_p": 0.1 if st == "closed" else 0.9,
                    "right_open_p": 0.1 if st == "closed" else 0.9, "state": st, "is_main": False})
    return {"per_face": per, "main_subject_face_id": "f0"}


def test_p3_eyes_closed_only_for_main_subject(ctx: RunContext) -> None:
    pid = "p_8833"
    put(ctx, pid, "sharpness_full_processed", sharp("sharp", 240), 1)
    put(ctx, pid, "detect_faces", FACES2, 2)
    put(ctx, pid, "sharpness_faces_processed", sharp("sharp", 205, "faces"), 3)
    put(ctx, pid, "eyes", _eyes("open", (0.95, 0.93), ["closed"]), 4)
    w = ap.check_write(ctx, [{"photo_id": pid, "verdict": "reject", "reason_codes": ["EYES_CLOSED"],
                              "confidence": 0.95}])
    assert w.blocks and w.blocks[0].policy == "P3"
    put(ctx, pid, "eyes", _eyes("closed", (0.06, 0.04), ["open"]), 5)
    w = ap.check_write(ctx, [{"photo_id": pid, "verdict": "reject", "reason_codes": ["EYES_CLOSED"],
                              "confidence": 0.95}])
    assert not w.blocks


def test_p3_group_photo_one_closed_is_review(ctx: RunContext) -> None:
    pid = "p_8833"
    faces3 = {"faces": FACES2["faces"] + [{"face_id": "f2", "bbox_px": [500, 0, 100, 120], "source": "yunet"}]}
    put(ctx, pid, "sharpness_full_processed", sharp("sharp", 240), 1)
    put(ctx, pid, "detect_faces", faces3, 2)
    put(ctx, pid, "sharpness_faces_processed", sharp("sharp", 205, "faces"), 3)
    put(ctx, pid, "eyes", _eyes("open", (0.9, 0.9), ["closed", "open"]), 4)
    w = ap.check_write(ctx, [{"photo_id": pid, "verdict": "reject", "reason_codes": ["EYES_CLOSED"],
                              "confidence": 0.95}])
    assert w.blocks and "REVIEW_POLICY_GROUP_EYES" in w.blocks[0].message


def test_p3_null_probabilities_are_unknown_not_closed(ctx: RunContext) -> None:
    pid = "p_8833"
    put(ctx, pid, "sharpness_full_processed", sharp("sharp", 240), 1)
    put(ctx, pid, "detect_faces", FACES2, 2)
    put(ctx, pid, "sharpness_faces_processed", sharp("sharp", 205, "faces"), 3)
    eyes = {"per_face": [{"face_id": "f0", "left_open_p": None, "right_open_p": None, "state": "unknown"}],
            "main_subject_face_id": "f0"}
    put(ctx, pid, "eyes", eyes, 4)
    w = ap.check_write(ctx, [{"photo_id": pid, "verdict": "reject", "reason_codes": ["EYES_CLOSED"],
                              "confidence": 0.95}])
    assert w.blocks and w.blocks[0].policy == "P3"


def test_p3_dark_small_faces_skip_eye_state(ctx: RunContext) -> None:
    pid = "p_8830"
    small = {"faces": [{"face_id": "f0", "bbox_px": [0, 0, 44, 50], "source": "yunet"}]}
    put(ctx, pid, "detect_faces", small, 1)
    put(ctx, pid, "exposure", {"mean_L": 30.0, "label": "under", "clip_lo_pct": 0.5, "confidence": 0.7}, 2)
    assert not ap.eyes_needed(ctx, pid)


def test_p4_never_reject_whole_group(ctx: RunContext) -> None:
    decs = [{"photo_id": p, "verdict": "reject", "reason_codes": ["BURST_NOT_BEST"], "confidence": 0.95,
             "group_id": "g_412"} for p in ctx.groups["g_412"]["members"]]
    w = ap.check_write(ctx, decs)
    assert any(b.policy == "P4" for b in w.blocks)


def test_p5_low_confidence_reject_needs_human(ctx: RunContext) -> None:
    pid = "p_9002"
    put(ctx, pid, "sharpness_saliency_processed", sharp("blurry", 30, "saliency", blur_kind="focus", conf=0.6), 1)
    w = ap.check_write(ctx, [{"photo_id": pid, "verdict": "reject", "reason_codes": ["BLUR_FOCUS"],
                              "confidence": 0.6}])
    assert not w.blocks and w.approvals and w.approvals[0]["photo_id"] == pid


def test_p5_small_margin_group_needs_human(ctx: RunContext) -> None:
    g = ctx.groups["g_412"]
    for i, p in enumerate(g["members"]):
        put(ctx, p, "sharpness_full_processed", sharp("sharp", 300 + i), 1)
    g["best"] = {"best_photo_id": "p_8830", "tie": False, "margin": 0.03,
                 "ranking": [{"photo_id": p, "score": 0.8 - 0.03 * i, "breakdown": {}}
                             for i, p in enumerate(g["members"])]}
    decs = [{"photo_id": p, "verdict": "keep" if i == 0 else "reject",
             "reason_codes": ["KEEP_BEST_OF_GROUP"] if i == 0 else ["BURST_NOT_BEST"], "confidence": 0.9,
             "group_id": "g_412"} for i, p in enumerate(g["members"])]
    w = ap.check_write(ctx, decs)
    assert not w.blocks and any(a.get("group_id") == "g_412" and isinstance(a["proposed"], dict)
                                for a in w.approvals)


def test_p6_artistic_intent_guard(ctx: RunContext) -> None:
    pid = "p_9002"  # ExposureTime 0.25 s, 22:00
    put(ctx, pid, "sharpness_saliency_processed",
        sharp("blurry", 35, "saliency", blur_kind="motion", conf=0.9), 1)
    w = ap.check_write(ctx, [{"photo_id": pid, "verdict": "reject", "reason_codes": ["BLUR_MOTION"],
                              "confidence": 0.9}])
    assert w.blocks and w.blocks[0].policy == "P6"


def test_p7_budget_blocks_optional_tools(ctx: RunContext) -> None:
    assert ap.check_tool_call(ctx, "score_composition", {"photo_id": "p_8830"}) is None
    ctx.tool_calls = ctx.event_call_cap() - 1  # < 20% left
    b = ap.check_tool_call(ctx, "score_composition", {"photo_id": "p_8830"})
    assert b is not None and b.policy == "P7"


def test_p1_prerequisites(ctx: RunContext) -> None:
    b = ap.check_tool_call(ctx, "score_eyes_open", {"photo_ids": ["p_8830"]})
    assert b is not None and "detect_faces" in b.message
    b = ap.check_tool_call(ctx, "pick_best_in_cluster", {"group_id": "g_412"})
    assert b is not None and b.policy == "P1"


def test_human_and_worker_attribution(ctx: RunContext) -> None:
    w = ap.check_write(ctx, [{"photo_id": "p_8830", "verdict": "keep", "reason_codes": ["KEEP_BEST_OF_GROUP"],
                              "confidence": 1.0, "decided_by": "human"}])
    assert w.blocks and w.blocks[0].policy == "HITL"
    w = ap.check_write(ctx, [{"photo_id": "p_8830", "verdict": "keep", "reason_codes": ["KEEP_BEST_OF_GROUP"],
                              "confidence": 1.0, "decided_by": "worker"}])
    assert w.blocks
    ctx.human_answers["g_412"] = {"p_8830": "keep"}
    w = ap.check_write(ctx, [{"photo_id": "p_8830", "verdict": "keep", "reason_codes": ["KEEP_BEST_OF_GROUP"],
                              "confidence": 1.0, "decided_by": "human"}])
    assert not w.blocks


def test_write_decision_validation() -> None:
    from keepframe.tools.decision import DecisionError, validate_decisions

    ok = {"photo_id": "p1", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"], "confidence": 0.9}
    assert validate_decisions([ok])[0]["decided_by"] == "agent"
    for bad in ({**ok, "verdict": "delete"}, {**ok, "reason_codes": []}, {**ok, "reason_text": "x" * 141},
                {**ok, "confidence": 1.5}, {**ok, "decided_by": "robot"}, {**ok, "reason_codes": ["NOPE"]},
                {**ok, "reason_codes": ["DUPLICATE_OF"]}):
        with pytest.raises(DecisionError):
            validate_decisions([bad])
    assert validate_decisions([{**ok, "decided_by": "worker"}])[0]["decided_by"] == "worker"
    # human/scripted need a matching human_source; agent/worker must not carry one
    assert validate_decisions([{**ok, "decided_by": "scripted", "human_source": "scripted"}])[0]["human_source"] == "scripted"
    assert validate_decisions([{**ok, "decided_by": "human", "human_source": "file"}])[0]["decided_by"] == "human"
    for bad in ({**ok, "decided_by": "human"}, {**ok, "decided_by": "human", "human_source": "scripted"},
                {**ok, "decided_by": "scripted", "human_source": "ui"}, {**ok, "human_source": "ui"}):
        with pytest.raises(DecisionError):
            validate_decisions([bad])
