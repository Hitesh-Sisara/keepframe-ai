"""The agent reads and writes per-run results keys (results/<E>/<runId>/...) plus the latest copy for one release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from keepframe.agent import strands_agent as sa
from keepframe.agent._tool_stubs import demo_scenario
from keepframe.agent.fake_model import FakeModel
from keepframe.io import manifest, proxies
from keepframe.store import dynamodb


@pytest.fixture(autouse=True)
def local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    monkeypatch.setenv("CULL_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("CULL_TOOLS", "stub")
    monkeypatch.setenv("CULL_MODEL_PROVIDER", "fake")
    monkeypatch.setenv("CULL_METRICS", "off")
    monkeypatch.setenv("CULL_TRACE_SINKS", "ddb,s3")
    monkeypatch.setenv("CULL_DDB", "local")
    proxies.reset_store()
    dynamodb.reset_backend()
    yield tmp_path
    proxies.reset_store()
    dynamodb.reset_backend()


def _write(root: Path, key: str, doc: Any) -> None:
    p = root / key
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(doc, list):
        p.write_text("".join(json.dumps(r) + "\n" for r in doc))
    else:
        p.write_text(json.dumps(doc))


def _worker_files(root: Path, prefix: str, group: str, verdict: str) -> None:
    _write(root, f"{prefix}groups.json", {"groups": [{"group_id": group, "members": ["a", "b"], "needs_approval": True}],
                                          "singletons": ["c"]})
    _write(root, f"{prefix}decisions.jsonl",
           [{"photo_id": p, "verdict": verdict, "reason_codes": ["KEEP_SINGLETON"], "confidence": 0.9,
             "decided_by": "worker"} for p in ("a", "b", "c")])


@pytest.fixture()
def manifest_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"photo_id": p, "event_id": "ev1"} for p in ("a", "b", "c")]
    monkeypatch.setattr(manifest, "read_manifest_dicts", lambda event_id, prefix="manifests/": rows)


@pytest.mark.usefixtures("manifest_rows")
def test_load_work_prefers_the_runs_own_worker_results(local_env: Path) -> None:
    _worker_files(local_env, "results/ev1/", "g_latest", "review")      # a later run overwrote the latest copy
    _worker_files(local_env, "results/ev1/run_a/", "g_run_a", "keep")   # this run's own worker output
    ctx = sa.load_work("ev1", "run_a")
    assert set(ctx.groups) == {"g_run_a"}
    ctx_old = sa.load_work("ev1", "run_pre_versioning")                 # no per-run dir: latest copy
    assert set(ctx_old.groups) == {"g_latest"}


@pytest.mark.usefixtures("manifest_rows")
def test_load_work_missing_worker_results_names_both_keys(local_env: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"results/ev1/run_x/groups\.json .*results/ev1/groups\.json"):
        sa.load_work("ev1", "run_x")


def test_agent_summary_written_versioned_and_latest(local_env: Path) -> None:
    res = sa.run_event_agent("ev_demo", "run_s1", model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "paused"
    resumed = sa.resume_with_decisions("run_s1", [
        {"group_id": "g_412", "human_verdict": {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]}}],
        model=FakeModel(), scenario=demo_scenario(), source="ui")
    assert resumed["status"] == "done"
    v = local_env / "results/ev_demo/run_s1/agent_summary.json"
    latest = local_env / "results/ev_demo/agent_summary.json"
    assert v.read_bytes() == latest.read_bytes()
    assert json.loads(v.read_text())["run_id"] == "run_s1"


def _rows(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_write_decision_appends_to_run_file_and_latest(local_env: Path) -> None:
    from keepframe.tools.decision import write_decision

    _worker_files(local_env, "results/ev1/run_a/", "g1", "review")  # the worker's rows for the same run
    _worker_files(local_env, "results/ev1/", "g1", "review")
    d = {"photo_id": "a", "verdict": "keep", "reason_codes": ["KEEP_BEST_OF_GROUP"], "confidence": 0.95}
    assert write_decision("ev1", [d], run_id="run_a")["written"] == 1
    run_rows, latest_rows = _rows(local_env / "results/ev1/run_a/decisions.jsonl"), _rows(local_env / "results/ev1/decisions.jsonl")
    for rows in (run_rows, latest_rows):
        assert len(rows) == 4 and [r["decided_by"] for r in rows[:3]] == ["worker"] * 3  # appended, nothing lost
        assert rows[-1]["photo_id"] == "a" and rows[-1]["verdict"] == "keep" and rows[-1]["decided_by"] == "agent"
    assert run_rows[-1]["run_id"] == "run_a"
    assert not (local_env / "results/ev1/run_b").exists()  # another run's evidence is untouched


def test_write_decision_without_run_id_only_latest(local_env: Path) -> None:
    from keepframe.tools.decision import write_decision

    d = {"photo_id": "z", "verdict": "review", "reason_codes": ["KEEP_SINGLETON"], "confidence": 0.5}
    write_decision("ev2", [d])
    assert [r["photo_id"] for r in _rows(local_env / "results/ev2/decisions.jsonl")] == ["z"]
    assert [p.name for p in (local_env / "results/ev2").iterdir()] == ["decisions.jsonl"]


def test_write_decision_rejects_unsafe_run_id(local_env: Path) -> None:
    from keepframe.tools.decision import write_decision

    d = {"photo_id": "z", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"], "confidence": 0.9}
    with pytest.raises(ValueError):
        write_decision("ev3", [d], run_id="../escape")


def test_trace_per_run_file_and_latest_copy(local_env: Path) -> None:
    human = [{"group_id": "g_412", "human_verdict": {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]}}]
    for rid in ("run_t1", "run_t2"):
        assert sa.run_event_agent("ev_demo", rid, model=FakeModel(), scenario=demo_scenario())["status"] == "paused"
        sa.resume_with_decisions(rid, human, model=FakeModel(), scenario=demo_scenario(), source="ui")
    for rid in ("run_t1", "run_t2"):
        lines = _rows(local_env / f"results/ev_demo/{rid}/trace.jsonl")
        assert {x["run_id"] for x in lines} == {rid}
        assert [x["seq"] for x in lines] == list(range(len(lines)))  # pause + resume, gap-free, no duplicates
        assert lines[0]["type"] == "run_header" and lines[-1]["type"] == "run_end"
    latest = _rows(local_env / "results/ev_demo/trace.jsonl")
    per_run = [_rows(local_env / f"results/ev_demo/{r}/trace.jsonl") for r in ("run_t1", "run_t2")]
    assert latest == per_run[0] + per_run[1]
