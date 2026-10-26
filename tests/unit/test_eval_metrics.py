"""compute_metrics on a tiny synthetic fixture whose answers are worked out by hand."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("compute_metrics", ROOT / "eval" / "compute_metrics.py")
cm = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(cm)

EV = "ev_test"
COLS = ["photo_id", "group_id_human", "verdict_human", "reasons_human", "best_of_group_human", "labeller",
        "labelled_at", "notes"]
# Human truth: burst G1 = p1,p2,p3 (p1 best), burst G2 = p4,p5 (p5 best), singletons p6,p7,p8.
LABELS_A = [
    ("p1", "G1", "keep", "", "1"),
    ("p2", "G1", "reject", "BURST_NOT_BEST", "0"),
    ("p3", "G1", "reject", "BLUR_MOTION", "0"),
    ("p4", "G2", "reject", "EYES_CLOSED", "0"),
    ("p5", "G2", "keep", "", "1"),
    ("p6", "", "keep", "", "0"),
    ("p7", "", "reject", "BLUR_FOCUS", "0"),
    ("p8", "", "keep", "", "0"),
]
# Second labeller on p1..p4: disagrees on p3 only.
LABELS_B = [("p1", "G1", "keep", "", "1"), ("p2", "G1", "reject", "BURST_NOT_BEST", "0"),
            ("p3", "G1", "keep", "", "0"), ("p4", "G2", "reject", "EYES_CLOSED", "0")]
# Agent: groups {p1,p2,p3}, {p4}, {p5}, singletons; best of g1 = p1, ranking p1>p3>p2.
DECISIONS = [
    {"photo_id": "p1", "verdict": "keep", "reason_codes": ["KEEP_BEST_OF_GROUP"], "group_id": "g1"},
    {"photo_id": "p2", "verdict": "reject", "reason_codes": ["DUPLICATE_OF:p1"], "group_id": "g1"},
    {"photo_id": "p3", "verdict": "reject", "reason_codes": ["BLUR_FOCUS"], "group_id": "g1"},
    {"photo_id": "p4", "verdict": "review", "reason_codes": ["REVIEW_POLICY_GROUP_EYES"]},
    {"photo_id": "p5", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"]},
    {"photo_id": "p6", "verdict": "reject", "reason_codes": ["EXPOSURE_UNDER"]},
    {"photo_id": "p7", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"]},
    {"photo_id": "p8", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"]},
]
GROUPS = {"n_groups": 1, "groups": [{"group_id": "g1", "photo_ids": ["p1", "p2", "p3"], "size": 3,
                                     "best_photo_id": "p1",
                                     "ranking": [{"photo_id": "p1"}, {"photo_id": "p3"}, {"photo_id": "p2"}]}],
          "singletons": ["p4", "p5", "p6", "p7", "p8"]}
# Shape of a real agent trace (run_agent_* on 2026-10-05): latency on tool_result lines, decisions in
# interrupt_resumed nested one list per interrupt.
TRACE = [
    {"seq": 0, "type": "run_header", "run_id": "run_t", "model": "m", "provider": "anthropic", "n_photos": 8,
     "n_items": 2},
    {"seq": 1, "type": "tool_call", "tool": "cluster_near_duplicates"},
    {"seq": 2, "type": "tool_result", "tool": "cluster_near_duplicates", "latency_ms": 10, "result": {}},
    {"seq": 3, "type": "tool_call", "tool": "score_sharpness", "args": {"photo_id": "p3", "roi": "full"}},
    {"seq": 4, "type": "tool_result", "tool": "score_sharpness", "latency_ms": 30,
     "result": {"label": "borderline", "lap_var": 80}},
    {"seq": 5, "type": "model_reasoning", "text": "borderline -> faces", "hook": "policy_P2"},
    {"seq": 6, "type": "tool_call", "tool": "score_sharpness", "args": {"photo_id": "p3", "roi": "faces"}},
    {"seq": 7, "type": "tool_result", "tool": "score_sharpness", "latency_ms": 50, "result": {"label": "sharp"}},
    {"seq": 8, "type": "tool_call", "tool": "request_human_approval",
     "args": {"items": [{"photo_id": "p4", "proposed": {"reject": ["p4"]}}]}},
    {"seq": 9, "type": "interrupt", "pending": []},
    {"seq": 10, "type": "interrupt_resumed",
     "decisions": [[{"id": "p4", "human_verdict": {"reject": ["p4"]}}]]},
    {"seq": 11, "type": "tool_call", "tool": "write_decision"},
    {"seq": 12, "type": "run_end", "summary": {"tool_calls": 4, "model_calls": 3, "wall_s": 12.5}},
]


@pytest.fixture()
def fixture_dirs(tmp_path: Path) -> tuple[Path, Path]:
    labels, results = tmp_path / "labels", tmp_path / "results"
    labels.mkdir()
    (results / EV).mkdir(parents=True)
    with (labels / f"{EV}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for lab, rows in (("photographer:alice", LABELS_A), ("photographer:bob", LABELS_B)):
            for pid, g, v, r, b in rows:
                w.writerow([pid, g, v, r, b, lab, "2026-10-05T00:00:00Z", ""])
        w.writerow(["p9", "", "", "", "", "photographer:alice", "", "todo row is skipped"])
    with (results / EV / "decisions.jsonl").open("w") as f:
        f.write(json.dumps({**DECISIONS[0], "verdict": "reject"}) + "\n")  # superseded by the later line
        for d in DECISIONS:
            f.write(json.dumps(d) + "\n")
    (results / EV / "groups.json").write_text(json.dumps(GROUPS))
    (results / EV / "trace.jsonl").write_text("".join(json.dumps(t) + "\n" for t in TRACE))
    (results / f"{EV}.run2").mkdir()
    run2 = [dict(d) for d in DECISIONS]
    run2[5]["verdict"] = "keep"  # one of eight differs
    (results / f"{EV}.run2" / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in run2))
    return labels, results


def test_event_metrics(fixture_dirs: tuple[Path, Path]) -> None:
    labels, results = fixture_dirs
    m = cm.evaluate_event(EV, labels, results)
    assert m["primary_labeller"] == "photographer:alice" and m["n_labelled"] == 8 and m["n_scored"] == 8

    pr = m["per_reason"]
    # DUPLICATE family: human p2 (BURST_NOT_BEST), agent p2 (DUPLICATE_OF) -> TP 1
    assert (pr["DUPLICATE"]["tp"], pr["DUPLICATE"]["fp"], pr["DUPLICATE"]["fn"]) == (1, 0, 0)
    # BLUR_FOCUS: agent p3 (human said motion) = FP, human p7 missed = FN
    assert (pr["BLUR_FOCUS"]["tp"], pr["BLUR_FOCUS"]["fp"], pr["BLUR_FOCUS"]["fn"]) == (0, 1, 1)
    assert pr["BLUR_FOCUS"]["f1"] == 0.0
    assert pr["BLUR_MOTION"]["recall"] == 0.0 and pr["BLUR_MOTION"]["precision"] is None
    assert pr["EYES_CLOSED"]["fn"] == 1  # agent sent p4 to review with a REVIEW_* code
    assert pr["EXPOSURE_UNDER"]["fp"] == 1

    # reject: human rejects p2,p3,p4,p7; agent rejects p2,p3,p6 (p4 review = not reject)
    rj = m["reject"]
    assert (rj["tp"], rj["fp"], rj["fn"], rj["tn"]) == (2, 1, 2, 3)
    assert rj["precision"] == pytest.approx(2 / 3, abs=1e-4) and rj["recall"] == 0.5
    assert m["review"] == {"n": 1, "pct": 12.5, "human_keep": 0, "human_reject": 1}

    cl = m["clusters"]
    # agent groups: g1{p1,p2,p3}->G1 (3), {p4}, {p5}, and 3 singletons -> every agent group is pure
    assert cl["purity"] == 1.0
    # human G1 inside one agent group, G2 split -> 1/2
    assert cl["completeness"] == 0.5 and cl["n_human_groups_multi"] == 2
    # inverse purity: G1 3, G2 max 1, singletons 3 -> 7/8
    assert cl["inverse_purity"] == 0.875

    rt = m["retrieval"]
    # queries with gt: p1,p2,p3 (recall 1), p4,p5 (recall 0) -> 3/5
    assert rt["recall"] == 0.6 and rt["n_queries_with_gt"] == 5
    assert rt["precision"] == 1.0 and rt["map"] == 0.6

    bb = m["best_of_burst"]
    # G1: agent best p1 == human p1. G2 was split into solo groups; tie -> p5's group, whose only
    # member p5 the agent kept -> agreement. top-2 for G1: p1 in ranking[:2]; G2: no ranking -> [p5].
    assert bb["n_groups"] == 2
    assert bb["top1_agreement"] == 1.0 and bb["top2_agreement"] == 1.0

    ia = m["inter_annotator"]
    assert ia["n_overlap"] == 4 and ia["verdict_raw_agreement"] == 0.75
    # alice: keep,reject,reject,reject ; bob: keep,reject,keep,reject -> po .75, pe .5 -> kappa .5
    assert ia["verdict_kappa"] == pytest.approx(0.5, abs=1e-4)
    assert ia["best_of_group_kappa"] == 1.0  # both marked only p1 best on the overlap

    al = m["agent_loop"]
    assert al["tool_calls"] == 5 and al["tool_calls_per_photo"] == 0.625
    assert al["photos_escalated"] == 1 and al["pct_escalated"] == 12.5
    # resume line without a recorded source: not counted as a person (unknown), excluded from agreement
    assert al["escalation_human_agreed_n"] == 0 and al["unknown_source_answers_excluded"] == 1
    assert al["human_source_breakdown"] == {"unknown": 1}
    assert al["tool_calls_by_tool"]["score_sharpness"] == 2
    assert al["tool_latency_ms_mean"] == 30.0 and al["tool_latency_ms_p50"] == 30.0
    assert al["pauses"] == 1 and al["policy_hooks"] == {"policy_P2": 1}
    assert al["model_calls"] == 3 and al["run_wall_s"] == 12.5
    assert m["labels"] is True and m["run_counts"]["verdicts"] == {"keep": 4, "reject": 3, "review": 1}
    assert m["group_shape"]["size_histogram"] == {"3": 1} and m["group_shape"]["n_singletons"] == 5

    rb = m["robustness"]
    assert rb["n"] == 8 and rb["identical_verdict_pct"] == 87.5


def test_cli_outputs(fixture_dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    labels, results = fixture_dirs
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["compute_metrics.py", "--labels-dir", str(labels), "--results-dir",
                                     str(results), "--out-dir", str(out), "--no-plots", "--raw-ids",
                                     "--determinism", str(tmp_path / "none.json")])
    assert cm.main() == 0
    rep = json.loads((out / "metrics.json").read_text())
    assert rep["aggregate"]["reject"]["tp"] == 2
    assert rep["aggregate"]["robustness"]["identical_verdict_pct"] == 87.5
    md = (out / "metrics.md").read_text()
    assert "| DUPLICATE | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |" in md
    assert (out / "groups_purity.csv").read_text().count("\n") == 3  # header + G1 + G2
    blob = (out / "metrics.json").read_text()
    assert not any(f"\"p{i}\"" in blob for i in range(1, 9))  # committed output carries no photo ids


def test_kappa_fallback_matches_sklearn() -> None:
    a = ["keep", "keep", "reject", "reject", "keep", "reject"]
    b = ["keep", "reject", "reject", "reject", "keep", "keep"]
    assert cm.cohen_kappa(a, b) == pytest.approx(1 / 3, abs=1e-9)
    assert cm.cohen_kappa(["keep"] * 3, ["keep"] * 3) == 1.0


def _pivots_stub(lines: list[dict]) -> list[dict]:
    """Stands in for services/api/app/pivots.py: borderline score_sharpness followed by a re-score."""
    out = []
    for a, b in zip(lines, lines[1:] + [{}]):
        if a.get("tool") == "score_sharpness" and a.get("type") == "tool_result" and \
                (a.get("result") or {}).get("label") == "borderline":
            out.append({"kind": "sharpness_rescore", "policy": "Policy 2", "result_seq": a["seq"]})
    return out


def test_no_labels_path(fixture_dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No eval/labels/<eventId>.csv: label sections say so, agent-loop/run/group metrics are still produced,
    and a scripted reviewer's answers never count as human agreement."""
    _, results = fixture_dirs
    empty_labels = tmp_path / "nolabels"
    empty_labels.mkdir()
    rows = [dict(d) for d in DECISIONS]
    for d in rows:
        if d["photo_id"] == "p4":
            d.update(decided_by="scripted", human_source="scripted", verdict="reject")
    (results / EV / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in rows))

    m = cm.evaluate_event(EV, empty_labels, results, _pivots_stub)
    assert m["labels"] is False and m["n_labelled"] == 0 and m["inter_annotator"] is None
    assert "per_reason" not in m and "reject" not in m and "clusters" not in m and "best_of_burst" not in m
    al = m["agent_loop"]
    assert al["tool_calls"] == 5 and al["pauses"] == 1 and al["photos_escalated"] == 1
    assert al["human_source_breakdown"] == {"scripted": 1}
    assert al["scripted_answers_excluded"] == 1
    assert al["escalation_human_agreed_n"] == 0 and al["escalation_human_agreed_pct"] is None
    assert al["pivots"] == 1 and al["pivots_by_policy"] == {"Policy 2": 1}
    assert al["photos_agent_decided"] == 1 and al["tool_calls_per_agent_photo"] == 5.0
    assert m["run_counts"]["decided_by"]["scripted"] == 1 and m["run_counts"]["human_source"] == {"scripted": 1}
    assert m["group_shape"]["n_groups"] == 1 and m["robustness"]["n"] == 8

    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["compute_metrics.py", "--labels-dir", str(empty_labels), "--results-dir",
                                     str(results), "--out-dir", str(out), "--events", EV, "--no-plots",
                                     "--determinism", str(tmp_path / "none.json")])
    assert cm.main() == 0
    md = (out / "metrics.md").read_text()
    # per-reason table (8 reasons x 4 cells) + 5 label-dependent sections
    assert md.count("no photographer labels yet (n=0)") == 8 * 4 + 5
    assert "No team labels yet (n=0)" in md
    assert "## Agent runs" in md and "scripted 1" in md
    rep = json.loads((out / "metrics.json").read_text())
    assert rep["aggregate"]["no_labels"] is True and rep["aggregate"]["labelled_events"] == []
    csv_text = (out / "agent_metrics.csv").read_text()
    assert csv_text.count("\n") == 2 and "no photographer labels yet (n=0)" in csv_text


