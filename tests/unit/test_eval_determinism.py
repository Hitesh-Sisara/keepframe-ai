"""eval/agent_determinism.py: scripted answers pass validate_answers, compare and $ arithmetic, no ids out."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from keepframe.tools.approval import validate_answers

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("agent_determinism", ROOT / "eval" / "agent_determinism.py")
ad = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
assert spec and spec.loader
spec.loader.exec_module(ad)


def test_scripted_answers_validate_for_groups_and_singletons() -> None:
    res = {"pending_approvals": [
        {"interrupt_id": "i1", "items": [{"group_id": "g1", "photo_id": None,
                                          "proposed": {"keep": ["a"], "reject": [], "review": ["b"]}}]},
        {"interrupt_id": "i2", "items": [{"group_id": None, "photo_id": "c", "proposed": {"verdict": "review"}}]},
        {"interrupt_id": "i3", "items": [{"group_id": None, "photo_id": "d", "proposed": {"reject": ["d"]}}]},
    ]}
    ans = ad.scripted_answers(res)
    assert ans[0]["human_verdict"] == {"keep": ["a"], "review": ["b"]}
    assert ans[1]["human_verdict"] == "review" and ans[2]["human_verdict"] == "reject"
    validate_answers(ans, {"g1": ["a", "b"]})


def _rep(root: Path, rep: str, rows: list[dict], usage: dict, approvals: list[dict]) -> None:
    ev, run = "ev_test", f"run_det_{rep}"
    d = root / rep / "s3" / "results" / ev
    d.mkdir(parents=True)
    old = {"photo_id": "zz", "verdict": "keep", "run_id": "run_live_old", "decided_by": "agent"}
    (d / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [old, *rows]))
    tr = [{"run_id": run, "seq": i, "type": "tool_call", "tool": "request_human_approval", "args": {"items": [a]}}
          for i, a in enumerate(approvals)]
    (d / "trace.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tr))
    (root / rep / "result.json").write_text(json.dumps({
        "rep": rep, "event_alias": "E99", "event_id": ev, "run_id": run, "model": "claude-sonnet-5",
        "status": "done", "pauses": len(approvals), "wall_s": 10.0, "summary": {"items": 2, "tool_calls": 5},
        "usage_per_invocation": [usage]}))


def test_compare(tmp_path: Path) -> None:
    u = {"input_tokens": 1_000_000, "output_tokens": 1000, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "cycles": 4, "cycles_zero_output": 2, "latency_ms": 0}
    row = {"run_id": "run_det_A", "decided_by": "agent"}
    _rep(tmp_path, "A", [{**row, "photo_id": "p1", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"]},
                         {**row, "photo_id": "p2", "verdict": "review", "reason_codes": ["X"],
                          "decided_by": "scripted"}],
         u, [{"photo_id": "p2", "proposed": {"review": ["p2"]}}])
    row = {"run_id": "run_det_B", "decided_by": "agent"}
    _rep(tmp_path, "B", [{**row, "photo_id": "p1", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"]},
                         {**row, "photo_id": "p2", "verdict": "reject", "reason_codes": ["Y"],
                          "decided_by": "scripted"}],
         u, [{"photo_id": "p2", "proposed": {"reject": ["p2"]}}])
    out = tmp_path / "out"
    rep = ad.compare(tmp_path, ["A", "B"], out)
    assert rep["photos_decided_in_both"] == 2 and rep["only_in_one_run"] == 0  # the old live run is ignored
    assert rep["identical_verdict_pct"] == 50.0 and rep["identical_reasons_pct"] == 50.0
    assert rep["by_decided_by"]["agent"]["identical_verdict_pct"] == 100.0
    assert rep["by_decided_by"]["scripted"]["identical_verdict_pct"] == 0.0
    assert rep["identical_proposal_pct"] == 0.0 and rep["escalated_sets_identical"] is True
    assert rep["usd_per_event_mean"] == 2.01  # 1M in x $2 + 1k out x $10 / 1M
    md = (out / "determinism.md").read_text()
    assert "p1" not in md and "run_det_A" not in md and "ev_test" not in md
    assert (out / "raw" / "determinism_pairs.json").exists()


def test_cost_unknown_model_is_none() -> None:
    assert ad.cost_usd("other-model", {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
                                       "cache_write_tokens": 0}) is None
