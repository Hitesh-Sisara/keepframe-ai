"""keepframe.worker.policies (docs/REPORT.md §4) on hand-built tool results. (tests/unit/test_policies.py is owned
by the agent tests; these cover the pure functions from the core CV side.)"""

from __future__ import annotations

from keepframe.worker import policies as P


def _sharp(label, conf=0.9, kind=None, lap=500.0):
    return {"label": label, "confidence": conf, "blur_kind": kind, "lap_var": lap, "roi_used": "full"}


def _exp(label="ok", conf=0.9, clip_lo=0.0, mean_l=120.0, no_subject=False):
    return {"label": label, "confidence": conf, "clip_lo_pct": clip_lo, "mean_L": mean_l, "no_subject": no_subject}


def _face(fid, px):
    return {"face_id": fid, "bbox_px": [0, 0, px, px]}


def _eye(fid, p, state):
    return {"face_id": fid, "left_open_p": p, "right_open_p": p, "state": state}


def test_p1_tools_and_p7_budget():
    assert P.tools_for(1)[:2] == ["score_sharpness:saliency", "score_exposure"]
    assert "detect_faces" in P.tools_for(3) and "pick_best_in_cluster" in P.tools_for(3)
    assert "score_composition" in P.tools_for(3, 0.5) and "score_composition" not in P.tools_for(3, 0.1)


def test_p2_escalation_chain():
    b = _sharp("borderline")
    assert P.next_sharpness_step(b, 2, True, ["full:processed"]) == {"roi": "faces", "variant": "processed"}
    assert P.next_sharpness_step(b, 2, True, ["full:processed", "faces:processed"]) == {"roi": "faces",
                                                                                         "variant": "original"}
    assert P.next_sharpness_step(b, 0, True, ["full:processed"]) == {"roi": "full", "variant": "original"}
    assert P.next_sharpness_step(b, 0, False, ["full:processed"]) is None
    assert P.next_sharpness_step(_sharp("sharp"), 2, True) is None
    # blurry full frame with faces is re-scored on faces (bokeh guard), but never goes to the original
    assert P.next_sharpness_step(_sharp("blurry"), 1, True) == {"roi": "faces", "variant": "processed"}
    assert P.next_sharpness_step(_sharp("blurry"), 1, True, ["faces:processed"]) is None


def test_p3_eyes_main_subject_and_group_photo():
    faces = [_face("f0", 200), _face("f1", 80), _face("f2", 60)]
    closed_main = {"per_face": [_eye("f0", 0.05, "closed"), _eye("f1", 0.9, "open")]}
    d = P.eyes_defect(closed_main, faces)
    assert d.code == "EYES_CLOSED" and d.verdict == "reject" and d.confidence >= 0.8
    weak = {"per_face": [_eye("f0", 0.25, "closed")]}
    assert P.eyes_defect(weak, faces).confidence < 0.8  # gated to review downstream
    one_other = {"per_face": [_eye("f0", 0.9, "open"), _eye("f1", 0.1, "closed"), _eye("f2", 0.9, "open")]}
    assert P.eyes_defect(one_other, faces).code == "REVIEW_POLICY_GROUP_EYES"
    assert P.eyes_defect(one_other, faces[:2]) is None  # < 3 faces
    assert not P.should_run_eyes(30.0, [_face("f0", 50)])  # dark + small -> skip
    assert P.should_run_eyes(30.0, [_face("f0", 200)])
    assert not P.should_run_eyes(120.0, [_face("f0", 20)])  # below main-subject size