def test_missing_everything_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "r" / "ev_x").mkdir(parents=True)
    with pytest.raises(SystemExit):
        cm.evaluate_event("ev_x", tmp_path, tmp_path / "r")


def test_paused_run_reports_pending_not_human(fixture_dirs: tuple[Path, Path]) -> None:
    """An interrupt with no matching interrupt_resumed: escalated photos are `pending`, the run is
    awaiting_approval, and nothing counts as a human answer (regression: was reported as `human`)."""
    _, results = fixture_dirs
    paused = [t for t in TRACE if t["type"] not in ("interrupt_resumed", "run_end") and t["seq"] < 11]
    (results / EV / "trace.jsonl").write_text("".join(json.dumps(x) + "\n" for x in paused))
    rows = [dict(d) for d in DECISIONS]
    for d in rows:
        if d["photo_id"] == "p4":  # PHOTO# row of a pending item: worker verdict, needs_approval, no source
            d.update(decided_by="worker", needs_approval=True)
            d.pop("human_source", None)
    (results / EV / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in rows))
    al = cm.evaluate_event(EV, results / "nolabels", results)["agent_loop"]
    assert al["run_status"] == "awaiting_approval"
    assert al["human_source_breakdown"] == {"pending": 1} and al["escalations_pending"] == 1
    assert al["escalation_human_agreed_n"] == 0 and al["scripted_answers_excluded"] == 0
    assert al["pauses"] == 1 and al["resumes"] == 0


