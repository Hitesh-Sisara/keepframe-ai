"""pick_best_in_cluster: weighted deterministic ranking, margin, tie (docs/REPORT.md §3)."""

from __future__ import annotations

from keepframe.tools.best import rank_group


def _r(lap, label="ok", eyes=None, fiqa=None, roi="full"):
    r = {f"sharpness_{roi}_processed": {"lap_var": lap}, "exposure": {"label": label}}
    if eyes is not None:
        r["eyes"] = {"per_face": [{"face_id": "f0", "left_open_p": eyes, "right_open_p": eyes, "state": "open"}]}
    if fiqa is not None:
        r["face_quality"] = {"per_face": [{"face_id": "f0", "fiqa": fiqa}]}
    return r


def test_sharpest_wins_without_faces():
    res = rank_group({"a": _r(2000), "b": _r(300), "c": _r(50)})
    assert res["best_photo_id"] == "a"
    assert [e["photo_id"] for e in res["ranking"]] == ["a", "b", "c"]
    bd = res["ranking"][0]["breakdown"]
    assert bd["sharp_faces_norm"] == 1.0 and bd["composition"] == 0.5 and bd["eyes_open_mean"] == 0.5
    assert res["margin"] > 0.05 and not res["tie"]


def test_weights_formula():
    res = rank_group({"a": _r(1000, eyes=1.0, fiqa=0.8), "b": _r(100, label="under", eyes=0.2, fiqa=0.4)})
    # 10x apart -> spread guard saturated, plain min-max applies
    a = next(e for e in res["ranking"] if e["photo_id"] == "a")
    expected = 0.35 * 1.0 + 0.25 * 1.0 + 0.15 * 1.0 + 0.15 * 0.8 + 0.10 * 0.5
    assert abs(a["score"] - expected) < 1e-6
    b = next(e for e in res["ranking"] if e["photo_id"] == "b")
    assert abs(b["score"] - (0.25 * 0.2 + 0.15 * 0.4 + 0.10 * 0.5)) < 1e-6


def test_eyes_beat_small_sharpness_gap_but_not_a_big_one():
    res = rank_group({"open": _r(800, eyes=0.95), "closed": _r(1000, eyes=0.05)})
    assert res["best_photo_id"] == "open"
    sh = {e["photo_id"]: e["breakdown"]["sharp_faces_norm"] for e in res["ranking"]}
    assert sh["closed"] == 1.0 and 0.8 < sh["open"] < 1.0
    res = rank_group({"open": _r(60, eyes=0.95), "closed": _r(2000, eyes=0.05)})
    assert res["best_photo_id"] == "closed"  # the policy layer (EYES_CLOSED) handles this case


def test_tie_and_deterministic_tiebreak():
    res = rank_group({"b": _r(500), "a": _r(500)})
    assert res["tie"] and res["margin"] == 0.0
    assert res["best_photo_id"] == "a"  # photo_id order breaks exact ties
    assert rank_group({"a": _r(500), "b": _r(500)}) == res


def test_custom_weights_and_like_for_like_roi():
    group = {"a": {**_r(100), **_r(900, roi="faces")}, "b": {**_r(800), **_r(200, roi="faces")}}
    res = rank_group(group)
    assert res["sharpness_key"] == "sharpness_faces_processed" and res["best_photo_id"] == "a"
    # without faces ROI on every member, fall back to the common full-frame key
    res2 = rank_group({"a": group["a"], "b": _r(800)})
    assert res2["sharpness_key"] == "sharpness_full_processed" and res2["best_photo_id"] == "b"
    res3 = rank_group({"a": _r(100, label="ok"), "b": _r(900, label="over")},
                      weights={"sharp_faces_norm": 0.0, "exposure_ok": 1.0})
    assert res3["best_photo_id"] == "a"


def test_singleton_group_margin():
    res = rank_group({"only": _r(10)})
    assert res["best_photo_id"] == "only" and res["margin"] == 1.0 and not res["tie"]