def test_p5_gate_and_singleton_decisions():
    assert P.gate_confidence("reject", 0.7) == ("review", True)
    assert P.gate_confidence("reject", 0.85) == ("reject", False)
    keep = P.decide_singleton(P.PhotoEvidence("p", _sharp("sharp"), _exp()))
    assert keep.verdict == "keep" and keep.reason_codes == ["KEEP_SINGLETON"]
    low = P.decide_singleton(P.PhotoEvidence("p", _sharp("blurry", 0.6, "focus"), _exp()))
    assert low.verdict == "review" and low.reason_codes == ["BLUR_FOCUS", "REVIEW_LOW_CONFIDENCE"]
    assert low.needs_approval
    rej = P.decide_singleton(P.PhotoEvidence("p", _sharp("blurry", 0.9, "focus"), _exp()))
    assert rej.verdict == "reject" and rej.reason_codes == ["BLUR_FOCUS"] and len(rej.reason_text) <= 140


def test_exposure_policy():
    under = P.decide_singleton(P.PhotoEvidence("p", _sharp("sharp"), _exp("under", 0.95, 0.6, 20.0)))
    assert under.verdict == "review" and under.reason_codes == ["EXPOSURE_UNDER"]  # dark alone is review
    faces = [_face("f0", 50)]
    dark_unreadable = P.PhotoEvidence("p", _sharp("sharp"), _exp("under", 0.95, 0.6, 20.0), faces=faces,
                                      faces_known=True, eyes={"per_face": [_eye("f0", None, "unknown")]},
                                      eyes_known=True)
    assert P.decide_singleton(dark_unreadable).verdict == "reject"
    over = P.decide_singleton(P.PhotoEvidence("p", _sharp("sharp"), _exp("over", 0.9)))
    assert over.verdict == "reject" and over.reason_codes == ["EXPOSURE_OVER"]
    flat = P.decide_singleton(P.PhotoEvidence("p", _sharp("blurry", 0.95, "focus", 0.0),
                                              _exp("low_contrast", 0.95, no_subject=True), mfaces=0))
    assert flat.reason_codes[0] == "NO_SUBJECT"


def test_p6_artistic_intent():
    s = _sharp("blurry", 0.9, "motion")
    long_exp = P.decide_singleton(P.PhotoEvidence("p", s, _exp(), exposure_time_s=1 / 15))
    assert long_exp.verdict == "review" and long_exp.reason_codes[0] == "REVIEW_ARTISTIC_INTENT"
    dance = P.PhotoEvidence("p", s, _exp(clip_lo=0.35), hour=22)
    assert P.decide_singleton(dance).verdict == "review"
    day = P.PhotoEvidence("p", s, _exp(clip_lo=0.35), hour=11)
    assert P.decide_singleton(day).verdict == "reject"
    assert P.is_reception_hour(23) and P.is_reception_hour(2) and not P.is_reception_hour(12)
    assert P.local_hour(0.0, None) == 5  # 1970-01-01T00:00Z = 05:30 IST


def _best(scores):
    ranking = sorted(({"photo_id": p, "score": s} for p, s in scores.items()), key=lambda r: (-r["score"], r["photo_id"]))
    return {"best_photo_id": ranking[0]["photo_id"], "ranking": ranking, "margin": ranking[0]["score"] -
            ranking[1]["score"], "tie": False}


def test_p4_never_reject_whole_group_and_p5_margin():
    ev = {p: P.PhotoEvidence(p, _sharp("blurry", 0.95, "focus", 10.0), _exp()) for p in ("a", "b", "c")}
    gd = P.decide_group("g", _best({"a": 0.6, "b": 0.4, "c": 0.2}), ev)
    verdicts = {d.photo_id: d.verdict for d in gd.decisions}
    assert verdicts["a"] == "keep" and gd.all_failed and gd.needs_approval
    assert [d.reason_codes for d in gd.decisions if d.photo_id == "a"] == [["KEEP_BEST_OF_GROUP"]]

    ok = {p: P.PhotoEvidence(p, _sharp("sharp"), _exp()) for p in ("a", "b", "c")}
    gd = P.decide_group("g", _best({"a": 0.90, "b": 0.88, "c": 0.3}), ok)
    v = {d.photo_id: d.verdict for d in gd.decisions}
    assert v == {"a": "keep", "b": "review", "c": "reject"} and gd.needs_approval  # margin 0.02 < 0.05

    clear = P.decide_group("g", _best({"a": 0.9, "b": 0.5}), {p: ok[p] for p in ("a", "b")})
    assert not clear.needs_approval and {d.photo_id: d.verdict for d in clear.decisions}["b"] == "reject"