def test_answered_source_comes_from_decision_or_resume(fixture_dirs: tuple[Path, Path]) -> None:
    _, results = fixture_dirs
    tr = [dict(x) for x in TRACE]
    for x in tr:
        if x["type"] == "interrupt_resumed":
            x["human_source"] = "ui"
    (results / EV / "trace.jsonl").write_text("".join(json.dumps(x) + "\n" for x in tr))
    al = cm.evaluate_event(EV, results / "nolabels", results)["agent_loop"]
    assert al["run_status"] == "finished"
    assert al["human_source_breakdown"] == {"ui": 1} and al["escalations_pending"] == 0
    assert al["escalation_human_agreed_n"] == 1 and al["escalation_human_agreed_pct"] == 100.0


def test_trace_with_two_runs_uses_summary_run(fixture_dirs: tuple[Path, Path]) -> None:
    """trace.jsonl is append-only: an older partial run's lines must not leak into the metrics."""
    _, results = fixture_dirs
    old = [{**x, "run_id": "run_old"} for x in TRACE if x["seq"] < 9]  # paused, never resumed
    new = [{**x, "run_id": "run_new"} for x in TRACE]
    (results / EV / "trace.jsonl").write_text("".join(json.dumps(x) + "\n" for x in old + new))
    (results / EV / "summary.json").write_text(json.dumps({"run_id": "run_new"}))
    al = cm.evaluate_event(EV, results / "nolabels", results)["agent_loop"]
    assert al["runs_in_trace"] == 2 and al["selected_run"] == "run_new" and al["other_runs"] == ["run_old"]
    assert al["tool_calls"] == 5 and al["pauses"] == 1 and al["run_status"] == "finished"


