"""C09/C22 (docs/FAILURE_CASES.md): byte-identical copies in a group are DUPLICATE_OF, certain, never asked about."""

from __future__ import annotations

from keepframe.worker import policies as P


def _sharp(label: str = "sharp") -> dict:
    return {"label": label, "confidence": 0.9, "blur_kind": None, "lap_var": 500.0, "roi_used": "full"}


def _exp() -> dict:
    return {"label": "ok", "confidence": 0.9, "clip_lo_pct": 0.0, "mean_L": 120.0, "no_subject": False}


def _best(scores: dict[str, float]) -> dict:
    ranking = sorted(({"photo_id": p, "score": s} for p, s in scores.items()), key=lambda r: (-r["score"], r["photo_id"]))
    return {"best_photo_id": ranking[0]["photo_id"], "ranking": ranking, "margin": 0.0, "tie": False}


def _ev(*pids: str) -> dict[str, P.PhotoEvidence]:
    return {p: P.PhotoEvidence(p, _sharp(), _exp()) for p in pids}


def test_md5_representatives_chain_to_root_and_prefer_keeper() -> None:
    dups = {"b": "a", "c": "a", "e": "d"}  # cluster.exact_duplicates: every copy -> first file of its md5 class
    assert P.md5_representatives(["c", "a", "b", "d", "e", "f"], "c", dups) == {"a": "c", "b": "c", "e": "d"}
    assert P.md5_representatives(["a", "b"], "z", {}) == {}


def test_copies_of_the_keeper_are_duplicate_of_even_when_the_map_points_elsewhere() -> None:
    # every copy maps to "a" (cluster.exact_duplicates); copies score the same, so the keeper is whichever copy the
    # ranking tie-break puts first, and every other copy must point at the keeper, not at "a"
    ev = _ev("a", "b", "c", "x")
    best = _best({"a": 0.90, "b": 0.90, "c": 0.90, "x": 0.40})
    best["ranking"] = [best["ranking"][i] for i in (1, 0, 2, 3)]  # tie-break put copy "b" first
    best["best_photo_id"] = "b"
    gd = P.decide_group("g", best, ev, duplicates={"b": "a", "c": "a"})
    by = {d.photo_id: d for d in gd.decisions}
    assert gd.keeper_photo_id == "b"
    for pid in ("a", "c"):  # before the fix "c" (whose map entry points at "a", not the keeper) was BURST_NOT_BEST
        assert by[pid].verdict == "reject" and by[pid].reason_codes == ["DUPLICATE_OF:b"]
        assert by[pid].confidence == 1.0 and not by[pid].needs_approval
    assert by["x"].reason_codes[0] == "BURST_NOT_BEST"


def test_copies_of_a_non_keeper_collapse_to_their_best_copy_and_are_never_asked() -> None:
    # r1 and r2 are the same file and a close rival of the keeper: the question is about r1 only
    ev = _ev("k", "r1", "r2", "z")
    gd = P.decide_group("g", _best({"k": 0.90, "r1": 0.88, "r2": 0.88, "z": 0.2}), ev, duplicates={"r2": "r1"},
                        phash_to={("k", "r1"): 14, ("k", "r2"): 14, ("k", "z"): 14})
    by = {d.photo_id: d for d in gd.decisions}
    assert by["r1"].verdict == "review" and gd.needs_approval
    assert by["r2"].verdict == "reject" and by["r2"].reason_codes == ["DUPLICATE_OF:r1"]
    assert by["r2"].confidence == 1.0 and not by["r2"].needs_approval
    assert gd.tied_candidates == ["k", "r1"]  # no identical files offered as alternatives (C22)
    assert sum(1 for d in gd.decisions if d.verdict == "review") == 1


def test_identical_twins_never_get_different_verdicts() -> None:
    ev = _ev("k", "a", "b", "c", "d")
    dups = {"b": "a", "d": "c"}
    gd = P.decide_group("g", _best({"k": 0.9, "a": 0.89, "b": 0.89, "c": 0.5, "d": 0.5}), ev, duplicates=dups,
                        phash_to={(x, y): 14 for x in ev for y in ev if x < y})
    by = {d.photo_id: d for d in gd.decisions}
    # the representative carries the judgement; its copy is a certain reject pointing at it
    assert {by["b"].reason_codes[0], by["d"].reason_codes[0]} == {"DUPLICATE_OF:a", "DUPLICATE_OF:c"}
    assert all(not by[p].needs_approval for p in ("b", "d"))


def test_decide_group_is_deterministic_with_md5_copies() -> None:
    ev = _ev("k", "a", "b", "c")
    args = ("g", _best({"k": 0.9, "a": 0.9, "b": 0.88, "c": 0.88}), ev, {"a": "k", "c": "b"})
    one, two = P.decide_group(*args), P.decide_group(*args)
    assert [(d.photo_id, d.verdict, d.reason_codes, d.confidence) for d in one.decisions] == \
           [(d.photo_id, d.verdict, d.reason_codes, d.confidence) for d in two.decisions]