def test_keeper_skips_defective_top_and_duplicates():
    ev = {"a": P.PhotoEvidence("a", _sharp("sharp"), _exp("over", 0.95)),
          "b": P.PhotoEvidence("b", _sharp("sharp"), _exp()),
          "c": P.PhotoEvidence("c", _sharp("sharp"), _exp())}
    gd = P.decide_group("g", _best({"a": 0.9, "b": 0.7, "c": 0.69}), ev, duplicates={"c": "b"})
    v = {d.photo_id: (d.verdict, d.reason_codes[0]) for d in gd.decisions}
    assert gd.keeper_photo_id == "b" and v["b"] == ("keep", "KEEP_BEST_OF_GROUP")
    assert v["c"] == ("reject", "DUPLICATE_OF:b")
    assert v["a"][0] in ("reject", "review") and gd.needs_approval  # top-scored had a defect
    # near-identical duplicate of the keeper is not a meaningful tie
    gd2 = P.decide_group("g", _best({"b": 0.7, "c": 0.7}), {p: ev[p] for p in ("b", "c")}, duplicates={"c": "b"})
    assert not gd2.tie and not gd2.needs_approval


def test_reason_text_limit():
    t = P.reason_text("reject", ["BLUR_MOTION", "EXPOSURE_UNDER", "LOW_CONTRAST", "BURST_NOT_BEST", "NO_SUBJECT",
                                 "DUPLICATE_OF:" + "x" * 80], {"sharpness": {"lap_var": 12.0}})
    assert len(t) <= 140 and t.startswith("Reject: motion blur")


def test_blurred_phash_twin_is_burst_not_duplicate():
    ev = {"a": P.PhotoEvidence("a", _sharp("sharp"), _exp()),
          "b": P.PhotoEvidence("b", _sharp("sharp"), _exp()),
          "c": P.PhotoEvidence("c", _sharp("blurry", 0.9, "focus", 20.0), _exp())}
    gd = P.decide_group("g", _best({"a": 0.9, "b": 0.88, "c": 0.4}), ev, phash_to={("a", "b"): 1, ("a", "c"): 1})
    v = {d.photo_id: d.reason_codes for d in gd.decisions}
    assert v["b"][0] == "DUPLICATE_OF:a"
    assert v["c"][:2] == ["BURST_NOT_BEST", "BLUR_FOCUS"]


def _eyes2(p_main, p_other=0.95):
    st = lambda p: "open" if p >= 0.6 else ("closed" if p <= 0.3 else "partial")  # noqa: E731
    return {"per_face": [_eye("f0", p_main, st(p_main)), _eye("f1", p_other, st(p_other))],
            "main_subject_face_id": "f0"}


def _burst(n, gap=0.001, eyes=None):
    """n near-identical frames, 2 faces, scores decreasing by `gap`."""
    faces = [_face("f0", 220), _face("f1", 180)]
    ev = {f"p{i:02d}": P.PhotoEvidence(f"p{i:02d}", _sharp("sharp"), _exp(), faces=faces, faces_known=True,
                                       eyes=(eyes or {}).get(i, _eyes2(0.95)), eyes_known=True)
          for i in range(n)}
    scores = {f"p{i:02d}": 0.9 - gap * i for i in range(n)}
    return ev, scores