def _write_labels(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for pid, v, lab, r in rows:
            w.writerow([pid, "", v, r, "0", lab, "2026-10-09T00:00:00Z", ""])


def test_labeller_classes() -> None:
    assert cm.labeller_class("photographer") == "photographer"
    assert cm.labeller_class("photographer:AB") == "photographer"
    assert cm.labeller_class("team:hs") == "team" and cm.labeller_class("TEAM") == "team"
    assert cm.labeller_class("lead-mechanism-test") == "lead_test"
    assert cm.labeller_class("alice") == "unclassified" and cm.labeller_class("") == "unclassified"


def test_team_labels_are_never_ground_truth(fixture_dirs: tuple[Path, Path], tmp_path: Path) -> None:
    """Team rows only feed their own kappa; lead-test rows are dropped; unclassified rows are only counted."""
    _, results = fixture_dirs
    labels = tmp_path / "team_labels"
    labels.mkdir()
    team = [(p, v, "team:hs", "") for p, v in
            (("p1", "keep"), ("p2", "reject"), ("p3", "reject"), ("p4", "keep"), ("p5", "keep"), ("p6", "reject"))]
    _write_labels(labels / f"{EV}.csv", team + [("p7", "reject", "lead-mechanism-test", "BLUR_FOCUS"),
                                                ("p8", "keep", "alice", "")])
    m = cm.evaluate_event(EV, labels, results)
    assert m["labels"] is False and m["n_scored"] == 0 and "reject" not in m  # no photographer -> no scoring
    assert m["label_counts"] == {"photographer": {"rows": 0, "labellers": 0},
                                 "team": {"rows": 6, "labellers": 1},
                                 "unclassified": {"rows": 1, "labellers": 1}}
    t = m["team_labels"]
    # final verdicts (review -> keep): p1 keep, p2 reject, p3 reject, p4 keep(review), p5 keep, p6 reject = all agree
    assert t["n_rows"] == 6 and t["vs_final"]["n"] == 6 and t["vs_final"]["kappa"] == 1.0
    assert t["vs_photographer"]["n"] == 0 and t["vs_photographer"]["kappa"] is None


def test_runs_file_overrides_trace_source(fixture_dirs: tuple[Path, Path]) -> None:
    """S3 trace says ui; the RUN# item (relabelled in DynamoDB) says lead_test -> lead_test wins."""
    _, results = fixture_dirs
    tr = [dict(x) for x in TRACE]
    for x in tr:
        if x["type"] == "interrupt_resumed":
            x["human_source"] = "ui"
    (results / EV / "trace.jsonl").write_text("".join(json.dumps(x) + "\n" for x in tr))
    rid = next(x["run_id"] for x in TRACE if x.get("run_id"))
    runs = [{"sk": f"RUN#{rid}", "resume_decisions": [{"human_source": "lead_test", "labeller": "lead-mechanism-test"}]}]
    m = cm.evaluate_event(EV, results / "nolabels", results, None, runs)
    al = m["agent_loop"]
    assert al["human_source_breakdown"] == {"lead_test": 1} and al["lead_test_answers_excluded"] == 1
    assert al["escalation_human_agreed_n"] == 0
    assert m["agent_runs"][0]["answer_sources"] == {"lead_test": 1}


def test_trace_active_seconds_excludes_waiting() -> None:
    tr = [{"seq": 0, "type": "run_header", "ts": "2026-10-09T00:00:00Z"},
          {"seq": 1, "type": "interrupt", "ts": "2026-10-09T00:00:10Z"},
          {"seq": 2, "type": "interrupt_resumed", "ts": "2026-10-09T05:00:00Z"},
          {"seq": 3, "type": "run_end", "ts": "2026-10-09T05:00:05Z"}]
    assert cm.trace_active_s(tr) == 15.0


def test_aliaser_scrubs_ids() -> None:
    eid = "9e3d2c71-4b8a-4f05-a6d2-7c19be50f3a4"
    a = cm.Aliaser({"E42": eid})
    rid = "run_aws_e42_agentcore_20261008T050441Z"
    out = a.scrub({"run_id": rid, "note": f"group g_{eid}_3 of {eid[:8]}", "x": [eid]}, eid, {rid})
    assert out == {"run_id": "E42-agentcore-20261008T050441Z", "note": "group g_E42_3 of E42", "x": ["E42"]}
    with pytest.raises(SystemExit):
        cm.assert_no_ids(f"leak {eid}", "metrics.md")


def test_team_csv_file_is_read_separately(fixture_dirs: tuple[Path, Path], tmp_path: Path) -> None:
    _, results = fixture_dirs
    labels = tmp_path / "split"
    labels.mkdir()
    _write_labels(labels / f"{EV}.team.csv", [("p1", "keep", "team:hs", ""), ("p2", "reject", "team:hs", "")])
    _write_labels(labels / f"{EV}.team.todo.csv", [("p3", "keep", "team:hs", "")])  # todo files never read
    m = cm.evaluate_event(EV, labels, results)
    assert m["label_counts"]["team"]["rows"] == 2 and m["team_labels"]["vs_final"]["n"] == 2