def test_twenty_frame_near_identical_burst_one_keep_one_question():
    ev, scores = _burst(20)
    pairs = {(a, b): 1 for a in ev for b in ev if a < b}  # pHash 1 everywhere
    gd = P.decide_group("g", _best(scores), ev, phash_to=pairs)
    v = [d.verdict for d in gd.decisions]
    assert v.count("keep") == 1 and v.count("review") == 0 and v.count("reject") == 19
    assert not gd.needs_approval and gd.tied_candidates == []
    for d in gd.decisions:
        if d.verdict == "reject":
            assert d.reason_codes[0] in ("BURST_NOT_BEST", "DUPLICATE_OF:p00") and d.confidence >= 0.95
            assert "REVIEW_LOW_CONFIDENCE" not in d.reason_codes


def test_burst_with_distinct_rival_asks_once_about_the_keep_slot():
    ev, scores = _burst(20, gap=0.002)
    pairs = {(a, b): 14 for a in ev for b in ev if a < b}  # distinct framing (pHash far) but close scores
    gd = P.decide_group("g", _best(scores), ev, phash_to=pairs)
    reviews = [d for d in gd.decisions if d.verdict == "review"]
    assert gd.needs_approval and len(gd.tied_candidates) <= 3 and gd.tied_candidates[0] == gd.keeper_photo_id
    assert len(reviews) == 1 and reviews[0].photo_id == gd.tied_candidates[1]  # the one asked alternative
    rejects = [d for d in gd.decisions if d.verdict == "reject"]
    assert len(rejects) == 18 and all(d.confidence >= 0.8 for d in rejects)
    assert all(d.needs_approval == (d.photo_id in gd.tied_candidates) for d in gd.decisions)
    assert sum(1 for d in gd.decisions if d.verdict == "keep") == 1


def test_runner_up_with_distinct_merit_is_review():
    # keeper (highest score) has the main subject's eyes closed only weakly flagged; runner-up has eyes open
    ev, scores = _burst(5, gap=0.1, eyes={0: _eyes2(0.35), 3: _eyes2(0.95)})
    ev["p00"] = P.PhotoEvidence("p00", _sharp("sharp"), _exp(), faces=ev["p00"].faces, faces_known=True,
                                eyes={"per_face": [_eye("f0", 0.2, "closed"), _eye("f1", 0.9, "open")],
                                      "main_subject_face_id": "f0"}, eyes_known=True)
    pairs = {(a, b): 12 for a in ev for b in ev if a < b}
    gd = P.decide_group("g", _best(scores), ev, phash_to=pairs)
    # p00 closed (p_closed 0.8) is rejected for EYES_CLOSED; keeper moves to p01
    by = {d.photo_id: d for d in gd.decisions}
    assert by["p00"].verdict == "reject" and "EYES_CLOSED" in by["p00"].reason_codes
    assert gd.keeper_photo_id == "p01" and gd.needs_approval  # top-scored had a defect: one question
    assert sum(1 for d in gd.decisions if d.verdict == "keep") == 1


def test_more_faces_is_a_distinct_merit():
    ev, scores = _burst(3, gap=0.2)
    ev["p02"] = P.PhotoEvidence("p02", _sharp("sharp"), _exp(), faces=[_face("f0", 220), _face("f1", 180),
                                                                         _face("f2", 150)], faces_known=True)
    gd = P.decide_group("g", _best(scores), ev, phash_to={("p00", "p02"): 15, ("p00", "p01"): 15})
    by = {d.photo_id: d.verdict for d in gd.decisions}
    assert by == {"p00": "keep", "p01": "reject", "p02": "review"} and gd.needs_approval


def test_exposure_over_soft_label_not_a_defect():
    over_lit = _exp("over", 0.95)
    over_lit["over_flag"] = False
    d = P.decide_singleton(P.PhotoEvidence("p", _sharp("sharp"), over_lit))
    assert d.verdict == "keep" and d.reason_codes == ["KEEP_SINGLETON"]
    blown = {**over_lit, "over_flag": True}
    assert P.decide_singleton(P.PhotoEvidence("p", _sharp("sharp"), blown)).reason_codes == ["EXPOSURE_OVER"]
